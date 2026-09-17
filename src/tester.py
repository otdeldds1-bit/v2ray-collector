import json, os, socket, statistics, subprocess, tempfile, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse
import base64
from collections import Counter

from parser import link_to_outbound
from sources import TEST_URL

XRAY_BIN = os.environ.get("XRAY_BIN", "./xray")

# --- Ослабленные пороги (для диагностики) ---
TCP_TIMEOUT = 2.0
SPEED_TIMEOUT = 6
PAUSE_BETWEEN_TESTS = 0.3
MAX_WORKERS = 100
REPEAT_TESTS = 2
MAX_STDEV_RATIO = 0.8          # было 0.5 — теперь мягче
MIN_SPEED_KBPS = 200           # было 800 — теперь 200 KB/s
GLOBAL_DEADLINE_SEC = 25 * 60

# --- Facebook отключён как обязательный фильтр ---
CHECK_FACEBOOK = False         # <-- включим позже, когда убедимся, что список не пустой
BLOCKED_URL = "https://www.facebook.com"
BLOCKED_TIMEOUT = 4

# Глобальный счётчик причин отбраковки
REJECT = Counter()


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _extract_host_port(link):
    try:
        if link.startswith("vmess://"):
            body = link[8:].split("#", 1)[0].strip()
            body += "=" * (-len(body) % 4)
            data = json.loads(base64.b64decode(body).decode("utf-8", "ignore"))
            return data.get("add", ""), int(data.get("port", 0))
        u = urlparse(link)
        return u.hostname or "", int(u.port or 0)
    except Exception:
        return "", 0


def _tcp_alive(host, port, timeout=TCP_TIMEOUT):
    if not host or not port:
        return False
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


def _run_speed_test(port):
    try:
        result = subprocess.run(
            [
                "curl", "-x", f"socks5h://127.0.0.1:{port}",
                "-o", "/dev/null", "-s",
                "-w", "%{http_code} %{time_total} %{speed_download}",
                "--connect-timeout", "4",
                "--max-time", str(SPEED_TIMEOUT),
                TEST_URL,
            ],
            capture_output=True, text=True, timeout=SPEED_TIMEOUT + 1,
        )
        parts = result.stdout.strip().split()
        if len(parts) != 3:
            return None, "bad_output"
        code, t_total, speed = parts[0], float(parts[1]), float(parts[2])
        speed_kbps = speed / 1024
        if code != "200":
            return None, f"http_{code}"
        if speed_kbps < MIN_SPEED_KBPS:
            return None, "too_slow"
        return {"latency": t_total, "speed_kbps": speed_kbps}, None
    except subprocess.TimeoutExpired:
        return None, "timeout"
    except Exception:
        return None, "exception"


def _run_blocked_test(port):
    try:
        result = subprocess.run(
            [
                "curl", "-x", f"socks5h://127.0.0.1:{port}",
                "-o", "/dev/null", "-s",
                "-w", "%{http_code}",
                "--connect-timeout", "2",
                "--max-time", str(BLOCKED_TIMEOUT),
                BLOCKED_URL,
            ],
            capture_output=True, text=True, timeout=BLOCKED_TIMEOUT + 1,
        )
        return result.stdout.strip() in ("200", "301", "302")
    except Exception:
        return False


def _test_one(link):
    outbound = link_to_outbound(link)
    if not outbound:
        REJECT["xray_parse_failed"] += 1
        return None

    port = _free_port()
    cfg = {
        "log": {"loglevel": "none"},
        "inbounds": [{
            "port": port, "listen": "127.0.0.1",
            "protocol": "socks",
            "settings": {"udp": False, "auth": "noauth"},
        }],
        "outbounds": [outbound],
    }

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(cfg, f)
        cfg_path = f.name

    proc = subprocess.Popen(
        [XRAY_BIN, "run", "-c", cfg_path],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        time.sleep(0.3)

        res1, err1 = _run_speed_test(port)
        if not res1:
            REJECT[f"test1:{err1}"] += 1
            return None

        if CHECK_FACEBOOK and not _run_blocked_test(port):
            REJECT["facebook_blocked"] += 1
            return None

        time.sleep(PAUSE_BETWEEN_TESTS)
        res2, err2 = _run_speed_test(port)
        if not res2:
            REJECT[f"test2:{err2}"] += 1
            return None

        speeds = [res1["speed_kbps"], res2["speed_kbps"]]
        latencies = [res1["latency"], res2["latency"]]
        avg_speed = statistics.mean(speeds)
        avg_latency = statistics.mean(latencies)
        stdev = statistics.stdev(speeds)

        if avg_speed > 0 and (stdev / avg_speed) > MAX_STDEV_RATIO:
            REJECT["unstable"] += 1
            return None

        stability_score = round(avg_speed / (1 + stdev), 1)
        return {
            "link": link,
            "latency": round(avg_latency, 3),
            "speed_kbps": round(avg_speed, 1),
            "stability_score": stability_score,
        }
    except Exception as e:
        REJECT[f"outer:{type(e).__name__}"] += 1
        return None
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=1)
        except Exception:
            proc.kill()
        try:
            os.unlink(cfg_path)
        except OSError:
            pass


def _stage1_tcp(links):
    print(f"--- Этап 1: TCP-проверка {len(links)} конфигов ---")
    alive = []
    with ThreadPoolExecutor(max_workers=250) as ex:
        futures = {ex.submit(_tcp_alive, *_extract_host_port(l)): l for l in links}
        done = 0
        for fut in as_completed(futures):
            done += 1
            if fut.result():
                alive.append(futures[fut])
            if done % 500 == 0:
                print(f"  TCP: {done}/{len(links)}  (живых: {len(alive)})")
    print(f"--- Этап 1 готов: {len(alive)} из {len(links)} отвечают по TCP ---")
    return alive


def test_many(links):
    t_start = time.time()
    links = _stage1_tcp(links)
    if not links:
        print("!!! После TCP-чека не осталось ни одного конфига")
        return []

    print(f"--- Этап 2: полный тест {len(links)} конфигов ---")
    working = []
    total = len(links)
    done = 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {ex.submit(_test_one, l): l for l in links}
        for fut in as_completed(futures):
            done += 1
            try:
                r = fut.result(timeout=1)
            except Exception:
                r = None
            if r:
                working.append(r)
                print(f"[{done}/{total}] OK  {r['latency']}s  "
                      f"{r['speed_kbps']} KB/s  (stab: {r['stability_score']})")
            elif done % 50 == 0:
                elapsed = time.time() - t_start
                print(f"[{done}/{total}] ...  прошло {elapsed:.0f}с, живых: {len(working)}")

            if time.time() - t_start > GLOBAL_DEADLINE_SEC:
                print(f"!!! Дедлайн {GLOBAL_DEADLINE_SEC}с достигнут")
                for f in futures:
                    f.cancel()
                break

    print("\n=== Причины отбраковки на этапе 2 ===")
    for reason, count in REJECT.most_common():
        print(f"  {reason}: {count}")
    print(f"=== Итого рабочих: {len(working)} ===")

    return working
