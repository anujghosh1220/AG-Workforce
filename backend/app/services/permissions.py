"""Agent action permission catalog.

Every action an agent may attempt is classified by risk. High-risk actions
always require human approval; medium-risk actions require approval unless the
agent's permissions explicitly allow autonomous execution; low-risk actions
may run autonomously when the agent is enabled.

An approval record is NEVER an executed action — execution is a separate,
explicitly authorized operation handled by a dedicated action service.
"""

from dataclasses import dataclass

from app.models import Agent, RiskLevel


@dataclass(frozen=True)
class ActionSpec:
    action: str
    risk: RiskLevel
    requires_approval: bool
    description: str


ACTION_CATALOG: dict[str, ActionSpec] = {
    # ---- low risk: internal, reversible, no external side effects
    "research_public_info": ActionSpec("research_public_info", RiskLevel.low, False,
        "Research public business information about a prospect"),
    "update_lead_record": ActionSpec("update_lead_record", RiskLevel.low, False,
        "Update internal lead fields (score, notes, status)"),
    "categorize_reply": ActionSpec("categorize_reply", RiskLevel.low, False,
        "Classify an incoming reply"),
    "summarize_conversation": ActionSpec("summarize_conversation", RiskLevel.low, False,
        "Summarize a conversation thread"),
    "create_internal_task": ActionSpec("create_internal_task", RiskLevel.low, False,
        "Create an internal task"),
    # ---- scheduling (Phase 5): internal availability work is low risk
    "check_availability": ActionSpec("check_availability", RiskLevel.low, False,
        "Check internal appointment availability and propose slots"),
    "propose_appointment_slots": ActionSpec("propose_appointment_slots", RiskLevel.low, False,
        "Create an appointment proposal with candidate time slots"),
    "create_booking_proposal": ActionSpec("create_booking_proposal", RiskLevel.low, False,
        "Create an internal booking proposal record"),
    "generate_meeting_brief": ActionSpec("generate_meeting_brief", RiskLevel.low, False,
        "Generate a meeting brief for an upcoming appointment"),
    # ---- scheduling commitments: bounded but externally visible
    "confirm_appointment": ActionSpec("confirm_appointment", RiskLevel.medium, True,
        "Confirm an appointment booking"),
    "reschedule_appointment": ActionSpec("reschedule_appointment", RiskLevel.medium, True,
        "Reschedule an existing appointment"),
    "cancel_appointment": ActionSpec("cancel_appointment", RiskLevel.medium, True,
        "Cancel an existing appointment"),
    "send_booking_confirmation": ActionSpec("send_booking_confirmation", RiskLevel.medium, True,
        "Send an appointment confirmation to the prospect"),
    # ---- high risk scheduling actions
    "create_calendar_event": ActionSpec("create_calendar_event", RiskLevel.high, True,
        "Create an event in an external calendar provider"),
    "commit_appointment_terms": ActionSpec("commit_appointment_terms", RiskLevel.high, True,
        "Commit to appointment terms outside normal availability"),
    # ---- medium risk: external-ish but bounded
    "send_approved_followup": ActionSpec("send_approved_followup", RiskLevel.medium, True,
        "Send a follow-up message using an already-approved template"),
    "schedule_reminder": ActionSpec("schedule_reminder", RiskLevel.medium, True,
        "Schedule an internal reminder or permitted notification"),
    "update_prospect_status": ActionSpec("update_prospect_status", RiskLevel.medium, True,
        "Move a prospect to a new pipeline stage"),
    # ---- high risk: external commitments / commercial terms
    "initiate_first_contact": ActionSpec("initiate_first_contact", RiskLevel.high, True,
        "Initiate first-contact outreach to a prospect"),
    "send_whatsapp_business": ActionSpec("send_whatsapp_business", RiskLevel.high, True,
        "Send a WhatsApp Business-initiated message"),
    "offer_discount": ActionSpec("offer_discount", RiskLevel.high, True,
        "Offer a discount to a prospect"),
    "change_pricing": ActionSpec("change_pricing", RiskLevel.high, True,
        "Change pricing presented to a prospect"),
    "contractual_promise": ActionSpec("contractual_promise", RiskLevel.high, True,
        "Make a contractual promise or commit to commercial terms"),
    "commit_meeting_terms": ActionSpec("commit_meeting_terms", RiskLevel.high, True,
        "Commit to meetings or commercial terms outside approved rules"),
}

# Backwards-compatible aliases used by earlier code / approval action types.
ACTION_ALIASES = {
    "send_email": "send_approved_followup",
    "send_whatsapp": "send_whatsapp_business",
    "schedule_meeting": "commit_meeting_terms",
}


def canonical_action(action: str) -> str:
    return ACTION_ALIASES.get(action, action)


def spec_for(action: str) -> ActionSpec | None:
    return ACTION_CATALOG.get(canonical_action(action))


def requires_approval(agent: Agent, action: str) -> bool:
    """Decide whether `action` by `agent` needs human approval.

    High risk  -> always approval.
    Medium     -> approval unless agent permissions set requires_approval=false
                  for this action class (explicit opt-in autonomy).
    Low/unknown-> approval only if the agent's permissions demand it.
    """
    spec = spec_for(action)
    perms = agent.permissions or {}
    if perms.get("autonomous") is True and (spec is None or spec.risk != RiskLevel.high):
        return False
    if spec is None:
        return bool(perms.get("requires_approval", True))
    if spec.risk == RiskLevel.high:
        return True
    if spec.risk == RiskLevel.medium:
        allowed = perms.get("autonomous_actions") or []
        return action not in allowed and perms.get("requires_approval", True)
    # low risk: internal/reversible — only gated if explicitly listed
    always = perms.get("approval_required_actions") or []
    return action in always


def catalog_public() -> list[dict]:
    return [
        {"action": s.action, "risk": s.risk.value, "requires_approval": s.requires_approval,
         "description": s.description}
        for s in ACTION_CATALOG.values()
    ]
