from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from db.tender_lifecycle_columns import TenderLifecycleColumnsMixin


class Base(DeclarativeBase):
    pass


class Tender(TenderLifecycleColumnsMixin, Base):
    __tablename__ = "tenders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    organization: Mapped[str] = mapped_column(String(300), default="")
    category: Mapped[str] = mapped_column(String(100), default="")
    posted_date: Mapped[str] = mapped_column(String(50), default="")
    closing_date: Mapped[str] = mapped_column(String(50), default="")
    estimated_value: Mapped[str] = mapped_column(String(100), default="")
    estimated_value_numeric: Mapped[float | None] = mapped_column(Float, nullable=True)
    buyer_level: Mapped[str] = mapped_column(String(20), default="")
    location: Mapped[str] = mapped_column(String(200), default="")
    tender_id: Mapped[str] = mapped_column(String(100), default="")
    url: Mapped[str] = mapped_column(String(500), unique=True, nullable=False)
    source: Mapped[str] = mapped_column(String(100), default="")
    ai_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ai_summary: Mapped[str] = mapped_column(Text, default="")
    ai_budget_estimate: Mapped[str] = mapped_column(Text, default="")
    scraped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    first_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Permit(Base):
    __tablename__ = "permits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    address: Mapped[str] = mapped_column(String(300), nullable=False)
    permit_type: Mapped[str] = mapped_column(String(100), default="")
    project_value: Mapped[str] = mapped_column(String(50), default="")
    applicant: Mapped[str] = mapped_column(String(300), default="")
    architect: Mapped[str] = mapped_column(String(300), default="")
    issue_date: Mapped[str] = mapped_column(String(20), default="")
    application_date: Mapped[str] = mapped_column(String(20), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    contractor: Mapped[str] = mapped_column(String(300), default="")
    local_area: Mapped[str] = mapped_column(String(100), default="")
    source: Mapped[str] = mapped_column(String(50), default="vancouver", index=True)
    city: Mapped[str] = mapped_column(String(100), default="Vancouver", index=True)
    external_id: Mapped[str] = mapped_column(String(100), default="", index=True)
    scraped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    lifecycle_status: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'active'"))
    lifecycle_status_override: Mapped[str | None] = mapped_column(String(20), nullable=True)
    status_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    source_status_raw: Mapped[str] = mapped_column(String(100), default="")
    company_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("companies.id"), nullable=True, index=True)
    canonical_merge_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    canonical_merge_method: Mapped[str] = mapped_column(String(50), default="")


class EarlySignalEvent(Base):
    """Pre-tender market signals: rezoning and development permit applications."""

    __tablename__ = "early_signal_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    external_id: Mapped[str] = mapped_column(String(100), default="", index=True)
    source: Mapped[str] = mapped_column(String(100), default="", index=True)
    transaction_date: Mapped[str] = mapped_column(String(20), default="")
    municipality: Mapped[str] = mapped_column(String(100), default="")
    region: Mapped[str] = mapped_column(String(100), default="")
    property_type: Mapped[str] = mapped_column(String(300), default="")
    signal_type: Mapped[str] = mapped_column(String(50), default="", index=True)
    url_link: Mapped[str] = mapped_column(String(500), default="")
    address: Mapped[str] = mapped_column(String(300), default="")
    applicant: Mapped[str] = mapped_column(String(300), default="")
    project_value: Mapped[str] = mapped_column(String(50), default="")
    scraped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ProjectContact(Base):
    """Project participant contact for Project Intelligence."""

    __tablename__ = "project_contacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    project_type: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    company_name: Mapped[str] = mapped_column(String(300), default="")
    contact_name: Mapped[str] = mapped_column(String(300), default="")
    phone: Mapped[str] = mapped_column(String(50), default="")
    email: Mapped[str] = mapped_column(String(320), default="")
    source: Mapped[str] = mapped_column(String(100), default="")


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


