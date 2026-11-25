"""Dashboard UI HTML view routes."""

from pathlib import Path
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.auth import get_current_user_optional
from app.jobs import job_manager
from config.categories import CATEGORIES
from config.metros import METROS

router = APIRouter(tags=["Views"])

BASE_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


@router.get("/", response_class=HTMLResponse)
async def dashboard_view(request: Request):
    """Render main web dashboard with scraping form, live monitor, and jobs list."""
    user = get_current_user_optional(request)
    if not user:
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/login", status_code=303)

    jobs = job_manager.list_jobs()
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "user": user,
            "jobs": jobs,
            "categories": CATEGORIES,
            "metros": METROS,
        },
    )


@router.get("/crm", response_class=HTMLResponse)
async def crm_view(request: Request):
    """Render the CarteFidele Prospecting CRM & Outreach workspace."""
    user = get_current_user_optional(request)
    if not user:
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/login", status_code=303)

    return templates.TemplateResponse(
        request=request,
        name="crm.html",
        context={"user": user},
    )
