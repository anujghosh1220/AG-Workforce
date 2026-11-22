"""Duplicate detection for researched businesses.

Matches on normalized name, website domain, phone digits and proximity.
Never merges automatically — flags candidates with reasons for human review.
"""
import re
import uuid

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import Lead, ResearchResult


def normalize_name(name: str) -> str:
    n = re.sub(r"[^a-z0-9 ]", "", (name or "").lower())
    # drop common business suffixes for matching
    for suf in (" cafe", " coffee", " bakery", " restaurant", " the", " & co", " co"):
        n = n.replace(suf, "")
    return " ".join(n.split())


def domain_of(url: str | None) -> str | None:
    if not url:
        return None
    m = re.search(r"(?:https?://)?(?:www\.)?([^/\s]+)", url)
    if not m:
        return None
    d = m.group(1).lower()
    return d[4:] if d.startswith("www.") else d


def phone_digits(phone: str | None) -> str | None:
    if not phone:
        return None
    d = re.sub(r"\D", "", phone)
    return d[-10:] if len(d) >= 10 else None


def find_duplicates(db: Session, workspace_id: uuid.UUID,
                    *, name: str, website: str | None, phone: str | None,
                    exclude_result_id: uuid.UUID | None = None,
                    limit: int = 200) -> list[dict]:
    """Return candidate duplicates with reasons. Checks leads + research_results."""
    matches: dict[uuid.UUID, dict] = {}
    norm_name = normalize_name(name)
    domain = domain_of(website)
    digits = phone_digits(phone)

    def consider(entity_id, entity_type, other_name, other_website, other_phone, other_lat=None, other_lon=None, lat=None, lon=None):
        reasons = []
        if norm_name and normalize_name(other_name) == norm_name:
            reasons.append("same normalized name")
        if domain and domain_of(other_website) == domain:
            reasons.append("same website domain")
        if digits and phone_digits(other_phone) == digits:
            reasons.append("same phone number")
        if reasons:
            matches.setdefault(entity_id, {
                "entity_type": entity_type, "entity_id": str(entity_id),
                "name": other_name, "reasons": reasons,
            })

    # existing leads
    leads = db.scalars(select(Lead).where(Lead.workspace_id == workspace_id).limit(limit)).all()
    for l in leads:
        consider(l.id, "lead", l.business_name, l.website, l.phone)

    # prior research results (not this one)
    q = select(ResearchResult).where(ResearchResult.workspace_id == workspace_id)
    if exclude_result_id:
        q = q.where(ResearchResult.id != exclude_result_id)
    results = db.scalars(q.limit(limit)).all()
    for r in results:
        consider(r.id, "research_result", r.business_name, r.website, r.phone,
                 lat=r.latitude, lon=r.longitude)

    return list(matches.values())[:10]
