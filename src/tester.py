import json, os, socket, subprocess, tempfile, time
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
from parser import link_to_outbound
from sources import TEST_URL

XRAY_BIN = os.environ.get("XRAY_BIN", "./xray")
TIMEOUT = 8            # сек на весь тест конфига
MAX_WORKERS = 60       # параллельных xray-процессов


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _test_one(link: str):
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
        time.sleep(0.4)  # дать xray подняться
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
        if code != "200" or speed < 1024:  # минимум 1 KB/s
            return None
        return {
            "link": link,
            "latency": round(t_total, 3),
            "speed_kbps": round(speed / 1024, 1),
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
    """Прогоняет все конфиги параллельно, возвращает только рабочие."""
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
                print(f"[{done}/{total}] OK  {r['latency']}s  {r['speed_kbps']} KB/s")
            elif done % 25 == 0:
                print(f"[{done}/{total}] ...")
    return working
