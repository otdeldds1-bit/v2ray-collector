# Источники подписок.
# Формат: plain-text (vless:// / vmess:// / trojan:// / ss://) ИЛИ base64-блок.
# Парсер сам определяет base64 и декодирует.

SOURCES = [
    # === igareck/vpn-configs-for-russia (проверено, формат с эмодзи-флагами) ===
    "https://raw.githubusercontent.com/igareck/vpn-configs-for-russia/main/Vless-Reality-White-Lists-Rus-Mobile.txt",
    "https://raw.githubusercontent.com/igareck/vpn-configs-for-russia/main/WHITE-CIDR-RU-all.txt",
    "https://raw.githubusercontent.com/igareck/vpn-configs-for-russia/main/BLACK_VLESS_RUS.txt",
    "https://raw.githubusercontent.com/igareck/vpn-configs-for-russia/main/BLACK_VLESS_RUS_mobile.txt",
    "https://raw.githubusercontent.com/igareck/vpn-configs-for-russia/main/BLACK_SS+All_RUS.txt",

    # === MahanKenway/Freedom-V2Ray (проверено, все протоколы в одном файле) ===
    "https://raw.githubusercontent.com/MahanKenway/Freedom-V2Ray/main/configs/mix.txt",

    # === Surfboardv2ray/TGParse (проверено, base64 → парсер декодирует) ===
    "https://raw.githubusercontent.com/Surfboardv2ray/TGParse/main/splitted/mixed",

    # === Дополнительные источники (могут быть недоступны, скрипт просто пропустит) ===
    "https://raw.githubusercontent.com/mahdibland/ShadowsocksAggregator/master/Eternity.txt",
    "https://raw.githubusercontent.com/aiboboxx/v2rayfree/main/v2",
    "https://raw.githubusercontent.com/Pawdroid/Free-servers/main/sub",
    "https://raw.githubusercontent.com/ALIILAPRO/v2rayNG-Config/main/server.txt",
    "https://raw.githubusercontent.com/freefq/free/master/v2",
    "https://raw.githubusercontent.com/mfuu/v2ray/master/v2ray",
    "https://raw.githubusercontent.com/ermaozi/get_subscribe/main/subscribe/v2ray.txt",
    "https://raw.githubusercontent.com/MrPooyaX/SansorchiFucker/main/data.txt",
    "https://raw.githubusercontent.com/lagzian/SS-Collector/main/mix.txt",
    "https://raw.githubusercontent.com/Epodonios/v2ray-configs/main/All_Configs_Sub.txt",
    "https://raw.githubusercontent.com/roosterkid/openproxylist/main/V2RAY_RAW.txt",
]

TEST_URL = "https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg"
