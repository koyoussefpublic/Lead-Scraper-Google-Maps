"""Versioned SQLite schema migrations for the CarteFidele CRM."""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from crm.db import get_db


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


MIGRATION_001 = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    category TEXT DEFAULT '',
    description TEXT DEFAULT '',
    address TEXT DEFAULT '',
    city TEXT DEFAULT '',
    region TEXT DEFAULT '',
    country TEXT DEFAULT '',
    latitude REAL,
    longitude REAL,
    plus_code TEXT DEFAULT '',
    phone TEXT DEFAULT '',
    phone_normalized TEXT DEFAULT '',
    email TEXT DEFAULT '',
    email_normalized TEXT DEFAULT '',
    website TEXT DEFAULT '',
    website_normalized TEXT DEFAULT '',
    has_website INTEGER NOT NULL DEFAULT 0,
    google_maps_url TEXT DEFAULT '',
    place_id TEXT,
    dedup_key TEXT NOT NULL UNIQUE,
    rating REAL,
    review_count INTEGER,
    claimed INTEGER,
    hours TEXT DEFAULT '',
    price_level TEXT DEFAULT '',
    facebook TEXT DEFAULT '',
    instagram TEXT DEFAULT '',
    linkedin TEXT DEFAULT '',
    raw_data_json TEXT DEFAULT '{}',
    lead_source TEXT NOT NULL DEFAULT 'google_maps',
    scraping_job_id TEXT DEFAULT '',
    search_query TEXT DEFAULT '',
    pipeline_status TEXT NOT NULL DEFAULT 'new',
    lead_owner TEXT DEFAULT '',
    tags_json TEXT NOT NULL DEFAULT '[]',
    notes TEXT DEFAULT '',
    score INTEGER NOT NULL DEFAULT 0,
    score_breakdown_json TEXT NOT NULL DEFAULT '{}',
    last_contacted_at TEXT,
    next_followup_at TEXT,
    first_discovered_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_leads_place_id
    ON leads(place_id) WHERE place_id IS NOT NULL AND place_id != '';
CREATE INDEX IF NOT EXISTS idx_leads_status ON leads(pipeline_status);
CREATE INDEX IF NOT EXISTS idx_leads_score ON leads(score DESC);
CREATE INDEX IF NOT EXISTS idx_leads_city_country ON leads(city, country);
CREATE INDEX IF NOT EXISTS idx_leads_phone_norm ON leads(phone_normalized);
CREATE INDEX IF NOT EXISTS idx_leads_email_norm ON leads(email_normalized);
CREATE INDEX IF NOT EXISTS idx_leads_job ON leads(scraping_job_id);

CREATE TABLE IF NOT EXISTS contacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id INTEGER NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    name TEXT DEFAULT '',
    position TEXT DEFAULT '',
    email TEXT DEFAULT '',
    email_normalized TEXT DEFAULT '',
    phone TEXT DEFAULT '',
    phone_normalized TEXT DEFAULT '',
    whatsapp_number TEXT DEFAULT '',
    preferred_language TEXT NOT NULL DEFAULT 'fr',
    source TEXT NOT NULL DEFAULT 'public_listing',
    collection_date TEXT NOT NULL,
    verification_status TEXT NOT NULL DEFAULT 'unverified',
    email_consent_status TEXT NOT NULL DEFAULT 'unknown',
    email_consent_source TEXT DEFAULT '',
    email_consent_date TEXT,
    email_legal_basis TEXT DEFAULT '',
    email_eligible INTEGER NOT NULL DEFAULT 0,
    whatsapp_consent_status TEXT NOT NULL DEFAULT 'unknown',
    whatsapp_consent_source TEXT DEFAULT '',
    whatsapp_consent_date TEXT,
    whatsapp_legal_basis TEXT DEFAULT '',
    whatsapp_eligible INTEGER NOT NULL DEFAULT 0,
    last_contacted_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_contacts_lead ON contacts(lead_id);
CREATE INDEX IF NOT EXISTS idx_contacts_email ON contacts(email_normalized);
CREATE INDEX IF NOT EXISTS idx_contacts_phone ON contacts(phone_normalized);

