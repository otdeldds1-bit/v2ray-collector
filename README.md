# v2ray-collector

Автоматический сборщик и тестер прокси-конфигов (VLESS / VMess / Trojan / Shadowsocks).

- Каждые 6 часов GitHub Actions скачивает публичные подписки.
- Каждый конфиг поднимается локально через `xray-core` и проверяется реальным
  запросом к CDN YouTube (`i.ytimg.com`).
- Замеряется задержка и скорость скачивания.
- В `output/` кладутся:
  - `top100.txt` — 100 самых быстрых рабочих конфигов
  - `top100_sub.txt` — то же в base64 (готово для импорта в клиент)
  - `working.txt` / `working_sub.txt` — все рабочие
  - `report.json` — статистика

## Подписка

После первого запуска добавь в клиент:

```
https://raw.githubusercontent.com/<user>/<repo>/main/output/top100_sub.txt
```

## Локальный запуск

```bash
# скачать xray
wget https://github.com/XTLS/Xray-core/releases/latest/download/Xray-linux-64.zip
unzip Xray-linux-64.zip xray && chmod +x xray

pip install -r requirements.txt
XRAY_BIN=./xray python3 src/main.py
```

## Настройка

- Источники — `src/sources.py`
- Порог по скорости / таймаут / кол-во воркеров — `src/tester.py`
- Размер итогового топа — `TOP_N` в `src/main.py`
- Расписание — `cron` в `.github/workflows/update.yml`
