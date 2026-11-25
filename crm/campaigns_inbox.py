"""Campaign manager, outreach sequences, unified inbox, analytics, and settings."""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from crm.compliance import check_channel_eligibility
from crm.db import get_db
from crm.email_service import send_email_message
from crm.templates_service import render_template_text
from crm.whatsapp_service import send_whatsapp_message

CONVERSATION_STATUSES = ("open", "waiting_reply", "replied", "interested", "demo_requested", "closed")


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_campaign(
    name: str,
    channel: str,
    language: str = "fr",
    description: str = "",
    template_id: int | None = None,
    target_countries: list[str] | None = None,
    target_cities: list[str] | None = None,
    target_categories: list[str] | None = None,
    min_score: int = 0,
    max_score: int = 100,
    target_statuses: list[str] | None = None,
    daily_limit: int = 30,
    delay_seconds: int = 5,
    db_path: Path | None = None,
) -> dict[str, Any]:
    """Create an outreach campaign."""
    now = _utcnow()
    with get_db(db_path) as conn:
        cur = conn.execute(
            """INSERT INTO campaigns (
                name, description, channel, language, template_id,
                target_countries_json, target_cities_json, target_categories_json,
                min_score, max_score, target_statuses_json,
                status, daily_limit, delay_seconds, stats_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft', ?, ?, '{}', ?, ?)""",
            (
                name,
                description,
                channel,
                language,
                template_id,
                json.dumps(target_countries or []),
                json.dumps(target_cities or []),
                json.dumps(target_categories or []),
                min_score,
                max_score,
                json.dumps(target_statuses or []),
                daily_limit,
                delay_seconds,
                now,
                now,
            ),
        )
        return dict(conn.execute("SELECT * FROM campaigns WHERE id = ?", (cur.lastrowid,)).fetchone())


def list_campaigns(db_path: Path | None = None) -> list[dict[str, Any]]:
    """List all campaigns."""
    with get_db(db_path) as conn:
        rows = conn.execute("SELECT * FROM campaigns ORDER BY created_at DESC").fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["stats"] = json.loads(d.get("stats_json") or "{}")
            result.append(d)
        return result


def preview_campaign_targets(campaign_id: int, db_path: Path | None = None) -> dict[str, Any]:
    """Preview eligible vs blocked leads for a campaign before sending."""
    with get_db(db_path) as conn:
        camp = conn.execute("SELECT * FROM campaigns WHERE id = ?", (campaign_id,)).fetchone()
        if not camp:
            raise ValueError("Campaign not found.")

        countries = [c.lower() for c in json.loads(camp["target_countries_json"] or "[]")]
        cities = [c.lower() for c in json.loads(camp["target_cities_json"] or "[]")]
        cats = [c.lower() for c in json.loads(camp["target_categories_json"] or "[]")]
        statuses = json.loads(camp["target_statuses_json"] or "[]")

        rows = conn.execute(
            "SELECT * FROM leads WHERE score >= ? AND score <= ? ORDER BY score DESC",
            (camp["min_score"], camp["max_score"]),
        ).fetchall()

    eligible_leads = []
    blocked_leads = []
    is_manual = camp["channel"].startswith("manual_")

    for row in rows:
        lead = dict(row)
        if countries and (lead.get("country") or "").lower() not in countries:
            continue
        if cities and (lead.get("city") or "").lower() not in cities:
            continue
        if cats and not any(c in (lead.get("category") or "").lower() for c in cats):
            continue
        if statuses and lead.get("pipeline_status") not in statuses:
            continue

        if is_manual:
            if lead.get("pipeline_status") not in ("do_not_contact", "not_interested", "disqualified"):
                eligible_leads.append(lead)
            else:
                blocked_leads.append({"lead_id": lead["id"], "name": lead["name"], "reason": f"Status is {lead['pipeline_status']}"})
        else:
            ok, reason = check_channel_eligibility(lead["id"], camp["channel"], db_path=db_path)
            if ok:
                eligible_leads.append(lead)
            else:
                blocked_leads.append({"lead_id": lead["id"], "name": lead["name"], "reason": reason})

    return {
        "campaign": dict(camp),
        "eligible_count": len(eligible_leads),
        "blocked_count": len(blocked_leads),
        "eligible_leads": eligible_leads[: camp["daily_limit"]],
        "blocked_sample": blocked_leads[:20],
    }


