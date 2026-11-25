"""CRM Lead, Contact, Activity, and CSV Import/Export service."""

import csv
import io
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from crm.db import get_db
from crm.normalizer import (
    compute_dedup_key,
    normalize_email,
    normalize_phone_international,
    normalize_website,
)
from crm.scoring import compute_lead_score

PIPELINE_STATUSES = [
    "new",
    "researching",
    "qualified",
    "contacted",
    "replied",
    "interested",
    "demo_scheduled",
    "demo_completed",
    "trial",
    "converted",
    "disqualified",
    "not_interested",
    "unreachable",
    "do_not_contact",
]


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_scoring_weights(conn) -> dict[str, int] | None:
    row = conn.execute("SELECT value_json FROM crm_settings WHERE key = 'scoring_weights'").fetchone()
    if row:
        try:
            return json.loads(row["value_json"])
        except Exception:
            return None
    return None


def _row_to_lead(row) -> dict[str, Any]:
    d = dict(row)
    for json_col, default in (("tags_json", []), ("score_breakdown_json", {}), ("raw_data_json", {})):
        try:
            d[json_col.replace("_json", "")] = json.loads(d.get(json_col) or json.dumps(default))
        except Exception:
            d[json_col.replace("_json", "")] = default
    return d


def record_activity(
    lead_id: int,
    activity_type: str,
    title: str,
    description: str = "",
    metadata: dict[str, Any] | None = None,
    contact_id: int | None = None,
    actor: str = "system",
    db_path: Path | None = None,
) -> int:
    """Append an activity entry to a lead's timeline."""
    now = _utcnow()
    with get_db(db_path) as conn:
        cur = conn.execute(
            """INSERT INTO lead_activities
            (lead_id, contact_id, activity_type, title, description, metadata_json, actor, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (lead_id, contact_id, activity_type, title, description, json.dumps(metadata or {}), actor, now),
        )
        return cur.lastrowid


def upsert_lead(
    data: dict[str, Any],
    lead_source: str = "google_maps",
    scraping_job_id: str = "",
    actor: str = "system",
    db_path: Path | None = None,
) -> tuple[int, bool]:
    """Insert or update a lead without creating duplicates.

    Matches by Google Place ID first, then dedup_key, normalized phone, or normalized email.
    Returns (lead_id, created_bool).
    """
    now = _utcnow()
    name = (data.get("name") or "").strip()
    if not name:
        raise ValueError("Business name is required.")

    country = (data.get("country") or "").strip()
    place_id = (data.get("place_id") or "").strip() or None
    phone = (data.get("phone") or "").strip()
    phone_norm = normalize_phone_international(phone, country_hint=country)
    email = (data.get("email") or "").strip()
    email_norm = normalize_email(email)
    website = (data.get("website") or data.get("website_url") or "").strip()
    website_norm = normalize_website(website)
    has_website = 1 if (data.get("has_website") or website_norm) else 0
    address = (data.get("address") or "").strip()
    dedup_key = compute_dedup_key(
        name=name,
        place_id=place_id,
        phone_norm=phone_norm,
        email_norm=email_norm,
        address=address,
    )

    with get_db(db_path) as conn:
        existing = None
        if place_id:
            existing = conn.execute("SELECT * FROM leads WHERE place_id = ?", (place_id,)).fetchone()
        if not existing:
            existing = conn.execute("SELECT * FROM leads WHERE dedup_key = ?", (dedup_key,)).fetchone()
        if not existing and phone_norm:
            existing = conn.execute(
                "SELECT * FROM leads WHERE phone_normalized = ? AND phone_normalized != ''",
                (phone_norm,),
            ).fetchone()
        if not existing and email_norm:
            existing = conn.execute(
                "SELECT * FROM leads WHERE email_normalized = ? AND email_normalized != ''",
                (email_norm,),
            ).fetchone()

        weights = _get_scoring_weights(conn)
        score_input = {
            "category": data.get("category") or (existing["category"] if existing else ""),
            "rating": data.get("rating") if data.get("rating") is not None else (existing["rating"] if existing else None),
            "review_count": data.get("review_count") if data.get("review_count") is not None else (existing["review_count"] if existing else None),
            "has_website": bool(has_website) or (bool(existing["has_website"]) if existing else False),
            "website": website_norm or (existing["website_normalized"] if existing else ""),
            "phone": phone_norm or (existing["phone_normalized"] if existing else ""),
            "email": email_norm or (existing["email_normalized"] if existing else ""),
            "address": address or (existing["address"] if existing else ""),
            "city": (data.get("city") or "").strip() or (existing["city"] if existing else ""),
            "place_id": place_id or (existing["place_id"] if existing else None),
            "facebook": data.get("facebook") or (existing["facebook"] if existing else ""),
            "instagram": data.get("instagram") or (existing["instagram"] if existing else ""),
            "linkedin": data.get("linkedin") or (existing["linkedin"] if existing else ""),
        }
        score, breakdown = compute_lead_score(score_input, weights=weights)

        if existing:
            lead_id = existing["id"]
            conn.execute(
                """UPDATE leads SET
                    category = COALESCE(NULLIF(?, ''), category),
                    address = COALESCE(NULLIF(?, ''), address),
                    city = COALESCE(NULLIF(?, ''), city),
                    region = COALESCE(NULLIF(?, ''), region),
                    country = COALESCE(NULLIF(?, ''), country),
                    latitude = COALESCE(?, latitude),
                    longitude = COALESCE(?, longitude),
                    plus_code = COALESCE(NULLIF(?, ''), plus_code),
                    phone = COALESCE(NULLIF(?, ''), phone),
                    phone_normalized = COALESCE(NULLIF(?, ''), phone_normalized),
                    email = COALESCE(NULLIF(?, ''), email),
                    email_normalized = COALESCE(NULLIF(?, ''), email_normalized),
                    website = COALESCE(NULLIF(?, ''), website),
                    website_normalized = COALESCE(NULLIF(?, ''), website_normalized),
                    has_website = MAX(has_website, ?),
                    google_maps_url = COALESCE(NULLIF(?, ''), google_maps_url),
                    place_id = COALESCE(place_id, ?),
                    rating = COALESCE(?, rating),
                    review_count = COALESCE(?, review_count),
                    facebook = COALESCE(NULLIF(?, ''), facebook),
                    instagram = COALESCE(NULLIF(?, ''), instagram),
                    linkedin = COALESCE(NULLIF(?, ''), linkedin),
                    score = ?,
                    score_breakdown_json = ?,
                    updated_at = ?
                WHERE id = ?""",
                (
                    data.get("category") or "",
                    address,
                    (data.get("city") or "").strip(),
                    (data.get("region") or data.get("state") or "").strip(),
                    country,
                    data.get("latitude"),
                    data.get("longitude"),
                    (data.get("plus_code") or "").strip(),
                    phone,
                    phone_norm,
                    email,
                    email_norm,
                    website,
                    website_norm,
                    has_website,
                    (data.get("google_maps_url") or "").strip(),
                    place_id,
                    data.get("rating"),
                    data.get("review_count"),
                    (data.get("facebook") or "").strip(),
                    (data.get("instagram") or "").strip(),
                    (data.get("linkedin") or "").strip(),
                    score,
                    json.dumps(breakdown),
                    now,
                    lead_id,
                ),
            )
            return lead_id, False

        claimed_val = None
        if data.get("claimed") is True:
            claimed_val = 1
        elif data.get("claimed") is False:
            claimed_val = 0

        cur = conn.execute(
            """INSERT INTO leads (
                name, category, description, address, city, region, country,
                latitude, longitude, plus_code,
                phone, phone_normalized, email, email_normalized,
                website, website_normalized, has_website,
                google_maps_url, place_id, dedup_key,
                rating, review_count, claimed, hours, price_level,
                facebook, instagram, linkedin, raw_data_json,
                lead_source, scraping_job_id, search_query,
                pipeline_status, score, score_breakdown_json,
                first_discovered_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', ?, ?, ?, ?)""",
            (
                name,
                (data.get("category") or "").strip(),
                (data.get("description") or "").strip(),
                address,
                (data.get("city") or "").strip(),
                (data.get("region") or data.get("state") or "").strip(),
                country,
                data.get("latitude"),
                data.get("longitude"),
                (data.get("plus_code") or "").strip(),
                phone,
                phone_norm,
                email,
                email_norm,
                website,
                website_norm,
                has_website,
                (data.get("google_maps_url") or "").strip(),
                place_id,
                dedup_key,
                data.get("rating"),
                data.get("review_count"),
                claimed_val,
                (data.get("hours") or "").strip(),
                (data.get("price_level") or "").strip(),
                (data.get("facebook") or "").strip(),
                (data.get("instagram") or "").strip(),
                (data.get("linkedin") or "").strip(),
                json.dumps(data, default=str),
                lead_source,
                scraping_job_id,
                (data.get("search_query") or "").strip(),
                score,
                json.dumps(breakdown),
                now,
                now,
            ),
        )
        lead_id = cur.lastrowid

        # Create primary contact record (default: not eligible until verified/consented)
        if phone_norm or email_norm:
            conn.execute(
                """INSERT INTO contacts (
                    lead_id, name, position, email, email_normalized,
                    phone, phone_normalized, whatsapp_number,
                    source, collection_date, created_at, updated_at
                ) VALUES (?, '', 'Business Contact', ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    lead_id,
                    email,
                    email_norm,
                    phone,
                    phone_norm,
                    phone_norm,
                    f"public_{lead_source}",
                    now,
                    now,
                    now,
                ),
            )

        act_type = "lead_imported" if lead_source == "csv_import" else "lead_discovered"
        conn.execute(
            """INSERT INTO lead_activities
            (lead_id, activity_type, title, description, metadata_json, actor, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                lead_id,
                act_type,
                f"Lead {'imported' if lead_source == 'csv_import' else 'discovered'}: {name}",
                f"Source: {lead_source} | Score: {score}/100",
                json.dumps({"scraping_job_id": scraping_job_id, "score": score}),
                actor,
                now,
            ),
        )
        return lead_id, True


def ingest_csv_file(
    csv_path: Path,
    scraping_job_id: str = "",
    lead_source: str = "google_maps",
    actor: str = "system",
    db_path: Path | None = None,
) -> dict[str, int]:
    """Ingest leads from a scraper or uploaded CSV file into the CRM."""
    if not csv_path.is_file():
        return {"created": 0, "updated": 0, "skipped": 0}
    text = csv_path.read_text(encoding="utf-8-sig")
    return ingest_csv_text(
        text,
        scraping_job_id=scraping_job_id,
        lead_source=lead_source,
        actor=actor,
        db_path=db_path,
    )


def ingest_csv_text(
    csv_text: str,
    scraping_job_id: str = "",
    lead_source: str = "csv_import",
    actor: str = "system",
    db_path: Path | None = None,
) -> dict[str, int]:
    """Parse CSV content and upsert rows into the CRM."""
    reader = csv.DictReader(io.StringIO(csv_text))
    if not reader.fieldnames:
        raise ValueError("CSV file has no headers.")

    created = 0
    updated = 0
    skipped = 0
    for row in reader:
        name = (row.get("Name") or row.get("name") or row.get("business_name") or "").strip()
        if not name:
            skipped += 1
            continue

        def _float_or_none(v):
            try:
                return float(v) if v not in (None, "") else None
            except ValueError:
                return None

        def _int_or_none(v):
            try:
                return int(float(v)) if v not in (None, "") else None
            except ValueError:
                return None

        has_web_raw = (row.get("Has Website") or row.get("has_website") or "").strip().lower()
        web_url = (row.get("Website URL") or row.get("website") or row.get("website_url") or "").strip()

        payload = {
            "name": name,
            "category": row.get("Category") or row.get("category") or "",
            "address": row.get("Address") or row.get("address") or "",
            "phone": row.get("Phone") or row.get("phone") or "",
            "email": row.get("Business Email") or row.get("Email") or row.get("email") or "",
            "rating": _float_or_none(row.get("Rating") or row.get("rating")),
            "review_count": _int_or_none(row.get("Reviews") or row.get("review_count")),
            "has_website": has_web_raw in ("yes", "true", "1") or bool(web_url),
            "website_url": web_url,
            "facebook": row.get("Facebook") or row.get("facebook") or "",
            "instagram": row.get("Instagram") or row.get("instagram") or "",
            "linkedin": row.get("LinkedIn") or row.get("linkedin") or "",
            "latitude": _float_or_none(row.get("Latitude") or row.get("latitude")),
            "longitude": _float_or_none(row.get("Longitude") or row.get("longitude")),
            "plus_code": row.get("Plus Code") or row.get("plus_code") or "",
            "place_id": row.get("Place ID") or row.get("place_id") or None,
            "google_maps_url": row.get("Google Maps URL") or row.get("google_maps_url") or "",
            "city": row.get("City") or row.get("city") or "",
            "region": row.get("State") or row.get("Region") or row.get("region") or "",
            "country": row.get("Country") or row.get("country") or "",
            "hours": row.get("Hours") or row.get("hours") or "",
            "price_level": row.get("Price Level") or row.get("price_level") or "",
            "search_query": row.get("Search Query") or row.get("search_query") or "",
        }
        _, is_new = upsert_lead(
            payload,
            lead_source=lead_source,
            scraping_job_id=scraping_job_id,
            actor=actor,
            db_path=db_path,
        )
        if is_new:
            created += 1
        else:
            updated += 1

    return {"created": created, "updated": updated, "skipped": skipped}


def list_leads(
    search: str = "",
    status: str = "",
    category: str = "",
    city: str = "",
    country: str = "",
    min_score: int | None = None,
    min_rating: float | None = None,
    min_reviews: int | None = None,
    has_website: bool | None = None,
    sort_by: str = "score",
    sort_dir: str = "desc",
    limit: int = 50,
    offset: int = 0,
    db_path: Path | None = None,
) -> dict[str, Any]:
    """Query leads with search, filters, sorting, and pagination."""
    where = ["1=1"]
    params: list[Any] = []

    if search:
        like = f"%{search.strip()}%"
        where.append("(name LIKE ? OR category LIKE ? OR city LIKE ? OR phone LIKE ? OR email LIKE ?)")
        params.extend([like, like, like, like, like])
    if status:
        where.append("pipeline_status = ?")
        params.append(status)
    if category:
        where.append("LOWER(category) LIKE ?")
        params.append(f"%{category.strip().lower()}%")
    if city:
        where.append("LOWER(city) LIKE ?")
        params.append(f"%{city.strip().lower()}%")
    if country:
        where.append("LOWER(country) LIKE ?")
        params.append(f"%{country.strip().lower()}%")
    if min_score is not None:
        where.append("score >= ?")
        params.append(min_score)
    if min_rating is not None:
        where.append("rating >= ?")
        params.append(min_rating)
    if min_reviews is not None:
        where.append("review_count >= ?")
        params.append(min_reviews)
    if has_website is not None:
        where.append("has_website = ?")
        params.append(1 if has_website else 0)

    allowed_sorts = {
        "score": "score",
        "name": "name",
        "rating": "rating",
        "review_count": "review_count",
        "created": "first_discovered_at",
        "updated": "updated_at",
        "last_contacted": "last_contacted_at",
    }
    col = allowed_sorts.get(sort_by, "score")
    direction = "ASC" if sort_dir.lower() == "asc" else "DESC"
    where_sql = " AND ".join(where)

    with get_db(db_path) as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM leads WHERE {where_sql}", params).fetchone()[0]
        rows = conn.execute(
            f"SELECT * FROM leads WHERE {where_sql} ORDER BY {col} {direction}, id DESC LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()
        return {
            "total": total,
            "items": [_row_to_lead(r) for r in rows],
            "limit": limit,
            "offset": offset,
        }


def get_lead_detail(lead_id: int, db_path: Path | None = None) -> dict[str, Any] | None:
    """Return full lead record with contacts, activities, messages, tasks, and demos."""
    with get_db(db_path) as conn:
        row = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
        if not row:
            return None
        lead = _row_to_lead(row)
        lead["contacts"] = [
            dict(r)
            for r in conn.execute("SELECT * FROM contacts WHERE lead_id = ? ORDER BY id ASC", (lead_id,)).fetchall()
        ]
        lead["activities"] = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM lead_activities WHERE lead_id = ? ORDER BY created_at DESC LIMIT 100",
                (lead_id,),
            ).fetchall()
        ]
        lead["messages"] = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM messages WHERE lead_id = ? ORDER BY created_at ASC",
                (lead_id,),
            ).fetchall()
        ]
        lead["tasks"] = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM tasks WHERE lead_id = ? ORDER BY due_date ASC",
                (lead_id,),
            ).fetchall()
        ]
        lead["demos"] = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM demos WHERE lead_id = ? ORDER BY scheduled_at DESC",
                (lead_id,),
            ).fetchall()
        ]
        return lead


