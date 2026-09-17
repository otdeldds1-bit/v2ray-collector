import os, sys, time, json, shutil
sys.path.insert(0, os.path.dirname(__file__))

import requests
from sources import SOURCES
from parser import parse_subscription, link_to_outbound
from tester import test_many

HERE = os.path.dirname(__file__)
OUT_DIR = os.path.abspath(os.path.join(HERE, "..", "output"))
TOP_N = 100
OUT_FILE = "proxies.txt"          # <-- ЕДИНСТВЕННЫЙ файл с результатом


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
    """
    Уникализация по (protocol+host+port+uuid/pass) БЕЗ учёта #fragment,
    чтобы дубликаты одного сервера с разными именами не занимали слот.
    Первый встреченный вариант (с его эмодзи-именем) сохраняется.
    """
    seen, out = set(), []
    for l in links:
        base = l.split("#", 1)[0].strip()
        if not base or base in seen:
            continue
        if not link_to_outbound(l):
            continue
        seen.add(base)
        out.append(l)   # сохраняем оригинал со всем #fragment
    return out


def write_single_output(working):
    # Полностью чистим output/, чтобы там был ровно один файл
    if os.path.isdir(OUT_DIR):
        shutil.rmtree(OUT_DIR)
    os.makedirs(OUT_DIR, exist_ok=True)

    # Топ-N по скорости (при равенстве — по меньшей задержке)
    top = sorted(working, key=lambda x: (-x["speed_kbps"], x["latency"]))[:TOP_N]

    path = os.path.join(OUT_DIR, OUT_FILE)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(x["link"] for x in top) + "\n")

    print(f"\nЗаписано {len(top)} конфигов в {path}")


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
    print(f"Рабочих: {len(working)}  (за {time.time()-t0:.1f}s)")

    print("=== 4. Запись результата ===")
    write_single_output(working)
    print("Готово.")


if __name__ == "__main__":
    main()
