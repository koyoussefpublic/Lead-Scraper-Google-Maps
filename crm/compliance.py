"""Mandatory compliance, consent, opt-out, and suppression enforcement."""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from crm.db import get_db
from crm.normalizer import normalize_email, normalize_phone_international


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def is_suppressed(
    channel: str,
    identifier: str = "",
    lead_id: int | None = None,
    db_path: Path | None = None,
) -> tuple[bool, str]:
    """Check if an identifier or lead is suppressed for the given channel or globally."""
    norm_id = (
        normalize_email(identifier)
        if channel == "email"
        else normalize_phone_international(identifier)
    )
    with get_db(db_path) as conn:
        if norm_id:
            row = conn.execute(
                """SELECT reason, channel FROM suppression_list
                WHERE identifier = ? AND channel IN (?, 'all')
                LIMIT 1""",
                (norm_id, channel),
            ).fetchone()
            if row:
                return True, f"Suppressed on {row['channel']}: {row['reason'] or 'opt-out'}"

        if lead_id is not None:
            row = conn.execute(
                """SELECT reason, channel FROM suppression_list
                WHERE lead_id = ? AND channel IN (?, 'all')
                LIMIT 1""",
                (lead_id, channel),
            ).fetchone()
            if row:
                return True, f"Lead suppressed on {row['channel']}: {row['reason'] or 'opt-out'}"

    return False, ""


def add_suppression(
    channel: str,
    identifier: str,
    reason: str = "Opt-out / Do Not Contact",
    source: str = "manual",
    lead_id: int | None = None,
    actor: str = "system",
    db_path: Path | None = None,
) -> int:
    """Add an identifier to the suppression list and stop active sequences."""
    now = _utcnow()
    norm_id = (
        normalize_email(identifier)
        if channel == "email"
        else normalize_phone_international(identifier) or identifier.strip().lower()
    )
    with get_db(db_path) as conn:
        conn.execute(
            """INSERT INTO suppression_list (channel, identifier, reason, source, lead_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(channel, identifier) DO UPDATE SET
                reason = excluded.reason,
                source = excluded.source,
                lead_id = COALESCE(excluded.lead_id, suppression_list.lead_id)""",
            (channel, norm_id, reason, source, lead_id, now),
        )
        sup_id = conn.execute(
            "SELECT id FROM suppression_list WHERE channel = ? AND identifier = ?",
            (channel, norm_id),
        ).fetchone()["id"]

        if lead_id is not None:
            if channel in ("email", "all"):
                conn.execute(
                    "UPDATE contacts SET email_eligible = 0, email_consent_status = 'opted_out', updated_at = ? WHERE lead_id = ?",
                    (now, lead_id),
                )
            if channel in ("whatsapp", "all"):
                conn.execute(
                    "UPDATE contacts SET whatsapp_eligible = 0, whatsapp_consent_status = 'opted_out', updated_at = ? WHERE lead_id = ?",
                    (now, lead_id),
                )
            conn.execute(
                """UPDATE sequence_enrollments
                SET status = 'stopped', stop_reason = ?, updated_at = ?
                WHERE lead_id = ? AND status = 'active'""",
                (f"Suppressed ({channel}): {reason}", now, lead_id),
            )
            conn.execute(
                """INSERT INTO lead_activities (lead_id, activity_type, title, description, metadata_json, actor, created_at)
                VALUES (?, 'opt_out_received', ?, ?, ?, ?, ?)""",
                (
                    lead_id,
                    f"Opt-out recorded ({channel})",
                    reason,
                    json.dumps({"channel": channel, "identifier": norm_id, "source": source}),
                    actor,
                    now,
                ),
            )

        conn.execute(
            """INSERT INTO audit_logs (actor, action, entity_type, entity_id, details_json, created_at)
            VALUES (?, 'suppression_added', 'suppression_list', ?, ?, ?)""",
            (actor, str(sup_id), json.dumps({"channel": channel, "identifier": norm_id, "reason": reason}), now),
        )
    return sup_id


def remove_suppression(suppression_id: int, actor: str = "admin", db_path: Path | None = None) -> bool:
    """Remove an entry from the suppression list."""
    with get_db(db_path) as conn:
        cur = conn.execute("DELETE FROM suppression_list WHERE id = ?", (suppression_id,))
        if cur.rowcount > 0:
            conn.execute(
                """INSERT INTO audit_logs (actor, action, entity_type, entity_id, details_json, created_at)
                VALUES (?, 'suppression_removed', 'suppression_list', ?, '{}', ?)""",
                (actor, str(suppression_id), _utcnow()),
            )
            return True
    return False


def list_suppressions(db_path: Path | None = None) -> list[dict[str, Any]]:
    """Return all suppression list records."""
    with get_db(db_path) as conn:
        rows = conn.execute("SELECT * FROM suppression_list ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]


def check_channel_eligibility(
    lead_id: int,
    channel: str,
    contact_id: int | None = None,
    db_path: Path | None = None,
) -> tuple[bool, str]:
    """Enforce compliance, suppression, and channel eligibility before sending.

    Publicly scraped phone numbers and emails are NOT automatically eligible
    for promotional outreach; explicit eligibility or legal basis is required.
    """
    with get_db(db_path) as conn:
        lead = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
        if not lead:
            return False, "Lead not found."

        if lead["pipeline_status"] in ("do_not_contact", "not_interested", "disqualified"):
            return False, f"Lead pipeline status is '{lead['pipeline_status']}'."

        if contact_id is not None:
            contact = conn.execute(
                "SELECT * FROM contacts WHERE id = ? AND lead_id = ?",
                (contact_id, lead_id),
            ).fetchone()
        else:
            contact = conn.execute(
                "SELECT * FROM contacts WHERE lead_id = ? ORDER BY id ASC LIMIT 1",
                (lead_id,),
            ).fetchone()

        identifier = ""
        if channel == "email":
            identifier = (contact["email_normalized"] if contact else "") or lead["email_normalized"]
            if not identifier:
                return False, "No valid email address on file."
        elif channel == "whatsapp":
            identifier = (
                (contact["whatsapp_number"] or contact["phone_normalized"]) if contact else ""
            ) or lead["phone_normalized"]
            if not identifier:
                return False, "No valid WhatsApp/phone number on file."
        else:
            return False, f"Unsupported automated outreach channel: {channel}"

    suppressed, sup_reason = is_suppressed(channel, identifier, lead_id=lead_id, db_path=db_path)
    if suppressed:
        return False, sup_reason

    if channel == "email":
        if not contact or not contact["email_eligible"]:
            return (
                False,
                "Contact is not marked eligible for email outreach (consent/legal basis required).",
            )
    elif channel == "whatsapp":
        if not contact or not contact["whatsapp_eligible"]:
            return (
                False,
                "Contact is not marked eligible for WhatsApp outreach (explicit consent/eligibility required).",
            )

    return True, "Eligible"
