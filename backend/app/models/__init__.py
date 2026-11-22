import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


JSONVariant = JSON().with_variant(JSONB(), "postgresql")


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------- enums


class UserRole(str, enum.Enum):
    owner = "owner"
    admin = "admin"
    member = "member"
    viewer = "viewer"


class AgentStatus(str, enum.Enum):
    active = "active"
    idle = "idle"
    paused = "paused"
    error = "error"


class LeadStage(str, enum.Enum):
    discovered = "discovered"
    researched = "researched"
    qualified = "qualified"
    approval_pending = "approval_pending"
    contacted = "contacted"
    replied = "replied"
    interested = "interested"
    demo_scheduled = "demo_scheduled"
    negotiation = "negotiation"
    won = "won"
    lost = "lost"
    do_not_contact = "do_not_contact"


class QualificationStatus(str, enum.Enum):
    unqualified = "unqualified"
    researching = "researching"
    qualified = "qualified"
    disqualified = "disqualified"


class Channel(str, enum.Enum):
    email = "email"
    whatsapp = "whatsapp"


class MessageDirection(str, enum.Enum):
    inbound = "inbound"
    outbound = "outbound"


class MessageStatus(str, enum.Enum):
    draft = "draft"
    pending_approval = "pending_approval"
    sent = "sent"
    delivered = "delivered"
    read = "read"
    failed = "failed"


class TaskPriority(str, enum.Enum):
    low = "low"
    medium = "medium"
    high = "high"
    urgent = "urgent"


class TaskStatus(str, enum.Enum):
    pending = "pending"
    in_progress = "in_progress"
    blocked = "blocked"
    completed = "completed"
    cancelled = "cancelled"


class AssigneeType(str, enum.Enum):
    agent = "agent"
    human = "human"


class ApprovalActionType(str, enum.Enum):
    send_email = "send_email"
    send_whatsapp = "send_whatsapp"
    schedule_meeting = "schedule_meeting"
    offer_discount = "offer_discount"
    change_pricing = "change_pricing"
    send_campaign = "send_campaign"
    custom_action = "custom_action"


class RiskLevel(str, enum.Enum):
    low = "low"
    medium = "medium"
    high = "high"


class ApprovalStatus(str, enum.Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"
    expired = "expired"
    cancelled = "cancelled"


class NotificationType(str, enum.Enum):
    new_qualified_lead = "new_qualified_lead"
    new_reply = "new_reply"
    interested_prospect = "interested_prospect"
    demo_request = "demo_request"
    pending_approval = "pending_approval"
    upcoming_meeting = "upcoming_meeting"
    won_deal = "won_deal"
    agent_error = "agent_error"
    system = "system"


class ActorType(str, enum.Enum):
    user = "user"
    agent = "agent"
    system = "system"


class RunStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    awaiting_approval = "awaiting_approval"


class ConsentStatus(str, enum.Enum):
    pending = "pending"
    granted = "granted"
    withdrawn = "withdrawn"


class KnowledgeCategory(str, enum.Enum):
    overview = "overview"
    features = "features"
    pricing = "pricing"
    plans = "plans"
    trial_policy = "trial_policy"
    faq = "faq"
    objection = "objection"
    demo_links = "demo_links"
    screenshots = "screenshots"
    other = "other"


class OpportunityStatus(str, enum.Enum):
    open = "open"
    won = "won"
    lost = "lost"


# ---------------------------------------------------------------- scheduling (Phase 5)


class AppointmentType(str, enum.Enum):
    product_demo = "product_demo"
    discovery_call = "discovery_call"
    sales_consultation = "sales_consultation"
    follow_up = "follow_up"
    onboarding = "onboarding"
    internal = "internal"


class AppointmentStatus(str, enum.Enum):
    proposed = "proposed"
    awaiting_confirmation = "awaiting_confirmation"
    confirmed = "confirmed"
    rescheduling_requested = "rescheduling_requested"
    cancelled = "cancelled"
    completed = "completed"
    no_show = "no_show"


class CalendarSyncStatus(str, enum.Enum):
    not_configured = "not_configured"
    pending = "pending"
    synced = "synced"
    failed = "failed"


class ReminderStatus(str, enum.Enum):
    pending = "pending"
    sent = "sent"
    internal_notified = "internal_notified"
    failed = "failed"
    cancelled = "cancelled"


# ---------------------------------------------------------------- mixins


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
        server_default=func.now(),
        nullable=False,
    )


class WorkspaceMixin:
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )


# ---------------------------------------------------------------- core


class Workspace(Base, TimestampMixin):
    __tablename__ = "workspaces"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    settings: Mapped[dict] = mapped_column(JSONVariant, default=dict, nullable=False)

    users: Mapped[list["User"]] = relationship(back_populates="workspace", cascade="all, delete-orphan")


