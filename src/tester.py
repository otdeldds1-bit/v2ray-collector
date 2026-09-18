"""
Упрощённый тестер для конфигов из «белых списков» (РФ).

Почему только TCP-чек:
    - GitHub Actions запускает тесты из дата-центров Microsoft (Амстердам,
      Франкфурт, иногда США). У этих серверов чистый интернет без ТСПУ и DPI.
    - Конфиги, которые работают ТОЛЬКО в «белых списках» российских
      операторов, из-за границы выглядят как мёртвые: сервер может отвечать
      на TCP, но не пропускать трафик к YouTube (он там просто заблокирован).
    - Поэтому полноценный тест скорости и доступности YouTube даёт ложные
      срабатывания и выбрасывает рабочие конфиги в мусор.

Что делает:
    1. Извлекает host:port из каждой share-ссылки (vless://, vmess://).
    2. Параллельно (400 потоков) проверяет TCP-подключение.
    3. Возвращает те, что отвечают — их дальше main.py запишет в
       output/proxies.txt с сохранением оригинального #fragment (эмодзи-флаг).
"""

import base64
import json
import socket
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse


# --- Настройки ---
TCP_TIMEOUT = 2.5           # сек на одну TCP-попытку
MAX_WORKERS = 400           # потоков для TCP-чека (только сеть, CPU не грузится)
PROGRESS_EVERY = 50         # как часто печатать прогресс


def _extract_host_port(link):
    """Возвращает (host, port) из share-ссылки. Не валидирует — только парсит."""
    try:
        # VLESS: vless://uuid@host:port?params#fragment
        if link.startswith("vless://"):
            u = urlparse(link)
            return u.hostname or "", int(u.port or 0)

        # VMess: vmess://base64(json)
        if link.startswith("vmess://"):
            body = link[8:].split("#", 1)[0].strip()
            body += "=" * (-len(body) % 4)
            data = json.loads(base64.b64decode(body).decode("utf-8", "ignore"))
            return data.get("add", ""), int(data.get("port", 0))

        # Trojan, SS — на всякий случай, хотя в этом списке их нет
        if link.startswith("trojan://"):
            u = urlparse(link)
            return u.hostname or "", int(u.port or 0)

        if link.startswith("ss://"):
            raw = link[5:].split("#", 1)[0]
            if "@" not in raw:
                raw = base64.b64decode(
                    raw + "=" * (-len(raw) % 4)
                ).decode("utf-8", "ignore")
            _, hostport = raw.rsplit("@", 1)
            host, port = hostport.rsplit(":", 1)
            return host, int(port)
    except Exception:
        pass
    return "", 0


def _tcp_alive(host, port, timeout=TCP_TIMEOUT):
    """Простая проверка: устанавливается ли TCP-соединение."""
    if not host or not port:
        return False
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


def test_many(links):
    """
    Принимает список share-ссылок.
    Возвращает список словарей в формате, который ожидает main.py:
        {"link": ..., "latency": 0, "speed_kbps": 0, "stability_score": 0}
    Сортировка в main.py будет по stability_score (все нули) — значит
    сохранится исходный порядок из источника, что как раз и нужно:
    автор all_subs уже отсортировал конфиги по своим тестам из РФ.
    """
    total = len(links)
    print(f"--- TCP-проверка {total} конфигов (без теста скорости) ---")

    working = []
    done = 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {ex.submit(_tcp_alive, *_extract_host_port(l)): l for l in links}
        for fut in as_completed(futures):
            done += 1
            try:
                alive = fut.result()
            except Exception:
                alive = False
            if alive:
                working.append({
                    "link": futures[fut],
                    "latency": 0,
                    "speed_kbps": 0,
                    "stability_score": 0,
                })
            if done % PROGRESS_EVERY == 0:
                print(f"  {done}/{total}  (живых: {len(working)})")

    print(f"--- TCP-чек готов: {len(working)} из {total} отвечают ---")
    return working
