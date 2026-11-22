import csv
import io
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import AuthContext, get_current_auth, require_roles
from app.api.pagination import Page, paginate
from app.core.database import get_db
from app.integrations.research.registry import available_providers
from app.models import (
    Lead,
    LeadStage,
    QualificationStatus,
    ResearchJob,
    ResearchJobStatus,
    ResearchResult,
    ResearchResultStatus,
    UserRole,
)
from app.schemas import (
    ProviderOut,
    ResearchJobCreate,
    ResearchJobOut,
    ResearchResultOut,
)
from app.services.helpers import get_or_404, log_activity
from app.models import ActorType
from app.tasks.research_worker import process_job, start_worker

router = APIRouter(prefix="/research", tags=["research"])
WRITE_ROLES = (UserRole.owner, UserRole.admin, UserRole.member)


def _job_out(j: ResearchJob) -> ResearchJobOut:
    return ResearchJobOut.model_validate(j)


def _result_out(r: ResearchResult) -> ResearchResultOut:
    from app.services.lead_scorer import priority_for
    data = ResearchResultOut.model_validate(r).model_dump()
    data["priority"] = priority_for(r.score)
    return ResearchResultOut(**data)


# ---------------------------------------------------------- providers


@router.get("/providers", response_model=list[ProviderOut])
def list_providers(auth: AuthContext = Depends(get_current_auth)):
    return available_providers()


# ---------------------------------------------------------- jobs


@router.get("/jobs", response_model=Page[ResearchJobOut])
def list_jobs(
    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
    status_: ResearchJobStatus | None = Query(None, alias="status"),
    auth: AuthContext = Depends(get_current_auth), db: Session = Depends(get_db),
):
    q = select(ResearchJob).where(ResearchJob.workspace_id == auth.workspace.id)
    if status_:
        q = q.where(ResearchJob.status == status_)
    q = q.order_by(ResearchJob.created_at.desc())
    items, total, page, page_size = paginate(db, q, page, page_size)
    return Page.build([_job_out(j) for j in items], total, page, page_size)


@router.post("/jobs", response_model=ResearchJobOut, status_code=status.HTTP_201_CREATED)
def create_job(
    body: ResearchJobCreate,
    auth: AuthContext = Depends(require_roles(*WRITE_ROLES)),
    db: Session = Depends(get_db),
):
    wid = auth.workspace.id
    # prevent duplicate concurrent jobs for same-ish campaign
    running = db.scalar(
        select(func.count(ResearchJob.id)).where(
            ResearchJob.workspace_id == wid,
            ResearchJob.status.in_([ResearchJobStatus.queued, ResearchJobStatus.running]),
        )
    )
    if running:
        raise HTTPException(status.HTTP_409_CONFLICT, "A research job is already queued or running")

    # validate requested providers exist & configured
    if body.providers:
        from app.integrations.research.registry import get_provider
        for name in body.providers:
            try:
                p = get_provider(name)
            except Exception:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown provider '{name}'")
            if not p.configured():
                raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Provider '{name}' is not configured")

    cfg = body.model_dump()
    job = ResearchJob(
        workspace_id=wid,
        config=cfg,
        stats={"discovered": 0, "researched": 0, "contacts_found": 0,
               "qualified": 0, "duplicates": 0, "errors": 0, "imported": 0},
        created_by_user_id=auth.user.id,
    )
    db.add(job)
    db.flush()
    log_activity(db, wid, "research.job_created", actor_id=auth.user.id,
                 actor_type=ActorType.user,
                 description=f"Research job created for {body.city or body.locality or body.state or body.country or 'area'}",
                 metadata={"job_id": str(job.id), "config": cfg})
    db.commit()
    db.refresh(job)
    start_worker()
    return _job_out(job)


@router.get("/jobs/{job_id}", response_model=ResearchJobOut)
def get_job(job_id: uuid.UUID, auth: AuthContext = Depends(get_current_auth), db: Session = Depends(get_db)):
    return _job_out(get_or_404(db, ResearchJob, job_id, auth.workspace.id))


