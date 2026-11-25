"""Comprehensive tests for CarteFidele CRM, compliance, Resend, and Evolution API."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app import config
from app.auth import create_session_token
from app.main import app
from crm.campaigns_inbox import (
    create_campaign,
    execute_campaign,
    get_analytics_summary,
    preview_campaign_targets,
)
from crm.compliance import add_suppression, check_channel_eligibility
from crm.db import get_db
from crm.email_service import process_resend_webhook, send_email_message
from crm.leads import (
    bulk_update_leads,
    get_lead_detail,
    ingest_csv_text,
    list_leads,
    update_contact_eligibility,
    update_lead_status,
    upsert_lead,
)
from crm.migrations import init_db
from crm.tasks_demos import complete_task, create_task, schedule_demo
from crm.templates_service import render_template_text
from crm.whatsapp_service import (
    EvolutionAPIProvider,
    MockWhatsAppProvider,
    WhatsAppConfigurationError,
    get_whatsapp_provider,
    process_evolution_webhook,
    send_whatsapp_message,
)

client = TestClient(app)


@pytest.fixture()
def temp_db(tmp_path: Path) -> Path:
    db_file = tmp_path / "test_crm.db"
    init_db(db_file)
    return db_file


@pytest.fixture()
def auth_client(tmp_path: Path, monkeypatch):
    db_file = tmp_path / "api_crm.db"
    monkeypatch.setenv("CRM_DB_PATH", str(db_file))
    init_db(db_file)
    token = create_session_token(config.APP_USERNAME)
    client.cookies.set(config.SESSION_COOKIE_NAME, token)
    yield db_file
    client.cookies.clear()


def test_migrations_and_deduplication(temp_db: Path):
    """Verify Place ID, phone, and email deduplication prevents duplicate leads."""
    id1, created1 = upsert_lead(
        {
            "name": "Cafe Atlas",
            "category": "Cafe",
            "city": "Casablanca",
            "country": "Morocco",
            "phone": "0612345678",
            "email": "Hello@CafeAtlas.ma",
            "place_id": "ChIJ_ATLAS_1",
            "rating": 4.6,
            "review_count": 120,
        },
        db_path=temp_db,
    )
    assert created1 is True

    # Same place_id -> updates existing lead instead of creating duplicate
    id2, created2 = upsert_lead(
        {
            "name": "Cafe Atlas",
            "place_id": "ChIJ_ATLAS_1",
            "review_count": 125,
        },
        db_path=temp_db,
    )
    assert created2 is False
    assert id2 == id1

    # Same normalized Moroccan phone without place_id -> still deduplicates
    id3, created3 = upsert_lead(
        {
            "name": "Cafe Atlas",
            "country": "Morocco",
            "phone": "+212612345678",
        },
        db_path=temp_db,
    )
    assert created3 is False
    assert id3 == id1

    detail = get_lead_detail(id1, db_path=temp_db)
    assert detail["score"] >= 70
    assert detail["phone_normalized"] == "+212612345678"
    assert detail["email_normalized"] == "hello@cafeatlas.ma"


def test_csv_import_and_bulk_operations(temp_db: Path):
    """Verify CSV ingestion, filtering, and bulk status updates."""
    csv_text = (
        "Name,Category,City,Country,Phone,Business Email,Rating,Reviews,Has Website,Place ID\n"
        "Boulangerie Nour,Bakery,Rabat,Morocco,0622334455,contact@nour.ma,4.5,60,No,PID1\n"
        "Salon Chic,Salon,Marrakech,Morocco,0633445566,,4.2,30,No,PID2\n"
    )
    res = ingest_csv_text(csv_text, db_path=temp_db)
    assert res == {"created": 2, "updated": 0, "skipped": 0}

    listed = list_leads(city="Rabat", db_path=temp_db)
    assert listed["total"] == 1
    lead_id = listed["items"][0]["id"]

    updated_count = bulk_update_leads([lead_id], action="status", value="qualified", db_path=temp_db)
    assert updated_count == 1
    assert get_lead_detail(lead_id, db_path=temp_db)["pipeline_status"] == "qualified"


def test_template_rendering_no_invented_facts():
    """Templates render available fields safely and omit/fallback missing ones."""
    lead = {"name": "Restaurant Medina", "category": "Restaurant", "city": "Fes", "country": "Morocco"}
    rendered = render_template_text(
        "Bonjour {{contact_name}}, {{business_name}} à {{city}} ({{business_website}}).",
        lead,
    )
    assert "Restaurant Medina" in rendered
    assert "Fes" in rendered
    assert "()" in rendered  # missing website is empty, not invented


def test_local_development_safety_blocks_real_evolution(monkeypatch):
    """When APP_ENV=development or ALLOW_EXTERNAL_MESSAGING=false, Evolution API is never initialized."""
    monkeypatch.setattr(config, "APP_ENV", "development")
    monkeypatch.setattr(config, "ALLOW_EXTERNAL_MESSAGING", False)
    monkeypatch.setattr(config, "WHATSAPP_ENABLED", True)
    monkeypatch.setattr(config, "WHATSAPP_PROVIDER", "evolution")

    provider = get_whatsapp_provider()
    assert isinstance(provider, MockWhatsAppProvider)


def test_production_missing_credentials_never_falls_back_to_mock(monkeypatch):
    """In production, missing Evolution API credentials must raise an error, never mock."""
    monkeypatch.setattr(config, "APP_ENV", "production")
    monkeypatch.setattr(config, "ALLOW_EXTERNAL_MESSAGING", True)
    monkeypatch.setattr(config, "WHATSAPP_ENABLED", True)
    monkeypatch.setattr(config, "WHATSAPP_PROVIDER", "evolution")
    monkeypatch.setattr(config, "EVOLUTION_API_ENABLED", True)
    monkeypatch.setattr(config, "EVOLUTION_API_BASE_URL", "")
    monkeypatch.setattr(config, "EVOLUTION_API_KEY", "")
    monkeypatch.setattr(config, "EVOLUTION_API_INSTANCE", "")

    with pytest.raises(WhatsAppConfigurationError):
        get_whatsapp_provider()


@pytest.mark.asyncio
async def test_compliance_blocks_ineligible_and_suppressed_contacts(temp_db: Path, monkeypatch):
    """Publicly scraped leads are blocked until marked eligible, and suppression overrides everything."""
    monkeypatch.setattr(config, "APP_ENV", "development")
    monkeypatch.setattr(config, "ALLOW_EXTERNAL_MESSAGING", False)

    lead_id, _ = upsert_lead(
        {
            "name": "Gym Atlas",
            "category": "Gym",
            "city": "Casablanca",
            "country": "Morocco",
            "phone": "0655667788",
            "email": "info@gymatlas.ma",
        },
        db_path=temp_db,
    )
    detail = get_lead_detail(lead_id, db_path=temp_db)
    contact_id = detail["contacts"][0]["id"]

    # Default: ineligible
    ok, _ = check_channel_eligibility(lead_id, "email", contact_id=contact_id, db_path=temp_db)
    assert ok is False
    with pytest.raises(PermissionError):
        await send_email_message(
            lead_id=lead_id,
            to_email="info@gymatlas.ma",
            subject="Test",
            body_text="Hello",
            contact_id=contact_id,
            db_path=temp_db,
        )

    # Mark eligible -> simulated send succeeds in dev mode
    update_contact_eligibility(contact_id, email_eligible=True, whatsapp_eligible=True, db_path=temp_db)
    msg = await send_email_message(
        lead_id=lead_id,
        to_email="info@gymatlas.ma",
        subject="CarteFidele",
        body_text="Hello",
        contact_id=contact_id,
        idempotency_key="idem_test_1",
        db_path=temp_db,
    )
    assert msg["delivery_status"] == "simulated"
    assert msg["is_simulated"] == 1

    # Idempotency: same key returns same message without creating duplicate
    msg_dup = await send_email_message(
        lead_id=lead_id,
        to_email="info@gymatlas.ma",
        subject="CarteFidele",
        body_text="Hello",
        contact_id=contact_id,
        idempotency_key="idem_test_1",
        db_path=temp_db,
    )
    assert msg_dup["id"] == msg["id"]

    # Add to suppression list -> blocks both email and whatsapp even though eligible
    add_suppression("all", "info@gymatlas.ma", reason="Unsubscribed", lead_id=lead_id, db_path=temp_db)
    ok_after, _ = check_channel_eligibility(lead_id, "email", contact_id=contact_id, db_path=temp_db)
    assert ok_after is False


@pytest.mark.asyncio
async def test_resend_mocked_http_and_webhook_bounce(temp_db: Path, monkeypatch):
    """Test Resend API call with mocked HTTP and bounce webhook auto-suppression."""
    monkeypatch.setattr(config, "APP_ENV", "production")
    monkeypatch.setattr(config, "ALLOW_EXTERNAL_MESSAGING", True)
    monkeypatch.setattr(config, "RESEND_API_KEY", "re_test_key")
    monkeypatch.setattr(config, "RESEND_FROM_EMAIL", "hello@outreach.cartefidele.com")
    monkeypatch.setattr(config, "RESEND_WEBHOOK_SECRET", "")

    lead_id, _ = upsert_lead(
        {"name": "Hotel Royal", "category": "Hotel", "city": "Tangier", "email": "book@hotelroyal.ma"},
        db_path=temp_db,
    )
    contact_id = get_lead_detail(lead_id, db_path=temp_db)["contacts"][0]["id"]
    update_contact_eligibility(contact_id, email_eligible=True, db_path=temp_db)

    mock_resp = MagicMock(status_code=200)
    mock_resp.json.return_value = {"id": "re_msg_123"}
    mock_client = AsyncMock()
    mock_client.__aenter__.return_value.post.return_value = mock_resp

    with patch("crm.email_service.httpx.AsyncClient", return_value=mock_client):
        msg = await send_email_message(
            lead_id=lead_id,
            to_email="book@hotelroyal.ma",
            subject="CarteFidele",
            body_text="Hello",
            contact_id=contact_id,
            db_path=temp_db,
        )
    assert msg["delivery_status"] == "sent"
    assert msg["provider_message_id"] == "re_msg_123"

    # Bounce webhook updates status and suppresses email; duplicate webhook is ignored
    webhook_payload = {
        "id": "evt_bounce_1",
        "type": "email.bounced",
        "data": {"email_id": "re_msg_123", "to": ["book@hotelroyal.ma"]},
    }
    w1 = process_resend_webhook(webhook_payload, db_path=temp_db)
    assert w1["status"] == "processed"
    w2 = process_resend_webhook(webhook_payload, db_path=temp_db)
    assert w2["status"] == "duplicate_ignored"

    ok, _ = check_channel_eligibility(lead_id, "email", contact_id=contact_id, db_path=temp_db)
    assert ok is False


@pytest.mark.asyncio
async def test_evolution_mocked_http_and_incoming_reply_webhook(temp_db: Path, monkeypatch):
    """Test Evolution API provider with mocked HTTP and incoming reply webhook."""
    monkeypatch.setattr(config, "APP_ENV", "production")
    monkeypatch.setattr(config, "ALLOW_EXTERNAL_MESSAGING", True)
    monkeypatch.setattr(config, "WHATSAPP_ENABLED", True)
    monkeypatch.setattr(config, "WHATSAPP_PROVIDER", "evolution")
    monkeypatch.setattr(config, "EVOLUTION_API_ENABLED", True)
    monkeypatch.setattr(config, "EVOLUTION_API_BASE_URL", "https://evo.example.com")
    monkeypatch.setattr(config, "EVOLUTION_API_KEY", "secret-evo-key")
    monkeypatch.setattr(config, "EVOLUTION_API_INSTANCE", "cartefidele-wa")
    monkeypatch.setattr(config, "EVOLUTION_WEBHOOK_SECRET", "")

    lead_id, _ = upsert_lead(
        {"name": "Patisserie Amal", "category": "Bakery", "city": "Casablanca", "country": "Morocco", "phone": "0611223344"},
        db_path=temp_db,
    )
    contact_id = get_lead_detail(lead_id, db_path=temp_db)["contacts"][0]["id"]
    update_contact_eligibility(contact_id, whatsapp_eligible=True, db_path=temp_db)

    # Enroll in active sequence to verify incoming reply stops it
    with get_db(temp_db) as conn:
        seq_id = conn.execute("SELECT id FROM sequences LIMIT 1").fetchone()["id"]
        conn.execute(
            "INSERT INTO sequence_enrollments (sequence_id, lead_id, status, created_at, updated_at) VALUES (?, ?, 'active', '2026-01-01', '2026-01-01')",
            (seq_id, lead_id),
        )

    mock_resp = MagicMock(status_code=201, content=b'{"key":{"id":"evo_msg_99"}}')
    mock_resp.json.return_value = {"key": {"id": "evo_msg_99"}}
    mock_client = AsyncMock()
    mock_client.__aenter__.return_value.request.return_value = mock_resp

    with patch("crm.whatsapp_service.httpx.AsyncClient", return_value=mock_client):
        msg = await send_whatsapp_message(
            lead_id=lead_id,
            phone="+212611223344",
            body_text="Bonjour Patisserie Amal",
            contact_id=contact_id,
            db_path=temp_db,
        )
    assert msg["delivery_status"] == "sent"
    assert msg["provider"] == "evolution"

    # Incoming reply webhook
    evo_payload = {
        "event": "messages.upsert",
        "event_id": "evo_evt_1",
        "data": {
            "key": {"id": "in_1", "fromMe": False, "remoteJid": "212611223344@s.whatsapp.net"},
            "message": {"conversation": "Oui, je suis intéressé par une démo !"},
        },
    }
    res1 = process_evolution_webhook(evo_payload, db_path=temp_db)
    assert res1["status"] == "processed"
    res2 = process_evolution_webhook(evo_payload, db_path=temp_db)
    assert res2["status"] == "duplicate_ignored"

    detail = get_lead_detail(lead_id, db_path=temp_db)
    assert detail["pipeline_status"] == "replied"
    with get_db(temp_db) as conn:
        enr = conn.execute("SELECT status FROM sequence_enrollments WHERE lead_id = ?", (lead_id,)).fetchone()
        assert enr["status"] == "stopped"


@pytest.mark.asyncio
async def test_tasks_demos_campaigns_and_crm_api(auth_client: Path):
    """Verify tasks, demo scheduling, campaigns, and CRM REST endpoints."""
    lead_id, _ = upsert_lead(
        {"name": "Cafe Soleil", "category": "Cafe", "city": "Rabat", "country": "Morocco", "email": "hi@cafesoleil.ma"},
        db_path=auth_client,
    )
    task = create_task(lead_id=lead_id, title="Call owner", due_date="2026-12-01", db_path=auth_client)
    assert task["status"] == "pending"
    done = complete_task(task["id"], db_path=auth_client)
    assert done["status"] == "completed"

    demo = schedule_demo(lead_id=lead_id, scheduled_at="2026-12-02 15:00", sales_rep="Amina", db_path=auth_client)
    assert demo["status"] == "scheduled"
    assert get_lead_detail(lead_id, db_path=auth_client)["pipeline_status"] == "demo_scheduled"

    camp = create_campaign(name="Rabat Manual Calls", channel="manual_call", language="fr", db_path=auth_client)
    preview = preview_campaign_targets(camp["id"], db_path=auth_client)
    assert preview["eligible_count"] == 1
    exec_res = await execute_campaign(camp["id"], db_path=auth_client)
    assert exec_res["manual_tasks"] == 1

    analytics = get_analytics_summary(db_path=auth_client)
    assert analytics["total_leads"] == 1

    # Verify authenticated CRM UI and API routes
    assert client.get("/crm").status_code == 200
    assert client.get("/api/crm/leads").status_code == 200
    assert client.get("/api/crm/analytics").status_code == 200
    assert client.get("/api/crm/whatsapp/status").json()["development_mock_mode"] is True
