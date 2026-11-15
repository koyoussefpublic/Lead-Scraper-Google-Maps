"""Authentication routes for UI and API clients."""

from fastapi import APIRouter, Form, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path

from app import config
from app.auth import check_credentials, create_session_token, get_current_user_optional
from app.models import LoginRequest

router = APIRouter(tags=["Auth"])

BASE_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, next: str = "/"):
    """Show login page or redirect to dashboard if already logged in."""
    if get_current_user_optional(request):
        return RedirectResponse(url=next or "/", status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"error": None, "next": next or "/"},
    )


@router.post("/login")
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form("/"),
):
    """Handle web login form submission."""
    if not check_credentials(username, password):
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={
                "error": "Invalid username or password.",
                "next": next or "/",
            },
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    token = create_session_token(username.strip())
    redirect_target = next if next and next.startswith("/") else "/"
    response = RedirectResponse(url=redirect_target, status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        key=config.SESSION_COOKIE_NAME,
        value=token,
        max_age=config.SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=False,  # Set to True automatically behind HTTPS / Railway if desired
    )
    return response


@router.post("/api/auth/login")
async def api_login(payload: LoginRequest, response: Response):
    """API endpoint to obtain a session token."""
    if not check_credentials(payload.username, payload.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password.",
        )
    token = create_session_token(payload.username.strip())
    response.set_cookie(
        key=config.SESSION_COOKIE_NAME,
        value=token,
        max_age=config.SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
    )
    return {"token": token, "token_type": "Bearer", "username": payload.username.strip()}


@router.post("/logout")
async def logout(request: Request):
    """Log out web user and clear cookie."""
    response = RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(config.SESSION_COOKIE_NAME)
    return response


@router.post("/api/auth/logout")
async def api_logout(response: Response):
    """API logout clearing session cookie."""
    response.delete_cookie(config.SESSION_COOKIE_NAME)
    return {"status": "logged_out"}