def update_lead_status(
    lead_id: int,
    new_status: str,
    actor: str = "user",
    note: str = "",
    db_path: Path | None = None,
) -> dict[str, Any] | None:
    """Update a lead's pipeline status and record an activity."""
    if new_status not in PIPELINE_STATUSES:
        raise ValueError(f"Invalid pipeline status: {new_status}")
    now = _utcnow()
    with get_db(db_path) as conn:
        row = conn.execute("SELECT pipeline_status FROM leads WHERE id = ?", (lead_id,)).fetchone()
        if not row:
            return None
        old_status = row["pipeline_status"]
        conn.execute(
            "UPDATE leads SET pipeline_status = ?, updated_at = ? WHERE id = ?",
            (new_status, now, lead_id),
        )
        conn.execute(
            """INSERT INTO lead_activities (lead_id, activity_type, title, description, metadata_json, actor, created_at)
            VALUES (?, 'status_changed', ?, ?, ?, ?, ?)""",
            (
                lead_id,
                f"Status: {old_status} → {new_status}",
                note,
                json.dumps({"from": old_status, "to": new_status}),
                actor,
                now,
            ),
        )
        if new_status in ("not_interested", "do_not_contact", "converted", "demo_scheduled"):
            conn.execute(
                """UPDATE sequence_enrollments
                SET status = 'stopped', stop_reason = ?, updated_at = ?
                WHERE lead_id = ? AND status = 'active'""",
                (f"Pipeline status changed to {new_status}", now, lead_id),
            )
        updated = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
        return _row_to_lead(updated)