async def execute_campaign(
    campaign_id: int,
    actor: str = "user",
    db_path: Path | None = None,
) -> dict[str, Any]:
    """Execute a campaign for eligible leads up to daily_limit."""
    preview = preview_campaign_targets(campaign_id, db_path=db_path)
    camp = preview["campaign"]
    targets = preview["eligible_leads"]

    with get_db(db_path) as conn:
        tpl = None
        if camp.get("template_id"):
            tpl = conn.execute("SELECT * FROM templates WHERE id = ?", (camp["template_id"],)).fetchone()
        if not tpl:
            tpl_channel = "social" if camp["channel"].startswith("manual_") else camp["channel"]
            tpl = conn.execute(
                "SELECT * FROM templates WHERE channel = ? AND language = ? ORDER BY is_default DESC LIMIT 1",
                (tpl_channel, camp["language"]),
            ).fetchone()

    sent = 0
    failed = 0
    manual_tasks = 0
    now = _utcnow()

    for lead in targets:
        subj = render_template_text(tpl["subject"] if tpl else "CarteFidele Demo", lead)
        body_txt = render_template_text(
            tpl["body_text"] if tpl else "Hello {{business_name}}, let's schedule a CarteFidele demo.",
            lead,
        )
        body_html = render_template_text(tpl["body_html"] if tpl else "", lead, escape_html_values=True)

        if camp["channel"].startswith("manual_"):
            with get_db(db_path) as conn:
                conn.execute(
                    """INSERT INTO tasks (lead_id, task_type, title, description, due_date, priority, status, related_campaign_id, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, 'medium', 'pending', ?, ?, ?)""",
                    (
                        lead["id"],
                        camp["channel"].replace("manual_", ""),
                        f"{camp['channel'].replace('manual_', '').title()} outreach: {lead['name']}",
                        body_txt,
                        now,
                        campaign_id,
                        now,
                        now,
                    ),
                )
            manual_tasks += 1
        elif camp["channel"] == "email":
            try:
                await send_email_message(
                    lead_id=lead["id"],
                    to_email=lead["email_normalized"],
                    subject=subj,
                    body_text=body_txt,
                    body_html=body_html,
                    campaign_id=campaign_id,
                    idempotency_key=f"camp_{campaign_id}_lead_{lead['id']}",
                    actor=actor,
                    db_path=db_path,
                )
                sent += 1
            except Exception:
                failed += 1
        elif camp["channel"] == "whatsapp":
            try:
                await send_whatsapp_message(
                    lead_id=lead["id"],
                    phone=lead["phone_normalized"],
                    body_text=body_txt,
                    campaign_id=campaign_id,
                    idempotency_key=f"camp_{campaign_id}_lead_{lead['id']}",
                    actor=actor,
                    db_path=db_path,
                )
                sent += 1
            except Exception:
                failed += 1

    stats = {"sent": sent, "failed": failed, "manual_tasks": manual_tasks, "blocked": preview["blocked_count"]}
    with get_db(db_path) as conn:
        conn.execute(
            "UPDATE campaigns SET status = 'completed', stats_json = ?, updated_at = ? WHERE id = ?",
            (json.dumps(stats), _utcnow(), campaign_id),
        )
    return {"campaign_id": campaign_id, **stats}


def list_conversations(status_filter: str = "", db_path: Path | None = None) -> list[dict[str, Any]]:
    """Return unified inbox conversations grouped by lead + channel."""
    where = ["1=1"]
    params: list[Any] = []
    if status_filter:
        where.append("m.conversation_status = ?")
        params.append(status_filter)

    with get_db(db_path) as conn:
        rows = conn.execute(
            f"""SELECT m.*, l.name AS lead_name, l.city AS lead_city, l.pipeline_status
            FROM messages m
            JOIN leads l ON l.id = m.lead_id
            WHERE {' AND '.join(where)}
            ORDER BY m.created_at DESC
            LIMIT 200""",
            params,
        ).fetchall()
        return [dict(r) for r in rows]


