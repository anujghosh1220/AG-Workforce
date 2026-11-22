"""Phase 7 — agent capability registry.

Each agent declares the task types it can execute, the executor function
in workflow_steps, risk level and required permissions. The orchestrator
never delegates a task_type the agent does not support.
"""
from __future__ import annotations

from app.models import RiskLevel

# task_type -> executor name in app.services.workflow_steps
CAPABILITIES: dict[str, dict] = {
    "lead-researcher": {
        "name": "Lead Researcher",
        "risk": "low",
        "tasks": {
            "discover_leads": "exec_discover_leads",
            "research_leads": "exec_research_leads",
            "enrich_lead": "exec_enrich_lead",
        },
    },
    "sales-executive": {
        "name": "Sales Executive",
        "risk": "medium",
        "tasks": {
            "draft_campaign": "exec_draft_campaign",
            "prepare_outreach": "exec_prepare_outreach",
            "prioritize_prospects": "exec_prioritize_prospects",
        },
    },
    "communication-manager": {
        "name": "Communication Manager",
        "risk": "medium",
        "tasks": {
            "handle_replies": "exec_handle_replies",
            "draft_reply": "exec_draft_reply",
            "send_approved_outreach": "exec_send_approved_outreach",
        },
    },
    "executive-assistant": {
        "name": "Executive Assistant",
        "risk": "low",
        "tasks": {
            "schedule_demos": "exec_schedule_demos",
            "manage_appointments": "exec_manage_appointments",
            "prepare_demo_brief": "exec_prepare_demo_brief",
        },
    },
    "sales-manager": {
        "name": "Sales Manager",
        "risk": "low",
        "tasks": {
            "prioritize_pipeline": "exec_prioritize_pipeline",
            "track_outcomes": "exec_track_outcomes",
            "generate_report": "exec_generate_report",
            "detect_bottlenecks": "exec_detect_bottlenecks",
        },
    },
}

# task types that always require an approval checkpoint before execution
APPROVAL_REQUIRED = {"send_approved_outreach"}
# task types that must never run autonomously regardless of mode
NEVER_AUTONOMOUS = {"send_approved_outreach", "draft_campaign"}


def agent_supported(slug: str, task_type: str) -> bool:
    return task_type in CAPABILITIES.get(slug, {}).get("tasks", {})


def executor_for(slug: str, task_type: str) -> str | None:
    return CAPABILITIES.get(slug, {}).get("tasks", {}).get(task_type)


def registry() -> dict:
    return {slug: {"name": c["name"], "risk": c["risk"],
                   "tasks": sorted(c["tasks"].keys())}
            for slug, c in CAPABILITIES.items()}


def requires_approval(task_type: str) -> bool:
    return task_type in APPROVAL_REQUIRED
