"""Transparent, configurable lead scoring (0-100).

Weights are configurable via SCORING_WEIGHTS env JSON or defaults below.
Points are only awarded for verified/present evidence — never for missing data.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

DEFAULT_WEIGHTS = {
    "relevance": 25,          # business category matches target types
    "verifiable_presence": 15,  # has website or multiple public listings
    "fit": 25,                # signals it could use café management software
    "contactability": 15,     # legitimate public contact channel exists
    "completeness": 10,       # research fields populated
    "operational_need": 10,   # evidence of digital/ops needs
}

PRIORITY_BANDS = [(80, "high"), (60, "good"), (40, "needs_research"), (0, "low")]

TARGET_CATEGORIES = {
    "cafe", "coffee", "coffee_shop", "cafe/coffee_shop", "bakery", "dessert",
    "tea", "restaurant", "cafeteria", "juice_bar", "ice_cream", "fast_food",
}


@dataclass
class ScoreResult:
    score: int
    breakdown: dict
    explanation: list[str]
    missing: list[str]
    priority: str


def _weights() -> dict:
    raw = os.environ.get("SCORING_WEIGHTS")
    if raw:
        try:
            w = json.loads(raw)
            return {**DEFAULT_WEIGHTS, **{k: int(v) for k, v in w.items() if k in DEFAULT_WEIGHTS}}
        except Exception:
            pass
    return dict(DEFAULT_WEIGHTS)


def priority_for(score: int) -> str:
    for min_score, label in PRIORITY_BANDS:
        if score >= min_score:
            return label
    return "low"


def score_business(biz: dict, evidence: list | None = None) -> ScoreResult:
    """Score a business dict (ResearchResult fields or RawBusiness attrs)."""
    w = _weights()
    breakdown: dict[str, dict] = {}
    explanation: list[str] = []
    missing: list[str] = []

    def award(key: str, points: int, why: str):
        breakdown[key] = {"points": points, "max": w[key], "reason": why}
        if points:
            explanation.append(f"+{points} {key.replace('_', ' ')}: {why}")

    # ---- relevance (category match)
    cat = (biz.get("business_category") or biz.get("category") or "").lower()
    rel = 0
    if any(t in cat for t in TARGET_CATEGORIES):
        rel = w["relevance"]
        award("relevance", rel, f"category '{cat}' matches café/hospitality target")
    else:
        breakdown["relevance"] = {"points": 0, "max": w["relevance"], "reason": f"category '{cat or 'unknown'}' not clearly a café"}
        missing.append("clear business category")

    # ---- verifiable presence
    has_website = bool(biz.get("website"))
    has_address = bool(biz.get("address"))
    has_coords = biz.get("latitude") is not None and biz.get("longitude") is not None
    presence_pts = 0
    reasons = []
    if has_website:
        presence_pts += 8; reasons.append("has website")
    if has_address:
        presence_pts += 4; reasons.append("full address")
    if has_coords:
        presence_pts += 3; reasons.append("precise coordinates")
    presence_pts = min(presence_pts, w["verifiable_presence"])
    award("verifiable_presence", presence_pts, "; ".join(reasons) if reasons else "no website/address/coords found")
    if not has_website:
        missing.append("website")

    # ---- fit for café management software
    fit_pts = 0
    fit_reasons = []
    ordering = biz.get("uses_online_ordering")
    menu = biz.get("uses_digital_menu")
    reserv = biz.get("has_reservation_system")
    if ordering == "yes":
        fit_pts += 10; fit_reasons.append("already uses online ordering")
    elif ordering == "no":
        fit_pts += 12; fit_reasons.append("no online ordering detected — strong fit")
    if menu == "yes":
        fit_pts += 5; fit_reasons.append("has digital menu")
    if reserv == "yes":
        fit_pts += 5; fit_reasons.append("has reservation/table system")
    if fit_reasons:
        fit_pts = min(fit_pts, w["fit"])
        award("fit", fit_pts, "; ".join(fit_reasons))
    else:
        breakdown["fit"] = {"points": 0, "max": w["fit"], "reason": "operational signals unknown"}
        missing.append("digital ordering/menu evidence")

    # ---- contactability via legitimate public channel
    contact_pts = 0
    chans = []
    if biz.get("email"):
        contact_pts += 7; chans.append("public email")
    if biz.get("phone"):
        contact_pts += 5; chans.append("public phone")
    if biz.get("website"):
        contact_pts += 3; chans.append("website contact page")
    contact_pts = min(contact_pts, w["contactability"])
    award("contactability", contact_pts, ", ".join(chans) if chans else "no public contact channel found")
    if not (biz.get("email") or biz.get("phone")):
        missing.append("public email/phone")

    # ---- research completeness
    fields = ["business_name", "business_category", "address", "city", "website", "phone", "email"]
    present = sum(1 for f in fields if biz.get(f))
    comp_pts = round(w["completeness"] * present / len(fields))
    award("completeness", comp_pts, f"{present}/{len(fields)} core fields populated")

    # ---- operational need evidence
    need_pts = 0
    need_reasons = []
    if ordering == "no" or menu == "no":
        need_pts += 6; need_reasons.append("no digital ordering/menu observed")
    if has_website and not ordering:
        need_pts += 4; need_reasons.append("website exists but ordering unclear")
    need_pts = min(need_pts, w["operational_need"])
    award("operational_need", need_pts, "; ".join(need_reasons) if need_reasons else "no operational signals")

    score = sum(b["points"] for b in breakdown.values())
    score = max(0, min(100, score))
    priority = priority_for(score)

    if not explanation:
        explanation.append("No positive evidence found — score reflects missing data only")
    explanation.append(f"Missing: {', '.join(missing) if missing else 'nothing critical'}")

    return ScoreResult(
        score=score,
        breakdown={k: v for k, v in breakdown.items()},
        explanation=explanation,
        missing=missing,
        priority=priority,
    )