@router.post("/jobs/{job_id}/cancel", response_model=ResearchJobOut)
def cancel_job(job_id: uuid.UUID, auth: AuthContext = Depends(require_roles(*WRITE_ROLES)), db: Session = Depends(get_db)):
    job = get_or_404(db, ResearchJob, job_id, auth.workspace.id)
    if job.status in (ResearchJobStatus.queued, ResearchJobStatus.running):
        job.status = ResearchJobStatus.cancelled
        job.finished_at = job.finished_at  # worker finalizes timestamps
        db.commit()
        db.refresh(job)
    return _job_out(job)


@router.post("/jobs/{job_id}/process", response_model=ResearchJobOut)
def process_now(job_id: uuid.UUID, auth: AuthContext = Depends(require_roles(*WRITE_ROLES)), db: Session = Depends(get_db)):
    """Dev convenience: run a queued job synchronously."""
    job = get_or_404(db, ResearchJob, job_id, auth.workspace.id)
    if job.status != ResearchJobStatus.queued:
        raise HTTPException(status.HTTP_409_CONFLICT, "Job is not queued")
    db.close()
    process_job(job_id)
    from app.core.database import SessionLocal
    s = SessionLocal()
    try:
        return _job_out(s.get(ResearchJob, job_id))
    finally:
        s.close()


# ---------------------------------------------------------- results


@router.get("/results", response_model=Page[ResearchResultOut])
def list_results(
    page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=200),
    job_id: uuid.UUID | None = None,
    status_: ResearchResultStatus | None = Query(None, alias="status"),
    q: str | None = None,
    min_score: int | None = None,
    has_contact: bool | None = None,
    auth: AuthContext = Depends(get_current_auth), db: Session = Depends(get_db),
):
    wid = auth.workspace.id
    query = select(ResearchResult).where(ResearchResult.workspace_id == wid)
    if job_id:
        query = query.where(ResearchResult.job_id == job_id)
    if status_:
        query = query.where(ResearchResult.status == status_)
    if q:
        like = f"%{q.strip()}%"
        query = query.where(or_(ResearchResult.business_name.ilike(like),
                                ResearchResult.city.ilike(like),
                                ResearchResult.email.ilike(like)))
    if min_score is not None:
        query = query.where(ResearchResult.score >= min_score)
    if has_contact is True:
        query = query.where(or_(ResearchResult.email.is_not(None), ResearchResult.phone.is_not(None), ResearchResult.website.is_not(None)))
    elif has_contact is False:
        query = query.where(ResearchResult.email.is_(None), ResearchResult.phone.is_(None), ResearchResult.website.is_(None))
    query = query.order_by(ResearchResult.score.desc(), ResearchResult.discovered_at.desc())
    items, total, page, page_size = paginate(db, query, page, page_size)
    return Page.build([_result_out(r) for r in items], total, page, page_size)


@router.get("/results/{result_id}", response_model=ResearchResultOut)
def get_result(result_id: uuid.UUID, auth: AuthContext = Depends(get_current_auth), db: Session = Depends(get_db)):
    return _result_out(get_or_404(db, ResearchResult, result_id, auth.workspace.id))


@router.post("/results/{result_id}/import", response_model=ResearchResultOut)
def import_result(result_id: uuid.UUID, auth: AuthContext = Depends(require_roles(*WRITE_ROLES)), db: Session = Depends(get_db)):
    """Manually import a researched business into the CRM as a lead."""
    res = get_or_404(db, ResearchResult, result_id, auth.workspace.id)
    if res.status == ResearchResultStatus.imported and res.lead_id:
        raise HTTPException(status.HTTP_409_CONFLICT, "Already imported")
    from app.tasks.research_worker import _upsert_lead
    from app.services.lead_scorer import score_business
    sc = score_business({c.name: getattr(res, c.name) for c in res.__table__.columns})
    _upsert_lead(db, auth.workspace.id, res, sc.score)
    res.status = ResearchResultStatus.imported
    res.lead_score_written = True
    db.commit()
    db.refresh(res)
    return _result_out(res)


