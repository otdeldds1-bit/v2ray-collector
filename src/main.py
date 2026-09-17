import os, sys, time, shutil, random
sys.path.insert(0, os.path.dirname(__file__))

import requests
from sources import SOURCES
from parser import parse_subscription, link_to_outbound
from tester import test_many
from geo import get_countries, decorate, _extract_host, _has_flag_emoji

HERE = os.path.dirname(__file__)
OUT_DIR = os.path.abspath(os.path.join(HERE, "..", "output"))
TOP_N = 150                     # сколько конфигов попадёт в финальный файл
OUT_FILE = "proxies.txt"        # единственный файл с результатом

# --- Фильтр по протоколу и лимит пула ---
ALLOWED_PROTOCOLS = ("vless://", "vmess://")   # VLESS + VMess, остальное отбрасываем
MAX_POOL = 5000                 # максимум конфигов, которые пойдут на тест
RANDOM_SEED = 42                # фиксированный seed — список стабилен от запуска к запуску


def fetch_all():
    """Скачивает все источники и собирает ссылки."""
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


def filter_and_dedupe(links):
    """
    1. Оставляем только VLESS и VMess (Trojan/SS отбрасываем).
    2. Уникализация по (protocol + host + port + uuid) без учёта #fragment.
    3. Ограничиваем пул до MAX_POOL конфигов (перемешав для разнообразия).
    """
    # --- Шаг 1: только разрешённые протоколы ---
    kept = [l for l in links if l.startswith(ALLOWED_PROTOCOLS)]
    print(f"VLESS+VMess-ссылок: {len(kept)} (отброшено остальных: {len(links) - len(kept)})")

    # --- Шаг 2: дедупликация ---
    seen, unique = set(), []
    for l in kept:
        base = l.split("#", 1)[0].strip()
        if not base or base in seen:
            continue
        if not link_to_outbound(l):
            continue
        seen.add(base)
        unique.append(l)
    print(f"Уникальных валидных: {len(unique)}")

    # --- Шаг 3: ограничение до MAX_POOL ---
    if len(unique) > MAX_POOL:
        random.seed(RANDOM_SEED)
        random.shuffle(unique)
        unique = unique[:MAX_POOL]
        print(f"Ограничил пул до {MAX_POOL} конфигов для скорости теста")

    return unique

def write_single_output(working):
    # Чистим output/, чтобы там был ровно один файл
    if os.path.isdir(OUT_DIR):
        shutil.rmtree(OUT_DIR)
    os.makedirs(OUT_DIR, exist_ok=True)

    # --- Сортировка: сначала по стабильности, потом по скорости, потом по latency ---
    top = sorted(
        working,
        key=lambda x: (
            -x.get("stability_score", 0),
            -x.get("speed_kbps", 0),
            x.get("latency", 999),
        ),
    )[:TOP_N]

    # --- Добавляем эмодзи-флаги для тех строк, где их нет ---
    print(f"Определяю страны для {len(top)} серверов...")
    hosts = list({_extract_host(x["link"]) for x in top})
    host_map = get_countries(hosts)

    decorated = [decorate(x["link"], host_map) for x in top]

    # --- Пишем единственный файл ---
    path = os.path.join(OUT_DIR, OUT_FILE)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(decorated) + "\n")

    with_flag = sum(1 for l in decorated if _has_flag_emoji(l.split("#", 1)[-1]))
    print(f"Записано {len(decorated)} конфигов в {path}")
    print(f"Из них с эмодзи-флагом: {with_flag}/{len(decorated)}")


def main():
    print("=== 1. Загрузка подписок ===")
    raw = fetch_all()
    print(f"Всего линков: {len(raw)}")

    print("=== 2. Фильтр VLESS + дедупликация ===")
    links = filter_and_dedupe(raw)
    print(f"К тестированию: {len(links)}")

    print("=== 3. Тестирование (YouTube + обход блокировок РФ + стабильность) ===")
    t0 = time.time()
    working = test_many(links)
    print(f"Рабочих VLESS: {len(working)}  (за {time.time()-t0:.1f}s)")

    print("=== 4. Запись результата ===")
    write_single_output(working)
    print("Готово.")


if __name__ == "__main__":
    main()
