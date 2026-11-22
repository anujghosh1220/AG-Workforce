"""Background research worker.

DB-backed queue processed by a daemon thread — no Redis/Celery required for
dev. Jobs are claimed with SELECT ... FOR UPDATE SKIP LOCKED semantics
(emulated), processed in batches, cancellable, and every step is recorded in
agent_runs + activities. Swap for Celery later without changing the API.
"""
import json
import logging
import os
import threading
import time
import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.integrations.research.registry import default_providers, get_provider
from app.models import (
    ActorType,
    Agent,
    AgentRun,
    NotificationType,
    ResearchJob,
    ResearchJobStatus,
    ResearchResult,
    ResearchResultStatus,
    RunStatus,
)
from app.services import dedup
from app.services.helpers import log_activity, notify_workspace
from app.services.lead_scorer import score_business
from app.services.website_enricher import enrich_website

logger = logging.getLogger("agworkforce.research")

_stop = threading.Event()
_thread: threading.Thread | None = None
BATCH = 25
POLL_SECONDS = 2.0
_HANDLE = None


def _handle():
    global _HANDLE
    if _HANDLE is None:
        from app.services.workers_health import WorkerHandle
        _HANDLE = WorkerHandle("research").start()
    return _HANDLE


def _now():
    return datetime.now(timezone.utc)