class CommercialTender(TenderLifecycleColumnsMixin, Base):
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
    buyer_level: Mapped[str] = mapped_column(String(20), default="")
    estimated_value_numeric: Mapped[float | None] = mapped_column(Float, nullable=True)
    ai_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ai_summary: Mapped[str] = mapped_column(Text, default="")
    ai_budget_estimate: Mapped[str] = mapped_column(Text, default="")
    scraped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    first_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


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
    company_tier: Mapped[str] = mapped_column(String(20), default="", index=True)
    construction_score: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"), index=True)
    construction_tier_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    construction_tier_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    enrichment_status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    last_enriched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    award_count: Mapped[int] = mapped_column(Integer, default=0, index=True)
    total_award_value: Mapped[float] = mapped_column(Float, default=0.0, index=True)
    avg_award_value: Mapped[float] = mapped_column(Float, default=0.0)
    award_categories: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    award_clients: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    buyer_levels: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    award_sources: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    first_award_date: Mapped[str] = mapped_column(String(20), default="")
    last_award_date: Mapped[str] = mapped_column(String(20), default="", index=True)
    primary_address: Mapped[str] = mapped_column(String(500), default="")
    primary_city: Mapped[str] = mapped_column(String(100), default="")
    primary_province: Mapped[str] = mapped_column(String(50), default="")
    data_sources: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    canonical_vendor_name: Mapped[str] = mapped_column(String(300), default="")
    primary_trade: Mapped[str] = mapped_column(String(50), default="", index=True)
    trade_tags: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    capability_profile_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    capability_profile_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cip_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    cip_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cip_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    dominant_sector: Mapped[str] = mapped_column(String(30), default="", index=True)
    work_orientation: Mapped[str] = mapped_column(String(20), default="")
    specialization_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    geographic_reach: Mapped[str] = mapped_column(String(20), default="")
    value_p25: Mapped[float | None] = mapped_column(Float, nullable=True)
    value_p75: Mapped[float | None] = mapped_column(Float, nullable=True)
    lifecycle_status: Mapped[str] = mapped_column(String(30), nullable=False, server_default=text("'active'"))
    lifecycle_status_override: Mapped[str | None] = mapped_column(String(30), nullable=True)
    last_activity_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_operating: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    google_place_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    google_business_category: Mapped[str] = mapped_column(String(200), default="")
    google_maps_url: Mapped[str] = mapped_column(String(500), default="")
    google_business_status: Mapped[str] = mapped_column(String(50), default="")
    google_website: Mapped[str] = mapped_column(String(500), default="")
    google_last_updated: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    google_last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    google_match_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    google_enrichment_status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    google_query_used: Mapped[str] = mapped_column(String(500), default="")
    website: Mapped[str] = mapped_column(String(500), default="")
    google_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    google_lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    display_name: Mapped[str] = mapped_column(String(300), default="")
    entity_role: Mapped[str] = mapped_column(String(30), nullable=False, server_default=text("'standalone'"))
    canonical_company_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("companies.id"), nullable=True, index=True
    )
    applicant_signatory: Mapped[str] = mapped_column(String(300), default="")
    canonical_merge_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    canonical_merge_method: Mapped[str] = mapped_column(String(50), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class CompanyApplicantAlias(Base):
    __tablename__ = "company_applicant_aliases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    canonical_company_id: Mapped[int] = mapped_column(Integer, ForeignKey("companies.id"), nullable=False, index=True)
    alias_company_id: Mapped[int] = mapped_column(Integer, ForeignKey("companies.id"), nullable=False, unique=True)
    applicant_name_raw: Mapped[str] = mapped_column(String(300), nullable=False)
    signatory_name: Mapped[str] = mapped_column(String(300), default="")
    merge_run_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("company_canonical_merge_runs.id"), nullable=True
    )
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    merge_method: Mapped[str] = mapped_column(String(50), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CompanyCanonicalMergeRun(Base):
    __tablename__ = "company_canonical_merge_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'planned'"))
    dry_run: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    report_json: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    summary_json: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))


class CompanyCanonicalMergeRollback(Base):
    __tablename__ = "company_canonical_merge_rollback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("company_canonical_merge_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    entity_type: Mapped[str] = mapped_column(String(30), nullable=False)
    entity_id: Mapped[int] = mapped_column(Integer, nullable=False)
    before_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CompanyScoreHistory(Base):
    """Historical construction score snapshots for trend analysis."""

    __tablename__ = "company_score_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(Integer, ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    construction_score: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    company_tier: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    calculated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    algorithm_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))


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
    primary_trade: Mapped[str] = mapped_column(String(50), default="", index=True)
    trade_tags: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    capability_profile_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    capability_profile_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cip_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    cip_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cip_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    dominant_sector: Mapped[str] = mapped_column(String(30), default="", index=True)
    work_orientation: Mapped[str] = mapped_column(String(20), default="")
    specialization_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    geographic_reach: Mapped[str] = mapped_column(String(20), default="")
    value_p25: Mapped[float | None] = mapped_column(Float, nullable=True)
    value_p75: Mapped[float | None] = mapped_column(Float, nullable=True)
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
    breakdown_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
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


