import base64, os, sys, time
from concurrent.futures import ThreadPoolExecutor
import requests

sys.path.insert(0, os.path.dirname(__file__))
from sources import SOURCES
from parser import parse_subscription, link_to_outbound
from tester import test_many

OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "output")
TOP_N = 100


def fetch_all():
    all_links = []
    for url in SOURCES:
        try:
            print(f"→ {url}")
            r = requests.get(url, timeout=25, headers={"User-Agent": "Mozilla/5.0"})
            r.raise_for_status()
            links = parse_subscription(r.text)
            print(f"   +{len(links)}")
            all_links.extend(links)
        except Exception as e:
            print(f"   ! {e}")
    return all_links


def dedupe(links):
    seen, out = set(), []
    for l in links:
        # ключ — сам линк без #имени (для стабильности)
        key = l.split("#", 1)[0]
        if key in seen:
            continue
        seen.add(key)
        # отсеиваем то, что не парсится вообще
        if link_to_outbound(l):
            out.append(l)
    return out


def write_outputs(working):
    os.makedirs(OUT_DIR, exist_ok=True)

    # 1. Все рабочие
    with open(os.path.join(OUT_DIR, "working.txt"), "w") as f:
        f.write("\n".join(x["link"] for x in working))

    # 2. Топ-100 по скорости
    top = sorted(working, key=lambda x: (-x["speed_kbps"], x["latency"]))[:TOP_N]
    with open(os.path.join(OUT_DIR, "top100.txt"), "w") as f:
        f.write("\n".join(x["link"] for x in top))

    # 3. Base64-подписки (совместимо с v2rayNG / Nekobox / Streisand)
    for name, arr in (("working", working), ("top100", top)):
        payload = "\n".join(x["link"] for x in arr).encode()
        with open(os.path.join(OUT_DIR, f"{name}_sub.txt"), "wb") as f:
            f.write(base64.b64encode(payload))

    # 4. JSON-отчёт со статистикой
    import json
    report = {
        "updated": int(time.time()),
        "total_working": len(working),
        "top100": [
            {"link": x["link"], "latency": x["latency"], "speed_kbps": x["speed_kbps"]}
            for x in top
        ],
    }
    with open(os.path.join(OUT_DIR, "report.json"), "w") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)


def main():
    print("=== 1. Загрузка подписок ===")
    raw = fetch_all()
    print(f"Всего линков: {len(raw)}")

    print("=== 2. Дедупликация и валидация ===")
    links = dedupe(raw)
    print(f"Уникальных валидных: {len(links)}")

    print("=== 3. Тестирование (YouTube + скорость) ===")
    t0 = time.time()
    working = test_many(links)
    print(f"Рабочих: {len(working)} (за {time.time()-t0:.1f}s)")

    print("=== 4. Запись результатов ===")
    write_outputs(working)
    print("Готово.")


if __name__ == "__main__":
    main()
