"""Enrich early_signal_events with Vancouver development application details.

Reliability architecture (fix for repeated production failures on this step):
a long-lived DB session must never be held open across the external HTTP
scraping phase, since an idle SSL connection gets dropped before a single
end-of-run commit could ever fire. Instead:

1. A short read session resolves candidate ids and immutable input fields,
   then is closed immediately.
2. External HTTP enrichment runs with no DB session/transaction open at all.
3. Results are written back in small chunks, each in its own short-lived
   session that commits (or rolls back) independently, so one bad record or
   one failed chunk write cannot erase already-committed progress.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import EarlySignalEvent, PipelineRun
from scraper.shapeyourcity_development import (
    build_shapeyourcity_url,
    extract_reference_number_from_url,
    find_best_project_match,
    find_project_by_reference,
    load_development_projects,
    parse_shapeyourcity_detail_page,
    parse_vancouver_detail_page,
    project_to_enrichment,
)
from scraper.utils import create_session, fetch_html

NOT_FOUND_TITLE = "can't be found"
DEFAULT_CHUNK_SIZE = 25

# Scheduled-enrichment page-size policy (see enrich_early_signal_events'
# docstring for the full rationale): a bare call with no `limit` is an
# INCREMENTAL page of this many candidates, not a full-table sweep. At
# roughly 1-2 HTTP fetches per matched candidate, ~100 candidates keeps a
# scheduled run's external I/O bounded to a few minutes instead of scaling
# unboundedly with table size. A caller that genuinely wants a full sweep
# in one pass must opt in explicitly with refresh_all=True.
DEFAULT_PAGE_LIMIT = 100


@dataclass(frozen=True)
class _Candidate:
    """Immutable snapshot of the input fields enrichment needs, read once
    from the short-lived read session before any external I/O begins."""

    id: int
    external_id: str
    url_link: str
    region: str
    property_type: str


def _resolve_project(
    projects: list[dict[str, Any]],
    *,
    url_link: str,
    region: str,
    property_type: str,
) -> dict[str, Any] | None:
    reference = extract_reference_number_from_url(url_link)
    if reference:
        matched = find_project_by_reference(projects, reference)
        if matched:
            return matched

    return find_best_project_match(projects, region=region, property_type=property_type)


def _fetch_detail_fields(
    http_session, project: dict[str, Any], url_link: str
) -> tuple[dict[str, str], bool]:
    """Return (detail fields, had_external_error). Never raises."""
    detail: dict[str, str] = {}
    had_error = False
    if url_link and "development-applications.aspx" in url_link.lower():
        try:
            html = fetch_html(http_session, url_link)
            title = html.lower()
            if NOT_FOUND_TITLE not in title:
                detail = parse_vancouver_detail_page(html)
        except Exception as exc:
            print(
                f"[Vancouver Enrichment] Detail page fetch failed for {url_link}: {exc}"
            )
            detail = {}
            had_error = True

    if detail.get("address") and detail.get("applicant"):
        return detail, had_error

    permalink = project.get("permalink")
    if not permalink:
        return detail, had_error

    try:
        page_url = build_shapeyourcity_url(permalink)
        html = fetch_html(http_session, page_url)
        page_detail = parse_shapeyourcity_detail_page(html)
    except Exception as exc:
        print(
            f"[Vancouver Enrichment] ShapeYourCity fetch failed for permalink={permalink}: {exc}"
        )
        return detail, True

    merged = dict(detail)
    for key, value in page_detail.items():
        if value and not merged.get(key):
            merged[key] = value
    return merged, had_error


def enrich_early_signal_event(
    candidate: _Candidate,
    projects: list[dict[str, Any]],
    *,
    http_session,
    fetch_details: bool = True,
) -> tuple[dict[str, str] | None, bool]:
    """Return (enrichment payload or None if no project match, had_external_error).

    Never raises: any external fetch failure is captured as had_external_error
    so one bad record cannot abort the rest of the batch.
    """
    project = _resolve_project(
        projects,
        url_link=candidate.url_link,
        region=candidate.region,
        property_type=candidate.property_type,
    )
    if project is None:
        return None, False

    detail: dict[str, str] = {}
    had_error = False
    if fetch_details:
        detail, had_error = _fetch_detail_fields(
            http_session, project, candidate.url_link
        )

    return project_to_enrichment(project, detail=detail), had_error


def _resolve_starting_cursor(
    read_session: Session, *, since_id: int | None, force: bool
) -> int:
    """Decide the keyset cursor a run should start after.

    Priority: an explicit `since_id` always wins (manual override). A
    `force` run always starts at 0 (a deliberate full sweep from the top,
    matching the pre-existing force=True contract). Otherwise, the cursor
    is read back from the most recent enrich-early-signals pipeline_runs
    row's own `next_cursor` -- each run persists where it left off so the
    next run (a separate process/request) can continue the same rotation
    without any new schema, coordinator, or caller changes. Never raises:
    a missing/malformed prior counts_json degrades to cursor 0.
    """
    if since_id is not None:
        return max(since_id, 0)
    if force:
        return 0

    last_run = read_session.scalars(
        select(PipelineRun)
        .where(PipelineRun.step == "enrich-early-signals")
        .order_by(PipelineRun.id.desc())
        .limit(1)
    ).first()
    if last_run is None:
        return 0

    try:
        counts = json.loads(last_run.counts_json or "{}")
    except (TypeError, ValueError):
        return 0
    cursor = counts.get("next_cursor") if isinstance(counts, dict) else None
    return cursor if isinstance(cursor, int) and cursor >= 0 else 0


def _fetch_candidates(
    read_session: Session, *, cursor: int, limit: int | None
) -> list[_Candidate]:
    """Stable keyset page: id > cursor, ordered by id, optionally capped.

    Deliberately not OFFSET-based -- OFFSET pagination can skip or repeat
    rows under concurrent inserts/deletes; `id > cursor ORDER BY id ASC` is
    monotonic and safe regardless of what else is writing to the table.
    """
    query = (
        select(EarlySignalEvent)
        .where(EarlySignalEvent.id > cursor)
        .order_by(EarlySignalEvent.id.asc())
    )
    if limit is not None:
        query = query.limit(limit)

    rows = read_session.scalars(query).all()
    return [
        _Candidate(
            id=row.id,
            external_id=row.external_id,
            url_link=row.url_link,
            region=row.region,
            property_type=row.property_type,
        )
        for row in rows
    ]


def _compute_next_cursor(
    candidates: list[_Candidate],
    blocked_ids: set[int],
    *,
    requested_limit: int | None,
) -> int:
    """Advance the cursor past everything this run looked at -- except a
    record blocked by a REAL problem, which must be retried on the very
    next run rather than waiting for the queue to wrap all the way around.
    `blocked_ids` covers both causes: a real external fetch error, and a
    candidate whose DB chunk write failed to commit (the write never
    happened, so the cursor must not advance past it either -- advancing
    past a failed write would silently drop that record from the queue
    forever). If this page came back short of the requested limit (or
    empty/unbounded), the table has been fully traversed from the previous
    cursor, so wrap back to 0.
    """
    if not candidates:
        return 0

    if blocked_ids:
        return min(blocked_ids) - 1

    max_id = max(c.id for c in candidates)
    if requested_limit is None or len(candidates) < requested_limit:
        # An unbounded page always reaches the true end of the table; a
        # capped page that came back short of the cap does too either way.
        return 0
    return max_id


def _chunked(items: list[Any], size: int) -> list[list[Any]]:
    if size < 1:
        size = 1
    return [items[i : i + size] for i in range(0, len(items), size)]


def _apply_enrichment_fields(row: EarlySignalEvent, payload: dict[str, str]) -> bool:
    """Idempotently merge payload fields into row.

    A field only ever moves from empty/different to a new non-empty value; a
    populated field is never overwritten with an empty value, and assigning
    a value identical to what's already stored is not counted as a change.
    Returns True iff at least one field actually changed value.
    """
    changed = False
    for field in ("url_link", "address", "applicant", "project_value"):
        value = payload.get(field)
        if value and getattr(row, field) != value:
            setattr(row, field, value)
            changed = True
    return changed


def _write_enrichment_chunk(
    chunk: list[tuple[int, _Candidate, dict[str, str]]],
    *,
    get_session,
) -> tuple[bool, dict[int, bool]]:
    """Write one chunk in its own short-lived session and commit it.

    Returns (committed, changed_by_idx), where changed_by_idx maps each
    chunk entry's index (into the caller's candidate list) to whether that
    row actually received a field change -- a row that matched a project but
    whose payload carried only empty values, or values identical to what's
    already stored, is NOT a change and must not be reported as enriched.

    On failure the session is rolled back and closed, and changed_by_idx is
    empty: nothing in a failed chunk was actually persisted, so nothing in
    it can be honestly reported as enriched. Because every chunk uses an
    independent session, a failure here cannot affect chunks already
    committed or chunks not yet processed.
    """
    write_session = get_session()
    try:
        changed_by_idx: dict[int, bool] = {}
        for idx, candidate, payload in chunk:
            row = write_session.get(EarlySignalEvent, candidate.id)
            changed_by_idx[idx] = bool(row) and _apply_enrichment_fields(row, payload)
        write_session.commit()
        return True, changed_by_idx
    except Exception as exc:
        print(
            f"[Vancouver Enrichment] Chunk write failed ({len(chunk)} records): {exc}"
        )
        write_session.rollback()
        return False, {}
    finally:
        write_session.close()


def enrich_early_signal_events(
    read_session: Session,
    *,
    limit: int | None = None,
    force: bool = False,
    since_id: int | None = None,
    refresh_all: bool = False,
    fetch_details: bool = True,
    persist: bool = True,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    get_session=None,
) -> dict[str, Any]:
    """Run the full read -> external I/O -> chunked-write pipeline.

    `read_session` is used only for phase 1 (candidate discovery) and is
    closed before any external HTTP call is made. `get_session` (defaults to
    db.connection.get_session) is called fresh for every write chunk.

    Candidates are selected by stable keyset pagination (id > cursor), not
    by "does this row still have an empty field" -- project_value is rarely
    present in the source data and an emptiness filter on it never
    converges, which is exactly why every run used to reselect the same
    head-of-table rows. The cursor is self-tracked in pipeline_runs (see
    _resolve_starting_cursor/_compute_next_cursor) so consecutive runs walk
    forward through the table and wrap around once they reach the end.
    `since_id`/`force` let a caller override the auto cursor explicitly.

    Scheduled-enrichment page-size policy: a bare call (limit=None,
    refresh_all=False -- the default for the internal API / scheduled
    trigger) is an INCREMENTAL page of DEFAULT_PAGE_LIMIT candidates, not a
    full-table sweep -- this is deliberate: an unbounded default would
    mean every scheduled run re-scrapes the ENTIRE table over HTTP, every
    time, forever, with cost scaling with table size. `refresh_all=True`
    is the explicit opt-in for the old "no limit, one full pass" behavior
    (used by the manual backfill script); an explicit `limit=N` always
    means exactly N regardless of `refresh_all`. Either way, the table is
    still fully covered over time: once a page's cursor reaches the end,
    it wraps to 0 and the rotation continues from the top on a later run.
    """
    if get_session is None:
        from db.connection import get_session as _default_get_session

        get_session = _default_get_session

    effective_limit = limit
    if effective_limit is None and not refresh_all:
        effective_limit = DEFAULT_PAGE_LIMIT

    # Phase 1: short read session -> plain candidate data, then close.
    cursor = _resolve_starting_cursor(read_session, since_id=since_id, force=force)
    candidates = _fetch_candidates(read_session, cursor=cursor, limit=effective_limit)
    read_session.close()

    # Phase 2: external HTTP scraping. No DB session is open past this point.
    # `http` is always closed on the way out, including if load_development_
    # projects or any per-candidate fetch raises unexpectedly.
    http = create_session()
    try:
        projects = load_development_projects(session=http)

        fetched = 0
        skipped = 0
        external_failures = 0
        results: list[dict[str, Any] | None] = [None] * len(candidates)
        to_write: list[tuple[int, _Candidate, dict[str, str]]] = []
        had_error_by_idx: dict[int, bool] = {}

        for idx, candidate in enumerate(candidates):
            payload, had_error = enrich_early_signal_event(
                candidate, projects, http_session=http, fetch_details=fetch_details
            )
            had_error_by_idx[idx] = had_error
            if had_error:
                external_failures += 1

            if payload is None:
                skipped += 1
                results[idx] = {
                    "id": candidate.id,
                    "external_id": candidate.external_id,
                    "property_type": candidate.property_type,
                    "region": candidate.region,
                    "status": "no_match",
                    "had_error": had_error,
                }
                continue

            fetched += 1
            to_write.append((idx, candidate, payload))
    finally:
        http.close()

    # Phase 3: chunked, independently-committed writes. No external I/O past
    # this point, and no single chunk's DB session spans another chunk.
    # "enriched" means an actual field value changed on an actual commit --
    # a matched-but-unchanged row (empty payload, or payload identical to
    # what's already stored) is honestly reported as no_new_values, not
    # enriched, and results[...]["status"] == "enriched" is set only after
    # that row's chunk has actually committed.
    enriched = 0
    no_new_values = 0
    committed_chunks = 0
    write_failures = 0
    write_failed_idx: set[int] = set()

    if persist:
        for chunk in _chunked(to_write, chunk_size):
            committed, changed_by_idx = _write_enrichment_chunk(
                chunk, get_session=get_session
            )
            if committed:
                committed_chunks += 1
                for idx, candidate, payload in chunk:
                    if changed_by_idx.get(idx):
                        enriched += 1
                        status = "enriched"
                    else:
                        no_new_values += 1
                        status = "no_new_values"
                    results[idx] = {
                        "id": candidate.id,
                        "external_id": candidate.external_id,
                        "property_type": candidate.property_type,
                        "region": candidate.region,
                        "status": status,
                        "had_error": had_error_by_idx.get(idx, False),
                        **payload,
                    }
            else:
                write_failures += 1
                for idx, candidate, payload in chunk:
                    write_failed_idx.add(idx)
                    results[idx] = {
                        "id": candidate.id,
                        "external_id": candidate.external_id,
                        "property_type": candidate.property_type,
                        "region": candidate.region,
                        "status": "write_failed",
                        "had_error": had_error_by_idx.get(idx, False),
                        **payload,
                    }
    else:
        for idx, candidate, payload in to_write:
            results[idx] = {
                "id": candidate.id,
                "external_id": candidate.external_id,
                "property_type": candidate.property_type,
                "region": candidate.region,
                "status": "not_persisted",
                "had_error": had_error_by_idx.get(idx, False),
                **payload,
            }

    # The cursor must not advance past a record whose chunk write failed --
    # that record was never actually persisted, so leaving it behind would
    # silently drop it from the queue forever. Computed here, after Phase
    # 3, specifically so write failures (not just external fetch errors)
    # are accounted for.
    blocked_ids = {
        candidates[idx].id for idx, had_error in had_error_by_idx.items() if had_error
    } | {candidates[idx].id for idx in write_failed_idx}
    next_cursor = _compute_next_cursor(
        candidates, blocked_ids, requested_limit=effective_limit
    )

    return {
        "projects_indexed": len(projects),
        "candidates": len(candidates),
        "fetched": fetched,
        "enriched": enriched,
        "no_new_values": no_new_values,
        "skipped": skipped,
        "external_failures": external_failures,
        "write_failures": write_failures,
        "committed_chunks": committed_chunks,
        "next_cursor": next_cursor,
        "page_limit": effective_limit,
        "refresh_all": refresh_all,
        "results": results,
    }


def run_early_signal_enrichment(
    *,
    limit: int | None = None,
    force: bool = False,
    since_id: int | None = None,
    refresh_all: bool = False,
    fetch_details: bool = True,
    persist: bool = True,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> dict[str, Any]:
    from db.connection import get_session, init_db

    init_db()
    read_session = get_session()
    try:
        return enrich_early_signal_events(
            read_session,
            limit=limit,
            force=force,
            since_id=since_id,
            refresh_all=refresh_all,
            fetch_details=fetch_details,
            persist=persist,
            chunk_size=chunk_size,
            get_session=get_session,
        )
    finally:
        read_session.close()
