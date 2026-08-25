"""
pricing.py

This is a direct port of the pricing logic already implemented and approved
in the frontend (index.html). Keeping it identical here is what makes the
price the user sees in the app match the price the server actually charges.
If you tweak a number in one place, tweak it here too.
"""

BASE_RATE_PER_GB_PER_MONTH = 10000  # تومان

DURATION_PLANS = {
    1:  {"label": "۱ ماهه",  "multiplier": 1.00},
    3:  {"label": "۳ ماهه",  "multiplier": 0.90},
    6:  {"label": "۶ ماهه",  "multiplier": 0.80},
    12: {"label": "۱۲ ماهه", "multiplier": 0.65},
}

# تخفیف پلکانی حجم (تومان، به‌ازای هر دستگاه)
VOLUME_DISCOUNT_TIERS = [
    {"min": 20,  "max": 49,  "discount": 0},
    {"min": 50,  "max": 99,  "discount": 50000},
    {"min": 100, "max": 249, "discount": 100000},
    {"min": 250, "max": 500, "discount": 200000},
]

CONNECTION_PLANS = {
    "single": {"label": "تک‌کاربره", "multiplier": 1.0},
    "double": {"label": "دو کاربره", "multiplier": 1.7},
    "multi":  {"label": "چند‌کاربره", "multiplier": 2.3},
}

UNLIMITED_BASE_PER_MONTH = 350000  # تومان، به‌ازای هر دستگاه در ماه


def get_volume_discount(volume_gb: int) -> int:
    for tier in VOLUME_DISCOUNT_TIERS:
        if tier["min"] <= volume_gb <= tier["max"]:
            return tier["discount"]
    return 0


def calculate_price(
    duration_months: int,
    connection_type: str,
    device_count: int,
    is_unlimited: bool = False,
    volume_gb: int | None = None,
) -> dict:
    """
    Returns a dict: {total, per_month, per_device, volume_discount}
    Mirrors calcPrice() in the frontend exactly.
    """
    if duration_months not in DURATION_PLANS:
        raise ValueError(f"Invalid duration: {duration_months}")
    if connection_type not in CONNECTION_PLANS:
        raise ValueError(f"Invalid connection type: {connection_type}")
    if device_count < 1:
        raise ValueError("device_count must be at least 1")

    plan = DURATION_PLANS[duration_months]
    conn = CONNECTION_PLANS[connection_type]

    if is_unlimited:
        per_device = UNLIMITED_BASE_PER_MONTH * plan["multiplier"] * duration_months
        volume_discount = 0
    else:
        if volume_gb is None:
            raise ValueError("volume_gb is required when is_unlimited is False")
        per_device_before_discount = volume_gb * BASE_RATE_PER_GB_PER_MONTH * plan["multiplier"] * duration_months
        volume_discount = get_volume_discount(volume_gb)
        per_device = max(0, per_device_before_discount - volume_discount)

    per_device_final = per_device * conn["multiplier"]
    total = per_device_final * device_count

    return {
        "total": total,
        "per_month": total / duration_months,
        "per_device": per_device_final,
        "volume_discount_total": volume_discount * device_count,
    }
