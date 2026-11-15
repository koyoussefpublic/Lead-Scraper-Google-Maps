"""Tests for web application startup, health endpoints, and authentication."""

import pytest
from fastapi.testclient import TestClient

from app import config
from app.auth import create_session_token, hash_password, verify_password
from app.main import app

client = TestClient(app)


def test_health_checks():
    """Verify healthcheck endpoints respond 200 for Railway and container monitors."""
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "healthy"
    assert data["app"] == "google-maps-lead-scraper"

    resp_api = client.get("/api/health")
    assert resp_api.status_code == 200
    assert resp_api.json()["status"] == "ok"


def test_public_metadata_endpoints():
    """Verify category and metro lists are accessible."""
    resp_cat = client.get("/api/categories")
    assert resp_cat.status_code == 200
    assert len(resp_cat.json()["categories"]) > 0

    resp_metro = client.get("/api/metros")
    assert resp_metro.status_code == 200
    assert len(resp_metro.json()["metros"]) > 0


def test_password_hashing():
    """Verify PBKDF2 password hashing and verification logic."""
    raw = "MySecurePassword123!"
    hashed = hash_password(raw)
    assert hashed.startswith("pbkdf2_sha256$")
    assert verify_password(raw, hashed) is True
    assert verify_password("WrongPassword", hashed) is False


def test_unauthenticated_access_is_blocked():
    """Protected web views redirect to login; protected APIs return 401."""
    # View route redirects to /login
    resp_view = client.get("/", follow_redirects=False)
    assert resp_view.status_code == 303
    assert "/login" in resp_view.headers["location"]

    # API route returns 401 Unauthorized
    resp_api = client.get("/api/jobs")
    assert resp_api.status_code == 401
    assert "Authentication required" in resp_api.text


def test_login_flow():
    """Test web form login and session cookie creation."""
    # Invalid password
    bad_login = client.post(
        "/login",
        data={"username": config.APP_USERNAME, "password": "wrong-password", "next": "/"},
        follow_redirects=False,
    )
    assert bad_login.status_code == 401
    assert "Invalid username or password" in bad_login.text

    # Valid password
    good_login = client.post(
        "/login",
        data={"username": config.APP_USERNAME, "password": config.APP_PASSWORD, "next": "/"},
        follow_redirects=False,
    )
    assert good_login.status_code == 303
    assert good_login.headers["location"] == "/"
    assert config.SESSION_COOKIE_NAME in good_login.cookies


def test_authenticated_view_and_api_access():
    """Test accessing protected endpoints with a valid session cookie."""
    token = create_session_token(config.APP_USERNAME)
    client.cookies.set(config.SESSION_COOKIE_NAME, token)

    # View route renders dashboard
    resp_view = client.get("/")
    assert resp_view.status_code == 200
    assert "Lead Scraper Dashboard" in resp_view.text

    # API route returns jobs list
    resp_api = client.get("/api/jobs")
    assert resp_api.status_code == 200
    assert isinstance(resp_api.json(), list)

    client.cookies.clear()


def test_api_auth_login_endpoint():
    """Test API login via JSON payload."""
    resp = client.post(
        "/api/auth/login",
        json={"username": config.APP_USERNAME, "password": config.APP_PASSWORD},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "token" in data
    assert data["token_type"] == "Bearer"

    # Use token in Authorization: Bearer <token>
    token = data["token"]
    auth_resp = client.get(
        "/api/jobs",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert auth_resp.status_code == 200


def test_http_basic_auth():
    """Verify HTTP Basic Auth works on protected endpoints."""
    resp = client.get(
        "/api/jobs",
        auth=(config.APP_USERNAME, config.APP_PASSWORD),
    )
    assert resp.status_code == 200


def test_logout():
    """Test that logging out removes the cookie."""
    token = create_session_token(config.APP_USERNAME)
    client.cookies.set(config.SESSION_COOKIE_NAME, token)

    resp = client.post("/logout", follow_redirects=False)
    assert resp.status_code == 303
    assert "/login" in resp.headers["location"]
    client.cookies.clear()
