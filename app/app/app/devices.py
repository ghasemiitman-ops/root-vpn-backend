"""
devices.py

The exact same device -> protocol rules from the frontend (index.html),
so the server always resolves the same protocol the user saw in the app.
"""

DEVICE_CATALOG = {
    "android_samsung": {"label": "اندروید (سامسونگ)",     "protocol": "IKEv2"},
    "android_xiaomi":  {"label": "اندروید (شیائومی)",      "protocol": "WireGuard"},
    "android_other":   {"label": "اندروید (سایر برندها)",  "protocol": "WireGuard"},
    "iphone":          {"label": "آیفون",                  "protocol": "L2TP/IPsec"},
    "windows":         {"label": "ویندوز",                 "protocol": "L2TP/IPsec (Shared Key)"},
    "mac":             {"label": "مک",                     "protocol": "WireGuard"},
}


def resolve_protocol(device_key: str) -> str:
    if device_key not in DEVICE_CATALOG:
        raise ValueError(f"Unknown device key: {device_key}")
    return DEVICE_CATALOG[device_key]["protocol"]
