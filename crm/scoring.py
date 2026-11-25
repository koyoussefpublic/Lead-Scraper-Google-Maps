"""Explainable 0-100 lead scoring engine for CarteFidele."""

from typing import Any

HIGH_FIT_CATEGORIES = {
    "restaurant", "restaurants", "cafe", "cafes", "café", "bakery", "bakeries",
    "boulangerie", "patisserie", "pâtisserie", "coffee", "hotel", "hotels",
    "salon", "salons", "barbershop", "barber", "beauty", "spa", "gym",
    "fitness", "retail", "boutique", "store", "shop", "pizzeria", "bistro",
}

MEDIUM_FIT_CATEGORIES = {
    "clinic", "dentist", "pharmacy", "car wash", "laundry", "florist",
    "pet", "optician", "jewelry", "supermarket", "grocery",
}

DEFAULT_WEIGHTS = {
    "business_fit": 35,
    "loyalty_opportunity": 25,
    "activity_reputation": 15,
    "digital_opportunity": 15,
    "data_quality": 10,
}


def compute_lead_score(
    lead: dict[str, Any],
    weights: dict[str, int] | None = None,
) -> tuple[int, dict[str, Any]]:
    """Compute an explainable 0-100 score and breakdown for a lead.

    Never invents business data; scores only observed fields.
    """
    w = {**DEFAULT_WEIGHTS, **(weights or {})}
    category = (lead.get("category") or "").lower()
    rating = lead.get("rating")
    reviews = lead.get("review_count")
    has_website = bool(lead.get("has_website") or lead.get("website"))
    phone = (lead.get("phone") or "").strip()
    email = (lead.get("email") or "").strip()
    has_socials = any(
        (lead.get(k) or "").strip() for k in ("facebook", "instagram", "linkedin")
    )

    reasons: list[str] = []

    # 1. Business Fit (default max 35)
    fit_max = w["business_fit"]
    if any(kw in category for kw in HIGH_FIT_CATEGORIES):
        fit_score = fit_max
        reasons.append(f"High-fit repeat-visit category ({lead.get('category')})")
    elif any(kw in category for kw in MEDIUM_FIT_CATEGORIES):
        fit_score = round(fit_max * 0.7)
        reasons.append(f"Moderate-fit local service category ({lead.get('category')})")
    elif category:
        fit_score = round(fit_max * 0.4)
        reasons.append(f"General local business ({lead.get('category')})")
    else:
        fit_score = 0
        reasons.append("No category recorded")

    # 2. Loyalty Opportunity (default max 25)
    loy_max = w["loyalty_opportunity"]
    if any(kw in category for kw in HIGH_FIT_CATEGORIES):
        loy_score = round(loy_max * 0.8)
        if reviews and reviews >= 30:
            loy_score = loy_max
            reasons.append("Strong repeat-customer potential with active foot traffic")
        else:
            reasons.append("Strong repeat-customer business model for digital loyalty")
    elif reviews and reviews >= 50:
        loy_score = round(loy_max * 0.65)
        reasons.append("Established customer base (50+ reviews)")
    else:
        loy_score = round(loy_max * 0.3)

    # 3. Business Activity & Reputation (default max 15)
    rep_max = w["activity_reputation"]
    rep_ratio = 0.0
    if rating is not None:
        if rating >= 4.2:
            rep_ratio += 0.5
        elif rating >= 3.5:
            rep_ratio += 0.3
        else:
            rep_ratio += 0.1
    if reviews is not None:
        if reviews >= 100:
            rep_ratio += 0.5
        elif reviews >= 25:
            rep_ratio += 0.35
        elif reviews >= 5:
            rep_ratio += 0.2
    rep_score = round(rep_max * min(1.0, rep_ratio))
    if rep_score > 0:
        reasons.append(
            f"Observed reputation: rating={rating if rating is not None else 'N/A'}, reviews={reviews if reviews is not None else 'N/A'}"
        )

    # 4. Digital Opportunity (default max 15)
    dig_max = w["digital_opportunity"]
    if not has_website:
        dig_score = dig_max
        reasons.append("No website listed — strong opportunity for digital loyalty & web presence")
    elif has_socials:
        dig_score = round(dig_max * 0.7)
        reasons.append("Active social presence — digitally receptive to loyalty tools")
    else:
        dig_score = round(dig_max * 0.4)
        reasons.append("Has website; can complement with digital loyalty")

    # 5. Data Quality (default max 10)
    dq_max = w["data_quality"]
    dq_parts = 0
    if phone:
        dq_parts += 1
    if email:
        dq_parts += 1
    if lead.get("address") or lead.get("city"):
        dq_parts += 1
    if lead.get("place_id"):
        dq_parts += 1
    dq_score = round(dq_max * (dq_parts / 4.0))
    if dq_parts >= 3:
        reasons.append("Complete contact/location metadata")

    total = max(0, min(100, fit_score + loy_score + rep_score + dig_score + dq_score))
    breakdown = {
        "total": total,
        "components": {
            "business_fit": fit_score,
            "loyalty_opportunity": loy_score,
            "activity_reputation": rep_score,
            "digital_opportunity": dig_score,
            "data_quality": dq_score,
        },
        "reasons": reasons,
    }
    return total, breakdown
