"""
Определение страны по IP и добавление эмодзи-флага в #fragment ссылки.
Использует бесплатный batch-API ip-api.com (лимит: 15 запросов/мин, до 100 IP за раз).
"""
import json
import re
import socket
import time
from urllib.parse import urlparse

import requests

_cache = {}   # ip -> (countryCode, country)


def _resolve(host: str) -> str:
    """Если host — это домен, резолвим его в IP. Если IP — возвращаем как есть."""
    try:
        if re.match(r"^\d{1,3}(\.\d{1,3}){3}$", host) or ":" in host:
            return host   # уже IPv4 или IPv6
        return socket.gethostbyname(host)
    except Exception:
        return host


def get_countries(hosts):
    """
    hosts: список хостов (IP или домены).
    Возвращает dict: host -> (countryCode, country).
    """
    # резолвим домены в IP
    resolved = {h: _resolve(h) for h in hosts if h and h not in _cache}
    to_fetch = list({ip for ip in resolved.values() if ip not in _cache})

    # batch-запросы по 100 IP
    for i in range(0, len(to_fetch), 100):
        batch = to_fetch[i:i + 100]
        try:
            r = requests.post(
                "http://ip-api.com/batch?fields=countryCode,country",
                json=batch, timeout=20,
            )
            data = r.json()
            for ip, info in zip(batch, data):
                _cache[ip] = (
                    (info.get("countryCode") or "").upper(),
                    info.get("country") or "",
                )
        except Exception as e:
            print(f"   ! geo api: {e}")
            for ip in batch:
                _cache[ip] = ("", "")
        time.sleep(4.5)   # держимся в пределах 15 req/min

    # возвращаем в исходных ключах (host -> country)
    result = {}
    for h, ip in resolved.items():
        result[h] = _cache.get(ip, ("", ""))
    return result


def flag_emoji(cc: str) -> str:
    """'RU' -> 🇷🇺, 'US' -> 🇺🇸. Если код битый — нейтральный флаг."""
    if not cc or len(cc) != 2 or not cc.isalpha():
        return "🏴"
    return "".join(
        chr(0x1F1E6 + ord(c.upper()) - ord("A")) for c in cc
    )


def _has_flag_emoji(s: str) -> bool:
    """Есть ли в строке regional-indicator символ (эмодзи-флаг)."""
    return any(0x1F1E6 <= ord(c) <= 0x1F1FF for c in s)


def _extract_host(link: str) -> str:
    """Достаёт host (IP или домен) из share-ссылки."""
    try:
        if link.startswith("vmess://"):
            import base64
            body = link[8:].split("#", 1)[0].strip()
            body += "=" * (-len(body) % 4)
            data = json.loads(base64.b64decode(body).decode("utf-8", "ignore"))
            return data.get("add", "")
        u = urlparse(link)
        return u.hostname or ""
    except Exception:
        return ""


def decorate(link: str, host_country_map: dict) -> str:
    """
    Если в #fragment уже есть эмодзи-флаг — не трогаем.
    Иначе добавляем ' | 🇽🇽 Country' к существующему имени (или как имя, если имени нет).
    """
    if "#" in link:
        base, frag = link.split("#", 1)
    else:
        base, frag = link, ""

    if _has_flag_emoji(frag):
        return link    # уже с флагом — сохраняем оригинал

    host = _extract_host(link)
    cc, country = host_country_map.get(host, ("", ""))
    flag = flag_emoji(cc)

    if country:
        add = f"{flag} {country}"
    else:
        add = flag

    new_frag = f"{frag} | {add}" if frag.strip() else add
    return f"{base}#{new_frag}"