class User(Base, TimestampMixin, WorkspaceMixin):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(500), nullable=False)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    role: Mapped[UserRole] = mapped_column(Enum(UserRole), default=UserRole.owner, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    workspace: Mapped[Workspace] = relationship(back_populates="users")


# ---------------------------------------------------------------- agents


class Agent(Base, TimestampMixin, WorkspaceMixin):
    __tablename__ = "agents"
    __table_args__ = (UniqueConstraint("workspace_id", "slug", name="uq_agent_workspace_slug"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    avatar_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False, default="")
    tools: Mapped[list] = mapped_column(JSONVariant, default=list, nullable=False)
    permissions: Mapped[dict] = mapped_column(JSONVariant, default=dict, nullable=False)
    config: Mapped[dict] = mapped_column(JSONVariant, default=dict, nullable=False)
    status: Mapped[AgentStatus] = mapped_column(Enum(AgentStatus), default=AgentStatus.idle, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    current_workload: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tasks_completed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_activity_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    performance: Mapped[dict] = mapped_column(JSONVariant, default=dict, nullable=False)

    runs: Mapped[list["AgentRun"]] = relationship(back_populates="agent", cascade="all, delete-orphan")


class AgentRun(Base):
    __tablename__ = "agent_runs"
    __table_args__ = (Index("ix_agent_runs_workspace_agent", "workspace_id", "agent_id"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=_uuid)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[RunStatus] = mapped_column(Enum(RunStatus), default=RunStatus.queued, nullable=False)
    input_payload: Mapped[dict] = mapped_column(JSONVariant, default=dict, nullable=False)
    output_payload: Mapped[dict] = mapped_column(JSONVariant, default=dict, nullable=False)
    tokens_prompt: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tokens_completion: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    )

    agent: Mapped[Agent] = relationship(back_populates="runs")


# ---------------------------------------------------------------- CRM


class LeadSource(Base, TimestampMixin, WorkspaceMixin):
    __tablename__ = "lead_sources"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    source_type: Mapped[str] = mapped_column(String(50), nullable=False, default="manual")


class Lead(Base, TimestampMixin, WorkspaceMixin):
    __tablename__ = "leads"
    __table_args__ = (
        Index("ix_leads_workspace_stage", "workspace_id", "stage"),
        Index("ix_leads_workspace_score", "workspace_id", "lead_score"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=_uuid)
    business_name: Mapped[str] = mapped_column(String(300), nullable=False)
    business_type: Mapped[str] = mapped_column(String(100), nullable=False, default="cafe")
    website: Mapped[str | None] = mapped_column(String(500), nullable=True)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    city: Mapped[str | None] = mapped_column(String(120), nullable=True)
    state: Mapped[str | None] = mapped_column(String(120), nullable=True)
    country: Mapped[str | None] = mapped_column(String(120), nullable=True)
    contact_person: Mapped[str | None] = mapped_column(String(200), nullable=True)
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("lead_sources.id", ondelete="SET NULL"), nullable=True
    )
    source: Mapped[str] = mapped_column(String(100), nullable=False, default="manual")
    lead_score: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    qualification_status: Mapped[QualificationStatus] = mapped_column(
        Enum(QualificationStatus), default=QualificationStatus.unqualified, nullable=False
    )
    stage: Mapped[LeadStage] = mapped_column(Enum(LeadStage), default=LeadStage.discovered, nullable=False)
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    estimated_value: Mapped[float] = mapped_column(Numeric(12, 2), default=0, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="USD", nullable=False)
    last_contacted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    contacts: Mapped[list["Contact"]] = relationship(
        back_populates="lead", cascade="all, delete-orphan", foreign_keys="Contact.lead_id"
    )
    activities: Mapped[list["Activity"]] = relationship(
        back_populates="lead", cascade="all, delete-orphan"
    )


class Contact(Base, TimestampMixin, WorkspaceMixin):
    __tablename__ = "contacts"
    __table_args__ = (Index("ix_contacts_workspace_lead", "workspace_id", "lead_id"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=_uuid)
    lead_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("leads.id", ondelete="CASCADE"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    role_title: Mapped[str | None] = mapped_column(String(120), nullable=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    lead: Mapped[Lead | None] = relationship(back_populates="contacts", foreign_keys=[lead_id])


class Opportunity(Base, TimestampMixin, WorkspaceMixin):
    __tablename__ = "opportunities"
    __table_args__ = (Index("ix_opportunities_workspace_status", "workspace_id", "status"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=_uuid)
    lead_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("leads.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    stage: Mapped[LeadStage] = mapped_column(Enum(LeadStage), default=LeadStage.discovered, nullable=False)
    value: Mapped[float] = mapped_column(Numeric(12, 2), default=0, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="USD", nullable=False)
    probability: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    expected_close_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[OpportunityStatus] = mapped_column(
        Enum(OpportunityStatus), default=OpportunityStatus.open, nullable=False
    )


# ---------------------------------------------------------------- comms


class Conversation(Base, TimestampMixin, WorkspaceMixin):
    __tablename__ = "conversations"
    __table_args__ = (Index("ix_conversations_workspace_lead", "workspace_id", "lead_id"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=_uuid)
    lead_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("leads.id", ondelete="CASCADE"), nullable=False
    )
    channel: Mapped[Channel] = mapped_column(Enum(Channel), nullable=False)
    external_thread_id: Mapped[str | None] = mapped_column(String(300), nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="open", nullable=False)
    state: Mapped[str] = mapped_column(String(50), default="new", nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    last_intent: Mapped[str | None] = mapped_column(String(60), nullable=True)
    needs_human: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    lead: Mapped[Lead] = relationship()
    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan", order_by="Message.created_at"
    )


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (Index("ix_messages_workspace_conversation", "workspace_id", "conversation_id"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=_uuid)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    direction: Mapped[MessageDirection] = mapped_column(Enum(MessageDirection), nullable=False)
    channel: Mapped[Channel] = mapped_column(Enum(Channel), nullable=False)
    subject: Mapped[str | None] = mapped_column(String(300), nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[MessageStatus] = mapped_column(
        Enum(MessageStatus), default=MessageStatus.draft, nullable=False
    )
    is_ai_draft: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    approval_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("approvals.id", ondelete="SET NULL"), nullable=True
    )
    # provider-side identifier (Resend email id / Meta wamid) for lifecycle
    # webhook correlation. Never contains message content.
    provider_message_id: Mapped[str | None] = mapped_column(String(300), nullable=True, index=True)
    provider_delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    provider_read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    provider_failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    provider_error: Mapped[str | None] = mapped_column(String(300), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    )

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


# ---------------------------------------------------------------- work items


class Task(Base, TimestampMixin, WorkspaceMixin):
    __tablename__ = "tasks"
    __table_args__ = (Index("ix_tasks_workspace_status", "workspace_id", "status"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=_uuid)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    assignee_type: Mapped[AssigneeType] = mapped_column(Enum(AssigneeType), nullable=False)
    assignee_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("agents.id", ondelete="SET NULL"), nullable=True
    )
    assignee_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    priority: Mapped[TaskPriority] = mapped_column(Enum(TaskPriority), default=TaskPriority.medium, nullable=False)
    status: Mapped[TaskStatus] = mapped_column(Enum(TaskStatus), default=TaskStatus.pending, nullable=False)
    due_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lead_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("leads.id", ondelete="SET NULL"), nullable=True
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    lead: Mapped[Lead | None] = relationship()


class Approval(Base, TimestampMixin, WorkspaceMixin):
    __tablename__ = "approvals"
    __table_args__ = (Index("ix_approvals_workspace_status", "workspace_id", "status"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=_uuid)
    action_type: Mapped[ApprovalActionType] = mapped_column(Enum(ApprovalActionType), nullable=False)
    risk_level: Mapped[RiskLevel] = mapped_column(Enum(RiskLevel), default=RiskLevel.high, nullable=False)
    status: Mapped[ApprovalStatus] = mapped_column(
        Enum(ApprovalStatus), default=ApprovalStatus.pending, nullable=False
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONVariant, default=dict, nullable=False)
    requested_by_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("agents.id", ondelete="SET NULL"), nullable=True
    )
    requested_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reviewed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    lead_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("leads.id", ondelete="SET NULL"), nullable=True
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    lead: Mapped[Lead | None] = relationship()


class Activity(Base):
    __tablename__ = "activities"
    __table_args__ = (Index("ix_activities_workspace_created", "workspace_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=_uuid)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    lead_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("leads.id", ondelete="CASCADE"), nullable=True
    )
    actor_type: Mapped[ActorType] = mapped_column(Enum(ActorType), default=ActorType.system, nullable=False)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    metadata_json: Mapped[dict] = mapped_column("metadata", JSONVariant, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    )

    lead: Mapped[Lead | None] = relationship(back_populates="activities")


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (Index("ix_notifications_workspace_user_read", "workspace_id", "user_id", "is_read"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=_uuid)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    type: Mapped[NotificationType] = mapped_column(Enum(NotificationType), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    link: Mapped[str | None] = mapped_column(String(500), nullable=True)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    )


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_logs_workspace_created", "workspace_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=_uuid)
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("workspaces.id", ondelete="SET NULL"), nullable=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    )


# ---------------------------------------------------------------- product knowledge


class Product(Base, TimestampMixin, WorkspaceMixin):
    __tablename__ = "products"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    slug: Mapped[str] = mapped_column(String(150), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    knowledge: Mapped[list["ProductKnowledge"]] = relationship(
        back_populates="product", cascade="all, delete-orphan"
    )


class ProductKnowledge(Base, TimestampMixin, WorkspaceMixin):
    __tablename__ = "product_knowledge"
    __table_args__ = (Index("ix_product_knowledge_workspace_category", "workspace_id", "category"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=_uuid)
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("products.id", ondelete="CASCADE"), nullable=True
    )
    category: Mapped[KnowledgeCategory] = mapped_column(
        Enum(KnowledgeCategory), default=KnowledgeCategory.other, nullable=False
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    product: Mapped[Product | None] = relationship(back_populates="knowledge")


# ---------------------------------------------------------------- compliance


class ChannelConsent(Base, TimestampMixin, WorkspaceMixin):
    __tablename__ = "channel_consents"
    __table_args__ = (
        UniqueConstraint("workspace_id", "channel", "value", name="uq_consent_workspace_channel_value"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=_uuid)
    channel: Mapped[Channel] = mapped_column(Enum(Channel), nullable=False)
    value: Mapped[str] = mapped_column(String(320), nullable=False)
    lead_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("leads.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[ConsentStatus] = mapped_column(Enum(ConsentStatus), default=ConsentStatus.pending, nullable=False)
    source: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    consented_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SuppressionRecord(Base):
    __tablename__ = "suppression_records"
    __table_args__ = (
        UniqueConstraint("workspace_id", "channel", "value", name="uq_suppression_workspace_channel_value"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=_uuid)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    channel: Mapped[Channel] = mapped_column(Enum(Channel), nullable=False)
    value: Mapped[str] = mapped_column(String(320), nullable=False)
    reason: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    )


class MessageTemplate(Base, TimestampMixin, WorkspaceMixin):
    __tablename__ = "message_templates"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    channel: Mapped[Channel] = mapped_column(Enum(Channel), nullable=False)
    subject: Mapped[str | None] = mapped_column(String(300), nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    language: Mapped[str] = mapped_column(String(10), default="en", nullable=False)
    provider_template_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    provider_status: Mapped[str] = mapped_column(String(50), default="draft", nullable=False)
    provider_category: Mapped[str | None] = mapped_column(String(50), nullable=True)
    components: Mapped[dict] = mapped_column(JSONVariant, default=dict, nullable=False)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# ---------------------------------------------------------------- research (Phase 2)


class ResearchJobStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class ResearchResultStatus(str, enum.Enum):
    pending = "pending"
    researched = "researched"
    imported = "imported"
    failed = "failed"
    duplicate = "duplicate"
    skipped = "skipped"


class ResearchJob(Base, TimestampMixin, WorkspaceMixin):
    __tablename__ = "research_jobs"
    __table_args__ = (Index("ix_research_jobs_workspace_status", "workspace_id", "status"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=_uuid)
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("agents.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[ResearchJobStatus] = mapped_column(
        Enum(ResearchJobStatus), default=ResearchJobStatus.queued, nullable=False
    )
    config: Mapped[dict] = mapped_column(JSONVariant, default=dict, nullable=False)
    stats: Mapped[dict] = mapped_column(JSONVariant, default=dict, nullable=False)
    progress: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ResearchResult(Base):
    __tablename__ = "research_results"
    __table_args__ = (
        Index("ix_research_results_workspace_job", "workspace_id", "job_id"),
        Index("ix_research_results_workspace_name", "workspace_id", "business_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=_uuid)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("research_jobs.id", ondelete="CASCADE"), nullable=False
    )
    lead_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("leads.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[ResearchResultStatus] = mapped_column(
        Enum(ResearchResultStatus), default=ResearchResultStatus.pending, nullable=False
    )
    # business fields
    business_name: Mapped[str] = mapped_column(String(300), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    business_category: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    address: Mapped[str] = mapped_column(Text, nullable=False, default="")
    city: Mapped[str | None] = mapped_column(String(120), nullable=True)
    state: Mapped[str | None] = mapped_column(String(120), nullable=True)
    country: Mapped[str | None] = mapped_column(String(120), nullable=True)
    postal_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    latitude: Mapped[float | None] = mapped_column(Numeric(10, 7), nullable=True)
    longitude: Mapped[float | None] = mapped_column(Numeric(10, 7), nullable=True)
    website: Mapped[str | None] = mapped_column(String(500), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    instagram: Mapped[str | None] = mapped_column(String(300), nullable=True)
    facebook: Mapped[str | None] = mapped_column(String(300), nullable=True)
    whatsapp: Mapped[str | None] = mapped_column(String(50), nullable=True)
    is_operating: Mapped[str | None] = mapped_column(String(20), nullable=True)  # yes/no/unknown
    uses_online_ordering: Mapped[str | None] = mapped_column(String(20), nullable=True)
    uses_digital_menu: Mapped[str | None] = mapped_column(String(20), nullable=True)
    has_reservation_system: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # provenance & scoring
    source_provider: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    source_ref: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    evidence: Mapped[list] = mapped_column(JSONVariant, default=list, nullable=False)
    score: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    score_breakdown: Mapped[dict] = mapped_column(JSONVariant, default=dict, nullable=False)
    score_explanation: Mapped[str] = mapped_column(Text, nullable=False, default="")
    missing_info: Mapped[list] = mapped_column(JSONVariant, default=list, nullable=False)
    research_notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    duplicate_of_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("research_results.id", ondelete="SET NULL"), nullable=True
    )
    duplicate_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    lead_score_written: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    discovered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    )
    researched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    )


# ---------------------------------------------------------------- comms engine (Phase 3)


class FollowUpStatus(str, enum.Enum):
    scheduled = "scheduled"
    sent = "sent"
    cancelled = "cancelled"
    completed = "completed"
    failed = "failed"
    skipped = "skipped"


class FollowUp(Base, TimestampMixin):
    __tablename__ = "follow_ups"
    __table_args__ = (Index("ix_follow_ups_due", "status", "scheduled_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=_uuid)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    lead_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("leads.id", ondelete="SET NULL"), nullable=True
    )
    channel: Mapped[Channel] = mapped_column(Enum(Channel), nullable=False)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    interval_hours: Mapped[int] = mapped_column(Integer, default=48, nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    status: Mapped[FollowUpStatus] = mapped_column(
        Enum(FollowUpStatus), default=FollowUpStatus.scheduled, nullable=False
    )
    subject: Mapped[str | None] = mapped_column(String(300), nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WebhookEvent(Base):
    __tablename__ = "webhook_events"
    __table_args__ = (
        UniqueConstraint("provider", "event_id", name="uq_webhook_provider_event"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=_uuid)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    event_id: Mapped[str] = mapped_column(String(300), nullable=False)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    payload: Mapped[dict] = mapped_column(JSONVariant, default=dict, nullable=False)
    processed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    )


class CampaignStatus(str, enum.Enum):
    draft = "draft"
    authorized = "authorized"
    sending = "sending"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class Campaign(Base, TimestampMixin, WorkspaceMixin):
    __tablename__ = "campaigns"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    status: Mapped[CampaignStatus] = mapped_column(
        Enum(CampaignStatus), default=CampaignStatus.draft, nullable=False
    )
    channel: Mapped[Channel] = mapped_column(Enum(Channel), default=Channel.email, nullable=False)
    lead_ids: Mapped[list] = mapped_column(JSONVariant, default=list, nullable=False)
    instructions: Mapped[str] = mapped_column(Text, nullable=False, default="")
    stats: Mapped[dict] = mapped_column(JSONVariant, default=dict, nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    authorized_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    authorized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# ---------------------------------------------------------------- scheduling (Phase 5)


class SchedulingSettings(Base, TimestampMixin, WorkspaceMixin):
    """Per-workspace availability & scheduling configuration (one row per workspace)."""

    __tablename__ = "scheduling_settings"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=_uuid)
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Kolkata", nullable=False)
    # ISO weekday numbers, Monday=0 .. Sunday=6
    working_days: Mapped[list] = mapped_column(JSONVariant, default=lambda: [0, 1, 2, 3, 4], nullable=False)
    work_start: Mapped[str] = mapped_column(String(5), default="09:00", nullable=False)
    work_end: Mapped[str] = mapped_column(String(5), default="18:00", nullable=False)
    lunch_start: Mapped[str | None] = mapped_column(String(5), nullable=True)
    lunch_end: Mapped[str | None] = mapped_column(String(5), nullable=True)
    slot_minutes: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    buffer_minutes: Mapped[int] = mapped_column(Integer, default=15, nullable=False)
    min_notice_hours: Mapped[int] = mapped_column(Integer, default=2, nullable=False)
    max_horizon_days: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    # ISO date strings ("YYYY-MM-DD") and {"start": iso, "end": iso} blackout windows
    holidays: Mapped[list] = mapped_column(JSONVariant, default=list, nullable=False)
    blackout_windows: Mapped[list] = mapped_column(JSONVariant, default=list, nullable=False)
    # minutes before start at which reminders fire, e.g. [1440, 60]
    reminder_offsets_minutes: Mapped[list] = mapped_column(
        JSONVariant, default=lambda: [1440, 60], nullable=False
    )
    default_duration_minutes: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    require_confirmation: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    allow_autonomous_booking: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    calendar_provider: Mapped[str] = mapped_column(String(50), default="internal", nullable=False)


class Appointment(Base, TimestampMixin, WorkspaceMixin):
    __tablename__ = "appointments"
    __table_args__ = (
        Index("ix_appointments_workspace_start", "workspace_id", "start_at"),
        Index("ix_appointments_workspace_status", "workspace_id", "status"),
        UniqueConstraint("workspace_id", "idempotency_key", name="uq_appointment_idempotency"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=_uuid)
    lead_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("leads.id", ondelete="CASCADE"), nullable=False, index=True
    )
    contact_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("contacts.id", ondelete="SET NULL"), nullable=True
    )
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True
    )
    appointment_type: Mapped[AppointmentType] = mapped_column(
        Enum(AppointmentType), default=AppointmentType.product_demo, nullable=False
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # stored in UTC; `timezone` records the workspace tz used for display
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Kolkata", nullable=False)
    location: Mapped[str | None] = mapped_column(String(500), nullable=True)
    meeting_link: Mapped[str | None] = mapped_column(String(500), nullable=True)
    assigned_to: Mapped[str] = mapped_column(String(200), default="primary", nullable=False)
    status: Mapped[AppointmentStatus] = mapped_column(
        Enum(AppointmentStatus), default=AppointmentStatus.proposed, nullable=False
    )
    proposed_slots: Mapped[list] = mapped_column(JSONVariant, default=list, nullable=False)
    reschedule_history: Mapped[list] = mapped_column(JSONVariant, default=list, nullable=False)
    cancellation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    cancelled_by: Mapped[str | None] = mapped_column(String(50), nullable=True)  # user|agent|prospect
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("agents.id", ondelete="SET NULL"), nullable=True
    )
    source: Mapped[str] = mapped_column(String(50), default="manual", nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # external calendar sync state
    calendar_provider: Mapped[str] = mapped_column(String(50), default="internal", nullable=False)
    external_event_id: Mapped[str | None] = mapped_column(String(300), nullable=True)
    calendar_sync: Mapped[CalendarSyncStatus] = mapped_column(
        Enum(CalendarSyncStatus), default=CalendarSyncStatus.not_configured, nullable=False
    )
    sync_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # DB-level overlap protection (Phase 8.8): the interval this appointment
    # occupies INCLUDING the workspace buffer at write time. On PostgreSQL an
    # exclusion constraint over tstzrange(block_start, block_end) prevents
    # concurrent double-booking; SQLite relies on the application check.
    block_start: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    block_end: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)

    lead: Mapped[Lead] = relationship()
    reminders: Mapped[list["AppointmentReminder"]] = relationship(
        back_populates="appointment", cascade="all, delete-orphan"
    )


class AppointmentReminder(Base):
    __tablename__ = "appointment_reminders"
    __table_args__ = (
        UniqueConstraint("appointment_id", "remind_at", "channel",
                         name="uq_reminder_dedupe"),
        Index("ix_reminders_due", "status", "remind_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=_uuid)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    appointment_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("appointments.id", ondelete="CASCADE"), nullable=False
    )
    remind_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    channel: Mapped[str] = mapped_column(String(20), default="internal", nullable=False)
    status: Mapped[ReminderStatus] = mapped_column(
        Enum(ReminderStatus), default=ReminderStatus.pending, nullable=False
    )
    delivery_detail: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    )

    appointment: Mapped[Appointment] = relationship(back_populates="reminders")


# ---------------------------------------------------------------- sales management (Phase 6)


class IssueSeverity(str, enum.Enum):
    informational = "informational"
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"


class IssueStatus(str, enum.Enum):
    open = "open"
    acknowledged = "acknowledged"
    resolved = "resolved"
    dismissed = "dismissed"


class RecommendationStatus(str, enum.Enum):
    new = "new"
    accepted = "accepted"
    dismissed = "dismissed"
    completed = "completed"


class ReportType(str, enum.Enum):
    daily = "daily"
    weekly = "weekly"


class AnalysisRunStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    skipped = "skipped"


class SalesIssue(Base, TimestampMixin):
    """Bottleneck/alert record produced by the rule-based detection engine."""
    __tablename__ = "sales_issues"
    __table_args__ = (
        Index("ix_sales_issues_workspace_status", "workspace_id", "status"),
        Index("ix_sales_issues_dedupe", "workspace_id", "issue_type", "fingerprint"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=_uuid)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    issue_type: Mapped[str] = mapped_column(String(80), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    severity: Mapped[IssueSeverity] = mapped_column(
        Enum(IssueSeverity), default=IssueSeverity.medium, nullable=False
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=False, default="")
    agent_slug: Mapped[str | None] = mapped_column(String(100), nullable=True)
    workflow: Mapped[str | None] = mapped_column(String(100), nullable=True)
    evidence: Mapped[dict] = mapped_column(JSONVariant, default=dict, nullable=False)
    suggested_resolution: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[IssueStatus] = mapped_column(
        Enum(IssueStatus), default=IssueStatus.open, nullable=False
    )
    first_detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    last_detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Recommendation(Base, TimestampMixin):
    __tablename__ = "recommendations"
    __table_args__ = (
        Index("ix_recommendations_workspace_status", "workspace_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=_uuid)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False, default="")
    supporting_data: Mapped[dict] = mapped_column(JSONVariant, default=dict, nullable=False)
    expected_benefit: Mapped[str] = mapped_column(Text, nullable=False, default="")
    priority: Mapped[TaskPriority] = mapped_column(
        Enum(TaskPriority), default=TaskPriority.medium, nullable=False
    )
    suggested_action: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # optional structured task spec created when the owner accepts
    task_spec: Mapped[dict] = mapped_column(JSONVariant, default=dict, nullable=False)
    related_records: Mapped[dict] = mapped_column(JSONVariant, default=dict, nullable=False)
    confidence: Mapped[str] = mapped_column(String(20), default="medium", nullable=False)
    source: Mapped[str] = mapped_column(String(50), default="rules", nullable=False)
    status: Mapped[RecommendationStatus] = mapped_column(
        Enum(RecommendationStatus), default=RecommendationStatus.new, nullable=False
    )
    issue_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("sales_issues.id", ondelete="SET NULL"), nullable=True
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ManagementReport(Base):
    __tablename__ = "management_reports"
    __table_args__ = (
        UniqueConstraint("workspace_id", "report_type", "period_start",
                         name="uq_report_period"),
        Index("ix_reports_workspace", "workspace_id", "generated_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=_uuid)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    report_type: Mapped[ReportType] = mapped_column(Enum(ReportType), nullable=False)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONVariant, default=dict, nullable=False)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    generated_by: Mapped[str] = mapped_column(String(50), default="system", nullable=False)


class AnalysisRun(Base):
    """Idempotency ledger for background analysis jobs (one per workspace/kind/period)."""
    __tablename__ = "analysis_runs"
    __table_args__ = (
        UniqueConstraint("workspace_id", "kind", "period_key",
                         name="uq_analysis_run_period"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=_uuid)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(50), nullable=False)   # scan|daily_report|weekly_report|intelligence
    period_key: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[AnalysisRunStatus] = mapped_column(
        Enum(AnalysisRunStatus), default=AnalysisRunStatus.queued, nullable=False
    )
    stats: Mapped[dict] = mapped_column(JSONVariant, default=dict, nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    )




# ---------------------------------------------------------------- orchestration (Phase 7)


class WorkflowStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    paused = "paused"
    waiting_approval = "waiting_approval"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class WorkflowStepStatus(str, enum.Enum):
    pending = "pending"
    ready = "ready"
    running = "running"
    awaiting_approval = "awaiting_approval"
    completed = "completed"
    failed = "failed"
    skipped = "skipped"


class WorkflowEventStatus(str, enum.Enum):
    pending = "pending"
    processed = "processed"
    failed = "failed"


class Workflow(Base, TimestampMixin, WorkspaceMixin):
    """Persistent orchestration workflow."""
    __tablename__ = "workflows"
    __table_args__ = (
        Index("ix_workflows_workspace_status", "workspace_id", "status"),
        Index("ix_workflows_recovery", "status", "updated_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=_uuid)
    workflow_type: Mapped[str] = mapped_column(String(80), default="cafe_acquisition", nullable=False)
    objective: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[WorkflowStatus] = mapped_column(
        Enum(WorkflowStatus), default=WorkflowStatus.queued, nullable=False
    )
    plan: Mapped[dict] = mapped_column(JSONVariant, default=dict, nullable=False)
    context: Mapped[dict] = mapped_column(JSONVariant, default=dict, nullable=False)
    limits: Mapped[dict] = mapped_column(JSONVariant, default=dict, nullable=False)
    autonomy_mode: Mapped[str] = mapped_column(String(20), default="hybrid", nullable=False)
    llm_calls_used: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    current_step: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str] = mapped_column(String(200), default="system", nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    steps: Mapped[list["WorkflowStep"]] = relationship(
        back_populates="workflow", cascade="all, delete-orphan",
        order_by="WorkflowStep.sequence")


class WorkflowStep(Base, TimestampMixin):
    __tablename__ = "workflow_steps"
    __table_args__ = (
        Index("ix_workflow_steps_workflow", "workflow_id"),
        Index("ix_workflow_steps_status", "status"),
        UniqueConstraint("workflow_id", "step_key", name="uq_step_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=_uuid)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False, index=True)
    workflow_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("workflows.id", ondelete="CASCADE"), nullable=False)
    step_key: Mapped[str] = mapped_column(String(120), nullable=False)
    agent_slug: Mapped[str] = mapped_column(String(100), nullable=False)
    task_type: Mapped[str] = mapped_column(String(100), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    depends_on: Mapped[list] = mapped_column(JSONVariant, default=list, nullable=False)
    parallel_group: Mapped[str | None] = mapped_column(String(80), nullable=True)
    status: Mapped[WorkflowStepStatus] = mapped_column(
        Enum(WorkflowStepStatus), default=WorkflowStepStatus.pending, nullable=False)
    input: Mapped[dict] = mapped_column("input_data", JSONVariant, default=dict, nullable=False)
    output: Mapped[dict] = mapped_column(JSONVariant, default=dict, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=2, nullable=False)
    approval_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("approvals.id", ondelete="SET NULL"), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    workflow: Mapped["Workflow"] = relationship(back_populates="steps")


class WorkflowEvent(Base):
    """Database-backed outbox/event log. Idempotent via idempotency_key."""
    __tablename__ = "workflow_events"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_event_idem"),
        Index("ix_events_workspace", "workspace_id", "created_at"),
        Index("ix_events_pending", "status", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=_uuid)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False, index=True)
    workflow_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("workflows.id", ondelete="SET NULL"), nullable=True)
    step_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("workflow_steps.id", ondelete="SET NULL"), nullable=True)
    event_type: Mapped[str] = mapped_column(String(120), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONVariant, default=dict, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[WorkflowEventStatus] = mapped_column(
        Enum(WorkflowEventStatus), default=WorkflowEventStatus.pending, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    )


class LeadStageHistory(Base):
    """Append-only record of every lead stage transition (Phase 8.5)."""
    __tablename__ = "lead_stage_history"
    __table_args__ = (
        Index("ix_stage_history_lead", "lead_id", "created_at"),
        Index("ix_stage_history_workspace", "workspace_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=_uuid)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False, index=True)
    lead_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("leads.id", ondelete="CASCADE"), nullable=False)
    from_stage: Mapped[str] = mapped_column(String(50), nullable=False)
    to_stage: Mapped[str] = mapped_column(String(50), nullable=False)
    actor: Mapped[str] = mapped_column(String(200), default="system", nullable=False)
    source: Mapped[str] = mapped_column(String(50), default="system", nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    metadata_json: Mapped[dict] = mapped_column("metadata", JSONVariant, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    )


class WorkerHeartbeat(Base):
    """Persistent worker liveness tracking (Phase 8.6/8.8)."""
    __tablename__ = "worker_heartbeats"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=_uuid)
    worker_id: Mapped[str] = mapped_column(String(100), nullable=False)
    worker_type: Mapped[str] = mapped_column(String(50), nullable=False)
    host: Mapped[str | None] = mapped_column(String(200), nullable=True)
    pid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="running", nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_heartbeat: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_failure_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_job_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(300), nullable=True)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    current_job: Mapped[str | None] = mapped_column(String(200), nullable=True)
    recovery_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    __table_args__ = (Index("uq_worker_heartbeat", "worker_id", unique=True),)


class LLMInvocation(Base):
    """Actual provider-call metering (Phase 8.8). One row per real LLM
    request — retries and failures count; heuristic fallbacks never do.
    Never stores prompt or response contents."""
    __tablename__ = "llm_invocations"
    __table_args__ = (
        Index("ix_llm_inv_workspace", "workspace_id", "created_at"),
        Index("ix_llm_inv_workflow", "workflow_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=_uuid)
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=True)
    workflow_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("workflows.id", ondelete="SET NULL"), nullable=True)
    step_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("workflow_steps.id", ondelete="SET NULL"), nullable=True)
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("agents.id", ondelete="SET NULL"), nullable=True)
    agent_slug: Mapped[str | None] = mapped_column(String(100), nullable=True)
    provider: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="success")
    tokens_prompt: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tokens_completion: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False)
