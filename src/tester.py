import json, os, socket, statistics, subprocess, tempfile, time
from concurrent.futures import ThreadPoolExecutor, as_completed

from parser import link_to_outbound
from sources import TEST_URL

XRAY_BIN = os.environ.get("XRAY_BIN", "./xray")

# --- Основные настройки ---
TIMEOUT = 8                 # сек на одну попытку curl
MAX_WORKERS = 60            # параллельных xray-процессов
REPEAT_TESTS = 3            # сколько раз повторять тест скорости
PAUSE_BETWEEN_TESTS = 1.0   # пауза между повторами (сек)
MAX_STDEV_RATIO = 0.4       # макс. допустимый разброс скорости (40% от средней)

# --- Проверка обхода блокировок РФ ---
# Если сайт не открывается через прокси — значит конфиг не годится для России.
BLOCKED_URL = "https://x.com" 
BLOCKED_TIMEOUT = 6


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _run_speed_test(port):
    """Один замер скорости через YouTube CDN."""
    try:
        result = subprocess.run(
            [
                "curl", "-x", f"socks5h://127.0.0.1:{port}",
                "-o", "/dev/null", "-s",
                "-w", "%{http_code} %{time_total} %{speed_download}",
                "--max-time", str(TIMEOUT),
                TEST_URL,
            ],
            capture_output=True, text=True, timeout=TIMEOUT + 2,
        )
        parts = result.stdout.strip().split()
        if len(parts) != 3:
            return None
        code, t_total, speed = parts[0], float(parts[1]), float(parts[2])
        if code != "200" or speed < 1024:      # минимум 1 KB/s
            return None
        return {"latency": t_total, "speed_kbps": speed / 1024}
    except Exception:
        return None


def _run_blocked_test(port):
    """Проверяет, открывается ли через прокси сайт, заблокированный в РФ."""
    try:
        result = subprocess.run(
            [
                "curl", "-x", f"socks5h://127.0.0.1:{port}",
                "-o", "/dev/null", "-s",
                "-w", "%{http_code}",
                "--max-time", str(BLOCKED_TIMEOUT),
                BLOCKED_URL,
            ],
            capture_output=True, text=True, timeout=BLOCKED_TIMEOUT + 2,
        )
        code = result.stdout.strip()
        return code in ("200", "301", "302")
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
            "port": port,
            "listen": "127.0.0.1",
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
        time.sleep(0.4)   # дать xray подняться

        # --- 1. Мульти-тест скорости (REPEAT_TESTS раз) ---
        speeds, latencies = [], []
        for i in range(REPEAT_TESTS):
            if i > 0:
                time.sleep(PAUSE_BETWEEN_TESTS)
            res = _run_speed_test(port)
            if res:
                speeds.append(res["speed_kbps"])
                latencies.append(res["latency"])

        # Если хоть один прогон провалился — конфиг нестабилен, отбрасываем
        if len(speeds) < REPEAT_TESTS:
            return None

        avg_speed = statistics.mean(speeds)
        avg_latency = statistics.mean(latencies)
        stdev = statistics.stdev(speeds) if len(speeds) > 1 else 0.0

        # --- 2. Проверка стабильности: разброс скорости не больше 40% ---
        if avg_speed > 0 and (stdev / avg_speed) > MAX_STDEV_RATIO:
            return None

        # --- 3. Проверка обхода блокировок РФ ---
        if not _run_blocked_test(port):
            return None

        # Итоговый балл: чем выше — тем лучше.
        # avg_speed с штрафом за нестабильность (stdev).
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


def test_many(links):
    """Прогоняет все конфиги параллельно, возвращает только прошедшие все проверки."""
    working = []
    total = len(links)
    done = 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {ex.submit(_test_one, l): l for l in links}
        for fut in as_completed(futures):
            done += 1
            r = fut.result()
            if r:
                working.append(r)
                print(f"[{done}/{total}] OK  {r['latency']}s  "
                      f"{r['speed_kbps']} KB/s  (stab: {r['stability_score']})")
            elif done % 25 == 0:
                print(f"[{done}/{total}] ...")
    return working
