import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models import (
    ActorType,
    AgentStatus,
    AppointmentStatus,
    AppointmentType,
    ApprovalActionType,
    ApprovalStatus,
    AssigneeType,
    Channel,
    ConsentStatus,
    KnowledgeCategory,
    LeadStage,
    MessageDirection,
    MessageStatus,
    NotificationType,
    OpportunityStatus,
    RiskLevel,
    QualificationStatus,
    RunStatus,
    TaskPriority,
    TaskStatus,
    UserRole,
)


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ------------------------------------------------------------ auth / workspace


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=200)
    full_name: str = Field(default="", max_length=200)
    workspace_name: str = Field(default="My Workspace", max_length=200)
    invite_code: str | None = Field(default=None, max_length=200)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserOut(ORMModel):
    id: uuid.UUID
    email: str
    full_name: str
    role: UserRole
    is_active: bool
    workspace_id: uuid.UUID
    created_at: datetime


class WorkspaceOut(ORMModel):
    id: uuid.UUID
    name: str
    slug: str
    settings: dict = {}
    created_at: datetime


class WorkspaceUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    inbound_email: str | None = Field(default=None, max_length=320)
    outbound_from_name: str | None = Field(default=None, max_length=200)


# ------------------------------------------------------------ agents


class AgentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    slug: str = Field(min_length=1, max_length=100)
    title: str = Field(default="", max_length=200)
    description: str = ""
    avatar_url: str | None = None
    system_prompt: str = ""
    tools: list = Field(default_factory=list)
    permissions: dict = Field(default_factory=dict)
    config: dict = Field(default_factory=dict)


class AgentUpdate(BaseModel):
    name: str | None = None
    title: str | None = None
    description: str | None = None
    avatar_url: str | None = None
    system_prompt: str | None = None
    tools: list | None = None
    permissions: dict | None = None
    config: dict | None = None
    status: AgentStatus | None = None
    enabled: bool | None = None


class AgentOut(ORMModel):
    id: uuid.UUID
    name: str
    slug: str
    title: str
    description: str
    avatar_url: str | None
    system_prompt: str
    tools: list
    permissions: dict
    config: dict
    status: AgentStatus
    enabled: bool
    current_workload: int
    tasks_completed: int
    last_activity_at: datetime | None
    performance: dict
    created_at: datetime
    updated_at: datetime


class AgentRunOut(ORMModel):
    id: uuid.UUID
    agent_id: uuid.UUID
    task_id: uuid.UUID | None
    status: RunStatus
    input_payload: dict
    output_payload: dict
    tokens_prompt: int
    tokens_completion: int
    error: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime


# ------------------------------------------------------------ CRM


class LeadSourceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    source_type: str = Field(default="manual", max_length=50)


class LeadSourceOut(ORMModel):
    id: uuid.UUID
    name: str
    source_type: str
    created_at: datetime


class LeadBase(BaseModel):
    business_name: str = Field(min_length=1, max_length=300)
    business_type: str = Field(default="cafe", max_length=100)
    website: str | None = Field(default=None, max_length=500)
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=50)
    city: str | None = Field(default=None, max_length=120)
    state: str | None = Field(default=None, max_length=120)
    country: str | None = Field(default=None, max_length=120)
    contact_person: str | None = Field(default=None, max_length=200)
    source_id: uuid.UUID | None = None
    source: str = Field(default="manual", max_length=100)
    lead_score: int = Field(default=0, ge=0, le=100)
    qualification_status: QualificationStatus = QualificationStatus.unqualified
    notes: str = ""
    estimated_value: Decimal = Field(default=Decimal("0"), ge=0)
    currency: str = Field(default="USD", max_length=3)


class LeadCreate(LeadBase):
    pass


class LeadUpdate(BaseModel):
    business_name: str | None = Field(default=None, max_length=300)
    business_type: str | None = Field(default=None, max_length=100)
    website: str | None = None
    email: EmailStr | None = None
    phone: str | None = None
    city: str | None = None
    state: str | None = None
    country: str | None = None
    contact_person: str | None = None
    source_id: uuid.UUID | None = None
    source: str | None = None
    lead_score: int | None = Field(default=None, ge=0, le=100)
    qualification_status: QualificationStatus | None = None
    notes: str | None = None
    estimated_value: Decimal | None = None
    currency: str | None = None
    last_contacted_at: datetime | None = None


