"""REST API endpoints for CarteFidele CRM, outreach, compliance, and webhooks."""

from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from app.auth import get_current_user
from crm.campaigns_inbox import (
    create_campaign,
    execute_campaign,
    get_all_settings,
    get_analytics_summary,
    list_campaigns,
    list_conversations,
    preview_campaign_targets,
    update_conversation_status,
    update_setting,
)
from crm.compliance import add_suppression, list_suppressions, remove_suppression
from crm.email_service import get_resend_status, process_resend_webhook, send_email_message
from crm.leads import (
    bulk_update_leads,
    export_leads_csv,
    get_lead_detail,
    ingest_csv_text,
    list_leads,
    record_activity,
    update_contact_eligibility,
    update_lead_status,
)
from crm.tasks_demos import (
    complete_task,
    create_task,
    list_demos,
    list_tasks,
    schedule_demo,
    update_demo,
)
from crm.templates_service import create_template, list_templates, render_template_text
from crm.whatsapp_service import (
    WhatsAppConfigurationError,
    get_safe_whatsapp_ui_status,
    get_whatsapp_provider,
    process_evolution_webhook,
    send_whatsapp_message,
)

router = APIRouter(tags=["CRM"])


class LeadStatusUpdate(BaseModel):
    status: str
    note: str = ""


class LeadNoteCreate(BaseModel):
    note: str = Field(..., min_length=1)


class BulkLeadRequest(BaseModel):
    lead_ids: list[int] = Field(..., min_length=1)
    action: str
    value: str = ""


class ContactEligibilityUpdate(BaseModel):
    email_eligible: bool | None = None
    whatsapp_eligible: bool | None = None
    consent_source: str = "manual_verification"
    legal_basis: str = "explicit_consent"
    preferred_language: str | None = None
    name: str | None = None
    position: str | None = None


class TaskCreate(BaseModel):
    lead_id: int
    title: str = Field(..., min_length=1)
    due_date: str
    task_type: str = "follow_up"
    description: str = ""
    priority: str = "medium"
    assigned_user: str = ""


class DemoCreate(BaseModel):
    lead_id: int
    scheduled_at: str
    sales_rep: str = ""
    meeting_link: str = ""
    notes: str = ""
    contact_id: int | None = None


class DemoUpdate(BaseModel):
    status: str
    outcome: str = ""
    notes: str = ""


class TemplateCreate(BaseModel):
    name: str = Field(..., min_length=1)
    channel: str
    language: str = "fr"
    body_text: str = Field(..., min_length=1)
    subject: str = ""
    body_html: str = ""
    category: str = ""


class TemplatePreviewRequest(BaseModel):
    lead_id: int
    subject: str = ""
    body_text: str = ""


class SendEmailRequest(BaseModel):
    to_email: str
    subject: str = Field(..., min_length=1)
    body_text: str = Field(..., min_length=1)
    body_html: str = ""
    contact_id: int | None = None
    is_test: bool = False


class SendWhatsAppRequest(BaseModel):
    phone: str
    body_text: str = Field(..., min_length=1)
    contact_id: int | None = None
    is_test: bool = False


class CampaignCreate(BaseModel):
    name: str = Field(..., min_length=1)
    channel: str
    language: str = "fr"
    description: str = ""
    template_id: int | None = None
    target_countries: list[str] = Field(default_factory=list)
    target_cities: list[str] = Field(default_factory=list)
    target_categories: list[str] = Field(default_factory=list)
    min_score: int = 0
    max_score: int = 100
    target_statuses: list[str] = Field(default_factory=list)
    daily_limit: int = 30
    delay_seconds: int = 5


class SuppressionCreate(BaseModel):
    channel: str = "all"
    identifier: str = Field(..., min_length=1)
    reason: str = "Manual opt-out"
    lead_id: int | None = None


class ConversationStatusUpdate(BaseModel):
    conversation_status: str
    assigned_user: str | None = None


@router.get("/api/crm/leads")
async def api_list_leads(
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
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    current_user: str = Depends(get_current_user),
):
    return list_leads(
        search=search,
        status=status,
        category=category,
        city=city,
        country=country,
        min_score=min_score,
        min_rating=min_rating,
        min_reviews=min_reviews,
        has_website=has_website,
        sort_by=sort_by,
        sort_dir=sort_dir,
        limit=limit,
        offset=offset,
    )


@router.post("/api/crm/leads/import-csv")
async def api_import_leads_csv(
    file: UploadFile = File(...),
    current_user: str = Depends(get_current_user),
):
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Please upload a valid .csv file.")
    raw = await file.read()
    if len(raw) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="CSV file exceeds 10MB limit.")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="CSV must be UTF-8 encoded.")
    return ingest_csv_text(text, lead_source="csv_import", actor=current_user)


@router.get("/api/crm/leads/export-csv")
async def api_export_leads_csv(
    status: str = "",
    current_user: str = Depends(get_current_user),
):
    csv_data = export_leads_csv(status=status)
    return PlainTextResponse(
        csv_data,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="cartefidele_crm_leads.csv"'},
    )