class ContractAward(Base):
    __tablename__ = "contract_awards"
    __table_args__ = (UniqueConstraint("source", "external_id", name="uq_contract_awards_source_external_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    external_id: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    url: Mapped[str] = mapped_column(String(500), default="")
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    procurement_category: Mapped[str] = mapped_column(String(40), default="", index=True)
    procurement_method: Mapped[str] = mapped_column(String(80), default="")
    winner_company: Mapped[str] = mapped_column(String(300), nullable=False, index=True)
    winner_address: Mapped[str] = mapped_column(String(500), default="")
    winner_city: Mapped[str] = mapped_column(String(100), default="")
    winner_province: Mapped[str] = mapped_column(String(50), default="")
    company_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("companies.id"), nullable=True, index=True)
    match_method: Mapped[str] = mapped_column(String(30), default="")
    match_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    buyer_organization: Mapped[str] = mapped_column(String(300), default="", index=True)
    buyer_level: Mapped[str] = mapped_column(String(20), default="")
    award_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    currency: Mapped[str] = mapped_column(String(10), default="CAD")
    award_date: Mapped[str] = mapped_column(String(20), default="", index=True)
    contract_start_date: Mapped[str] = mapped_column(String(20), default="")
    contract_end_date: Mapped[str] = mapped_column(String(20), default="")
    delivery_region: Mapped[str] = mapped_column(String(200), default="")
    scraped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class CompanyWiki(Base):
    __tablename__ = "company_wiki"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    company_kind: Mapped[str] = mapped_column(String(20), nullable=False, default="construction", index=True)
    company_name: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    wiki_markdown: Mapped[str] = mapped_column(Text, nullable=False, default="")
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    specializations: Mapped[str] = mapped_column(Text, nullable=False, default="")
    market_position: Mapped[str] = mapped_column(Text, nullable=False, default="")
    geographic_focus: Mapped[str] = mapped_column(Text, nullable=False, default="")
    competitive_profile: Mapped[str] = mapped_column(Text, nullable=False, default="")
    data_snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    model_used: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )



class ClientProfile(Base):
    """Email alert preferences for TenderScope clients (Clerk-authenticated users)."""

    __tablename__ = "client_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    clerk_user_id: Mapped[str] = mapped_column(String(100), nullable=False, default="", index=True)
    company_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    company_name: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    regions: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    specializations: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    min_project_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_project_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    alerts_enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TenderOutcome(Base):
    """Recorded bid outcome for win/loss tracking (Phase X.1.5)."""

    __tablename__ = "tender_outcomes"
    __table_args__ = (
        UniqueConstraint("company_id", "tender_id", name="uq_tender_outcomes_company_tender"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    tender_id: Mapped[str] = mapped_column(String(255), nullable=False)
    tender_title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    outcome: Mapped[str] = mapped_column(String(20), nullable=False)
    bid_amount: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    award_amount: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class GoogleEnrichmentLog(Base):
    __tablename__ = "google_enrichment_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    run_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    attempted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    query_used: Mapped[str] = mapped_column(String(500), default="")
    provider: Mapped[str] = mapped_column(String(30), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    match_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    google_place_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    candidate_count: Mapped[int] = mapped_column(Integer, default=0)
    candidate_snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error_message: Mapped[str] = mapped_column(Text, default="")
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    external_run_id: Mapped[str] = mapped_column(String(100), default="")


class GoogleEnrichmentReview(Base):
    __tablename__ = "google_enrichment_reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    run_id: Mapped[str] = mapped_column(String(36), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    query_used: Mapped[str] = mapped_column(String(500), default="")
    match_confidence: Mapped[float] = mapped_column(Float, nullable=False)
    candidate_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reviewed_by: Mapped[str] = mapped_column(String(100), default="")
    review_notes: Mapped[str] = mapped_column(Text, default="")
    chosen_place_id: Mapped[str | None] = mapped_column(String(200), nullable=True)


class ArchTender(TenderLifecycleColumnsMixin, Base):
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
    first_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
