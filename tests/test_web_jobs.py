"""Tests for job submission, validation, status tracking, and lifecycle management."""

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


def test_job_input_validation():
    """Verify input validation rejects bad or incomplete payloads."""
    # Missing categories
    resp = client.post("/api/jobs", json={"city": "Austin TX", "categories": []})
    assert resp.status_code == 422

    # Blank city
    resp = client.post("/api/jobs", json={"city": "   ", "categories": ["plumbers"]})
    assert resp.status_code == 422

    # Invalid depth option
    resp = client.post(
        "/api/jobs",
        json={"city": "Austin TX", "categories": ["plumbers"], "depth": "invalid_depth"},
    )
    assert resp.status_code == 422


def test_job_creation_and_listing():
    """Verify valid job creation sets status to queued and persists metadata."""
    payload = {
        "city": "Denver CO",
        "categories": ["electricians", "roofing"],
        "depth": "city",
        "profile": "no_website",
        "require_phone": True,
        "max_results": 25,
    }
    resp = client.post("/api/jobs", json=payload)
    assert resp.status_code == 201
    job = resp.json()

    job_id = job["job_id"]
    assert job_id.startswith("job_")
    assert job["status"] in ("queued", "running")
    assert job["params"]["city"] == "Denver CO"
    assert job["params"]["categories"] == ["electricians", "roofing"]

    # Verify listing contains the job
    list_resp = client.get("/api/jobs")
    assert list_resp.status_code == 200
    job_ids = [j["job_id"] for j in list_resp.json()]
    assert job_id in job_ids

    # Verify job detail
    detail_resp = client.get(f"/api/jobs/{job_id}")
    assert detail_resp.status_code == 200
    detail = detail_resp.json()
    assert detail["job_id"] == job_id
    assert "recent_logs" in detail
    assert len(detail["recent_logs"]) > 0

    # Clean up
    job_manager.delete_job(job_id)


def test_job_logs_endpoint():
    """Verify log retrieval endpoint."""
    job = job_manager.create_job({"city": "Seattle WA", "categories": ["plumbers"]})
    job_id = job.job_id

    # Append test log to scraper.log
    job_dir = job_manager.get_job_dir(job_id)
    with open(job_dir / "scraper.log", "w", encoding="utf-8") as f:
        f.write("[12:00:00] [INFO] Unit 1 started\n[12:00:05] [INFO] Found 5 leads\n")

    resp = client.get(f"/api/jobs/{job_id}/logs")
    assert resp.status_code == 200
    assert "Unit 1 started" in resp.text
    assert "Found 5 leads" in resp.text

    job_manager.delete_job(job_id)


def test_job_cancellation():
    """Verify cancelling a job transitions status to cancelled."""
    job = job_manager.create_job({"city": "Phoenix AZ", "categories": ["dentists"]})
    job_id = job.job_id

    cancel_resp = client.post(f"/api/jobs/{job_id}/cancel")
    assert cancel_resp.status_code == 200
    assert cancel_resp.json()["status"] == "cancelled"

    detail = job_manager.get_job(job_id)
    assert detail.status == "cancelled"

    job_manager.delete_job(job_id)


def test_job_deletion():
    """Verify job deletion cleans up the record and directory."""
    job = job_manager.create_job({"city": "Dallas TX", "categories": ["hvac"]})
    job_id = job.job_id
    job_dir = job_manager.get_job_dir(job_id)
    assert job_dir.exists()

    del_resp = client.delete(f"/api/jobs/{job_id}")
    assert del_resp.status_code == 200
    assert del_resp.json()["status"] == "deleted"

    assert job_manager.get_job(job_id) is None
    assert not job_dir.exists()


def test_job_duplication():
    """Verify duplicating an existing job creates a new job with identical parameters."""
    payload = {
        "city": "Austin TX",
        "categories": ["electricians", "plumbers"],
        "depth": "areas",
        "profile": "no_email",
        "max_results": 50,
        "require_phone": False,
        "crawl_websites": True,
    }
    orig_resp = client.post("/api/jobs", json=payload)
    assert orig_resp.status_code == 201
    orig_job = orig_resp.json()
    orig_id = orig_job["job_id"]

    dup_resp = client.post(f"/api/jobs/{orig_id}/duplicate")
    assert dup_resp.status_code == 201
    dup_job = dup_resp.json()
    dup_id = dup_job["job_id"]

    try:
        assert dup_id != orig_id
        assert dup_id.startswith("job_")
        assert dup_job["status"] in ("queued", "running")
        assert dup_job["params"] == orig_job["params"]
    finally:
        job_manager.delete_job(orig_id)
        job_manager.delete_job(dup_id)


def test_duplicate_missing_job_returns_404():
    """Duplicating a non-existent job returns 404."""
    resp = client.post("/api/jobs/nonexistent_job_999/duplicate")
    assert resp.status_code == 404
