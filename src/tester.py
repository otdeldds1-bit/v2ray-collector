import json, os, socket, statistics, subprocess, tempfile, threading, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse
import base64
from collections import Counter

from parser import link_to_outbound
from sources import TEST_URL

XRAY_BIN = os.environ.get("XRAY_BIN", "./xray")

# --- Тайминги ---
TCP_TIMEOUT = 2.0
SPEED_TIMEOUT = 8
BLOCKED_TIMEOUT = 4
PAUSE_BETWEEN_TESTS = 0.5
XRAY_READY_TIMEOUT = 3.0     # сколько ждём, пока xray откроет SOCKS-порт
XRAY_READY_POLL = 0.1

# --- Параллелизм ---
MAX_WORKERS = 60             # 100 → 60, меньше коллизий
REPEAT_TESTS = 2
MAX_STDEV_RATIO = 0.9        # мягче, чтобы не отсеивать «нормальные»
MIN_SPEED_KBPS = 50          # 200 → 50, нам нужны хотя бы какие-то рабочие

GLOBAL_DEADLINE_SEC = 25 * 60

# --- Опциональная проверка обхода РФ (выключена для диагностики) ---
CHECK_FACEBOOK = False
BLOCKED_URL = "https://www.facebook.com"

# --- Диагностика ---
DEBUG_LOG_LIMIT = 15         # сколько первых ошибок xray залогировать
REJECT = Counter()
REJECT_SAMPLES = []          # (link, err_snippet)
_debug_lock = threading.Lock()

# --- Пул портов (thread-safe, без коллизий) ---
_port_lock = threading.Lock()
_used_ports = set()


def _free_port():
    with _port_lock:
        for _ in range(200):
            with socket.socket() as s:
                s.bind(("127.0.0.1", 0))
                p = s.getsockname()[1]
            if p not in _used_ports:
                _used_ports.add(p)
                return p
        raise RuntimeError("no free ports")


def _port_ready(port, timeout=XRAY_READY_TIMEOUT):
    """Ждём, пока xray реально откроет SOCKS-порт."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.3):
                return True
        except OSError:
            time.sleep(XRAY_READY_POLL)
    return False


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
            capture_output=True, text=True, timeout=SPEED_TIMEOUT + 2,
        )
        parts = result.stdout.strip().split()
        if len(parts) != 3:
            return None, f"bad_output:{result.stdout.strip()[:30]}"
        code, t_total, speed = parts[0], float(parts[1]), float(parts[2])
        speed_kbps = speed / 1024
        if code != "200":
            return None, f"http_{code}"
        if speed_kbps < MIN_SPEED_KBPS:
            return None, "too_slow"
        return {"latency": t_total, "speed_kbps": speed_kbps}, None
    except subprocess.TimeoutExpired:
        return None, "timeout"
    except Exception as e:
        return None, f"exc:{type(e).__name__}"


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


def _save_debug(link, err_text, cfg_json):
    """Сохраняем первые N ошибок xray, чтобы понять, что не так."""
    global REJECT_SAMPLES
    with _debug_lock:
        if len(REJECT_SAMPLES) >= DEBUG_LOG_LIMIT:
            return
        err_snippet = (err_text or "").strip().splitlines()[-3:]
        REJECT_SAMPLES.append({
            "link": link[:90],
            "err": " | ".join(err_snippet)[:400],
            "cfg": cfg_json[:400],
        })


def _test_one(link):
    outbound = link_to_outbound(link)
    if not outbound:
        REJECT["xray_parse_failed"] += 1
        return None

    port = _free_port()
    cfg = {
        "log": {"loglevel": "warning"},
        "inbounds": [{
            "port": port, "listen": "127.0.0.1",
            "protocol": "socks",
            "settings": {"udp": False, "auth": "noauth"},
        }],
        "outbounds": [outbound],
    }
    cfg_json = json.dumps(cfg, ensure_ascii=False)

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
        f.write(cfg_json)
        cfg_path = f.name
    err_path = cfg_path + ".err"

    proc = subprocess.Popen(
        [XRAY_BIN, "run", "-c", cfg_path],
        stdout=subprocess.DEVNULL,
        stderr=open(err_path, "w"),
    )

    try:
        # Ждём, пока xray откроет SOCKS-порт
        if not _port_ready(port):
            err_text = ""
            try:
                with open(err_path) as ef:
                    err_text = ef.read()
            except Exception:
                pass
            _save_debug(link, err_text, cfg_json)
            REJECT["xray_not_ready"] += 1
            return None

        # --- Тест №1 ---
        res1, err1 = _run_speed_test(port)
        if not res1:
            REJECT[f"test1:{err1}"] += 1
            if err1 == "http_000":
                try:
                    with open(err_path) as ef:
                        _save_debug(link, ef.read(), cfg_json)
                except Exception:
                    pass
            return None

        # --- Facebook (выключен для диагностики) ---
        if CHECK_FACEBOOK and not _run_blocked_test(port):
            REJECT["facebook_blocked"] += 1
            return None

        # --- Тест №2 ---
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

        return {
            "link": link,
            "latency": round(avg_latency, 3),
            "speed_kbps": round(avg_speed, 1),
            "stability_score": round(avg_speed / (1 + stdev), 1),
        }
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=1)
        except Exception:
            proc.kill()
        for p in (cfg_path, err_path):
            try:
                os.unlink(p)
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
            elif done % 100 == 0:
                elapsed = time.time() - t_start
                print(f"[{done}/{total}] ...  {elapsed:.0f}с, живых: {len(working)}, "
                      f"топ-причины: {dict(REJECT.most_common(3))}")

            if time.time() - t_start > GLOBAL_DEADLINE_SEC:
                print(f"!!! Дедлайн {GLOBAL_DEADLINE_SEC}с достигнут")
                for f in futures:
                    f.cancel()
                break

    print("\n=== Причины отбраковки на этапе 2 ===")
    for reason, count in REJECT.most_common():
        print(f"  {reason}: {count}")

    if REJECT_SAMPLES:
        print("\n=== Первые ошибки xray (для диагностики) ===")
        for i, s in enumerate(REJECT_SAMPLES, 1):
            print(f"\n[{i}] {s['link']}")
            print(f"    xray stderr: {s['err']}")
            print(f"    cfg: {s['cfg']}")

    print(f"\n=== Итого рабочих: {len(working)} ===")
    return working