def update_conversation_status(
    message_id: int,
    conversation_status: str,
    assigned_user: str | None = None,
    db_path: Path | None = None,
) -> dict[str, Any] | None:
    """Update conversation status and assignment."""
    if conversation_status not in CONVERSATION_STATUSES:
        raise ValueError(f"Invalid conversation status: {conversation_status}")
    now = _utcnow()
    with get_db(db_path) as conn:
        row = conn.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
        if not row:
            return None
        conn.execute(
            "UPDATE messages SET conversation_status = ?, assigned_user = COALESCE(?, assigned_user), updated_at = ? WHERE id = ?",
            (conversation_status, assigned_user, now, message_id),
        )
        return dict(conn.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone())


def get_analytics_summary(db_path: Path | None = None) -> dict[str, Any]:
    """Compute CRM, outreach, pipeline, and conversion analytics."""
    with get_db(db_path) as conn:
        total_leads = conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
        by_status = {
            r["pipeline_status"]: r["cnt"]
            for r in conn.execute("SELECT pipeline_status, COUNT(*) AS cnt FROM leads GROUP BY pipeline_status").fetchall()
        }
        eligible_email = conn.execute("SELECT COUNT(*) FROM contacts WHERE email_eligible = 1").fetchone()[0]
        eligible_wa = conn.execute("SELECT COUNT(*) FROM contacts WHERE whatsapp_eligible = 1").fetchone()[0]

        msg_stats = {
            f"{r['channel']}_{r['direction']}_{r['delivery_status']}": r["cnt"]
            for r in conn.execute(
                "SELECT channel, direction, delivery_status, COUNT(*) AS cnt FROM messages GROUP BY channel, direction, delivery_status"
            ).fetchall()
        }
        demos_total = conn.execute("SELECT COUNT(*) FROM demos").fetchone()[0]
        demos_completed = conn.execute("SELECT COUNT(*) FROM demos WHERE status = 'completed'").fetchone()[0]

        top_categories = [
            dict(r)
            for r in conn.execute(
                "SELECT category, COUNT(*) AS count, ROUND(AVG(score), 1) AS avg_score FROM leads WHERE category != '' GROUP BY category ORDER BY count DESC LIMIT 10"
            ).fetchall()
        ]
        top_cities = [
            dict(r)
            for r in conn.execute(
                "SELECT city, country, COUNT(*) AS count FROM leads WHERE city != '' GROUP BY city, country ORDER BY count DESC LIMIT 10"
            ).fetchall()
        ]

    converted = by_status.get("converted", 0)
    conversion_rate = round((converted / total_leads) * 100, 2) if total_leads else 0.0
    return {
        "total_leads": total_leads,
        "new_leads": by_status.get("new", 0),
        "qualified_leads": by_status.get("qualified", 0),
        "contacted_leads": by_status.get("contacted", 0),
        "replied_leads": by_status.get("replied", 0),
        "interested_leads": by_status.get("interested", 0),
        "demos_scheduled": by_status.get("demo_scheduled", 0),
        "demos_completed": demos_completed or by_status.get("demo_completed", 0),
        "demos_total": demos_total,
        "converted_customers": converted,
        "conversion_rate_pct": conversion_rate,
        "eligible_contacts": {"email": eligible_email, "whatsapp": eligible_wa},
        "by_status": by_status,
        "message_stats": msg_stats,
        "top_categories": top_categories,
        "top_cities": top_cities,
    }


def get_all_settings(db_path: Path | None = None) -> dict[str, Any]:
    """Return all non-secret CRM settings."""
    with get_db(db_path) as conn:
        rows = conn.execute("SELECT key, value_json FROM crm_settings").fetchall()
        return {r["key"]: json.loads(r["value_json"]) for r in rows}


def update_setting(key: str, value: dict[str, Any], db_path: Path | None = None) -> dict[str, Any]:
    """Update a CRM settings group."""
    now = _utcnow()
    with get_db(db_path) as conn:
        conn.execute(
            """INSERT INTO crm_settings (key, value_json, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json, updated_at = excluded.updated_at""",
            (key, json.dumps(value), now),
        )
    return value