CREATE TABLE IF NOT EXISTS lead_activities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id INTEGER NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    contact_id INTEGER REFERENCES contacts(id) ON DELETE SET NULL,
    activity_type TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT DEFAULT '',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    actor TEXT DEFAULT 'system',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_activities_lead ON lead_activities(lead_id, created_at DESC);

CREATE TABLE IF NOT EXISTS templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    channel TEXT NOT NULL,
    language TEXT NOT NULL DEFAULT 'fr',
    category TEXT DEFAULT '',
    subject TEXT DEFAULT '',
    body_html TEXT DEFAULT '',
    body_text TEXT NOT NULL,
    is_default INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS campaigns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT DEFAULT '',
    channel TEXT NOT NULL,
    language TEXT NOT NULL DEFAULT 'fr',
    template_id INTEGER REFERENCES templates(id) ON DELETE SET NULL,
    target_countries_json TEXT NOT NULL DEFAULT '[]',
    target_cities_json TEXT NOT NULL DEFAULT '[]',
    target_categories_json TEXT NOT NULL DEFAULT '[]',
    min_score INTEGER NOT NULL DEFAULT 0,
    max_score INTEGER NOT NULL DEFAULT 100,
    target_statuses_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'draft',
    daily_limit INTEGER NOT NULL DEFAULT 30,
    delay_seconds INTEGER NOT NULL DEFAULT 10,
    start_date TEXT,
    end_date TEXT,
    stats_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sequences (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT DEFAULT '',
    steps_json TEXT NOT NULL DEFAULT '[]',
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sequence_enrollments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sequence_id INTEGER NOT NULL REFERENCES sequences(id) ON DELETE CASCADE,
    lead_id INTEGER NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    campaign_id INTEGER REFERENCES campaigns(id) ON DELETE SET NULL,
    current_step INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active',
    stop_reason TEXT DEFAULT '',
    next_run_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_enrollments_lead ON sequence_enrollments(lead_id, status);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id INTEGER NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    contact_id INTEGER REFERENCES contacts(id) ON DELETE SET NULL,
    campaign_id INTEGER REFERENCES campaigns(id) ON DELETE SET NULL,
    sequence_id INTEGER REFERENCES sequences(id) ON DELETE SET NULL,
    channel TEXT NOT NULL,
    direction TEXT NOT NULL,
    subject TEXT DEFAULT '',
    body_text TEXT NOT NULL,
    body_html TEXT DEFAULT '',
    delivery_status TEXT NOT NULL DEFAULT 'queued',
    provider TEXT NOT NULL DEFAULT 'internal',
    provider_message_id TEXT DEFAULT '',
    idempotency_key TEXT UNIQUE,
    reply_to_message_id INTEGER REFERENCES messages(id) ON DELETE SET NULL,
    conversation_status TEXT NOT NULL DEFAULT 'open',
    assigned_user TEXT DEFAULT '',
    error_message TEXT DEFAULT '',
    is_simulated INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_lead ON messages(lead_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_messages_provider_id ON messages(provider_message_id);
CREATE INDEX IF NOT EXISTS idx_messages_conv_status ON messages(conversation_status);

CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id INTEGER NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    task_type TEXT NOT NULL DEFAULT 'follow_up',
    title TEXT NOT NULL,
    description TEXT DEFAULT '',
    due_date TEXT NOT NULL,
    priority TEXT NOT NULL DEFAULT 'medium',
    assigned_user TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    related_campaign_id INTEGER REFERENCES campaigns(id) ON DELETE SET NULL,
    related_activity_id INTEGER REFERENCES lead_activities(id) ON DELETE SET NULL,
    completed_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tasks_due ON tasks(status, due_date);
CREATE INDEX IF NOT EXISTS idx_tasks_lead ON tasks(lead_id);

CREATE TABLE IF NOT EXISTS demos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id INTEGER NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    contact_id INTEGER REFERENCES contacts(id) ON DELETE SET NULL,
    scheduled_at TEXT NOT NULL,
    sales_rep TEXT DEFAULT '',
    meeting_link TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'scheduled',
    notes TEXT DEFAULT '',
    outcome TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_demos_lead ON demos(lead_id);
CREATE INDEX IF NOT EXISTS idx_demos_scheduled ON demos(scheduled_at);

CREATE TABLE IF NOT EXISTS suppression_list (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel TEXT NOT NULL,
    identifier TEXT NOT NULL,
    reason TEXT DEFAULT '',
    source TEXT DEFAULT 'manual',
    lead_id INTEGER REFERENCES leads(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL,
    UNIQUE(channel, identifier)
);

CREATE INDEX IF NOT EXISTS idx_suppression_lookup ON suppression_list(channel, identifier);

CREATE TABLE IF NOT EXISTS webhook_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,
    event_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    processed_at TEXT NOT NULL,
    UNIQUE(provider, event_id)
);

CREATE TABLE IF NOT EXISTS crm_settings (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT DEFAULT '',
    details_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
"""

MIGRATIONS: list[tuple[int, str, str]] = [
    (1, "initial_crm_schema", MIGRATION_001),
]


def _seed_defaults(conn: sqlite3.Connection) -> None:
    """Insert default templates, scoring weights, and general settings if missing."""
    now = _utcnow()

    existing_settings = conn.execute("SELECT COUNT(*) FROM crm_settings").fetchone()[0]
    if existing_settings == 0:
        defaults = {
            "general": {
                "company_name": "CarteFidele",
                "company_website": "https://cartefidele.com",
                "default_language": "fr",
                "timezone": "Africa/Casablanca",
                "sender_name": "CarteFidele Team",
                "sales_email": "contact@cartefidele.com",
            },
            "scoring_weights": {
                "business_fit": 35,
                "loyalty_opportunity": 25,
                "activity_reputation": 15,
                "digital_opportunity": 15,
                "data_quality": 10,
            },
            "campaigns": {
                "daily_email_limit": 50,
                "daily_whatsapp_limit": 20,
                "quiet_hours_start": 20,
                "quiet_hours_end": 9,
                "default_followup_days": [0, 3, 7],
            },
            "compliance": {
                "morocco_law_09_08_strict": True,
                "require_explicit_whatsapp_eligibility": True,
                "require_email_eligibility": True,
                "data_retention_days": 365,
            },
        }
        for key, val in defaults.items():
            conn.execute(
                "INSERT OR IGNORE INTO crm_settings (key, value_json, updated_at) VALUES (?, ?, ?)",
                (key, json.dumps(val), now),
            )

    existing_templates = conn.execute("SELECT COUNT(*) FROM templates").fetchone()[0]
    if existing_templates == 0:
        default_templates = [
            (
                "CarteFidele - Restaurants & Cafés (FR)",
                "email",
                "fr",
                "restaurant",
                "Fidélisez les clients de {{business_name}} avec CarteFidele",
                "<p>Bonjour {{contact_name}},</p><p>Avec <strong>CarteFidele</strong> (<a href='https://cartefidele.com'>cartefidele.com</a>), les restaurants et cafés à {{city}} transforment leurs visiteurs occasionnels en clients réguliers grâce à des cartes de fidélité digitales, points et récompenses simples à gérer.</p><p>Seriez-vous disponible pour une courte démo de 15 minutes cette semaine ?</p><p>Cordialement,<br>{{sender_name}} — CarteFidele</p>",
                "Bonjour {{contact_name}},\n\nAvec CarteFidele (https://cartefidele.com), les restaurants et cafés à {{city}} transforment leurs visiteurs en clients réguliers grâce aux cartes de fidélité digitales et récompenses.\n\nSeriez-vous disponible pour une démo de 15 minutes cette semaine ?\n\nCordialement,\n{{sender_name}} — CarteFidele",
                1,
            ),
            (
                "CarteFidele - Salons & Beauté (FR)",
                "email",
                "fr",
                "salon",
                "Augmentez la rétention client chez {{business_name}}",
                "<p>Bonjour {{contact_name}},</p><p>CarteFidele aide les salons et centres de beauté à {{city}} à fidéliser leur clientèle avec un programme de points digital et un tableau de bord dédié.</p><p>Souhaitez-vous découvrir comment cela fonctionne lors d'une brève démo ?</p><p>Cordialement,<br>{{sender_name}} — CarteFidele</p>",
                "Bonjour {{contact_name}},\n\nCarteFidele aide les salons et centres de beauté à {{city}} à fidéliser leur clientèle avec un programme de points digital.\n\nSouhaitez-vous planifier une brève démo ?\n\nCordialement,\n{{sender_name}} — CarteFidele",
                0,
            ),
            (
                "CarteFidele - Local Business Intro (EN)",
                "email",
                "en",
                "",
                "Digital loyalty program for {{business_name}}",
                "<p>Hello {{contact_name}},</p><p><strong>CarteFidele</strong> (https://cartefidele.com) helps local businesses in {{city}} increase repeat visits with digital loyalty cards, points, rewards, and customer insights.</p><p>Would you be open to a quick 15-minute demo?</p><p>Best regards,<br>{{sender_name}} — CarteFidele</p>",
                "Hello {{contact_name}},\n\nCarteFidele (https://cartefidele.com) helps local businesses in {{city}} increase repeat visits with digital loyalty cards, points, and rewards.\n\nWould you be open to a quick 15-minute demo?\n\nBest regards,\n{{sender_name}} — CarteFidele",
                1,
            ),
            (
                "CarteFidele - WhatsApp Intro (FR)",
                "whatsapp",
                "fr",
                "",
                "",
                "",
                "Bonjour {{contact_name}}, ici {{sender_name}} de CarteFidele (cartefidele.com). Nous aidons les commerces à {{city}} comme {{business_name}} à fidéliser leurs clients via des cartes de fidélité digitales. Souhaitez-vous voir une rapide démo ?",
                1,
            ),
            (
                "CarteFidele - WhatsApp Intro (AR)",
                "whatsapp",
                "ar",
                "",
                "",
                "",
                "مرحبا {{contact_name}}، معكم {{sender_name}} من CarteFidele (cartefidele.com). نساعد الأنشطة التجارية في {{city}} مثل {{business_name}} على تعزيز ولاء الزبناء عبر بطاقات الوفاء الرقمية. هل تودون حجز عرض توضيحي قصير؟",
                0,
            ),
        ]
        for name, channel, lang, cat, subj, html, txt, is_def in default_templates:
            conn.execute(
                """INSERT INTO templates
                (name, channel, language, category, subject, body_html, body_text, is_default, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (name, channel, lang, cat, subj, html, txt, is_def, now, now),
            )

    existing_seq = conn.execute("SELECT COUNT(*) FROM sequences").fetchone()[0]
    if existing_seq == 0:
        steps = [
            {"day": 0, "channel": "email", "label": "Initial Email"},
            {"day": 3, "channel": "email", "label": "Follow-up Email"},
            {"day": 7, "channel": "email", "label": "Final Follow-up"},
        ]
        conn.execute(
            "INSERT INTO sequences (name, description, steps_json, is_active, created_at, updated_at) VALUES (?, ?, ?, 1, ?, ?)",
            (
                "Standard 3-Step CarteFidele Outreach",
                "Day 0 initial email, Day 3 follow-up, Day 7 final follow-up (auto-stops on reply, opt-out, or demo).",
                json.dumps(steps),
                now,
                now,
            ),
        )


def init_db(db_path: Path | None = None) -> None:
    """Run all pending migrations and seed defaults."""
    with get_db(db_path) as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                applied_at TEXT NOT NULL
            )"""
        )
        applied = {
            row["version"]
            for row in conn.execute("SELECT version FROM schema_migrations").fetchall()
        }
        for version, name, sql in MIGRATIONS:
            if version not in applied:
                conn.executescript(sql)
                conn.execute(
                    "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?)",
                    (version, name, _utcnow()),
                )
        _seed_defaults(conn)
