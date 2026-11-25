"""Follow-up task management and demo scheduling for CarteFidele."""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from crm.db import get_db

DEMO_STATUSES = ("requested", "scheduled", "completed", "no_show", "rescheduled", "cancelled")


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_task(
    lead_id: int,
    title: str,
    due_date: str,
    task_type: str = "follow_up",
    description: str = "",
    priority: str = "medium",
    assigned_user: str = "",
    related_campaign_id: int | None = None,
    actor: str = "user",
    db_path: Path | None = None,
) -> dict[str, Any]:
    """Create a follow-up task and update lead next_followup_at."""
    now = _utcnow()
    with get_db(db_path) as conn:
        cur = conn.execute(
            """INSERT INTO tasks
            (lead_id, task_type, title, description, due_date, priority, assigned_user, status, related_campaign_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)""",
            (lead_id, task_type, title, description, due_date, priority, assigned_user, related_campaign_id, now, now),
        )
        task_id = cur.lastrowid
        conn.execute(
            "UPDATE leads SET next_followup_at = ?, updated_at = ? WHERE id = ?",
            (due_date, now, lead_id),
        )
        conn.execute(
            """INSERT INTO lead_activities (lead_id, activity_type, title, description, metadata_json, actor, created_at)
            VALUES (?, 'followup_scheduled', ?, ?, ?, ?, ?)""",
            (lead_id, f"Task scheduled: {title}", f"Due: {due_date}", json.dumps({"task_id": task_id}), actor, now),
        )
        return dict(conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone())


def list_tasks(bucket: str = "all", db_path: Path | None = None) -> list[dict[str, Any]]:
    """List tasks filtered by bucket: today, overdue, upcoming, completed, or all."""
    today_prefix = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with get_db(db_path) as conn:
        rows = conn.execute(
            """SELECT t.*, l.name AS lead_name, l.city AS lead_city, l.pipeline_status
            FROM tasks t
            JOIN leads l ON l.id = t.lead_id
            ORDER BY t.due_date ASC"""
        ).fetchall()
        items = [dict(r) for r in rows]

    if bucket == "completed":
        return [t for t in items if t["status"] == "completed"]
    pending = [t for t in items if t["status"] == "pending"]
    if bucket == "today":
        return [t for t in pending if (t["due_date"] or "")[:10] == today_prefix]
    if bucket == "overdue":
        return [t for t in pending if (t["due_date"] or "")[:10] < today_prefix]
    if bucket == "upcoming":
        return [t for t in pending if (t["due_date"] or "")[:10] > today_prefix]
    return items


def complete_task(task_id: int, actor: str = "user", db_path: Path | None = None) -> dict[str, Any] | None:
    """Mark a task as completed."""
    now = _utcnow()
    with get_db(db_path) as conn:
        cur = conn.execute(
            "UPDATE tasks SET status = 'completed', completed_at = ?, updated_at = ? WHERE id = ?",
            (now, now, task_id),
        )
        if cur.rowcount == 0:
            return None
        row = dict(conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone())
        return row


def schedule_demo(
    lead_id: int,
    scheduled_at: str,
    sales_rep: str = "",
    meeting_link: str = "",
    notes: str = "",
    contact_id: int | None = None,
    actor: str = "user",
    db_path: Path | None = None,
) -> dict[str, Any]:
    """Schedule a CarteFidele demo, update pipeline status, and stop active sequences."""
    now = _utcnow()
    with get_db(db_path) as conn:
        cur = conn.execute(
            """INSERT INTO demos
            (lead_id, contact_id, scheduled_at, sales_rep, meeting_link, status, notes, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'scheduled', ?, ?, ?)""",
            (lead_id, contact_id, scheduled_at, sales_rep, meeting_link, notes, now, now),
        )
        demo_id = cur.lastrowid

        conn.execute(
            "UPDATE leads SET pipeline_status = 'demo_scheduled', updated_at = ? WHERE id = ?",
            (now, lead_id),
        )
        conn.execute(
            """UPDATE sequence_enrollments
            SET status = 'stopped', stop_reason = 'Demo scheduled', updated_at = ?
            WHERE lead_id = ? AND status = 'active'""",
            (now, lead_id),
        )
        conn.execute(
            """INSERT INTO lead_activities (lead_id, activity_type, title, description, metadata_json, actor, created_at)
            VALUES (?, 'demo_scheduled', 'CarteFidele Demo Scheduled', ?, ?, ?, ?)""",
            (
                lead_id,
                f"Scheduled for {scheduled_at} ({sales_rep or 'Sales team'})",
                json.dumps({"demo_id": demo_id, "meeting_link": meeting_link}),
                actor,
                now,
            ),
        )
        return dict(conn.execute("SELECT * FROM demos WHERE id = ?", (demo_id,)).fetchone())


def update_demo(
    demo_id: int,
    status: str,
    outcome: str = "",
    notes: str = "",
    actor: str = "user",
    db_path: Path | None = None,
) -> dict[str, Any] | None:
    """Update demo status and outcome."""
    if status not in DEMO_STATUSES:
        raise ValueError(f"Invalid demo status: {status}")
    now = _utcnow()
    with get_db(db_path) as conn:
        row = conn.execute("SELECT * FROM demos WHERE id = ?", (demo_id,)).fetchone()
        if not row:
            return None
        conn.execute(
            "UPDATE demos SET status = ?, outcome = COALESCE(NULLIF(?, ''), outcome), notes = COALESCE(NULLIF(?, ''), notes), updated_at = ? WHERE id = ?",
            (status, outcome, notes, now, demo_id),
        )
        if status == "completed":
            conn.execute(
                "UPDATE leads SET pipeline_status = 'demo_completed', updated_at = ? WHERE id = ?",
                (now, row["lead_id"]),
            )
            conn.execute(
                """INSERT INTO lead_activities (lead_id, activity_type, title, description, metadata_json, actor, created_at)
                VALUES (?, 'demo_completed', 'Demo Completed', ?, ?, ?, ?)""",
                (row["lead_id"], outcome or notes, json.dumps({"demo_id": demo_id}), actor, now),
            )
        return dict(conn.execute("SELECT * FROM demos WHERE id = ?", (demo_id,)).fetchone())


def list_demos(db_path: Path | None = None) -> list[dict[str, Any]]:
    """Return all demos joined with lead info."""
    with get_db(db_path) as conn:
        rows = conn.execute(
            """SELECT d.*, l.name AS lead_name, l.city AS lead_city
            FROM demos d
            JOIN leads l ON l.id = d.lead_id
            ORDER BY d.scheduled_at DESC"""
        ).fetchall()
        return [dict(r) for r in rows]
