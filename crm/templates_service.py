"""Message template management and safe variable personalization."""

import html
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from crm.db import get_db


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def list_templates(channel: str = "", language: str = "", db_path: Path | None = None) -> list[dict[str, Any]]:
    """List templates with optional channel and language filters."""
    where = ["1=1"]
    params: list[Any] = []
    if channel:
        where.append("channel = ?")
        params.append(channel)
    if language:
        where.append("language = ?")
        params.append(language)
    with get_db(db_path) as conn:
        rows = conn.execute(
            f"SELECT * FROM templates WHERE {' AND '.join(where)} ORDER BY is_default DESC, id ASC",
            params,
        ).fetchall()
        return [dict(r) for r in rows]


def create_template(
    name: str,
    channel: str,
    language: str,
    body_text: str,
    subject: str = "",
    body_html: str = "",
    category: str = "",
    db_path: Path | None = None,
) -> dict[str, Any]:
    """Create a new reusable outreach template."""
    now = _utcnow()
    with get_db(db_path) as conn:
        cur = conn.execute(
            """INSERT INTO templates
            (name, channel, language, category, subject, body_html, body_text, is_default, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?)""",
            (name, channel, language, category, subject, body_html, body_text, now, now),
        )
        return dict(conn.execute("SELECT * FROM templates WHERE id = ?", (cur.lastrowid,)).fetchone())


def render_template_text(
    template_str: str,
    lead: dict[str, Any],
    contact: dict[str, Any] | None = None,
    sender_name: str = "CarteFidele Team",
    escape_html_values: bool = False,
) -> str:
    """Render personalization variables safely without inventing missing facts."""
    contact_name = (contact.get("name") if contact else "") or ""
    if not contact_name:
        contact_name = "team" if (lead.get("city") or "").strip() else "there"

    mapping = {
        "business_name": (lead.get("name") or "your business").strip(),
        "business_category": (lead.get("category") or "local business").strip(),
        "city": (lead.get("city") or "your area").strip(),
        "country": (lead.get("country") or "").strip(),
        "contact_name": contact_name.strip(),
        "business_website": (lead.get("website") or "").strip(),
        "sender_name": sender_name.strip() or "CarteFidele Team",
        "company_name": "CarteFidele",
    }

    def _replace(match: re.Match) -> str:
        key = match.group(1).strip()
        val = mapping.get(key, "")
        return html.escape(val) if escape_html_values else val

    return re.sub(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}", _replace, template_str or "")