def bulk_update_leads(
    lead_ids: list[int],
    action: str,
    value: str = "",
    actor: str = "user",
    db_path: Path | None = None,
) -> int:
    """Perform bulk status, tag, owner, or delete operations on leads."""
    if not lead_ids:
        return 0
    now = _utcnow()
    count = 0
    with get_db(db_path) as conn:
        for lid in lead_ids:
            if action == "status" and value in PIPELINE_STATUSES:
                conn.execute(
                    "UPDATE leads SET pipeline_status = ?, updated_at = ? WHERE id = ?",
                    (value, now, lid),
                )
                count += 1
            elif action == "assign":
                conn.execute(
                    "UPDATE leads SET lead_owner = ?, updated_at = ? WHERE id = ?",
                    (value, now, lid),
                )
                count += 1
            elif action == "tag" and value.strip():
                row = conn.execute("SELECT tags_json FROM leads WHERE id = ?", (lid,)).fetchone()
                if row:
                    tags = json.loads(row["tags_json"] or "[]")
                    if value.strip() not in tags:
                        tags.append(value.strip())
                    conn.execute(
                        "UPDATE leads SET tags_json = ?, updated_at = ? WHERE id = ?",
                        (json.dumps(tags), now, lid),
                    )
                    count += 1
            elif action == "delete":
                cur = conn.execute("DELETE FROM leads WHERE id = ?", (lid,))
                count += cur.rowcount
    return count


