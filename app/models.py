"""Pydantic data models and schemas for the web API."""

from enum import Enum
from typing import Any
from pydantic import BaseModel, Field, field_validator


class DepthEnum(str, Enum):
    CITY = "city"
    AREAS = "areas"
    GRID = "grid"


class LeadProfileEnum(str, Enum):
    NO_WEBSITE = "no_website"
    NO_EMAIL = "no_email"
    ALL = "all"


class JobCreate(BaseModel):
    city: str = Field(..., min_length=1, max_length=200, description="City or geographic area")
    categories: list[str] = Field(..., min_length=1, description="Business categories to scrape")
    depth: DepthEnum = Field(default=DepthEnum.CITY, description="Search depth: city, areas, or grid")
    profile: LeadProfileEnum = Field(
        default=LeadProfileEnum.NO_WEBSITE,
        description="Lead qualification profile: no_website, no_email, or all",
    )
    max_results: int | None = Field(
        default=None, ge=1, le=2000, description="Cap results per search unit"
    )
    grid_rows: int = Field(default=3, ge=1, le=10, description="Grid rows (for depth=grid)")
    grid_cols: int = Field(default=3, ge=1, le=10, description="Grid columns (for depth=grid)")
    grid_span_km: float = Field(
        default=18.0, ge=1.0, le=200.0, description="Grid span in km (for depth=grid)"
    )
    require_phone: bool = Field(default=True, description="Require valid phone number to qualify lead")
    crawl_websites: bool = Field(
        default=True, description="Crawl websites for emails and social links when allowed by profile"
    )
    concurrency: int = Field(
        default=1, ge=1, le=4, description="Browser concurrency per run"
    )

    @field_validator("city")
    @classmethod
    def clean_city(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("City name cannot be empty")
        return s

    @field_validator("categories")
    @classmethod
    def clean_categories(cls, v: list[str]) -> list[str]:
        cleaned = [c.strip() for c in v if c and c.strip()]
        if not cleaned:
            raise ValueError("At least one valid category is required")
        return cleaned


class JobStats(BaseModel):
    total_units: int = 0
    units_done: int = 0
    units_failed: int = 0
    units_skipped: int = 0
    found: int = 0
    unique: int = 0
    duplicates: int = 0
    qualified: int = 0
    blocks: int = 0
    elapsed_seconds: float = 0.0
    eta_seconds: float | None = None


class JobFiles(BaseModel):
    raw_csv: bool = False
    qualified_csv: bool = False
    xlsx: bool = False
    log: bool = False


class JobStatus(BaseModel):
    job_id: str
    status: str  # queued, running, completed, failed, cancelled
    params: dict[str, Any]
    stats: JobStats
    created_at: str
    started_at: str | None = None
    completed_at: str | None = None
    error: str | None = None
    current_phase: str | None = None
    files: JobFiles


class JobDetail(JobStatus):
    recent_logs: list[str] = Field(default_factory=list)


class LoginRequest(BaseModel):
    username: str
    password: str
