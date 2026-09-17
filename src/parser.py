import base64
import json
from urllib.parse import urlparse, parse_qs, unquote


def _b64(s: str) -> str:
    s = s.strip().replace("-", "+").replace("_", "/")
    s += "=" * (-len(s) % 4)
    return base64.b64decode(s).decode("utf-8", "ignore")


def _stream_settings(net, security, params, sni):
    # type=raw — новое имя для tcp в Xray
    if net == "raw":
        net = "tcp"

    ss = {"network": net}

    if security == "tls":
        tls = {"serverName": sni or params.get("host", ""), "allowInsecure": False}
        if params.get("fp"):
            tls["fingerprint"] = params["fp"]
        if params.get("alpn"):
            tls["alpn"] = params["alpn"].split(",")
        ss["security"] = "tls"
        ss["tlsSettings"] = tls

    elif security == "reality":
        reality = {
            "serverName": sni or params.get("host", ""),
            "publicKey": params.get("pbk", ""),
            "shortId": params.get("sid", ""),
            "fingerprint": params.get("fp", "chrome"),
        }
        if params.get("spx"):
            reality["spiderX"] = params["spx"]
        ss["security"] = "reality"
        ss["realitySettings"] = reality

    if net == "ws":
        ss["wsSettings"] = {
            "path": params.get("path", "/"),
            "headers": {"Host": params.get("host", sni or "")},
        }
    elif net == "grpc":
        gs = {"serviceName": params.get("serviceName", "")}
        if params.get("mode") == "multi":
            gs["multiMode"] = True
        ss["grpcSettings"] = gs
    elif net == "tcp" and params.get("headerType") == "http":
        ss["tcpSettings"] = {
            "header": {
                "type": "http",
                "request": {
                    "path": [params.get("path", "/")],
                    "headers": {"Host": [params.get("host", sni or "")]},
                },
            }
        }
    return ss


def link_to_outbound(link: str):
    """Преобразует share-link в outbound-конфиг xray. None, если не распарсилось."""
    try:
        # ============ VLESS ============
        if link.startswith("vless://"):
            u = urlparse(link)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            user = {"id": u.username, "encryption": "none"}
            if q.get("flow"):
                user["flow"] = q["flow"]
            return {
                "protocol": "vless",
                "settings": {
                    "vnext": [{
                        "address": u.hostname,
                        "port": int(u.port),
                        "users": [user],
                    }]
                },
                "streamSettings": _stream_settings(
                    q.get("type", "tcp"), q.get("security", "none"), q, q.get("sni")
                ),
            }

        # ============ TROJAN ============
        if link.startswith("trojan://"):
            u = urlparse(link)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            return {
                "protocol": "trojan",
                "settings": {
                    "servers": [{
                        "address": u.hostname,
                        "port": int(u.port),
                        "password": unquote(u.username or ""),
                    }]
                },
                "streamSettings": _stream_settings(
                    q.get("type", "tcp"), q.get("security", "tls"), q, q.get("sni")
                ),
            }

        # ============ VMESS ============
        if link.startswith("vmess://"):
            data = json.loads(_b64(link[8:]))
            net = data.get("net", "tcp")
            sec = data.get("tls", "none") or "none"
            params = {
                "type": net,
                "host": data.get("host", ""),
                "path": data.get("path", "/"),
                "serviceName": data.get("path", ""),
                "headerType": data.get("type", "none"),
            }
            return {
                "protocol": "vmess",
                "settings": {
                    "vnext": [{
                        "address": data["add"],
                        "port": int(data["port"]),
                        "users": [{
                            "id": data["id"],
                            "alterId": int(data.get("aid", 0)),
                            "security": data.get("scy", "auto"),
                        }],
                    }]
                },
                "streamSettings": _stream_settings(net, sec, params, data.get("sni")),
            }

        # ============ SHADOWSOCKS ============
        if link.startswith("ss://"):
            raw = link[5:]
            fragment = ""
            if "#" in raw:
                raw, fragment = raw.split("#", 1)
            if "@" not in raw:
                raw = _b64(raw)
            if "@" in raw:
                creds, hostpart = raw.rsplit("@", 1)
                if ":" not in creds:
                    creds = _b64(creds)
                method, password = creds.split(":", 1)
                host, port = hostpart.split(":", 1)
            else:
                decoded = _b64(raw)
                method_pw, host_port = decoded.rsplit("@", 1)
                method, password = method_pw.split(":", 1)
                host, port = host_port.split(":", 1)
            return {
                "protocol": "shadowsocks",
                "settings": {
                    "servers": [{
                        "address": host,
                        "port": int(port),
                        "method": method,
                        "password": password,
                    }]
                },
                "streamSettings": {"network": "tcp"},
            }
    except Exception:
        return None
    return None


def parse_subscription(text: str):
    """Возвращает список share-link'ов из тела подписки (plain или base64)."""
    text = text.strip()
    if "://" not in text[:200]:
        try:
            text = _b64(text)
        except Exception:
            pass
    links = []
    for line in text.splitlines():
        line = line.strip()
        if any(line.startswith(p) for p in ("vless://", "vmess://", "trojan://", "ss://")):
            links.append(line)
    return links
