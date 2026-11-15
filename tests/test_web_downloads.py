"""Tests for secure file downloads and path traversal prevention."""

import pytest
from fastapi.testclient import TestClient

from app import config
from app.auth import create_session_token
from app.jobs import job_manager
from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def auth_client():
    token = create_session_token(config.APP_USERNAME)
    client.cookies.set(config.SESSION_COOKIE_NAME, token)
    yield
    client.cookies.clear()


def test_download_legitimate_files():
    """Verify download endpoints for raw CSV, qualified CSV, Excel, and log."""
    job = job_manager.create_job({"city": "Tampa FL", "categories": ["plumbers"]})
    job_id = job.job_id
    job_dir = job_manager.get_job_dir(job_id)

    # Create dummy output files
    raw_csv = job_dir / "leads_raw.csv"
    raw_csv.write_text("Name,Category\nTest Plumbing,Plumber\n", encoding="utf-8")

    qual_csv = job_dir / "leads_qualified.csv"
    qual_csv.write_text("Name,Category\nTest Plumbing,Plumber\n", encoding="utf-8")

    log_file = job_dir / "scraper.log"
    log_file.write_text("Log started\n", encoding="utf-8")

    xlsx_file = job_dir / "leads.xlsx"
    xlsx_file.write_bytes(b"PK\x03\x04mock_excel_bytes")

    # Download raw CSV
    resp_raw = client.get(f"/api/jobs/{job_id}/download/leads_raw.csv")
    assert resp_raw.status_code == 200
    assert "text/csv" in resp_raw.headers["content-type"]
    assert "Test Plumbing" in resp_raw.text
    assert f"{job_id}_leads_raw.csv" in resp_raw.headers["content-disposition"]

    # Download qualified CSV
    resp_qual = client.get(f"/api/jobs/{job_id}/download/leads_qualified.csv")
    assert resp_qual.status_code == 200
    assert "text/csv" in resp_qual.headers["content-type"]

    # Download Excel file
    resp_xlsx = client.get(f"/api/jobs/{job_id}/download/leads.xlsx")
    assert resp_xlsx.status_code == 200
    assert resp_xlsx.content == b"PK\x03\x04mock_excel_bytes"

    # Download Log file
    resp_log = client.get(f"/api/jobs/{job_id}/download/scraper.log")
    assert resp_log.status_code == 200
    assert "Log started" in resp_log.text

    job_manager.delete_job(job_id)


def test_download_missing_file_returns_404():
    """Requesting a file that has not been generated yet returns 404."""
    job = job_manager.create_job({"city": "Orlando FL", "categories": ["plumbers"]})
    job_id = job.job_id

    resp = client.get(f"/api/jobs/{job_id}/download/leads_qualified.csv")
    assert resp.status_code == 404
    assert "has not been generated" in resp.text

    job_manager.delete_job(job_id)


def test_path_traversal_attempts_are_blocked():
    """Attempting path traversal attacks must be rejected."""
    job = job_manager.create_job({"city": "Boston MA", "categories": ["plumbers"]})
    job_id = job.job_id

    # 1. Directory traversal in filename
    resp1 = client.get(f"/api/jobs/{job_id}/download/../../etc/passwd")
    assert resp1.status_code in (400, 404)

    # 2. Requesting arbitrary or sensitive server file (e.g. meta.json, .env, main.py)
    resp2 = client.get(f"/api/jobs/{job_id}/download/meta.json")
    assert resp2.status_code == 400
    assert "Invalid file request" in resp2.text

    resp3 = client.get(f"/api/jobs/{job_id}/download/.env")
    assert resp3.status_code == 400

    resp4 = client.get(f"/api/jobs/{job_id}/download/web.py")
    assert resp4.status_code == 400

    job_manager.delete_job(job_id)


def test_unauthorized_download_blocked():
    """Downloads without authentication must be rejected."""
    client.cookies.clear()
    resp = client.get("/api/jobs/job_test_123/download/leads_raw.csv")
    assert resp.status_code == 401
