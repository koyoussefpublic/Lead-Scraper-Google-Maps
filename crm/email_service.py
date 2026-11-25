"""Resend email outreach service with idempotency, safety guards, and webhook handling."""

import hashlib
import hmac
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from app import config
from crm.compliance import add_suppression, check_channel_eligibility
from crm.db import get_db

RESEND_API_URL = "https://api.resend.com/emails"


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_resend_status() -> dict[str, Any]:
    """Return safe Resend configuration status without exposing secrets."""
    configured = bool(config.RESEND_API_KEY and config.RESEND_FROM_EMAIL)
    return {
        "configured": configured,
        "from_email": config.RESEND_FROM_EMAIL or "(not set)",
        "reply_to": config.RESEND_REPLY_TO or "contact@cartefidele.com",
        "external_messaging_allowed": config.is_external_messaging_allowed(),
        "app_env": config.APP_ENV,
    }


async def send_email_message(
    lead_id: int,
    to_email: str,
    subject: str,
    body_text: str,
    body_html: str = "",
    contact_id: int | None = None,
    campaign_id: int | None = None,
    idempotency_key: str | None = None,
    is_test: bool = False,
    actor: str = "user",
    db_path: Path | None = None,
) -> dict[str, Any]:
    """Send an email via Resend (or simulate in development/safe mode)."""
    if not is_test:
        eligible, reason = check_channel_eligibility(
            lead_id=lead_id, channel="email", contact_id=contact_id, db_path=db_path
        )
        if not eligible:
            raise PermissionError(f"Compliance block: {reason}")

    idem_key = idempotency_key or f"email_{lead_id}_{uuid.uuid4().hex[:12]}"
    now = _utcnow()

    # Check idempotency: never blindly re-send an existing idempotency key
    with get_db(db_path) as conn:
        existing = conn.execute(
            "SELECT * FROM messages WHERE idempotency_key = ?", (idem_key,)
        ).fetchone()
        if existing:
            return dict(existing)

    # Local development / external messaging disabled -> simulate safely
    if not config.is_external_messaging_allowed():
        provider_id = f"sim_resend_{uuid.uuid4().hex[:10]}"
        return _record_outgoing_email(
            lead_id=lead_id,
            contact_id=contact_id,
            campaign_id=campaign_id,
            subject=subject,
            body_text=body_text,
            body_html=body_html,
            delivery_status="simulated",
            provider="mock_resend",
            provider_message_id=provider_id,
            idempotency_key=idem_key,
            is_simulated=1,
            actor=actor,
            db_path=db_path,
        )

    if not config.RESEND_API_KEY or not config.RESEND_FROM_EMAIL:
        raise RuntimeError("Resend is not configured (RESEND_API_KEY and RESEND_FROM_EMAIL required).")

    payload: dict[str, Any] = {
        "from": config.RESEND_FROM_EMAIL,
        "to": [to_email],
        "reply_to": config.RESEND_REPLY_TO or "contact@cartefidele.com",
        "subject": subject,
        "text": body_text,
    }
    if body_html:
        payload["html"] = body_html

    headers = {
        "Authorization": f"Bearer {config.RESEND_API_KEY}",
        "Content-Type": "application/json",
        "Idempotency-Key": idem_key,
    }

    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(RESEND_API_URL, json=payload, headers=headers)

    if resp.status_code >= 400:
        err_msg = f"Resend API error {resp.status_code}: {resp.text[:300]}"
        _record_outgoing_email(
            lead_id=lead_id,
            contact_id=contact_id,
            campaign_id=campaign_id,
            subject=subject,
            body_text=body_text,
            body_html=body_html,
            delivery_status="failed",
            provider="resend",
            provider_message_id="",
            idempotency_key=idem_key,
            error_message=err_msg,
            is_simulated=0,
            actor=actor,
            db_path=db_path,
        )
        raise RuntimeError(err_msg)

    data = resp.json()
    provider_id = data.get("id", "")
    return _record_outgoing_email(
        lead_id=lead_id,
        contact_id=contact_id,
        campaign_id=campaign_id,
        subject=subject,
        body_text=body_text,
        body_html=body_html,
        delivery_status="sent",
        provider="resend",
        provider_message_id=provider_id,
        idempotency_key=idem_key,
        is_simulated=0,
        actor=actor,
        db_path=db_path,
    )