def _claim_next_job(db: Session) -> ResearchJob | None:
    job = db.scalar(
        select(ResearchJob)
        .where(ResearchJob.status == ResearchJobStatus.queued)
        .order_by(ResearchJob.created_at)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if job is None:
        return None
    job.status = ResearchJobStatus.running
    job.started_at = _now()
    db.commit()
    return job


def _job_cancelled(db: Session, job_id) -> bool:
    status = db.scalar(select(ResearchJob.status).where(ResearchJob.id == job_id))
    return status == ResearchJobStatus.cancelled


def process_job(job_id: uuid.UUID) -> None:
    """Execute one research job end-to-end. Safe to call directly in tests."""
    h = _handle()
    h.set_job(f"research_job:{job_id}")
    h.beat_now()
    db = SessionLocal()
    job = db.get(ResearchJob, job_id)
    if job is None:
        db.close()
        return
    wid = job.workspace_id
    cfg = job.config or {}
    stats = {"discovered": 0, "researched": 0, "contacts_found": 0,
             "qualified": 0, "duplicates": 0, "errors": 0, "imported": 0}
    job.stats = stats

    agent_id = job.agent_id
    if agent_id is None:
        agent = db.scalar(select(Agent).where(
            Agent.workspace_id == wid, Agent.slug == "lead-researcher"))
        agent_id = agent.id if agent else None

    run = AgentRun(
        workspace_id=wid, agent_id=agent_id, status=RunStatus.running,
        input_payload={"job_id": str(job.id), "config": cfg},
        started_at=_now(),
    )
    db.add(run)
    db.flush()

    geo = {
        "country": cfg.get("country"), "state": cfg.get("state"),
        "district": cfg.get("district"), "city": cfg.get("city"),
        "town": cfg.get("town"), "locality": cfg.get("locality"),
        "postal_code": cfg.get("postal_code"),
        "latitude": cfg.get("latitude"), "longitude": cfg.get("longitude"),
        "radius_m": int(cfg.get("radius_m") or 5000),
    }
    business_types = cfg.get("business_types") or []
    keywords = cfg.get("keywords") or []
    exclusions = cfg.get("exclusions") or []
    limit = int(cfg.get("max_results") or 50)
    provider_names = cfg.get("providers") or None

    providers = []
    if provider_names:
        for n in provider_names:
            try:
                p = get_provider(n)
                if p.configured():
                    providers.append(p)
            except Exception as exc:
                stats["errors"] += 1
                logger.warning("provider %s unavailable: %s", n, exc)
    else:
        providers = default_providers()
    if not providers:
        job.error = "No configured data providers available"
        stats["errors"] += 1

    try:
        collected = 0
        for provider in providers:
            if collected >= limit or _job_cancelled(db, job.id):
                break
            try:
                for biz in provider.search(_geo(geo), business_types, keywords, exclusions,
                                           limit - collected):
                    if _job_cancelled(db, job.id):
                        break
                    collected += 1
                    stats["discovered"] += 1
                    _store_result(db, wid, job.id, provider.name, biz, stats)
                    if collected % BATCH == 0:
                        job.progress = min(95, int(collected / max(limit, 1) * 90))
                        job.stats = dict(stats)
                        db.commit()
            except Exception as exc:  # noqa: BLE001
                stats["errors"] += 1
                logger.warning("provider %s failed: %s", provider.name, exc)
                job.error = f"{provider.name}: {str(exc)[:300]}"
                job.stats = dict(stats)
                db.commit()

        # finalize statuses
        if _job_cancelled(db, job.id):
            job.status = ResearchJobStatus.cancelled
            run.status = RunStatus.failed
            run.error = "cancelled by user"
        else:
            job.status = ResearchJobStatus.completed
            run.status = RunStatus.succeeded
            run.output_payload = {"stats": stats, "results": collected}
        job.finished_at = _now()
        job.progress = 100 if job.status == ResearchJobStatus.completed else job.progress
        job.stats = dict(stats)
        db.commit()

        notify_workspace(
            db, wid, NotificationType.system,
            f"Research job {'completed' if job.status == ResearchJobStatus.completed else 'stopped'}: {stats['researched']} businesses researched",
            body=f"{stats['qualified']} qualified, {stats['duplicates']} duplicates, {stats['errors']} errors",
            link="/research",
        )
        log_activity(
            db, wid, "research.job_finished", actor_type=ActorType.agent, actor_id=agent_id,
            description=f"Research job finished: {stats['researched']} researched, {stats['qualified']} qualified",
            metadata={"job_id": str(job.id), "stats": stats},
        )
    except Exception as exc:  # noqa: BLE001
        h.set_job(None)
        h.record_failure(exc)
        job.status = ResearchJobStatus.failed
        job.error = str(exc)[:500]
        job.finished_at = _now()
        run.status = RunStatus.failed
        run.error = str(exc)[:500]
        run.finished_at = _now()
        db.commit()
        logger.exception("research job %s failed", job.id)
    else:
        h.set_job(None)
        h.record_success()
    finally:
        agent = db.get(Agent, agent_id) if agent_id else None
        if agent:
            agent.last_activity_at = _now()
        db.commit()
        db.close()


def _geo(d: dict):
    from app.integrations.research.base import GeoQuery
    return GeoQuery(**{k: v for k, v in d.items()})


def _store_result(db: Session, wid, job_id, provider_name, biz, stats) -> None:
    from app.integrations.research.base import RawBusiness

    name = biz.name if isinstance(biz, RawBusiness) else biz.get("name", "")
    res = ResearchResult(
        workspace_id=wid, job_id=job_id,
        business_name=name[:300],
        normalized_name=dedup.normalize_name(name),
        business_category=(biz.category or "")[:120] if isinstance(biz, RawBusiness) else (biz.get("category") or "")[:120],
        description=(biz.description or "") if isinstance(biz, RawBusiness) else (biz.get("description") or ""),
        address=(biz.address or "") if isinstance(biz, RawBusiness) else (biz.get("address") or ""),
        city=(biz.city if isinstance(biz, RawBusiness) else biz.get("city")),
        state=(biz.state if isinstance(biz, RawBusiness) else biz.get("state")),
        country=(biz.country if isinstance(biz, RawBusiness) else biz.get("country")),
        postal_code=(biz.postal_code if isinstance(biz, RawBusiness) else biz.get("postal_code")),
        latitude=(float(biz.latitude) if getattr(biz, "latitude", None) is not None else None),
        longitude=(float(biz.longitude) if getattr(biz, "longitude", None) is not None else None),
        website=(biz.website if isinstance(biz, RawBusiness) else biz.get("website")),
        phone=(biz.phone if isinstance(biz, RawBusiness) else biz.get("phone")),
        email=(biz.email if isinstance(biz, RawBusiness) else biz.get("email")),
        instagram=(biz.instagram if isinstance(biz, RawBusiness) else biz.get("instagram")),
        facebook=(biz.facebook if isinstance(biz, RawBusiness) else biz.get("facebook")),
        whatsapp=(biz.whatsapp if isinstance(biz, RawBusiness) else biz.get("whatsapp")),
        source_provider=provider_name,
        source_ref=(biz.source_ref if isinstance(biz, RawBusiness) else biz.get("source_ref", ""))[:500],
        evidence=(biz.evidence if isinstance(biz, RawBusiness) else biz.get("evidence", [])),
        status=ResearchResultStatus.pending,
    )
    db.add(res)
    db.flush()

    # ---- website enrichment (public pages only)
    if res.website:
        try:
            enrich = enrich_website(res.website)
            filled = []
            new_evidence = []
            for f in ("email", "phone", "instagram", "facebook"):
                if not getattr(res, f) and enrich.get(f):
                    setattr(res, f, enrich[f])
                    filled.append(f)
                    new_evidence.append({"field": f, "value": enrich[f], "source": res.website, "verified": True})
            existing_fields = {e.get("field") for e in res.evidence}
            res.evidence = list(res.evidence) + [e for e in new_evidence if e["field"] not in existing_fields]
            res.research_notes = f"Website checked ({enrich['pages_checked']} pages)" + (f"; error: {enrich['error']}" if enrich.get("error") else "")
        except Exception as exc:  # noqa: BLE001
            res.research_notes = f"Website enrichment failed: {str(exc)[:200]}"

    # ---- operating signals from enrichment/raw
    res.uses_online_ordering = "unknown"
    res.uses_digital_menu = "unknown"
    res.has_reservation_system = "unknown"

    # ---- scoring
    biz_dict = {c.name: getattr(res, c.name) for c in res.__table__.columns}
    sc = score_business(biz_dict, res.evidence)
    res.score = sc.score
    res.score_breakdown = sc.breakdown
    res.score_explanation = "\n".join(sc.explanation)
    res.missing_info = sc.missing

    # ---- duplicate detection (flag only, never auto-merge)
    dups = dedup.find_duplicates(
        db, wid, name=res.business_name, website=res.website,
        phone=res.phone, exclude_result_id=res.id,
    )
    if dups:
        res.status = ResearchResultStatus.duplicate
        res.duplicate_of_id = uuid.UUID(dups[0]["entity_id"]) if dups[0]["entity_type"] == "research_result" else None
        res.duplicate_reason = "; ".join(
            f"{d['entity_type']} '{d['name']}': {', '.join(d['reasons'])}" for d in dups[:3]
        )
        stats["duplicates"] += 1
    else:
        res.status = ResearchResultStatus.researched
        stats["researched"] += 1
        if res.email or res.phone or res.website:
            stats["contacts_found"] += 1
        if sc.score >= 60:
            stats["qualified"] += 1
        _upsert_lead(db, wid, res, sc.score)

    res.researched_at = _now()
    j = db.get(ResearchJob, job_id)
    if j is not None:
        j.stats = dict(stats)
    db.commit()


def _upsert_lead(db: Session, wid, res: ResearchResult, score: int) -> None:
    """Create a CRM lead for a researched business. Never overwrites existing
    lead data — only fills fields that are empty on the lead."""
    from app.models import Lead, LeadStage, QualificationStatus

    # skip if a lead already exists for this business (dedup by name+domain)
    existing = db.scalar(
        select(Lead).where(Lead.workspace_id == wid, Lead.business_name == res.business_name).limit(1)
    )
    if existing is None and res.website:
        from app.services.dedup import domain_of
        dom = domain_of(res.website)
        if dom:
            for l in db.scalars(select(Lead).where(Lead.workspace_id == wid)).all():
                if domain_of(l.website) == dom:
                    existing = l
                    break
    if existing is not None:
        res.lead_id = existing.id
        res.lead_score_written = True
        # fill only missing fields — never clobber CRM data
        for f in ("website", "email", "phone", "city", "state", "country", "notes"):
            if not getattr(existing, f) and getattr(res, f):
                setattr(existing, f, getattr(res, f))
        if score > existing.lead_score:
            existing.lead_score = score
        if existing.stage == LeadStage.discovered and score >= 60:
            existing.stage = LeadStage.researched
            existing.qualification_status = QualificationStatus.qualified
        return

    qual = (QualificationStatus.qualified if score >= 60
            else QualificationStatus.researching if score >= 40
            else QualificationStatus.unqualified)
    stage = LeadStage.researched if score >= 40 else LeadStage.discovered
    lead = Lead(
        workspace_id=wid,
        business_name=res.business_name,
        business_type=res.business_category or "cafe",
        website=res.website, email=res.email, phone=res.phone,
        city=res.city, state=res.state, country=res.country,
        source="research", source_id=None,
        lead_score=score,
        qualification_status=qual,
        stage=stage,
        notes=(res.score_explanation or "")[:4000],
    )
    db.add(lead)
    db.flush()
    res.lead_id = lead.id
    res.lead_score_written = True
    stats_note = f"Auto-created by Lead Researcher (score {score})"
    log_activity(db, wid, "lead.researched_import", lead_id=lead.id,
                 actor_type=ActorType.agent, actor_id=None,
                 description=f"Research import: '{lead.business_name}' (score {score})",
                 metadata={"result_id": str(res.id), "provider": res.source_provider})


def _worker_loop():
    while not _stop.is_set():
        db = SessionLocal()
        try:
            job = _claim_next_job(db)
            if job is None:
                db.close()
                time.sleep(POLL_SECONDS)
                continue
            job_id = job.id
            db.close()
            process_job(job_id)
            _handle().beat_now()
        except Exception:
            logger.exception("worker loop error")
            try:
                db.close()
            except Exception:
                pass
            time.sleep(POLL_SECONDS)


def start_worker() -> None:
    if os.environ.get("ENVIRONMENT") == "test" or os.environ.get("AGW_DISABLE_WORKER") == "1":
        return
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_worker_loop, daemon=True, name="research-worker")
    _thread.start()
    logger.info("research worker started")


def stop_worker() -> None:
    _stop.set()
    if _HANDLE is not None:
        _HANDLE.shutdown()
