"""Normalization helpers for phone numbers, emails, websites, and deduplication."""

import re
from urllib.parse import urlparse

from data.normalize import normalize_address, normalize_name, normalize_phone


def normalize_email(email: str | None) -> str:
    """Normalize an email address to lowercase trimmed form."""
    if not email:
        return ""
    cleaned = email.strip().lower()
    if "@" not in cleaned or " " in cleaned:
        return ""
    return cleaned


def normalize_website(url: str | None) -> str:
    """Normalize a website URL to lowercase scheme+host+path without trailing slash."""
    if not url:
        return ""
    raw = url.strip()
    if not raw:
        return ""
    if not raw.startswith(("http://", "https://")):
        raw = "https://" + raw
    try:
        parsed = urlparse(raw)
        host = parsed.netloc.lower()
        if host.startswith("www."):
            host = host[4:]
        path = parsed.path.rstrip("/")
        return f"https://{host}{path}" if host else ""
    except Exception:
        return raw.lower().rstrip("/")


def normalize_phone_international(phone: str | None, country_hint: str = "") -> str:
    """Normalize a phone number toward E.164 international format.

    Handles Morocco local numbers (05/06/07xxxxxxxx -> +212...) when country
    or prefix indicates Morocco, and preserves existing +country codes.
    """
    if not phone:
        return ""
    raw = phone.strip()
    if not raw:
        return ""

    has_plus = raw.startswith("+")
    digits = re.sub(r"\D", "", raw)
    if not digits:
        return ""

    if raw.startswith("00") and len(digits) > 4:
        return "+" + digits[2:]
    if has_plus:
        return "+" + digits

    hint = (country_hint or "").lower()
    is_morocco = "morocco" in hint or "maroc" in hint or hint == "ma"
    if is_morocco and len(digits) == 10 and digits.startswith(("05", "06", "07")):
        return "+212" + digits[1:]
    if digits.startswith("212") and len(digits) == 12:
        return "+" + digits

    if len(digits) == 10 and ("us" in hint or "usa" in hint or "united states" in hint):
        return "+1" + digits

    return normalize_phone(raw)


def compute_dedup_key(
    name: str,
    place_id: str | None = None,
    phone_norm: str = "",
    email_norm: str = "",
    address: str | None = None,
) -> str:
    """Compute a stable deduplication key preferring Google Place ID."""
    if place_id and place_id.strip():
        return f"pid::{place_id.strip()}"
    n_name = normalize_name(name)
    if phone_norm:
        return f"{n_name}::{phone_norm}"
    if email_norm:
        return f"{n_name}::{email_norm}"
    n_addr = normalize_address(address)
    return f"{n_name}::{n_addr}"