def _record_outgoing_email(
    *,
    lead_id: int,
    contact_id: int | None,
    campaign_id: int | None,
    subject: str,
    body_text: str,
    body_html: str,
    delivery_status: str,
    provider: str,
    provider_message_id: str,
    idempotency_key: str,
    is_simulated: int,
    error_message: str = "",
    actor: str = "user",
    db_path: Path | None = None,
) -> dict[str, Any]:
    now = _utcnow()
    with get_db(db_path) as conn:
        cur = conn.execute(
            """INSERT INTO messages (
                lead_id, contact_id, campaign_id, channel, direction,
                subject, body_text, body_html, delivery_status, provider,
                provider_message_id, idempotency_key, conversation_status,
                error_message, is_simulated, created_at, updated_at
            ) VALUES (?, ?, ?, 'email', 'outgoing', ?, ?, ?, ?, ?, ?, ?, 'waiting_reply', ?, ?, ?, ?)""",
            (
                lead_id,
                contact_id,
                campaign_id,
                subject,
                body_text,
                body_html,
                delivery_status,
                provider,
                provider_message_id,
                idempotency_key,
                error_message,
                is_simulated,
                now,
                now,
            ),
        )
        msg_id = cur.lastrowid
        if delivery_status in ("sent", "simulated"):
            conn.execute(
                """UPDATE leads SET
                    last_contacted_at = ?,
                    pipeline_status = CASE WHEN pipeline_status IN ('new', 'researching', 'qualified') THEN 'contacted' ELSE pipeline_status END,
                    updated_at = ?
                WHERE id = ?""",
                (now, now, lead_id),
            )
            conn.execute(
                """INSERT INTO lead_activities (lead_id, contact_id, activity_type, title, description, metadata_json, actor, created_at)
                VALUES (?, ?, 'email_sent', ?, ?, ?, ?, ?)""",
                (
                    lead_id,
                    contact_id,
                    f"Email {'simulated' if is_simulated else 'sent'}: {subject}",
                    f"Provider: {provider} | Status: {delivery_status}",
                    json.dumps({"message_id": msg_id, "provider_message_id": provider_message_id}),
                    actor,
                    now,
                ),
            )
        return dict(conn.execute("SELECT * FROM messages WHERE id = ?", (msg_id,)).fetchone())


def process_resend_webhook(
    payload: dict[str, Any],
    signature_header: str = "",
    raw_body: bytes = b"",
    db_path: Path | None = None,
) -> dict[str, Any]:
    """Handle Resend webhook events idempotently (delivery, bounce, complaint)."""
    if config.RESEND_WEBHOOK_SECRET:
        expected = hmac.new(
            config.RESEND_WEBHOOK_SECRET.encode("utf-8"),
            raw_body,
            hashlib.sha256,
        ).hexdigest()
        if not signature_header or not hmac.compare_digest(signature_header, expected):
            raise PermissionError("Invalid Resend webhook signature.")

    event_type = payload.get("type", "")
    data = payload.get("data", {}) or {}
    email_id = data.get("email_id", "")
    event_id = payload.get("id") or f"{event_type}:{email_id}:{payload.get('created_at', '')}"
    now = _utcnow()

    with get_db(db_path) as conn:
        existing = conn.execute(
            "SELECT id FROM webhook_events WHERE provider = 'resend' AND event_id = ?",
            (event_id,),
        ).fetchone()
        if existing:
            return {"status": "duplicate_ignored", "event_id": event_id}

        conn.execute(
            "INSERT INTO webhook_events (provider, event_id, event_type, payload_json, processed_at) VALUES ('resend', ?, ?, ?, ?)",
            (event_id, event_type, json.dumps(payload), now),
        )

        msg = None
        if email_id:
            msg = conn.execute(
                "SELECT * FROM messages WHERE provider_message_id = ?", (email_id,)
            ).fetchone()

        status_map = {
            "email.delivered": "delivered",
            "email.bounced": "bounced",
            "email.complained": "complained",
            "email.delivery_delayed": "delayed",
        }
        new_status = status_map.get(event_type)
        if msg and new_status:
            conn.execute(
                "UPDATE messages SET delivery_status = ?, updated_at = ? WHERE id = ?",
                (new_status, now, msg["id"]),
            )

    if event_type in ("email.bounced", "email.complained"):
        to_list = data.get("to") or []
        for addr in to_list:
            add_suppression(
                channel="email",
                identifier=addr,
                reason=f"Resend {event_type}",
                source="resend_webhook",
                lead_id=msg["lead_id"] if msg else None,
                actor="resend_webhook",
                db_path=db_path,
            )

    return {"status": "processed", "event_id": event_id, "event_type": event_type}
