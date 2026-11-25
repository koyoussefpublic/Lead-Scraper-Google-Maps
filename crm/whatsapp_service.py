"""WhatsApp messaging provider abstraction, Mock provider, and Evolution API adapter."""

import hmac
import json
import logging
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from app import config
from crm.compliance import check_channel_eligibility
from crm.db import get_db
from crm.normalizer import normalize_phone_international

logger = logging.getLogger(__name__)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class WhatsAppConfigurationError(RuntimeError):
    """Raised when production WhatsApp is enabled without valid Evolution API config."""


class MessagingProvider(ABC):
    """Provider-independent WhatsApp messaging interface."""

    provider_name: str = "base"
    is_mock: bool = False

    @abstractmethod
    async def get_status(self) -> dict[str, Any]:
        """Return connection and provider status."""

    @abstractmethod
    async def get_qr_code(self) -> dict[str, Any]:
        """Retrieve QR code payload for Baileys WhatsApp Web pairing."""

    @abstractmethod
    async def disconnect(self) -> dict[str, Any]:
        """Disconnect the active WhatsApp session."""

    @abstractmethod
    async def send_text(self, phone: str, text: str, idempotency_key: str = "") -> dict[str, Any]:
        """Send a text message and return provider result."""


class MockWhatsAppProvider(MessagingProvider):
    """Safe local development mock WhatsApp provider (zero external HTTP calls)."""

    provider_name = "mock"
    is_mock = True

    async def get_status(self) -> dict[str, Any]:
        return {
            "provider": "mock",
            "is_mock": True,
            "configured": True,
            "connected": True,
            "state": "open (simulated)",
            "instance": "local-mock-instance",
            "phone_number": "+212600000000 (simulated)",
            "message": "Development mock mode active. No external Evolution API requests are made.",
        }

    async def get_qr_code(self) -> dict[str, Any]:
        return {
            "provider": "mock",
            "is_mock": True,
            "qr_available": False,
            "message": "QR code pairing is disabled in local mock mode. Deploy Evolution API on Railway for real QR pairing.",
        }

    async def disconnect(self) -> dict[str, Any]:
        return {"provider": "mock", "is_mock": True, "disconnected": True}

    async def send_text(self, phone: str, text: str, idempotency_key: str = "") -> dict[str, Any]:
        norm = normalize_phone_international(phone)
        if not norm:
            return {
                "success": False,
                "provider": "mock",
                "is_simulated": True,
                "status": "failed",
                "provider_message_id": "",
                "error": "Invalid phone number.",
            }
        return {
            "success": True,
            "provider": "mock",
            "is_simulated": True,
            "status": "simulated",
            "provider_message_id": f"sim_wa_{uuid.uuid4().hex[:10]}",
            "error": "",
        }