class StageUpdate(BaseModel):
    stage: LeadStage
    note: str | None = None


class LeadOut(LeadBase):
    id: uuid.UUID
    stage: LeadStage
    last_contacted_at: datetime | None
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)


class ContactCreate(BaseModel):
    lead_id: uuid.UUID | None = None
    name: str = Field(min_length=1, max_length=200)
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=50)
    role_title: str | None = Field(default=None, max_length=120)
    is_primary: bool = False


class ContactUpdate(BaseModel):
    name: str | None = None
    email: EmailStr | None = None
    phone: str | None = None
    role_title: str | None = None
    is_primary: bool | None = None


class ContactOut(ORMModel):
    id: uuid.UUID
    lead_id: uuid.UUID | None
    name: str
    email: str | None
    phone: str | None
    role_title: str | None
    is_primary: bool
    created_at: datetime


class ActivityOut(ORMModel):
    id: uuid.UUID
    lead_id: uuid.UUID | None
    actor_type: ActorType
    actor_id: uuid.UUID | None
    action: str
    description: str
    metadata_json: dict
    created_at: datetime


# ------------------------------------------------------------ conversations


class ConversationOut(ORMModel):
    id: uuid.UUID
    lead_id: uuid.UUID
    channel: Channel
    external_thread_id: str | None
    status: str
    created_at: datetime
    updated_at: datetime
    lead_business_name: str | None = None
    messages: list["MessageOut"] = []


class MessageOut(ORMModel):
    id: uuid.UUID
    conversation_id: uuid.UUID
    direction: MessageDirection
    channel: Channel
    subject: str | None
    body: str
    status: MessageStatus
    is_ai_draft: bool
    approval_id: uuid.UUID | None
    sent_at: datetime | None
    created_at: datetime


ConversationOut.model_rebuild()


# ------------------------------------------------------------ tasks / approvals


class TaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    description: str = ""
    assignee_type: AssigneeType
    assignee_agent_id: uuid.UUID | None = None
    assignee_user_id: uuid.UUID | None = None
    priority: TaskPriority = TaskPriority.medium
    due_date: datetime | None = None
    lead_id: uuid.UUID | None = None


class TaskUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    assignee_type: AssigneeType | None = None
    assignee_agent_id: uuid.UUID | None = None
    assignee_user_id: uuid.UUID | None = None
    priority: TaskPriority | None = None
    status: TaskStatus | None = None
    due_date: datetime | None = None
    lead_id: uuid.UUID | None = None


class TaskOut(ORMModel):
    id: uuid.UUID
    title: str
    description: str
    assignee_type: AssigneeType
    assignee_agent_id: uuid.UUID | None
    assignee_user_id: uuid.UUID | None
    priority: TaskPriority
    status: TaskStatus
    due_date: datetime | None
    lead_id: uuid.UUID | None
    created_by_user_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
    assignee_name: str | None = None
    lead_business_name: str | None = None


class ApprovalCreate(BaseModel):
    action_type: ApprovalActionType
    risk_level: RiskLevel | None = None
    title: str = Field(min_length=1, max_length=300)
    payload: dict = Field(default_factory=dict)
    requested_by_agent_id: uuid.UUID | None = None
    lead_id: uuid.UUID | None = None


class ApprovalReview(BaseModel):
    status: ApprovalStatus
    note: str | None = None


class ApprovalOut(ORMModel):
    id: uuid.UUID
    action_type: ApprovalActionType
    risk_level: RiskLevel
    status: ApprovalStatus
    title: str
    payload: dict
    requested_by_agent_id: uuid.UUID | None
    requested_by_user_id: uuid.UUID | None
    reviewed_by_user_id: uuid.UUID | None
    lead_id: uuid.UUID | None
    decided_at: datetime | None
    created_at: datetime
    updated_at: datetime
    lead_business_name: str | None = None
    requester_name: str | None = None


