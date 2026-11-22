"""Channel consent & suppression enforcement.

Rules enforced before ANY outbound communication is queued or approved:

1. The lead must not be in the `do_not_contact` stage.
2. The contact value must not appear in `suppression_records` for that channel.
3. A `channel_consents` row for (channel, value) must exist with status
   `granted` — or the workspace must explicitly allow first-contact outreach
   policy (handled by callers; this function reports facts, callers decide).

Agents can never bypass these checks: `agent_service.execute_task` calls
`outbound_block_reason` before creating an approval for a send action.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Channel,
    ChannelConsent,
    ConsentStatus,
    Lead,
    LeadStage,
    SuppressionRecord,
)


def is_suppressed(db: Session, workspace_id: uuid.UUID, channel: Channel, value: str) -> bool:
    if not value:
        return False
    return db.scalar(
        select(SuppressionRecord.id).where(
            SuppressionRecord.workspace_id == workspace_id,
            SuppressionRecord.channel == channel,
            SuppressionRecord.value == value.strip().lower(),
        ).limit(1)
    ) is not None


def consent_status(db: Session, workspace_id: uuid.UUID, channel: Channel, value: str) -> ConsentStatus | None:
    if not value:
        return None
    row = db.scalar(
        select(ChannelConsent).where(
            ChannelConsent.workspace_id == workspace_id,
            ChannelConsent.channel == channel,
            ChannelConsent.value == value.strip().lower(),
        ).limit(1)
    )
    return row.status if row else None


def outbound_block_reason(
    db: Session,
    workspace_id: uuid.UUID,
    channel: Channel,
    *,
    value: str | None = None,
    lead_id: uuid.UUID | None = None,
) -> str | None:
    """Return a human-readable reason outbound contact is blocked, else None."""
    lead: Lead | None = None
    if lead_id:
        lead = db.scalar(select(Lead).where(Lead.id == lead_id, Lead.workspace_id == workspace_id))
        if lead is not None:
            if lead.stage == LeadStage.do_not_contact:
                return "Lead is marked do-not-contact"
            if not value:
                value = lead.email if channel == Channel.email else lead.phone

    if value and is_suppressed(db, workspace_id, channel, value):
        return f"{channel.value} address is on the suppression list"

    if value:
        status = consent_status(db, workspace_id, channel, value)
        if status == ConsentStatus.withdrawn:
            return f"Contact withdrew {channel.value} consent"
    return None


def record_consent(
    db: Session,
    workspace_id: uuid.UUID,
    channel: Channel,
    value: str,
    status: ConsentStatus,
    *,
    source: str = "",
    lead_id: uuid.UUID | None = None,
) -> ChannelConsent:
    normalized = value.strip().lower()
    row = db.scalar(
        select(ChannelConsent).where(
            ChannelConsent.workspace_id == workspace_id,
            ChannelConsent.channel == channel,
            ChannelConsent.value == normalized,
        )
    )
    if row is None:
        row = ChannelConsent(workspace_id=workspace_id, channel=channel, value=normalized)
        db.add(row)
    row.status = status
    row.source = source
    row.lead_id = lead_id
    from datetime import datetime, timezone
    row.consented_at = datetime.now(timezone.utc) if status == ConsentStatus.granted else None
    return row