def update_contact_eligibility(
    contact_id: int,
    email_eligible: bool | None = None,
    whatsapp_eligible: bool | None = None,
    consent_source: str = "manual_verification",
    legal_basis: str = "explicit_consent",
    preferred_language: str | None = None,
    name: str | None = None,
    position: str | None = None,
    actor: str = "user",
    db_path: Path | None = None,
) -> dict[str, Any] | None:
    """Update a contact's channel eligibility, consent metadata, or details."""
    now = _utcnow()
    with get_db(db_path) as conn:
        row = conn.execute("SELECT * FROM contacts WHERE id = ?", (contact_id,)).fetchone()
        if not row:
            return None
        updates = ["updated_at = ?"]
        params: list[Any] = [now]

        if email_eligible is not None:
            updates.extend([
                "email_eligible = ?",
                "email_consent_status = ?",
                "email_consent_source = ?",
                "email_consent_date = ?",
                "email_legal_basis = ?",
            ])
            params.extend([
                1 if email_eligible else 0,
                "eligible" if email_eligible else "ineligible",
                consent_source,
                now,
                legal_basis,
            ])

        if whatsapp_eligible is not None:
            updates.extend([
                "whatsapp_eligible = ?",
                "whatsapp_consent_status = ?",
                "whatsapp_consent_source = ?",
                "whatsapp_consent_date = ?",
                "whatsapp_legal_basis = ?",
            ])
            params.extend([
                1 if whatsapp_eligible else 0,
                "eligible" if whatsapp_eligible else "ineligible",
                consent_source,
                now,
                legal_basis,
            ])

        if preferred_language:
            updates.append("preferred_language = ?")
            params.append(preferred_language)
        if name is not None:
            updates.append("name = ?")
            params.append(name)
        if position is not None:
            updates.append("position = ?")
            params.append(position)

        params.append(contact_id)
        conn.execute(f"UPDATE contacts SET {', '.join(updates)} WHERE id = ?", params)
        updated = dict(conn.execute("SELECT * FROM contacts WHERE id = ?", (contact_id,)).fetchone())
        return updated


def export_leads_csv(status: str = "", db_path: Path | None = None) -> str:
    """Export CRM leads to CSV string."""
    res = list_leads(status=status, limit=10000, offset=0, db_path=db_path)
    buf = io.StringIO()
    fields = [
        "id", "name", "category", "city", "country", "address",
        "phone", "email", "website", "rating", "review_count",
        "score", "pipeline_status", "lead_owner", "google_maps_url", "place_id",
    ]
    writer = csv.DictWriter(buf, fieldnames=fields)
    writer.writeheader()
    for item in res["items"]:
        writer.writerow({k: item.get(k, "") for k in fields})
    return buf.getvalue()