@router.post("/results/bulk-import", response_model=dict)
def bulk_import(
    result_ids: list[uuid.UUID],
    auth: AuthContext = Depends(require_roles(*WRITE_ROLES)),
    db: Session = Depends(get_db),
):
    imported, skipped = 0, 0
    for rid in result_ids:
        res = db.scalar(select(ResearchResult).where(
            ResearchResult.id == rid, ResearchResult.workspace_id == auth.workspace.id))
        if res is None or res.lead_id:
            skipped += 1
            continue
        try:
            from app.tasks.research_worker import _upsert_lead
            from app.services.lead_scorer import score_business
            sc = score_business({c.name: getattr(res, c.name) for c in res.__table__.columns})
            _upsert_lead(db, auth.workspace.id, res, sc.score)
            res.status = ResearchResultStatus.imported
            res.lead_score_written = True
            imported += 1
        except Exception:
            skipped += 1
    db.commit()
    return {"imported": imported, "skipped": skipped}


# ---------------------------------------------------------- CSV import

CSV_FIELD_MAP = {
    "business_name": ["business_name", "name", "business", "cafe", "title"],
    "business_category": ["business_category", "category", "type"],
    "website": ["website", "site", "url"],
    "email": ["email", "e-mail", "mail"],
    "phone": ["phone", "mobile", "tel", "telephone", "whatsapp"],
    "city": ["city", "town"],
    "state": ["state", "province", "region"],
    "country": ["country"],
    "postal_code": ["postal_code", "zip", "pincode"],
    "address": ["address", "full_address", "location"],
    "contact_person": ["contact_person", "contact", "owner"],
    "lead_score": ["lead_score", "score"],
}


@router.post("/import-csv", response_model=dict, status_code=status.HTTP_201_CREATED)
async def import_csv(
    file: UploadFile,
    auth: AuthContext = Depends(require_roles(*WRITE_ROLES)),
    db: Session = Depends(get_db),
):
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Please upload a .csv file")
    raw = (await file.read()).decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(raw))
    if not reader.fieldnames:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "CSV has no header row")

    # map headers
    colmap: dict[str, str] = {}
    for field, aliases in CSV_FIELD_MAP.items():
        for h in reader.fieldnames:
            if h.strip().lower() in aliases:
                colmap[field] = h
                break
    if "business_name" not in colmap:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "CSV must include a business name column (name/business_name)")

    wid = auth.workspace.id
    created, skipped, errors = 0, 0, []
    for i, row in enumerate(reader, start=2):
        name = (row.get(colmap["business_name"]) or "").strip()
        if not name:
            skipped += 1
            continue
        try:
            score = int(float(row.get(colmap.get("lead_score", ""), "") or 0))
        except (ValueError, TypeError):
            score = 0
        score = max(0, min(100, score))
        qual = (QualificationStatus.qualified if score >= 60
                else QualificationStatus.researching if score >= 40
                else QualificationStatus.unqualified)
        lead = Lead(
            workspace_id=wid,
            business_name=name[:300],
            business_type=(row.get(colmap.get("business_category", ""), "") or "cafe")[:100],
            website=(row.get(colmap.get("website", ""), "") or None),
            email=(row.get(colmap.get("email", ""), "") or None),
            phone=(row.get(colmap.get("phone", ""), "") or None),
            city=(row.get(colmap.get("city", ""), "") or None),
            state=(row.get(colmap.get("state", ""), "") or None),
            country=(row.get(colmap.get("country", ""), "") or None),
            contact_person=(row.get(colmap.get("contact_person", ""), "") or None),
            source="csv_import",
            lead_score=score,
            qualification_status=qual,
            stage=LeadStage.discovered,
            notes=" ".join(x for x in [
                (row.get(colmap.get("address", ""), "") or ""),
                (row.get(colmap.get("postal_code", ""), "") or ""),
            ] if x)[:4000],
        )
        # simple dedup: skip exact normalized-name duplicates in workspace
        from app.services.dedup import normalize_name
        dup = db.scalar(select(Lead.id).where(
            Lead.workspace_id == wid,
            Lead.business_name.ilike(name),
        ).limit(1))
        if dup:
            skipped += 1
            continue
        db.add(lead)
        created += 1
        if created >= 500:
            errors.append("Stopped at 500 rows per import")
            break
    db.commit()
    log_activity(db, wid, "research.csv_import", actor_id=auth.user.id,
                 actor_type=ActorType.user,
                 description=f"CSV import: {created} created, {skipped} skipped",
                 metadata={"filename": file.filename, "created": created, "skipped": skipped})
    db.commit()
    return {"created": created, "skipped": skipped, "errors": errors}
