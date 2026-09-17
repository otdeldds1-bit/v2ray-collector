import json, os, socket, statistics, subprocess, tempfile, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse
import base64

from parser import link_to_outbound
from sources import TEST_URL

XRAY_BIN = os.environ.get("XRAY_BIN", "./xray")

# --- Настройки скорости ---
TCP_TIMEOUT = 2.0           # быстрый TCP-пре-чек (сек)
SPEED_TIMEOUT = 5           # один curl-замер
BLOCKED_TIMEOUT = 4         # проверка Facebook
PAUSE_BETWEEN_TESTS = 0.3   # пауза между повторами
MAX_WORKERS = 100           # параллельных xray-процессов
REPEAT_TESTS = 2            # 2 прогона (было 3) — компромисс скорость/стабильность
MAX_STDEV_RATIO = 0.5       # чуть мягче (было 0.4)
MIN_SPEED_KBPS = 800        # отсекаем всё медленнее 800 KB/s

# --- Общий дедлайн теста (после него возвращаем что успели) ---
GLOBAL_DEADLINE_SEC = 25 * 60

# --- Проверка обхода блокировок РФ ---
BLOCKED_URL = "https://www.facebook.com"


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _extract_host_port(link):
    """Достаём host:port из share-ссылки без xray — быстро."""
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
    """Быстрая проверка: открыт ли TCP-порт на сервере. 90% мёртвых — отсеются тут."""
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
                "--connect-timeout", "3",
                "--max-time", str(SPEED_TIMEOUT),
                TEST_URL,
            ],
            capture_output=True, text=True, timeout=SPEED_TIMEOUT + 1,
        )
        parts = result.stdout.strip().split()
        if len(parts) != 3:
            return None
        code, t_total, speed = parts[0], float(parts[1]), float(parts[2])
        if code != "200" or (speed / 1024) < MIN_SPEED_KBPS:
            return None
        return {"latency": t_total, "speed_kbps": speed / 1024}
    except Exception:
        return None


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
        time.sleep(0.25)   # xray поднимается очень быстро

        # --- Первый (и главный) замер ---
        res1 = _run_speed_test(port)
        if not res1:
            return None

        # --- Проверка Facebook (только если скорость уже ок) ---
        if not _run_blocked_test(port):
            return None

        # --- Второй замер для стабильности ---
        time.sleep(PAUSE_BETWEEN_TESTS)
        res2 = _run_speed_test(port)
        if not res2:
            return None

        speeds = [res1["speed_kbps"], res2["speed_kbps"]]
        latencies = [res1["latency"], res2["latency"]]

        avg_speed = statistics.mean(speeds)
        avg_latency = statistics.mean(latencies)
        stdev = statistics.stdev(speeds)

        if avg_speed > 0 and (stdev / avg_speed) > MAX_STDEV_RATIO:
            return None

        stability_score = round(avg_speed / (1 + stdev), 1)
        return {
            "link": link,
            "latency": round(avg_latency, 3),
            "speed_kbps": round(avg_speed, 1),
            "stability_score": stability_score,
        }
    except Exception:
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
    """Быстрый TCP-пре-чек. Возвращает только те ссылки, чьи сервера отвечают."""
    print(f"--- Этап 1: TCP-проверка {len(links)} конфигов ---")
    alive = []
    with ThreadPoolExecutor(max_workers=250) as ex:   # TCP-чек — только сеть, можно много
        futures = {ex.submit(_tcp_alive, *_extract_host_port(l)): l for l in links}
        done = 0
        for fut in as_completed(futures):
            done += 1
            if fut.result():
                alive.append(futures[fut])
            if done % 250 == 0:
                print(f"  TCP: {done}/{len(links)}  (живых: {len(alive)})")
    print(f"--- Этап 1 готов: {len(alive)} из {len(links)} отвечают по TCP ---")
    return alive


def test_many(links):
    """Двухэтапный тест: TCP → полный xray-тест. С общим дедлайном."""
    t_start = time.time()

    # Этап 1: TCP
    links = _stage1_tcp(links)
    if not links:
        return []

    # Этап 2: полный тест
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

            # Жёсткий дедлайн — что успели, то и берём
            if time.time() - t_start > GLOBAL_DEADLINE_SEC:
                print(f"!!! Дедлайн {GLOBAL_DEADLINE_SEC}с достигнут, "
                      f"останавливаюсь на {len(working)} рабочих")
                for f in futures:
                    f.cancel()
                break

    return working