# ------------------------------------------------------------ notifications


class NotificationOut(ORMModel):
    id: uuid.UUID
    user_id: uuid.UUID | None
    type: NotificationType
    title: str
    body: str
    link: str | None
    is_read: bool
    created_at: datetime


# ------------------------------------------------------------ products / knowledge


class ProductCreate(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    slug: str = Field(min_length=1, max_length=150)
    description: str = ""
    is_active: bool = True


class ProductUpdate(BaseModel):
    name: str | None = None
    slug: str | None = None
    description: str | None = None
    is_active: bool | None = None


class ProductOut(ORMModel):
    id: uuid.UUID
    name: str
    slug: str
    description: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


class KnowledgeCreate(BaseModel):
    product_id: uuid.UUID | None = None
    category: KnowledgeCategory = KnowledgeCategory.other
    title: str = Field(min_length=1, max_length=300)
    content: str = ""
    sort_order: int = 0


class KnowledgeUpdate(BaseModel):
    product_id: uuid.UUID | None = None
    category: KnowledgeCategory | None = None
    title: str | None = None
    content: str | None = None
    sort_order: int | None = None


class KnowledgeOut(ORMModel):
    id: uuid.UUID
    product_id: uuid.UUID | None
    category: KnowledgeCategory
    title: str
    content: str
    sort_order: int
    created_at: datetime
    updated_at: datetime


# ------------------------------------------------------------ pipeline / analytics


class PipelineStageOut(BaseModel):
    stage: LeadStage
    count: int
    total_value: float


class AnalyticsOverview(BaseModel):
    total_leads: int
    leads_by_stage: dict[str, int]
    leads_by_source: dict[str, int]
    qualified_leads: int
    emails_sent: int
    whatsapp_sent: int
    interested_prospects: int
    demo_requests: int
    won_deals: int
    pipeline_value: float
    won_value: float
    tasks_by_status: dict[str, int]
    pending_approvals: int
    messages_received: int
    meetings_booked: int
    meetings_completed: int = 0
    revenue_won: float = 0.0
    response_rate: float | None
    qualification_rate: float | None
    conversion_rate: float | None
    leads_over_time: list[dict]
    messages_over_time: list[dict]
    agent_activity: list[dict]
    # Phase 5 appointment metrics (from appointment records, not messages)
    demos_requested: int = 0
    demos_proposed: int = 0
    demos_booked: int = 0
    demos_completed: int = 0
    demos_cancelled: int = 0
    demo_no_shows: int = 0
    demo_attendance_rate: float | None = None
    demo_to_customer_rate: float | None = None


# ------------------------------------------------------------ compliance


class ConsentCreate(BaseModel):
    channel: Channel
    value: str = Field(min_length=1, max_length=320)
    status: ConsentStatus = ConsentStatus.granted
    source: str = Field(default="", max_length=200)
    lead_id: uuid.UUID | None = None


class ConsentOut(ORMModel):
    id: uuid.UUID
    channel: Channel
    value: str
    lead_id: uuid.UUID | None
    status: ConsentStatus
    source: str
    consented_at: datetime | None
    created_at: datetime


class SuppressionCreate(BaseModel):
    channel: Channel
    value: str = Field(min_length=1, max_length=320)
    reason: str = Field(default="", max_length=300)


class SuppressionOut(ORMModel):
    id: uuid.UUID
    channel: Channel
    value: str
    reason: str
    created_at: datetime


class ComplianceCheckOut(BaseModel):
    allowed: bool
    reason: str | None = None
    consent_status: str | None = None
    suppressed: bool


# ------------------------------------------------------------ research (Phase 2)


class ResearchJobCreate(BaseModel):
    country: str | None = None
    state: str | None = None
    district: str | None = None
    city: str | None = None
    town: str | None = None
    locality: str | None = None
    postal_code: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    radius_m: int = Field(default=5000, ge=500, le=50000)
    business_types: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    exclusions: list[str] = Field(default_factory=list)
    max_results: int = Field(default=50, ge=1, le=500)
    providers: list[str] | None = None


class ResearchJobOut(ORMModel):
    id: uuid.UUID
    agent_id: uuid.UUID | None
    status: str
    config: dict
    stats: dict
    progress: int
    error: str | None
    created_by_user_id: uuid.UUID | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ResearchResultOut(ORMModel):
    id: uuid.UUID
    job_id: uuid.UUID
    lead_id: uuid.UUID | None
    status: str
    business_name: str
    business_category: str
    description: str
    address: str
    city: str | None
    state: str | None
    country: str | None
    postal_code: str | None
    latitude: float | None
    longitude: float | None
    website: str | None
    phone: str | None
    email: str | None
    instagram: str | None
    facebook: str | None
    whatsapp: str | None
    is_operating: str | None
    uses_online_ordering: str | None
    uses_digital_menu: str | None
    has_reservation_system: str | None
    source_provider: str
    source_ref: str
    evidence: list
    score: int
    score_breakdown: dict
    score_explanation: str
    missing_info: list
    research_notes: str
    duplicate_of_id: uuid.UUID | None
    duplicate_reason: str | None
    lead_score_written: bool
    discovered_at: datetime
    researched_at: datetime | None
    created_at: datetime

    priority: str = ""


class ProviderOut(BaseModel):
    name: str
    configured: bool
    requires_key: bool


# ------------------------------------------------------------ comms engine (Phase 3)


class ConversationDetailOut(ConversationOut):
    state: str = "new"
    summary: str = ""
    last_intent: str | None = None
    needs_human: bool = False


class InboundMessageIn(BaseModel):
    from_address: str
    to_address: str | None = None
    subject: str | None = None
    body: str = Field(min_length=1)
    external_id: str | None = None
    thread_id: str | None = None


class ReplyDraftOut(BaseModel):
    subject: str
    body: str
    needs_human_review: bool = False
    reason: str = ""
    provider: str = ""


class SendReplyIn(BaseModel):
    subject: str | None = None
    body: str = Field(min_length=1)


class FollowUpOut(ORMModel):
    id: uuid.UUID
    conversation_id: uuid.UUID
    lead_id: uuid.UUID | None
    channel: Channel
    scheduled_at: datetime
    interval_hours: int
    attempt: int
    max_attempts: int
    status: str
    subject: str | None
    body: str
    last_error: str | None
    sent_at: datetime | None
    created_at: datetime


class FollowUpCreate(BaseModel):
    hours: int = Field(default=24, ge=1, le=720)
    max_attempts: int = Field(default=3, ge=1, le=10)


class CampaignCreate(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    lead_ids: list[uuid.UUID] = Field(min_length=1)
    channel: Channel = Channel.email
    instructions: str = ""


class CampaignAuthorize(BaseModel):
    confirm: bool = True


class CampaignOut(ORMModel):
    id: uuid.UUID
    name: str
    status: str
    channel: Channel
    lead_ids: list
    instructions: str
    stats: dict
    error: str | None
    authorized_by_user_id: uuid.UUID | None
    authorized_at: datetime | None
    created_at: datetime
    updated_at: datetime


class EligibilityOut(BaseModel):
    lead_id: uuid.UUID
    business_name: str
    allowed: bool
    reason: str | None
    has_contact: bool
    contact_value: str | None
    consent_status: str | None
    channel: str


# ------------------------------------------------------------ scheduling (Phase 5)


class AppointmentCreate(BaseModel):
    lead_id: uuid.UUID
    appointment_type: AppointmentType = AppointmentType.product_demo
    title: str | None = Field(default=None, max_length=300)
    description: str = ""
    start_at: datetime
    end_at: datetime
    contact_id: uuid.UUID | None = None
    conversation_id: uuid.UUID | None = None
    location: str | None = Field(default=None, max_length=500)
    meeting_link: str | None = Field(default=None, max_length=500)
    assigned_to: str = Field(default="primary", max_length=200)
    status: AppointmentStatus = AppointmentStatus.confirmed
    proposed_slots: list = Field(default_factory=list)


class AppointmentUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=300)
    description: str | None = None
    location: str | None = Field(default=None, max_length=500)
    meeting_link: str | None = Field(default=None, max_length=500)
    assigned_to: str | None = Field(default=None, max_length=200)
    contact_id: uuid.UUID | None = None
    conversation_id: uuid.UUID | None = None
    appointment_type: AppointmentType | None = None


class AppointmentReschedule(BaseModel):
    start_at: datetime
    end_at: datetime
    reason: str = Field(default="", max_length=500)


class AppointmentBookSlot(BaseModel):
    start: datetime
    end: datetime


class AppointmentCancel(BaseModel):
    reason: str = Field(default="", max_length=500)


class AppointmentPropose(BaseModel):
    lead_id: uuid.UUID
    appointment_type: AppointmentType = AppointmentType.product_demo
    title: str | None = None
    conversation_id: uuid.UUID | None = None
    duration_minutes: int | None = Field(default=None, ge=5, le=480)
    slots: list | None = None


class AppointmentOut(ORMModel):
    id: uuid.UUID
    lead_id: uuid.UUID
    contact_id: uuid.UUID | None
    conversation_id: uuid.UUID | None
    appointment_type: AppointmentType
    title: str
    description: str
    start_at: datetime
    end_at: datetime
    timezone: str
    location: str | None
    meeting_link: str | None
    assigned_to: str
    status: AppointmentStatus
    proposed_slots: list
    reschedule_history: list
    cancellation_reason: str | None
    cancelled_by: str | None
    cancelled_at: datetime | None
    confirmed_at: datetime | None
    completed_at: datetime | None
    source: str
    source_agent_id: uuid.UUID | None
    calendar_provider: str
    external_event_id: str | None
    calendar_sync: str
    sync_error: str | None
    last_synced_at: datetime | None
    created_at: datetime
    updated_at: datetime
    lead_business_name: str | None = None
    lead_email: str | None = None
    lead_phone: str | None = None


class AppointmentReminderOut(ORMModel):
    id: uuid.UUID
    appointment_id: uuid.UUID
    remind_at: datetime
    channel: str
    status: str
    delivery_detail: str
    last_error: str | None
    sent_at: datetime | None
    created_at: datetime


class SlotOut(BaseModel):
    start: str
    end: str
    start_local: str
    end_local: str


class SchedulingSettingsOut(ORMModel):
    id: uuid.UUID
    timezone: str
    working_days: list
    work_start: str
    work_end: str
    lunch_start: str | None
    lunch_end: str | None
    slot_minutes: int
    buffer_minutes: int
    min_notice_hours: int
    max_horizon_days: int
    holidays: list
    blackout_windows: list
    reminder_offsets_minutes: list
    default_duration_minutes: int
    require_confirmation: bool
    allow_autonomous_booking: bool
    calendar_provider: str


class SchedulingSettingsUpdate(BaseModel):
    timezone: str | None = Field(default=None, max_length=64)
    working_days: list[int] | None = None
    work_start: str | None = Field(default=None, max_length=5)
    work_end: str | None = Field(default=None, max_length=5)
    lunch_start: str | None = Field(default=None, max_length=5)
    lunch_end: str | None = Field(default=None, max_length=5)
    slot_minutes: int | None = Field(default=None, ge=5, le=240)
    buffer_minutes: int | None = Field(default=None, ge=0, le=120)
    min_notice_hours: int | None = Field(default=None, ge=0, le=168)
    max_horizon_days: int | None = Field(default=None, ge=1, le=365)
    holidays: list | None = None
    blackout_windows: list | None = None
    reminder_offsets_minutes: list[int] | None = None
    default_duration_minutes: int | None = Field(default=None, ge=5, le=480)
    require_confirmation: bool | None = None
    allow_autonomous_booking: bool | None = None
    calendar_provider: str | None = Field(default=None, max_length=50)


class AssistantDashboard(BaseModel):
    upcoming_demos: int
    today_meetings: int
    pending_booking_requests: int
    rescheduling_requests: int
    cancellations: int
    no_shows: int
    completed_demos: int
    calendar_provider: str
