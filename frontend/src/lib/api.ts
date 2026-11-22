// Central API client. All requests go through the Vite proxy (/api -> backend).

const TOKEN_KEY = 'agw_token'

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY)
}
export function setToken(token: string | null) {
  if (token) localStorage.setItem(TOKEN_KEY, token)
  else localStorage.removeItem(TOKEN_KEY)
}

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(options.headers as Record<string, string>),
  }
  const token = getToken()
  if (token) headers['Authorization'] = `Bearer ${token}`

  const resp = await fetch(`/api/v1${path}`, { ...options, headers })
  if (resp.status === 401) {
    setToken(null)
    if (!location.pathname.startsWith('/login') && !location.pathname.startsWith('/register')) {
      location.href = '/login'
    }
    throw new ApiError(401, 'Session expired')
  }
  if (resp.status === 204) return undefined as T
  const body = await resp.json().catch(() => ({}))
  if (!resp.ok) {
    const detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail ?? body)
    throw new ApiError(resp.status, detail || `Request failed (${resp.status})`)
  }
  return body as T
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, data?: unknown) =>
    request<T>(path, { method: 'POST', body: data === undefined ? undefined : JSON.stringify(data) }),
  patch: <T>(path: string, data?: unknown) =>
    request<T>(path, { method: 'PATCH', body: data === undefined ? undefined : JSON.stringify(data) }),
  put: <T>(path: string, data?: unknown) =>
    request<T>(path, { method: 'PUT', body: data === undefined ? undefined : JSON.stringify(data) }),
  delete: <T>(path: string) => request<T>(path, { method: 'DELETE' }),
}

// ---- shared types (mirror backend schemas) ----
export interface Page<T> {
  items: T[]
  total: number
  page: number
  page_size: number
  pages: number
}
export interface User {
  id: string; email: string; full_name: string; role: string
  is_active: boolean; workspace_id: string; created_at: string
}
export interface Workspace { id: string; name: string; slug: string; created_at: string }
export interface Agent {
  id: string; name: string; slug: string; title: string; description: string
  avatar_url: string | null; system_prompt: string; tools: unknown[]
  permissions: Record<string, unknown>; config: Record<string, unknown>
  status: 'active' | 'idle' | 'paused' | 'error'; enabled: boolean
  current_workload: number; tasks_completed: number
  last_activity_at: string | null; performance: Record<string, unknown>
  created_at: string; updated_at: string
}
export interface Lead {
  id: string; business_name: string; business_type: string; website: string | null
  email: string | null; phone: string | null; city: string | null; state: string | null
  country: string | null; contact_person: string | null; source_id: string | null
  source: string; lead_score: number
  qualification_status: 'unqualified' | 'researching' | 'qualified' | 'disqualified'
  stage: string; notes: string; estimated_value: string; currency: string
  last_contacted_at: string | null; created_at: string; updated_at: string
}
export interface Contact {
  id: string; lead_id: string | null; name: string; email: string | null
  phone: string | null; role_title: string | null; is_primary: boolean; created_at: string
}
export interface TaskItem {
  id: string; title: string; description: string
  assignee_type: 'agent' | 'human'; assignee_agent_id: string | null
  assignee_user_id: string | null; priority: 'low' | 'medium' | 'high' | 'urgent'
  status: 'pending' | 'in_progress' | 'blocked' | 'completed' | 'cancelled'
  due_date: string | null; lead_id: string | null; created_by_user_id: string | null
  created_at: string; updated_at: string
  assignee_name?: string | null; lead_business_name?: string | null
}
export interface Approval {
  id: string; action_type: string; risk_level?: 'low' | 'medium' | 'high'; status: 'pending' | 'approved' | 'rejected' | 'expired' | 'cancelled'
  title: string; payload: Record<string, unknown>
  requested_by_agent_id: string | null; requested_by_user_id: string | null
  reviewed_by_user_id: string | null; lead_id: string | null; decided_at: string | null
  created_at: string; updated_at: string
  lead_business_name?: string | null; requester_name?: string | null
}
export interface Activity {
  id: string; lead_id: string | null; actor_type: 'user' | 'agent' | 'system'
  actor_id: string | null; action: string; description: string
  metadata_json: Record<string, unknown>; created_at: string
}
export interface Notification {
  id: string; user_id: string | null; type: string; title: string; body: string
  link: string | null; is_read: boolean; created_at: string
}
export interface Conversation {
  id: string; lead_id: string; channel: 'email' | 'whatsapp'
  external_thread_id: string | null; status: string
  created_at: string; updated_at: string
  lead_business_name?: string | null
  messages?: Message[]
}
export interface Message {
  id: string; conversation_id: string; direction: 'inbound' | 'outbound'
  channel: string; subject: string | null; body: string; status: string
  is_ai_draft: boolean; approval_id: string | null; sent_at: string | null; created_at: string
}
export interface Product { id: string; name: string; slug: string; description: string; is_active: boolean; created_at: string; updated_at: string }
export interface Knowledge {
  id: string; product_id: string | null; category: string; title: string
  content: string; sort_order: number; created_at: string; updated_at: string
}
export interface PipelineStage { stage: string; count: number; total_value: number }
export interface AnalyticsOverview {
  total_leads: number; leads_by_stage: Record<string, number>; leads_by_source: Record<string, number>
  qualified_leads: number; emails_sent: number; whatsapp_sent: number
  interested_prospects: number; demo_requests: number; won_deals: number
  pipeline_value: number; won_value: number; tasks_by_status: Record<string, number>
  pending_approvals: number; messages_received: number; meetings_booked: number
  response_rate: number | null; qualification_rate: number | null
  conversion_rate: number | null; leads_over_time: { date: string; count: number }[]
  messages_over_time: { date: string; count: number }[]; agent_activity: { status: string; runs: number; tokens: number }[]
}

