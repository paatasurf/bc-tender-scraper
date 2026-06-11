from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Tender(Base):
    __tablename__ = "tenders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    organization: Mapped[str] = mapped_column(String(300), default="")
    category: Mapped[str] = mapped_column(String(100), default="")
    posted_date: Mapped[str] = mapped_column(String(50), default="")
    closing_date: Mapped[str] = mapped_column(String(50), default="")
    estimated_value: Mapped[str] = mapped_column(String(100), default="")
    location: Mapped[str] = mapped_column(String(200), default="")
    tender_id: Mapped[str] = mapped_column(String(100), default="")
    url: Mapped[str] = mapped_column(String(500), unique=True, nullable=False)
    source: Mapped[str] = mapped_column(String(100), default="")
    ai_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ai_summary: Mapped[str] = mapped_column(Text, default="")
    ai_budget_estimate: Mapped[str] = mapped_column(Text, default="")
    scraped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Permit(Base):
    __tablename__ = "permits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    address: Mapped[str] = mapped_column(String(300), nullable=False)
    permit_type: Mapped[str] = mapped_column(String(100), default="")
    project_value: Mapped[str] = mapped_column(String(50), default="")
    applicant: Mapped[str] = mapped_column(String(300), default="")
    architect: Mapped[str] = mapped_column(String(300), default="")
    issue_date: Mapped[str] = mapped_column(String(20), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    scraped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RedditSignal(Base):
    __tablename__ = "reddit"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    text: Mapped[str] = mapped_column(Text, default="")
    upvotes: Mapped[int] = mapped_column(Integer, default=0)
    date: Mapped[str] = mapped_column(String(20), default="")
    url: Mapped[str] = mapped_column(String(500), unique=True, nullable=False)
    subreddit: Mapped[str] = mapped_column(String(100), default="")
    scraped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class NewsSignal(Base):
    __tablename__ = "news"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    text: Mapped[str] = mapped_column(Text, default="")
    publisher: Mapped[str] = mapped_column(String(200), default="")
    date: Mapped[str] = mapped_column(String(20), default="")
    url: Mapped[str] = mapped_column(String(500), unique=True, nullable=False)
    scraped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LinkedInSignal(Base):
    __tablename__ = "linkedin_signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    content: Mapped[str] = mapped_column(Text, default="")
    author: Mapped[str] = mapped_column(String(300), default="")
    date: Mapped[str] = mapped_column(String(20), default="")
    url: Mapped[str] = mapped_column(String(500), unique=True, nullable=False)
    likes_count: Mapped[int] = mapped_column(Integer, default=0)
    scraped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_title: Mapped[str] = mapped_column(String(300), nullable=False)
    company: Mapped[str] = mapped_column(String(300), default="")
    location: Mapped[str] = mapped_column(String(200), default="")
    salary: Mapped[str] = mapped_column(String(100), default="")
    date: Mapped[str] = mapped_column(String(50), default="")
    url: Mapped[str] = mapped_column(String(500), unique=True, nullable=False)
    scraped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CommercialTender(Base):
    __tablename__ = "commercial_tenders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    company: Mapped[str] = mapped_column(Text, default="")
    value: Mapped[str] = mapped_column(Text, default="")
    deadline: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(Text, default="")
    url: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    tender_id: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(Text, default="")
    ai_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ai_summary: Mapped[str] = mapped_column(Text, default="")
    ai_budget_estimate: Mapped[str] = mapped_column(Text, default="")
    scraped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Company(Base):
    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(300), unique=True, nullable=False)
    total_projects: Mapped[int] = mapped_column(Integer, default=0)
    total_value: Mapped[float] = mapped_column(Float, default=0.0)
    avg_project_value: Mapped[float] = mapped_column(Float, default=0.0)
    project_types: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    neighborhoods: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    first_project_date: Mapped[str] = mapped_column(String(20), default="")
    last_project_date: Mapped[str] = mapped_column(String(20), default="")
    google_rating: Mapped[float | None] = mapped_column(Float, nullable=True)
    google_reviews_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    google_address: Mapped[str] = mapped_column(String(500), default="")
    google_phone: Mapped[str] = mapped_column(String(50), default="")
    ai_reliability_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ai_summary: Mapped[str] = mapped_column(Text, default="")
    company_type: Mapped[str] = mapped_column(String(50), default="", index=True)
    confidence_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    company_lifecycle: Mapped[str] = mapped_column(String(20), default="", index=True)
    company_tier: Mapped[str] = mapped_column(String(20), default="")
    enrichment_status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    last_enriched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ArchCompany(Base):
    __tablename__ = "arch_companies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(300), unique=True, nullable=False)
    total_projects: Mapped[int] = mapped_column(Integer, default=0)
    total_value: Mapped[float] = mapped_column(Float, default=0.0)
    avg_project_value: Mapped[float] = mapped_column(Float, default=0.0)
    project_types: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    neighborhoods: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    first_project_date: Mapped[str] = mapped_column(String(20), default="")
    last_project_date: Mapped[str] = mapped_column(String(20), default="")
    google_rating: Mapped[float | None] = mapped_column(Float, nullable=True)
    google_reviews_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    google_address: Mapped[str] = mapped_column(String(500), default="")
    google_phone: Mapped[str] = mapped_column(String(50), default="")
    google_place_id: Mapped[str | None] = mapped_column(String(200), unique=True, nullable=True)
    website: Mapped[str] = mapped_column(String(500), default="")
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    houzz_projects_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    houzz_project_types: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    houzz_service_areas: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    houzz_reviews_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    houzz_rating: Mapped[float | None] = mapped_column(Float, nullable=True)
    houzz_profile_url: Mapped[str] = mapped_column(String(500), default="")
    aibc_status: Mapped[str] = mapped_column(String(50), default="")
    website_projects_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    website_specializations: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    website_service_areas: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    website_notable_projects: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    ai_reliability_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ai_summary: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class TenderMatch(Base):
    __tablename__ = "tender_matches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    company_kind: Mapped[str] = mapped_column(String(20), nullable=False, default="architecture", index=True)
    company_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    tender_source: Mapped[str] = mapped_column(String(20), nullable=False, default="arch", index=True)
    tender_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reasoning: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PipelineRun(Base):
    __tablename__ = "pipeline_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    step: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="running")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str] = mapped_column(Text, default="")
    counts_json: Mapped[str] = mapped_column(Text, default="{}")


class ArchTender(Base):
    __tablename__ = "arch_tenders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    company: Mapped[str] = mapped_column(String(300), default="")
    value: Mapped[str] = mapped_column(String(100), default="")
    deadline: Mapped[str] = mapped_column(String(50), default="")
    status: Mapped[str] = mapped_column(String(50), default="")
    category: Mapped[str] = mapped_column(String(200), default="")
    url: Mapped[str] = mapped_column(String(500), unique=True, nullable=False)
    tender_id: Mapped[str] = mapped_column(String(100), default="")
    ai_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ai_summary: Mapped[str] = mapped_column(Text, default="")
    ai_budget_estimate: Mapped[str] = mapped_column(String(100), default="")
    scraped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
