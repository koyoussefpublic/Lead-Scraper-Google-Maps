"""Authentication, password verification, and session token handling."""

import hashlib
import hmac
import secrets
import time
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app import config

security_basic = HTTPBasic(auto_error=False)


def hash_password(password: str) -> str:
    """Hash a password using PBKDF2-HMAC-SHA256 with a random salt."""
    salt = secrets.token_hex(16)
    iterations = 100_000
    derived = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), iterations
    )
    return f"pbkdf2_sha256${iterations}${salt}${derived.hex()}"


def verify_password(plain_password: str, hashed_or_plain: str) -> bool:
    """Verify a password against a PBKDF2 hash or fallback to constant-time comparison."""
    if not hashed_or_plain:
        return False
    if hashed_or_plain.startswith("pbkdf2_sha256$"):
        try:
            _, iter_str, salt, target_hex = hashed_or_plain.split("$")
            iterations = int(iter_str)
            computed = hashlib.pbkdf2_hmac(
                "sha256", plain_password.encode("utf-8"), salt.encode("utf-8"), iterations
            )
            return hmac.compare_digest(computed.hex(), target_hex)
        except Exception:
            return False
    return hmac.compare_digest(plain_password, hashed_or_plain)


def check_credentials(username: str, password: str) -> bool:
    """Check username and password against configured app credentials."""
    if not hmac.compare_digest(username.strip(), config.APP_USERNAME.strip()):
        return False
    expected = config.APP_PASSWORD_HASH or config.APP_PASSWORD
    return verify_password(password, expected)


def create_session_token(username: str) -> str:
    """Create a tamper-proof signed session token: {username}:{timestamp}:{signature}."""
    timestamp = str(int(time.time()))
    payload = f"{username}:{timestamp}"
    signature = hmac.new(
        config.SECRET_KEY.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return f"{payload}:{signature}"


def verify_session_token(token: str) -> str | None:
    """Verify a signed session token. Returns username if valid and not expired."""
    if not token or ":" not in token:
        return None
    parts = token.split(":")
    if len(parts) != 3:
        return None
    username, timestamp_str, sig = parts
    payload = f"{username}:{timestamp_str}"
    expected_sig = hmac.new(
        config.SECRET_KEY.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(sig, expected_sig):
        return None

    try:
        timestamp = int(timestamp_str)
        if time.time() - timestamp > config.SESSION_MAX_AGE:
            return None
    except ValueError:
        return None

    if not hmac.compare_digest(username.strip(), config.APP_USERNAME.strip()):
        return None

    return username


def get_current_user_optional(
    request: Request,
    credentials: Annotated[HTTPBasicCredentials | None, Depends(security_basic)] = None,
) -> str | None:
    """Extract authenticated username from session cookie or HTTP Basic auth."""
    # Check session cookie
    token = request.cookies.get(config.SESSION_COOKIE_NAME)
    if token:
        user = verify_session_token(token)
        if user:
            return user

    # Check Authorization: Bearer <session_token>
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        bearer_token = auth_header[7:].strip()
        user = verify_session_token(bearer_token)
        if user:
            return user

    # Check HTTP Basic Auth
    if credentials:
        if check_credentials(credentials.username, credentials.password):
            return credentials.username

    return None


def get_current_user(
    request: Request,
    credentials: Annotated[HTTPBasicCredentials | None, Depends(security_basic)] = None,
) -> str:
    """FastAPI dependency for protected API endpoints. Raises 401 if unauthenticated."""
    user = get_current_user_optional(request, credentials)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Basic"},
        )
    return user


def require_ui_auth(request: Request) -> str | RedirectResponse:
    """Dependency for UI view routes. Redirects to /login if unauthenticated."""
    user = get_current_user_optional(request)
    if not user:
        return RedirectResponse(
            url=f"/login?next={request.url.path}", status_code=status.HTTP_303_SEE_OTHER
        )
    return user