export interface ResearchJob {
  id: string; agent_id: string | null; status: 'queued'|'running'|'completed'|'failed'|'cancelled'
  config: Record<string, unknown>; stats: Record<string, number>
  progress: number; error: string | null; created_at: string; finished_at: string | null
}
export interface ResearchResult {
  id: string; job_id: string; lead_id: string | null; status: string
  business_name: string; business_category: string; address: string
  city: string | null; state: string | null; country: string | null
  website: string | null; phone: string | null; email: string | null
  instagram: string | null; facebook: string | null
  source_provider: string; score: number; priority: string
  score_breakdown: Record<string, {points:number;max:number;reason:string}>
  score_explanation: string; missing_info: string[]; research_notes: string
  duplicate_of_id: string | null; duplicate_reason: string | null
  lead_score_written: boolean; discovered_at: string; researched_at: string | null
  evidence: {field:string;value:string;source:string;verified:boolean}[]
}
export interface Provider { name: string; configured: boolean; requires_key: boolean }

export interface ConversationDetail {
  id: string; lead_id: string; channel: 'email'|'whatsapp'; status: string
  state: string; summary: string; last_intent: string | null; needs_human: boolean
  external_thread_id: string | null; lead_business_name?: string | null
  created_at: string; updated_at: string
  messages: { id:string; direction:'inbound'|'outbound'; channel:string; subject:string|null; body:string; status:string; is_ai_draft:boolean; approval_id:string|null; sent_at:string|null; created_at:string|null }[]
}
export interface FollowUp { id:string; conversation_id:string; scheduled_at:string; status:string; attempt:number; max_attempts:number; channel:string }
export interface Campaign { id:string; name:string; status:string; channel:string; lead_ids:string[]; instructions:string; stats:Record<string,number>; error:string|null; authorized_at:string|null; created_at:string }
export interface Eligibility { lead_id:string; business_name:string; allowed:boolean; reason:string|null; has_contact:boolean; contact_value:string|null; consent_status:string|null; channel:string }

export interface SystemStatus {
  email: { provider: string; configured: boolean; from_address_set: boolean; webhook_secret_set: boolean }
  llm: { provider: string; configured: boolean; model: string; fallback: string }
  workspace: { inbound_email: string | null; inbound_configured: boolean }
  calendar?: { providers: { name: string; configured: boolean }[]; internal_scheduling: string }
  environment: string
}

// ---- Phase 5: scheduling / appointments ----
export type AppointmentStatus =
  | 'proposed' | 'awaiting_confirmation' | 'confirmed'
  | 'rescheduling_requested' | 'cancelled' | 'completed' | 'no_show'
export type AppointmentType =
  | 'product_demo' | 'discovery_call' | 'sales_consultation'
  | 'follow_up' | 'onboarding' | 'internal'
export interface Appointment {
  id: string; lead_id: string; contact_id: string | null
  conversation_id: string | null; appointment_type: AppointmentType
  title: string; description: string
  start_at: string; end_at: string; timezone: string
  location: string | null; meeting_link: string | null
  assigned_to: string; status: AppointmentStatus
  proposed_slots: { start: string; end: string }[]
  reschedule_history: { from_start: string; from_end: string; to_start: string; to_end: string; reason: string; at: string; by: string }[]
  cancellation_reason: string | null; cancelled_by: string | null; cancelled_at: string | null
  confirmed_at: string | null; completed_at: string | null
  source: string; source_agent_id: string | null
  calendar_provider: string; external_event_id: string | null
  calendar_sync: string; sync_error: string | null; last_synced_at: string | null
  created_at: string; updated_at: string
  lead_business_name?: string | null; lead_email?: string | null; lead_phone?: string | null
}
export interface AppointmentReminder {
  id: string; appointment_id: string; remind_at: string; channel: string
  status: string; delivery_detail: string; last_error: string | null
  sent_at: string | null; created_at: string
}
export interface Slot { start: string; end: string; start_local: string; end_local: string }
export interface SchedulingSettings {
  id: string; timezone: string; working_days: number[]
  work_start: string; work_end: string
  lunch_start: string | null; lunch_end: string | null
  slot_minutes: number; buffer_minutes: number
  min_notice_hours: number; max_horizon_days: number
  holidays: string[]; blackout_windows: { start?: string; end?: string; date?: string }[]
  reminder_offsets_minutes: number[]; default_duration_minutes: number
  require_confirmation: boolean; allow_autonomous_booking: boolean
  calendar_provider: string
}
export interface AssistantDashboard {
  upcoming_demos: number; today_meetings: number
  pending_booking_requests: number; rescheduling_requests: number
  cancellations: number; no_shows: number; completed_demos: number
  calendar_provider: string
}
export interface AnalyticsOverview {
  demos_requested?: number; demos_proposed?: number; demos_booked?: number
  demos_completed?: number; demos_cancelled?: number; demo_no_shows?: number
  demo_attendance_rate?: number | null; demo_to_customer_rate?: number | null
}