class EvolutionAPIProvider(MessagingProvider):
    """Adapter for a self-hosted Evolution API v2 instance (Baileys WhatsApp Web)."""

    provider_name = "evolution"
    is_mock = False

    def __init__(self, base_url: str, api_key: str, instance: str):
        if not base_url or not api_key or not instance:
            raise WhatsAppConfigurationError(
                "Evolution API requires EVOLUTION_API_BASE_URL, EVOLUTION_API_KEY, and EVOLUTION_API_INSTANCE."
            )
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.instance = instance

    def _headers(self) -> dict[str, str]:
        return {
            "apikey": self.api_key,
            "Content-Type": "application/json",
        }

    async def _request(self, method: str, path: str, json_body: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.request(method, url, headers=self._headers(), json=json_body)
        if resp.status_code >= 400:
            raise RuntimeError(f"Evolution API HTTP {resp.status_code}: {resp.text[:250]}")
        return resp.json() if resp.content else {}

    async def get_status(self) -> dict[str, Any]:
        try:
            data = await self._request("GET", f"/instance/connectionState/{self.instance}")
            state = (data.get("instance") or {}).get("state") or data.get("state") or "unknown"
            return {
                "provider": "evolution",
                "is_mock": False,
                "configured": True,
                "connected": state == "open",
                "state": state,
                "instance": self.instance,
                "phone_number": (data.get("instance") or {}).get("owner", ""),
                "message": f"Evolution API instance '{self.instance}' state: {state}",
            }
        except Exception as exc:
            logger.warning(f"Evolution API status check failed for instance '{self.instance}': {exc}")
            return {
                "provider": "evolution",
                "is_mock": False,
                "configured": True,
                "connected": False,
                "state": "error",
                "instance": self.instance,
                "phone_number": "",
                "error": str(exc),
            }

    async def get_qr_code(self) -> dict[str, Any]:
        try:
            data = await self._request("GET", f"/instance/connect/{self.instance}")
        except RuntimeError:
            # Attempt to create instance if it does not exist yet
            await self._request(
                "POST",
                "/instance/create",
                {"instanceName": self.instance, "integration": "WHATSAPP-BAILEYS", "qrcode": True},
            )
            data = await self._request("GET", f"/instance/connect/{self.instance}")

        return {
            "provider": "evolution",
            "is_mock": False,
            "qr_available": bool(data.get("base64") or data.get("code")),
            "base64": data.get("base64", ""),
            "code": data.get("code", ""),
            "pairing_code": data.get("pairingCode", ""),
        }

    async def disconnect(self) -> dict[str, Any]:
        data = await self._request("DELETE", f"/instance/logout/{self.instance}")
        return {"provider": "evolution", "is_mock": False, "result": data}

    async def send_text(self, phone: str, text: str, idempotency_key: str = "") -> dict[str, Any]:
        norm = normalize_phone_international(phone)
        digits = "".join(ch for ch in norm if ch.isdigit())
        if not digits:
            return {
                "success": False,
                "provider": "evolution",
                "is_simulated": False,
                "status": "failed",
                "provider_message_id": "",
                "error": "Invalid recipient phone number.",
            }
        try:
            data = await self._request(
                "POST",
                f"/message/sendText/{self.instance}",
                {"number": digits, "text": text},
            )
            msg_id = (data.get("key") or {}).get("id") or data.get("id") or ""
            return {
                "success": True,
                "provider": "evolution",
                "is_simulated": False,
                "status": "sent",
                "provider_message_id": msg_id,
                "error": "",
            }
        except Exception as exc:
            return {
                "success": False,
                "provider": "evolution",
                "is_simulated": False,
                "status": "failed",
                "provider_message_id": "",
                "error": str(exc),
            }


class FutureWhatsAppCloudProvider(MessagingProvider):
    """Placeholder adapter for future official Meta WhatsApp Cloud API support."""

    provider_name = "whatsapp_cloud"
    is_mock = False

    async def get_status(self) -> dict[str, Any]:
        return {"provider": "whatsapp_cloud", "configured": False, "connected": False, "state": "not_implemented"}

    async def get_qr_code(self) -> dict[str, Any]:
        return {"provider": "whatsapp_cloud", "qr_available": False}

    async def disconnect(self) -> dict[str, Any]:
        return {"provider": "whatsapp_cloud", "disconnected": False}

    async def send_text(self, phone: str, text: str, idempotency_key: str = "") -> dict[str, Any]:
        raise NotImplementedError("Official WhatsApp Cloud API adapter is reserved for future use.")


def get_whatsapp_provider() -> MessagingProvider:
    """Resolve the active WhatsApp provider with strict local-dev safety guarantees.

    - In APP_ENV=development or when ALLOW_EXTERNAL_MESSAGING is false:
      ALWAYS returns MockWhatsAppProvider (never contacts Evolution API).
    - In APP_ENV=production with external messaging enabled and provider=evolution:
      Requires complete Evolution API credentials; never silently falls back to mock.
    """
    if config.APP_ENV != "production" or not config.ALLOW_EXTERNAL_MESSAGING:
        return MockWhatsAppProvider()

    if not config.WHATSAPP_ENABLED or config.WHATSAPP_PROVIDER == "mock":
        raise WhatsAppConfigurationError(
            "Production WhatsApp sending is disabled (WHATSAPP_ENABLED=false or WHATSAPP_PROVIDER=mock)."
        )

    if config.WHATSAPP_PROVIDER == "evolution":
        if not config.EVOLUTION_API_ENABLED:
            raise WhatsAppConfigurationError("EVOLUTION_API_ENABLED must be true in production.")
        return EvolutionAPIProvider(
            base_url=config.EVOLUTION_API_BASE_URL,
            api_key=config.EVOLUTION_API_KEY,
            instance=config.EVOLUTION_API_INSTANCE,
        )

    raise WhatsAppConfigurationError(f"Unsupported WHATSAPP_PROVIDER: {config.WHATSAPP_PROVIDER}")


def get_safe_whatsapp_ui_status() -> dict[str, Any]:
    """Return non-secret WhatsApp configuration summary for the settings/WhatsApp page."""
    is_dev = config.APP_ENV != "production" or not config.ALLOW_EXTERNAL_MESSAGING
    configured = bool(
        config.EVOLUTION_API_ENABLED
        and config.EVOLUTION_API_BASE_URL
        and config.EVOLUTION_API_KEY
        and config.EVOLUTION_API_INSTANCE
    )
    return {
        "app_env": config.APP_ENV,
        "allow_external_messaging": config.ALLOW_EXTERNAL_MESSAGING,
        "development_mock_mode": is_dev,
        "whatsapp_enabled": config.WHATSAPP_ENABLED,
        "provider": "mock" if is_dev else config.WHATSAPP_PROVIDER,
        "evolution_configured": configured,
        "evolution_instance": config.EVOLUTION_API_INSTANCE or "(not set)",
        "evolution_base_url": config.EVOLUTION_API_BASE_URL or "(not set)",
    }


async def send_whatsapp_message(
    lead_id: int,
    phone: str,
    body_text: str,
    contact_id: int | None = None,
    campaign_id: int | None = None,
    idempotency_key: str | None = None,
    is_test: bool = False,
    actor: str = "user",
    db_path: Path | None = None,
) -> dict[str, Any]:
    """Send a WhatsApp message with compliance checks, idempotency, and provider isolation."""
    if not is_test:
        eligible, reason = check_channel_eligibility(
            lead_id=lead_id, channel="whatsapp", contact_id=contact_id, db_path=db_path
        )
        if not eligible:
            raise PermissionError(f"Compliance block: {reason}")

    idem_key = idempotency_key or f"wa_{lead_id}_{uuid.uuid4().hex[:12]}"
    with get_db(db_path) as conn:
        existing = conn.execute(
            "SELECT * FROM messages WHERE idempotency_key = ?", (idem_key,)
        ).fetchone()
        if existing:
            return dict(existing)

    provider = get_whatsapp_provider()
    result = await provider.send_text(phone=phone, text=body_text, idempotency_key=idem_key)

    now = _utcnow()
    is_sim = 1 if result.get("is_simulated") else 0
    delivery_status = result.get("status", "failed")
    provider_msg_id = result.get("provider_message_id", "")
    err = result.get("error", "")

    with get_db(db_path) as conn:
        cur = conn.execute(
            """INSERT INTO messages (
                lead_id, contact_id, campaign_id, channel, direction,
                subject, body_text, body_html, delivery_status, provider,
                provider_message_id, idempotency_key, conversation_status,
                error_message, is_simulated, created_at, updated_at
            ) VALUES (?, ?, ?, 'whatsapp', 'outgoing', '', ?, '', ?, ?, ?, ?, 'waiting_reply', ?, ?, ?, ?)""",
            (
                lead_id,
                contact_id,
                campaign_id,
                body_text,
                delivery_status,
                provider.provider_name,
                provider_msg_id,
                idem_key,
                err,
                is_sim,
                now,
                now,
            ),
        )
        msg_id = cur.lastrowid
        if result.get("success"):
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
                VALUES (?, ?, 'whatsapp_sent', ?, ?, ?, ?, ?)""",
                (
                    lead_id,
                    contact_id,
                    f"WhatsApp {'simulated' if is_sim else 'sent'}",
                    f"Provider: {provider.provider_name} | Status: {delivery_status}",
                    json.dumps({"message_id": msg_id, "provider_message_id": provider_msg_id}),
                    actor,
                    now,
                ),
            )
        return dict(conn.execute("SELECT * FROM messages WHERE id = ?", (msg_id,)).fetchone())


def process_evolution_webhook(
    payload: dict[str, Any],
    secret_header: str = "",
    db_path: Path | None = None,
) -> dict[str, Any]:
    """Process Evolution API webhook events idempotently (no auto-replies)."""
    if config.EVOLUTION_WEBHOOK_SECRET:
        if not secret_header or not hmac.compare_digest(secret_header, config.EVOLUTION_WEBHOOK_SECRET):
            raise PermissionError("Invalid Evolution webhook secret.")

    event_type = (payload.get("event") or "").lower()
    data = payload.get("data") or {}
    key_obj = data.get("key") or {}
    msg_id = key_obj.get("id") or data.get("id") or ""
    event_id = payload.get("event_id") or f"{event_type}:{msg_id}:{payload.get('date_time', '')}"
    now = _utcnow()

    with get_db(db_path) as conn:
        existing = conn.execute(
            "SELECT id FROM webhook_events WHERE provider = 'evolution' AND event_id = ?",
            (event_id,),
        ).fetchone()
        if existing:
            return {"status": "duplicate_ignored", "event_id": event_id}

        conn.execute(
            "INSERT INTO webhook_events (provider, event_id, event_type, payload_json, processed_at) VALUES ('evolution', ?, ?, ?, ?)",
            (event_id, event_type, json.dumps(payload), now),
        )

        if event_type in ("messages.upsert", "message.received") and not key_obj.get("fromMe", False):
            remote_jid = key_obj.get("remoteJid") or ""
            sender_digits = "".join(ch for ch in remote_jid.split("@")[0] if ch.isdigit())
            text_body = (
                (data.get("message") or {}).get("conversation")
                or ((data.get("message") or {}).get("extendedTextMessage") or {}).get("text")
                or data.get("text")
                or ""
            )
            if sender_digits:
                lead = conn.execute(
                    "SELECT id FROM leads WHERE phone_normalized LIKE ? LIMIT 1",
                    (f"%{sender_digits[-9:]}",),
                ).fetchone()
                if lead:
                    lead_id = lead["id"]
                    conn.execute(
                        """INSERT INTO messages (
                            lead_id, channel, direction, subject, body_text, body_html,
                            delivery_status, provider, provider_message_id, idempotency_key,
                            conversation_status, is_simulated, created_at, updated_at
                        ) VALUES (?, 'whatsapp', 'incoming', '', ?, '', 'received', 'evolution', ?, ?, 'replied', 0, ?, ?)""",
                        (lead_id, text_body, msg_id, f"in_evo_{event_id}", now, now),
                    )
                    conn.execute(
                        "UPDATE leads SET pipeline_status = 'replied', updated_at = ? WHERE id = ?",
                        (now, lead_id),
                    )
                    conn.execute(
                        """UPDATE sequence_enrollments
                        SET status = 'stopped', stop_reason = 'WhatsApp reply received', updated_at = ?
                        WHERE lead_id = ? AND status = 'active'""",
                        (now, lead_id),
                    )
                    conn.execute(
                        """INSERT INTO lead_activities (lead_id, activity_type, title, description, metadata_json, actor, created_at)
                        VALUES (?, 'reply_received', 'WhatsApp Reply Received', ?, ?, 'evolution_webhook', ?)""",
                        (lead_id, text_body[:200], json.dumps({"event_id": event_id}), now),
                    )
        elif event_type == "messages.update" and msg_id:
            status_val = (data.get("status") or "").lower()
            if status_val:
                conn.execute(
                    "UPDATE messages SET delivery_status = ?, updated_at = ? WHERE provider_message_id = ?",
                    (status_val, now, msg_id),
                )

    return {"status": "processed", "event_id": event_id, "event_type": event_type}