@router.post("/api/crm/leads/bulk")
async def api_bulk_leads(
    payload: BulkLeadRequest,
    current_user: str = Depends(get_current_user),
):
    updated = bulk_update_leads(
        lead_ids=payload.lead_ids,
        action=payload.action,
        value=payload.value,
        actor=current_user,
    )
    return {"updated": updated}


@router.get("/api/crm/leads/{lead_id}")
async def api_lead_detail(lead_id: int, current_user: str = Depends(get_current_user)):
    lead = get_lead_detail(lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found.")
    return lead


@router.patch("/api/crm/leads/{lead_id}/status")
async def api_update_lead_status(
    lead_id: int,
    payload: LeadStatusUpdate,
    current_user: str = Depends(get_current_user),
):
    try:
        updated = update_lead_status(lead_id, payload.status, actor=current_user, note=payload.note)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not updated:
        raise HTTPException(status_code=404, detail="Lead not found.")
    return updated


@router.post("/api/crm/leads/{lead_id}/notes")
async def api_add_lead_note(
    lead_id: int,
    payload: LeadNoteCreate,
    current_user: str = Depends(get_current_user),
):
    if not get_lead_detail(lead_id):
        raise HTTPException(status_code=404, detail="Lead not found.")
    act_id = record_activity(
        lead_id=lead_id,
        activity_type="note_added",
        title="Sales Note",
        description=payload.note,
        actor=current_user,
    )
    return {"id": act_id, "status": "ok"}


@router.patch("/api/crm/contacts/{contact_id}/eligibility")
async def api_update_contact_eligibility(
    contact_id: int,
    payload: ContactEligibilityUpdate,
    current_user: str = Depends(get_current_user),
):
    updated = update_contact_eligibility(
        contact_id=contact_id,
        email_eligible=payload.email_eligible,
        whatsapp_eligible=payload.whatsapp_eligible,
        consent_source=payload.consent_source,
        legal_basis=payload.legal_basis,
        preferred_language=payload.preferred_language,
        name=payload.name,
        position=payload.position,
        actor=current_user,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Contact not found.")
    return updated


@router.get("/api/crm/tasks")
async def api_list_tasks(bucket: str = "all", current_user: str = Depends(get_current_user)):
    return list_tasks(bucket=bucket)


@router.post("/api/crm/tasks", status_code=status.HTTP_201_CREATED)
async def api_create_task(payload: TaskCreate, current_user: str = Depends(get_current_user)):
    return create_task(**payload.model_dump(), actor=current_user)


@router.post("/api/crm/tasks/{task_id}/complete")
async def api_complete_task(task_id: int, current_user: str = Depends(get_current_user)):
    res = complete_task(task_id, actor=current_user)
    if not res:
        raise HTTPException(status_code=404, detail="Task not found.")
    return res


@router.get("/api/crm/demos")
async def api_list_demos(current_user: str = Depends(get_current_user)):
    return list_demos()


@router.post("/api/crm/demos", status_code=status.HTTP_201_CREATED)
async def api_schedule_demo(payload: DemoCreate, current_user: str = Depends(get_current_user)):
    return schedule_demo(**payload.model_dump(), actor=current_user)


@router.patch("/api/crm/demos/{demo_id}")
async def api_update_demo(
    demo_id: int,
    payload: DemoUpdate,
    current_user: str = Depends(get_current_user),
):
    try:
        res = update_demo(demo_id, status=payload.status, outcome=payload.outcome, notes=payload.notes, actor=current_user)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not res:
        raise HTTPException(status_code=404, detail="Demo not found.")
    return res


@router.get("/api/crm/templates")
async def api_list_templates(
    channel: str = "",
    language: str = "",
    current_user: str = Depends(get_current_user),
):
    return list_templates(channel=channel, language=language)


@router.post("/api/crm/templates", status_code=status.HTTP_201_CREATED)
async def api_create_template(payload: TemplateCreate, current_user: str = Depends(get_current_user)):
    return create_template(**payload.model_dump())


@router.post("/api/crm/templates/preview")
async def api_preview_template(
    payload: TemplatePreviewRequest,
    current_user: str = Depends(get_current_user),
):
    lead = get_lead_detail(payload.lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found.")
    contact = lead["contacts"][0] if lead.get("contacts") else None
    return {
        "subject": render_template_text(payload.subject, lead, contact),
        "body_text": render_template_text(payload.body_text, lead, contact),
    }


@router.post("/api/crm/leads/{lead_id}/send-email")
async def api_send_email(
    lead_id: int,
    payload: SendEmailRequest,
    current_user: str = Depends(get_current_user),
):
    try:
        return await send_email_message(
            lead_id=lead_id,
            to_email=payload.to_email,
            subject=payload.subject,
            body_text=payload.body_text,
            body_html=payload.body_html,
            contact_id=payload.contact_id,
            is_test=payload.is_test,
            actor=current_user,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.post("/api/crm/leads/{lead_id}/send-whatsapp")
async def api_send_whatsapp(
    lead_id: int,
    payload: SendWhatsAppRequest,
    current_user: str = Depends(get_current_user),
):
    try:
        return await send_whatsapp_message(
            lead_id=lead_id,
            phone=payload.phone,
            body_text=payload.body_text,
            contact_id=payload.contact_id,
            is_test=payload.is_test,
            actor=current_user,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except WhatsAppConfigurationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/api/crm/whatsapp/status")
async def api_whatsapp_status(current_user: str = Depends(get_current_user)):
    ui_status = get_safe_whatsapp_ui_status()
    try:
        provider = get_whatsapp_provider()
        live = await provider.get_status()
    except WhatsAppConfigurationError as exc:
        live = {"configured": False, "connected": False, "state": "unconfigured", "error": str(exc)}
    return {**ui_status, "live": live}


@router.get("/api/crm/whatsapp/qr")
async def api_whatsapp_qr(current_user: str = Depends(get_current_user)):
    try:
        provider = get_whatsapp_provider()
        return await provider.get_qr_code()
    except WhatsAppConfigurationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/api/crm/whatsapp/disconnect")
async def api_whatsapp_disconnect(current_user: str = Depends(get_current_user)):
    try:
        provider = get_whatsapp_provider()
        return await provider.disconnect()
    except WhatsAppConfigurationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/api/webhooks/resend")
async def api_resend_webhook(request: Request):
    raw_body = await request.body()
    payload = await request.json()
    sig = request.headers.get("svix-signature") or request.headers.get("x-resend-signature") or ""
    try:
        return process_resend_webhook(payload, signature_header=sig, raw_body=raw_body)
    except PermissionError as exc:
        raise HTTPException(status_code=401, detail=str(exc))


@router.post("/api/webhooks/evolution")
async def api_evolution_webhook(request: Request):
    payload = await request.json()
    secret = request.headers.get("x-webhook-secret") or request.headers.get("apikey") or ""
    try:
        return process_evolution_webhook(payload, secret_header=secret)
    except PermissionError as exc:
        raise HTTPException(status_code=401, detail=str(exc))


@router.get("/api/crm/campaigns")
async def api_list_campaigns(current_user: str = Depends(get_current_user)):
    return list_campaigns()


@router.post("/api/crm/campaigns", status_code=status.HTTP_201_CREATED)
async def api_create_campaign(payload: CampaignCreate, current_user: str = Depends(get_current_user)):
    return create_campaign(**payload.model_dump())


@router.get("/api/crm/campaigns/{campaign_id}/preview")
async def api_preview_campaign(campaign_id: int, current_user: str = Depends(get_current_user)):
    try:
        return preview_campaign_targets(campaign_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/api/crm/campaigns/{campaign_id}/execute")
async def api_execute_campaign(campaign_id: int, current_user: str = Depends(get_current_user)):
    try:
        return await execute_campaign(campaign_id, actor=current_user)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/api/crm/inbox")
async def api_list_inbox(status_filter: str = "", current_user: str = Depends(get_current_user)):
    return list_conversations(status_filter=status_filter)


@router.patch("/api/crm/inbox/{message_id}")
async def api_update_inbox_status(
    message_id: int,
    payload: ConversationStatusUpdate,
    current_user: str = Depends(get_current_user),
):
    try:
        res = update_conversation_status(message_id, payload.conversation_status, payload.assigned_user)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not res:
        raise HTTPException(status_code=404, detail="Message not found.")
    return res


@router.get("/api/crm/suppression")
async def api_list_suppression(current_user: str = Depends(get_current_user)):
    return list_suppressions()


@router.post("/api/crm/suppression", status_code=status.HTTP_201_CREATED)
async def api_add_suppression(payload: SuppressionCreate, current_user: str = Depends(get_current_user)):
    sup_id = add_suppression(
        channel=payload.channel,
        identifier=payload.identifier,
        reason=payload.reason,
        source="manual",
        lead_id=payload.lead_id,
        actor=current_user,
    )
    return {"id": sup_id, "status": "suppressed"}


@router.delete("/api/crm/suppression/{sup_id}")
async def api_remove_suppression(sup_id: int, current_user: str = Depends(get_current_user)):
    ok = remove_suppression(sup_id, actor=current_user)
    if not ok:
        raise HTTPException(status_code=404, detail="Suppression entry not found.")
    return {"status": "removed"}


@router.get("/api/crm/unsubscribe")
async def api_public_unsubscribe(email: str = "", channel: str = "email"):
    """Public one-click unsubscribe endpoint."""
    if not email:
        raise HTTPException(status_code=400, detail="Email is required.")
    add_suppression(channel=channel, identifier=email, reason="Unsubscribed via link", source="unsubscribe_link")
    return {"status": "unsubscribed", "identifier": email}


@router.get("/api/crm/analytics")
async def api_analytics(current_user: str = Depends(get_current_user)):
    return get_analytics_summary()


@router.get("/api/crm/settings")
async def api_get_settings(current_user: str = Depends(get_current_user)):
    return {
        "crm": get_all_settings(),
        "resend": get_resend_status(),
        "whatsapp": get_safe_whatsapp_ui_status(),
    }


@router.put("/api/crm/settings/{key}")
async def api_update_setting(
    key: str,
    payload: dict[str, Any],
    current_user: str = Depends(get_current_user),
):
    return update_setting(key, payload)
