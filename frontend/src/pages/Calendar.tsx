import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { api, ApiError, type Appointment, type AppointmentReminder, type AppointmentStatus, type AppointmentType, type AssistantDashboard, type Lead, type SchedulingSettings } from '../lib/api'
import { PageHeader } from '../components/metrics'
import {
  Badge, Button, Card, CardHeader, ConfirmDialog, Drawer, EmptyState, Field,
  Input, Select, Skeleton, Textarea, useToast,
} from '../components/ui'
import { cn, formatDateTime, titleCase } from '../lib/utils'

// ---------------------------------------------------------------- helpers

const STATUS_TONE: Record<AppointmentStatus, 'neutral' | 'violet' | 'mint' | 'amber' | 'red' | 'blue'> = {
  proposed: 'blue', awaiting_confirmation: 'amber', confirmed: 'mint',
  rescheduling_requested: 'violet', cancelled: 'red', completed: 'neutral', no_show: 'red',
}
const TYPE_LABEL: Record<AppointmentType, string> = {
  product_demo: 'Product demo', discovery_call: 'Discovery call',
  sales_consultation: 'Sales consultation', follow_up: 'Follow-up',
  onboarding: 'Onboarding', internal: 'Internal',
}
const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

function startOfDay(d: Date) { const x = new Date(d); x.setHours(0, 0, 0, 0); return x }
function addDays(d: Date, n: number) { const x = new Date(d); x.setDate(x.getDate() + n); return x }
function sameDay(a: Date, b: Date) { return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate() }

function apptInRange(a: Appointment, from: Date, to: Date) {
  const s = new Date(a.start_at)
  return s >= from && s < to
}

// ---------------------------------------------------------------- page

type Tab = 'schedule' | 'appointments' | 'availability' | 'assistant'

export default function CalendarPage() {
  const [tab, setTab] = useState<Tab>('schedule')
  const [selected, setSelected] = useState<Appointment | null>(null)

  return (
    <div>
      <PageHeader
        title="Calendar & Appointments"
        subtitle="Scheduling, demos and appointment management powered by the Executive Assistant"
      />
      <div className="mb-5 flex gap-2">
        {([['schedule', 'Calendar'], ['appointments', 'Appointments'], ['availability', 'Availability'], ['assistant', 'Assistant']] as [Tab, string][]).map(([k, label]) => (
          <button key={k} onClick={() => setTab(k)}
            className={cn('rounded-full px-4 py-1.5 text-xs font-medium transition-colors cursor-pointer',
              tab === k ? 'bg-viol-500/20 text-viol-300 border border-viol-500/40' : 'bg-ink-800 text-ink-400 border border-ink-700 hover:text-ink-200')}>
            {label}
          </button>
        ))}
      </div>

      {tab === 'schedule' && <ScheduleTab onSelect={setSelected} />}
      {tab === 'appointments' && <AppointmentsTab onSelect={setSelected} />}
      {tab === 'availability' && <AvailabilityTab />}
      {tab === 'assistant' && <AssistantTab />}

      <AppointmentDrawer appt={selected} onClose={() => setSelected(null)} />
    </div>
  )
}

// ---------------------------------------------------------------- schedule views

function ScheduleTab({ onSelect }: { onSelect: (a: Appointment) => void }) {
  const [view, setView] = useState<'month' | 'week' | 'day'>('week')
  const [cursor, setCursor] = useState(() => startOfDay(new Date()))
  const [statusFilter, setStatusFilter] = useState('')
  const [typeFilter, setTypeFilter] = useState('')

  const range = useMemo(() => {
    if (view === 'day') return { from: cursor, to: addDays(cursor, 1) }
    if (view === 'week') return { from: startOfDay(addDays(cursor, -((cursor.getDay() + 6) % 7))), to: addDays(startOfDay(addDays(cursor, -((cursor.getDay() + 6) % 7))), 7) }
    const first = new Date(cursor.getFullYear(), cursor.getMonth(), 1)
    const start = startOfDay(addDays(first, -((first.getDay() + 6) % 7)))
    const last = new Date(cursor.getFullYear(), cursor.getMonth() + 1, 0)
    const end = addDays(startOfDay(addDays(last, 1 - ((last.getDay() + 6) % 7))), 1)
    return { from: start, to: end }
  }, [cursor, view])

  const params = new URLSearchParams()
  if (statusFilter) params.set('status', statusFilter)
  if (typeFilter) params.set('appointment_type', typeFilter)
  params.set('page_size', '200')
  const { data, isLoading } = useQuery({
    queryKey: ['appointments', 'range', range.from.toISOString(), range.to.toISOString(), statusFilter, typeFilter],
    queryFn: () => api.get<{ items: Appointment[]; total: number }>(`/appointments?${params}`),
  })
  const appts = (data?.items ?? []).filter((a) => apptInRange(a, range.from, range.to))

  const nav = (dir: number) => {
    if (view === 'day') setCursor(addDays(cursor, dir))
    else if (view === 'week') setCursor(addDays(cursor, dir * 7))
    else setCursor(new Date(cursor.getFullYear(), cursor.getMonth() + dir, 1))
  }

  const title = view === 'day'
    ? cursor.toLocaleDateString(undefined, { weekday: 'long', month: 'long', day: 'numeric' })
    : view === 'week'
      ? `${range.from.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })} – ${addDays(range.to, -1).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })}`
      : cursor.toLocaleDateString(undefined, { month: 'long', year: 'numeric' })

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <Button size="sm" variant="secondary" onClick={() => nav(-1)}>‹</Button>
          <Button size="sm" variant="secondary" onClick={() => setCursor(startOfDay(new Date()))}>Today</Button>
          <Button size="sm" variant="secondary" onClick={() => nav(1)}>›</Button>
          <h2 className="ml-2 text-sm font-semibold">{title}</h2>
        </div>
        <div className="flex items-center gap-2">
          <Select className="h-8 w-40 text-xs" value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
            <option value="">All statuses</option>
            {(['proposed', 'awaiting_confirmation', 'confirmed', 'rescheduling_requested', 'cancelled', 'completed', 'no_show'] as AppointmentStatus[]).map((s) => <option key={s} value={s}>{titleCase(s)}</option>)}
          </Select>
          <Select className="h-8 w-40 text-xs" value={typeFilter} onChange={(e) => setTypeFilter(e.target.value)}>
            <option value="">All types</option>
            {(Object.keys(TYPE_LABEL) as AppointmentType[]).map((t) => <option key={t} value={t}>{TYPE_LABEL[t]}</option>)}
          </Select>
          <div className="flex rounded-lg border border-ink-600/70 p-0.5">
            {(['month', 'week', 'day'] as const).map((v) => (
              <button key={v} onClick={() => setView(v)}
                className={cn('rounded-md px-3 py-1 text-xs font-medium cursor-pointer', view === v ? 'bg-viol-500/20 text-viol-300' : 'text-ink-400 hover:text-ink-200')}>
                {titleCase(v)}
              </button>
            ))}
          </div>
        </div>
      </div>

      {isLoading ? <Skeleton className="h-96" /> : view === 'month' ? (
        <MonthGrid range={range} appts={appts} onSelect={onSelect} cursor={cursor} setCursor={setCursor} />
      ) : view === 'week' ? (
        <WeekGrid range={range} appts={appts} onSelect={onSelect} />
      ) : (
        <DayList range={range} appts={appts} onSelect={onSelect} />
      )}

      <UpcomingList appts={appts} onSelect={onSelect} />
    </div>
  )
}

function MonthGrid({ range, appts, onSelect, cursor, setCursor }: {
  range: { from: Date; to: Date }; appts: Appointment[]
  onSelect: (a: Appointment) => void; cursor: Date; setCursor: (d: Date) => void
}) {
  const days: Date[] = []
  for (let d = new Date(range.from); d < range.to; d = addDays(d, 1)) days.push(new Date(d))
  const byDay = new Map<string, Appointment[]>()
  for (const a of appts) {
    const key = new Date(a.start_at).toDateString()
    byDay.set(key, [...(byDay.get(key) ?? []), a])
  }
  return (
    <Card className="overflow-hidden">
      <div className="grid grid-cols-7 border-b border-ink-700/50">
        {WEEKDAYS.map((d) => <div key={d} className="px-3 py-2 text-[11px] font-semibold uppercase tracking-wide text-ink-400">{d}</div>)}
      </div>
      <div className="grid grid-cols-7">
        {days.map((d, i) => {
          const list = byDay.get(d.toDateString()) ?? []
          const inMonth = d.getMonth() === cursor.getMonth()
          return (
            <button key={i} onClick={() => { setCursor(d); }}
              className={cn('min-h-24 border-b border-r border-ink-800/60 p-1.5 text-left align-top hover:bg-ink-800/40 cursor-pointer',
                !inMonth && 'opacity-40', sameDay(d, new Date()) && 'bg-viol-500/5')}>
              <span className={cn('text-[11px] font-medium', sameDay(d, new Date()) ? 'text-viol-300' : 'text-ink-400')}>{d.getDate()}</span>
              <div className="mt-1 space-y-0.5">
                {list.slice(0, 3).map((a) => (
                  <div key={a.id} onClick={(e) => { e.stopPropagation(); onSelect(a) }}
                    className="truncate rounded bg-ink-800 px-1.5 py-0.5 text-[10px] text-ink-200 hover:bg-viol-500/20 cursor-pointer">
                    {new Date(a.start_at).toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })} {a.title}
                  </div>
                ))}
                {list.length > 3 && <p className="px-1 text-[10px] text-ink-400">+{list.length - 3} more</p>}
              </div>
            </button>
          )
        })}
      </div>
    </Card>
  )
}

function WeekGrid({ range, appts, onSelect }: { range: { from: Date; to: Date }; appts: Appointment[]; onSelect: (a: Appointment) => void }) {
  const days: Date[] = []
  for (let d = new Date(range.from); d < range.to; d = addDays(d, 1)) days.push(new Date(d))
  return (
    <Card className="overflow-hidden">
      <div className="grid grid-cols-7 border-b border-ink-700/50">
        {days.map((d) => (
          <div key={d.toISOString()} className={cn('px-3 py-2 text-center', sameDay(d, new Date()) && 'bg-viol-500/5')}>
            <p className="text-[10px] font-semibold uppercase text-ink-400">{d.toLocaleDateString(undefined, { weekday: 'short' })}</p>
            <p className={cn('text-sm font-semibold', sameDay(d, new Date()) ? 'text-viol-300' : 'text-ink-200')}>{d.getDate()}</p>
          </div>
        ))}
      </div>
      <div className="grid grid-cols-7">
        {days.map((d) => {
          const list = appts.filter((a) => sameDay(new Date(a.start_at), d))
          return (
            <div key={d.toISOString()} className={cn('min-h-40 space-y-1 border-r border-ink-800/60 p-1.5', sameDay(d, new Date()) && 'bg-viol-500/5')}>
              {list.map((a) => (
                <button key={a.id} onClick={() => onSelect(a)}
                  className="block w-full truncate rounded-md border border-ink-700 bg-ink-800/80 px-2 py-1 text-left text-[11px] text-ink-200 hover:border-viol-500/50 cursor-pointer">
                  <span className="font-medium">{new Date(a.start_at).toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })}</span> {a.title}
                </button>
              ))}
            </div>
          )
        })}
      </div>
    </Card>
  )
}

function DayList({ range, appts, onSelect }: { range: { from: Date; to: Date }; appts: Appointment[]; onSelect: (a: Appointment) => void }) {
  const list = appts.filter((a) => apptInRange(a, range.from, range.to)).sort((a, b) => a.start_at.localeCompare(b.start_at))
  if (!list.length) return <EmptyState title="No appointments this day" />
  return (
    <div className="space-y-2">
      {list.map((a) => <AppointmentRow key={a.id} a={a} onSelect={onSelect} />)}
    </div>
  )
}

function AppointmentRow({ a, onSelect }: { a: Appointment; onSelect: (a: Appointment) => void }) {
  return (
    <Card className="flex flex-wrap items-center gap-4 p-4">
      <div className="w-20 shrink-0 text-center">
        <p className="text-sm font-bold">{new Date(a.start_at).toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })}</p>
        <p className="text-[10px] text-ink-400">{new Date(a.start_at).toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' })}</p>
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <p className="text-sm font-semibold">{a.title}</p>
          <Badge tone={STATUS_TONE[a.status]}>{titleCase(a.status)}</Badge>
          <Badge tone="neutral">{TYPE_LABEL[a.appointment_type]}</Badge>
        </div>
        <p className="mt-1 text-xs text-ink-400">
          {a.lead_business_name ?? '—'} · {formatDateTime(a.end_at)} · {a.assigned_to}
        </p>
      </div>
      <Button size="sm" variant="secondary" onClick={() => onSelect(a)}>Details</Button>
    </Card>
  )
}

function UpcomingList({ appts, onSelect }: { appts: Appointment[]; onSelect: (a: Appointment) => void }) {
  const upcoming = useMemo(() => appts
    .filter((a) => a.status === 'confirmed' || a.status === 'awaiting_confirmation')
    .sort((a, b) => a.start_at.localeCompare(b.start_at)).slice(0, 8), [appts])
  if (!upcoming.length) return null
  return (
    <div className="mt-6">
      <h3 className="mb-3 text-sm font-semibold text-ink-200">Upcoming in this view</h3>
      <div className="space-y-2">
        {upcoming.map((a) => <AppointmentRow key={a.id} a={a} onSelect={onSelect} />)}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------- appointments tab

function AppointmentsTab({ onSelect }: { onSelect: (a: Appointment) => void }) {
  const [statusFilter, setStatusFilter] = useState('')
  const [typeFilter, setTypeFilter] = useState('')
  const [q, setQ] = useState('')
  const [createOpen, setCreateOpen] = useState(false)

  const params = new URLSearchParams({ page_size: '100' })
  if (statusFilter) params.set('status', statusFilter)
  if (typeFilter) params.set('appointment_type', typeFilter)
  if (q) params.set('q', q)
  const { data, isLoading } = useQuery({
    queryKey: ['appointments', statusFilter, typeFilter, q],
    queryFn: () => api.get<{ items: Appointment[]; total: number }>(`/appointments?${params}`),
  })

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <Input className="h-8 w-56 text-xs" placeholder="Search lead or title…" value={q} onChange={(e) => setQ(e.target.value)} />
        <Select className="h-8 w-44 text-xs" value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
          <option value="">All statuses</option>
          {(['proposed', 'awaiting_confirmation', 'confirmed', 'rescheduling_requested', 'cancelled', 'completed', 'no_show'] as AppointmentStatus[]).map((s) => <option key={s} value={s}>{titleCase(s)}</option>)}
        </Select>
        <Select className="h-8 w-44 text-xs" value={typeFilter} onChange={(e) => setTypeFilter(e.target.value)}>
          <option value="">All types</option>
          {(Object.keys(TYPE_LABEL) as AppointmentType[]).map((t) => <option key={t} value={t}>{TYPE_LABEL[t]}</option>)}
        </Select>
        <div className="ml-auto"><Button size="sm" variant="primary" onClick={() => setCreateOpen(true)}>New appointment</Button></div>
      </div>
      {isLoading ? <div className="space-y-3">{Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} className="h-16" />)}</div>
        : !data?.items.length ? <EmptyState title="No appointments" hint="Create one manually or let prospects book through conversations." action={<Button size="sm" variant="primary" onClick={() => setCreateOpen(true)}>New appointment</Button>} />
        : <div className="space-y-2">{data.items.map((a) => <AppointmentRow key={a.id} a={a} onSelect={onSelect} />)}</div>}
      <CreateAppointmentDrawer open={createOpen} onClose={() => setCreateOpen(false)} />
    </div>
  )
}

function CreateAppointmentDrawer({ open, onClose }: { open: boolean; onClose: () => void }) {
  const qc = useQueryClient()
  const toast = useToast()
  const [form, setForm] = useState({ lead_id: '', appointment_type: 'product_demo', title: '', start_at: '', end_at: '', location: '', meeting_link: '', description: '' })
  const { data: leads } = useQuery({ queryKey: ['leads-options'], queryFn: () => api.get<{ items: Lead[] }>('/leads?page_size=100') })

  const create = useMutation({
    mutationFn: () => api.post<Appointment>('/appointments', {
      lead_id: form.lead_id, appointment_type: form.appointment_type,
      title: form.title || null, description: form.description,
      start_at: new Date(form.start_at).toISOString(), end_at: new Date(form.end_at).toISOString(),
      location: form.location || null, meeting_link: form.meeting_link || null,
    }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['appointments'] }); qc.invalidateQueries({ queryKey: ['analytics'] }); onClose(); toast('Appointment created', 'success') },
    onError: (e) => toast(e instanceof ApiError ? e.message : 'Create failed', 'error'),
  })

  return (
    <Drawer open={open} onClose={onClose} title="New appointment">
      <form onSubmit={(e) => { e.preventDefault(); create.mutate() }} className="space-y-4">
        <Field label="Lead *">
          <Select required value={form.lead_id} onChange={(e) => setForm({ ...form, lead_id: e.target.value })}>
            <option value="">Select lead…</option>
            {(leads?.items ?? []).map((l) => <option key={l.id} value={l.id}>{l.business_name}</option>)}
          </Select>
        </Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Type">
            <Select value={form.appointment_type} onChange={(e) => setForm({ ...form, appointment_type: e.target.value })}>
              {(Object.keys(TYPE_LABEL) as AppointmentType[]).map((t) => <option key={t} value={t}>{TYPE_LABEL[t]}</option>)}
            </Select>
          </Field>
          <Field label="Title"><Input value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} placeholder="Auto-generated if blank" /></Field>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Start *"><Input required type="datetime-local" value={form.start_at} onChange={(e) => setForm({ ...form, start_at: e.target.value })} /></Field>
          <Field label="End *"><Input required type="datetime-local" value={form.end_at} onChange={(e) => setForm({ ...form, end_at: e.target.value })} /></Field>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Location"><Input value={form.location} onChange={(e) => setForm({ ...form, location: e.target.value })} /></Field>
          <Field label="Meeting link"><Input value={form.meeting_link} onChange={(e) => setForm({ ...form, meeting_link: e.target.value })} placeholder="https://…" /></Field>
        </div>
        <Field label="Description"><Textarea value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} /></Field>
        <div className="flex justify-end gap-2 pt-2">
          <Button type="button" variant="ghost" onClick={onClose}>Cancel</Button>
          <Button type="submit" variant="primary" disabled={create.isPending}>{create.isPending ? 'Creating…' : 'Create'}</Button>
        </div>
      </form>
    </Drawer>
  )
}

// ---------------------------------------------------------------- detail drawer

function AppointmentDrawer({ appt, onClose }: { appt: Appointment | null; onClose: () => void }) {
  const qc = useQueryClient()
  const toast = useToast()
  const [resched, setResched] = useState({ start_at: '', end_at: '', reason: '' })
  const [cancelOpen, setCancelOpen] = useState(false)

  const { data: reminders } = useQuery({
    queryKey: ['appointment-reminders', appt?.id],
    queryFn: () => api.get<AppointmentReminder[]>(`/appointments/${appt!.id}/reminders`),
    enabled: !!appt,
  })

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ['appointments'] })
    qc.invalidateQueries({ queryKey: ['analytics'] })
    qc.invalidateQueries({ queryKey: ['notifications'] })
  }
  const act = useMutation({
    mutationFn: ({ action, body }: { action: string; body?: unknown }) =>
      api.post<Appointment>(`/appointments/${appt!.id}/${action}`, body ?? {}),
    onSuccess: () => { invalidate(); toast('Updated', 'success') },
    onError: (e) => toast(e instanceof ApiError ? e.message : 'Action failed', 'error'),
  })
  const doReschedule = useMutation({
    mutationFn: () => api.post<Appointment>(`/appointments/${appt!.id}/reschedule`, {
      start_at: new Date(resched.start_at).toISOString(),
      end_at: new Date(resched.end_at).toISOString(),
      reason: resched.reason,
    }),
    onSuccess: () => { invalidate(); setResched({ start_at: '', end_at: '', reason: '' }); toast('Rescheduled', 'success') },
    onError: (e) => toast(e instanceof ApiError ? e.message : 'Reschedule failed', 'error'),
  })
  const doCancel = useMutation({
    mutationFn: () => api.post<Appointment>(`/appointments/${appt!.id}/cancel`, { reason: 'cancelled by user' }),
    onSuccess: () => { invalidate(); setCancelOpen(false); toast('Cancelled', 'success') },
    onError: (e) => toast(e instanceof ApiError ? e.message : 'Cancel failed', 'error'),
  })
  const sync = useMutation({
    mutationFn: () => api.post<{ synced: boolean; reason?: string }>(`/appointments/${appt!.id}/sync-calendar`),
    onSuccess: (r) => toast(r.synced ? 'Calendar synced' : `Sync: ${r.reason ?? 'not synced'}`, r.synced ? 'success' : 'info'),
    onError: (e) => toast(e instanceof ApiError ? e.message : 'Sync failed', 'error'),
  })

  if (!appt) return null
  const editable = !['cancelled', 'completed', 'no_show'].includes(appt.status)
  const canConfirm = ['proposed', 'awaiting_confirmation', 'rescheduling_requested'].includes(appt.status)

  return (
    <>
      <Drawer open={!!appt} onClose={onClose} title={appt.title} width="max-w-2xl">
        <div className="space-y-5">
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone={STATUS_TONE[appt.status]}>{titleCase(appt.status)}</Badge>
            <Badge tone="neutral">{TYPE_LABEL[appt.appointment_type]}</Badge>
            <Badge tone="neutral">via {appt.source}</Badge>
            {appt.calendar_sync === 'synced' && <Badge tone="mint">calendar synced</Badge>}
            {appt.calendar_sync === 'failed' && <Badge tone="red">calendar sync failed</Badge>}
          </div>

          <div className="grid grid-cols-2 gap-4 text-sm">
            <div>
              <p className="text-[11px] uppercase tracking-wide text-ink-400">Prospect</p>
              <p className="font-medium">{appt.lead_business_name ?? '—'}</p>
              {appt.lead_email && <p className="text-xs text-ink-400">{appt.lead_email}</p>}
              {appt.lead_phone && <p className="text-xs text-ink-400">{appt.lead_phone}</p>}
            </div>
            <div>
              <p className="text-[11px] uppercase tracking-wide text-ink-400">When</p>
              <p className="font-medium">{formatDateTime(appt.start_at)} → {formatDateTime(appt.end_at)}</p>
              <p className="text-xs text-ink-400">TZ: {appt.timezone} · Rep: {appt.assigned_to}</p>
            </div>
            {(appt.location || appt.meeting_link) && (
              <div className="col-span-2">
                <p className="text-[11px] uppercase tracking-wide text-ink-400">Meeting details</p>
                {appt.location && <p className="text-sm">{appt.location}</p>}
                {appt.meeting_link && <a className="text-sm text-viol-300 underline" href={appt.meeting_link} target="_blank" rel="noreferrer">{appt.meeting_link}</a>}
              </div>
            )}
            {appt.description && (
              <div className="col-span-2">
                <p className="text-[11px] uppercase tracking-wide text-ink-400">Description</p>
                <p className="text-sm text-ink-300">{appt.description}</p>
              </div>
            )}
            {appt.conversation_id && (
              <div className="col-span-2 text-xs text-ink-400">
                Linked conversation: <span className="font-mono">{appt.conversation_id}</span>
              </div>
            )}
          </div>

          {appt.proposed_slots?.length > 0 && appt.status === 'proposed' && (
            <div>
              <p className="mb-2 text-[11px] uppercase tracking-wide text-ink-400">Offered slots</p>
              <div className="flex flex-wrap gap-2">
                {appt.proposed_slots.map((s) => (
                  <Button key={s.start} size="sm" variant="secondary"
                    onClick={() => act.mutate({ action: 'book-slot', body: { start: s.start, end: s.end } })}>
                    {formatDateTime(s.start)}
                  </Button>
                ))}
              </div>
            </div>
          )}

          {appt.reschedule_history?.length > 0 && (
            <div>
              <p className="mb-2 text-[11px] uppercase tracking-wide text-ink-400">Booking history</p>
              <div className="space-y-1">
                {appt.reschedule_history.map((h, i) => (
                  <p key={i} className="text-xs text-ink-400">
                    {formatDateTime(h.from_start)} → {formatDateTime(h.to_start)} ({h.reason || 'rescheduled'})
                  </p>
                ))}
              </div>
            </div>
          )}

          {appt.cancellation_reason && (
            <p className="text-xs text-red-300">Cancelled by {appt.cancelled_by}: {appt.cancellation_reason}</p>
          )}

          {reminders && reminders.length > 0 && (
            <div>
              <p className="mb-2 text-[11px] uppercase tracking-wide text-ink-400">Reminders</p>
              <div className="space-y-1">
                {reminders.map((r) => (
                  <div key={r.id} className="flex items-center justify-between rounded-lg bg-ink-800/60 px-3 py-1.5 text-xs">
                    <span>{formatDateTime(r.remind_at)} · {r.channel}</span>
                    <Badge tone={r.status === 'sent' ? 'mint' : r.status === 'failed' ? 'red' : r.status === 'cancelled' ? 'neutral' : 'amber'}>{r.status.replace('_', ' ')}</Badge>
                  </div>
                ))}
              </div>
            </div>
          )}

          {editable && (
            <div className="space-y-4 border-t border-ink-700/50 pt-4">
              <p className="text-[11px] uppercase tracking-wide text-ink-400">Manage</p>
              <div className="grid grid-cols-2 gap-3">
                <Field label="New start"><Input type="datetime-local" value={resched.start_at} onChange={(e) => setResched({ ...resched, start_at: e.target.value })} /></Field>
                <Field label="New end"><Input type="datetime-local" value={resched.end_at} onChange={(e) => setResched({ ...resched, end_at: e.target.value })} /></Field>
              </div>
              <Field label="Reschedule reason"><Input value={resched.reason} onChange={(e) => setResched({ ...resched, reason: e.target.value })} /></Field>
              <div className="flex flex-wrap gap-2">
                {canConfirm && <Button size="sm" variant="success" disabled={act.isPending} onClick={() => act.mutate({ action: 'confirm' })}>Confirm</Button>}
                <Button size="sm" variant="secondary" disabled={!resched.start_at || !resched.end_at || doReschedule.isPending}
                  onClick={() => doReschedule.mutate()}>Reschedule</Button>
                <Button size="sm" variant="secondary" disabled={sync.isPending} onClick={() => sync.mutate()}>Sync calendar</Button>
                <Button size="sm" variant="success" disabled={act.isPending} onClick={() => act.mutate({ action: 'complete' })}>Mark completed</Button>
                <Button size="sm" variant="secondary" disabled={act.isPending} onClick={() => act.mutate({ action: 'no-show' })}>No-show</Button>
                <Button size="sm" variant="danger" onClick={() => setCancelOpen(true)}>Cancel</Button>
              </div>
            </div>
          )}
        </div>
      </Drawer>
      <ConfirmDialog open={cancelOpen} title="Cancel appointment" danger
        message={`Cancel '${appt.title}'? Reminders will be cancelled and the prospect history preserved.`}
        confirmLabel="Cancel appointment" busy={doCancel.isPending}
        onConfirm={() => doCancel.mutate()} onCancel={() => setCancelOpen(false)} />
    </>
  )
}

// ---------------------------------------------------------------- availability tab

function AvailabilityTab() {
  const qc = useQueryClient()
  const toast = useToast()
  const { data: settings, isLoading } = useQuery({
    queryKey: ['scheduling-settings'],
    queryFn: () => api.get<SchedulingSettings>('/scheduling/settings'),
  })
  const [form, setForm] = useState<Record<string, unknown> | null>(null)
  const [newHoliday, setNewHoliday] = useState('')
  const [newBlackout, setNewBlackout] = useState({ date: '', start: '13:00', end: '14:00' })

  const f = form ?? (settings ? {
    timezone: settings.timezone, working_days: settings.working_days,
    work_start: settings.work_start, work_end: settings.work_end,
    lunch_start: settings.lunch_start ?? '', lunch_end: settings.lunch_end ?? '',
    slot_minutes: settings.slot_minutes, buffer_minutes: settings.buffer_minutes,
    min_notice_hours: settings.min_notice_hours, max_horizon_days: settings.max_horizon_days,
    holidays: settings.holidays, blackout_windows: settings.blackout_windows,
    reminder_offsets_minutes: settings.reminder_offsets_minutes,
    default_duration_minutes: settings.default_duration_minutes,
    require_confirmation: settings.require_confirmation,
    allow_autonomous_booking: settings.allow_autonomous_booking,
    calendar_provider: settings.calendar_provider,
  } : null)

  const save = useMutation({
    mutationFn: () => api.put<SchedulingSettings>('/scheduling/settings', {
      ...f,
      lunch_start: (f?.lunch_start as string) || null,
      lunch_end: (f?.lunch_end as string) || null,
      holidays: f?.holidays ?? [],
      blackout_windows: f?.blackout_windows ?? [],
      reminder_offsets_minutes: (f?.reminder_offsets_minutes as number[]) ?? [1440, 60],
    }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['scheduling-settings'] }); setForm(null); toast('Availability settings saved', 'success') },
    onError: (e) => toast(e instanceof ApiError ? e.message : 'Save failed', 'error'),
  })

  const set = (k: string, v: unknown) => setForm({ ...(f ?? {}), [k]: v })

  const { data: providers } = useQuery({
    queryKey: ['calendar-providers'],
    queryFn: () => api.get<{ providers: { name: string; configured: boolean }[]; internal_engine: string }>('/scheduling/providers'),
  })

  if (isLoading || !f) return <Skeleton className="h-96" />
  const blackout = (f.blackout_windows as { date?: string; start?: string; end?: string }[]) ?? []
  const holidays = (f.holidays as string[]) ?? []

  return (
    <div className="grid gap-5 lg:grid-cols-3">
      <Card className="lg:col-span-2">
        <CardHeader title="Working hours & booking rules" subtitle="Used by the internal scheduling engine — no external calendar required" />
        <div className="space-y-4 p-5">
          <div className="grid grid-cols-2 gap-3">
            <Field label="Timezone">
              <Select value={f.timezone as string} onChange={(e) => set('timezone', e.target.value)}>
                {['Asia/Kolkata', 'UTC', 'America/New_York', 'Europe/London', 'Asia/Dubai', 'Asia/Singapore'].map((tz) => <option key={tz}>{tz}</option>)}
              </Select>
            </Field>
            <Field label="Calendar provider">
              <Select value={f.calendar_provider as string} onChange={(e) => set('calendar_provider', e.target.value)}>
                <option value="internal">Internal only</option>
                {(providers?.providers ?? []).map((p) => <option key={p.name} value={p.name}>{p.name}{p.configured ? '' : ' (not configured)'}</option>)}
              </Select>
            </Field>
          </div>
          <Field label="Working days">
            <div className="flex gap-1.5">
              {WEEKDAYS.map((d, i) => {
                const days = (f.working_days as number[]) ?? []
                const active = days.includes(i)
                return (
                  <button key={d} type="button"
                    onClick={() => set('working_days', active ? days.filter((x) => x !== i) : [...days, i].sort())}
                    className={cn('h-8 w-11 rounded-lg text-xs font-medium border cursor-pointer',
                      active ? 'bg-viol-500/20 text-viol-300 border-viol-500/40' : 'bg-ink-800 text-ink-400 border-ink-700')}>
                    {d}
                  </button>
                )
              })}
            </div>
          </Field>
          <div className="grid grid-cols-4 gap-3">
            <Field label="Work start"><Input type="time" value={f.work_start as string} onChange={(e) => set('work_start', e.target.value)} /></Field>
            <Field label="Work end"><Input type="time" value={f.work_end as string} onChange={(e) => set('work_end', e.target.value)} /></Field>
            <Field label="Lunch start"><Input type="time" value={(f.lunch_start as string) ?? ''} onChange={(e) => set('lunch_start', e.target.value || null)} /></Field>
            <Field label="Lunch end"><Input type="time" value={(f.lunch_end as string) ?? ''} onChange={(e) => set('lunch_end', e.target.value || null)} /></Field>
          </div>
          <div className="grid grid-cols-4 gap-3">
            <Field label="Slot (min)"><Input type="number" min={5} value={f.slot_minutes as number} onChange={(e) => set('slot_minutes', Number(e.target.value))} /></Field>
            <Field label="Buffer (min)"><Input type="number" min={0} value={f.buffer_minutes as number} onChange={(e) => set('buffer_minutes', Number(e.target.value))} /></Field>
            <Field label="Min notice (h)"><Input type="number" min={0} value={f.min_notice_hours as number} onChange={(e) => set('min_notice_hours', Number(e.target.value))} /></Field>
            <Field label="Horizon (days)"><Input type="number" min={1} value={f.max_horizon_days as number} onChange={(e) => set('max_horizon_days', Number(e.target.value))} /></Field>
          </div>
          <div className="grid grid-cols-3 gap-3">
            <Field label="Duration (min)"><Input type="number" min={5} value={f.default_duration_minutes as number} onChange={(e) => set('default_duration_minutes', Number(e.target.value))} /></Field>
            <Field label="Reminders (min before)"><Input value={(f.reminder_offsets_minutes as number[]).join(', ')} onChange={(e) => set('reminder_offsets_minutes', e.target.value.split(',').map((x) => parseInt(x.trim(), 10)).filter((n) => !isNaN(n)))} /></Field>
          </div>
          <div className="flex flex-wrap gap-4">
            <label className="flex items-center gap-2 text-sm text-ink-200 cursor-pointer">
              <input type="checkbox" checked={!!f.require_confirmation} onChange={(e) => set('require_confirmation', e.target.checked)} className="accent-viol-500" />
              Require approval before confirming bookings
            </label>
            <label className="flex items-center gap-2 text-sm text-ink-200 cursor-pointer">
              <input type="checkbox" checked={!!f.allow_autonomous_booking} onChange={(e) => set('allow_autonomous_booking', e.target.checked)} className="accent-viol-500" />
              Allow autonomous slot booking
            </label>
          </div>
          <div className="flex justify-end gap-2 border-t border-ink-700/50 pt-4">
            <Button variant="ghost" onClick={() => { setForm(null); }}>Reset</Button>
            <Button variant="primary" disabled={save.isPending} onClick={() => save.mutate()}>{save.isPending ? 'Saving…' : 'Save settings'}</Button>
          </div>
        </div>
      </Card>

      <div className="space-y-5">
        <Card>
          <CardHeader title="Holidays" subtitle="Dates with no availability (YYYY-MM-DD)" />
          <div className="space-y-2 p-4">
            {holidays.map((h) => (
              <div key={h} className="flex items-center justify-between rounded-lg bg-ink-800/60 px-3 py-1.5 text-xs">
                <span>{h}</span>
                <button className="text-red-300 hover:text-red-200 cursor-pointer" onClick={() => set('holidays', holidays.filter((x) => x !== h))}>Remove</button>
              </div>
            ))}
            <div className="flex gap-2">
              <Input type="date" value={newHoliday} onChange={(e) => setNewHoliday(e.target.value)} />
              <Button size="sm" variant="secondary" disabled={!newHoliday}
                onClick={() => { set('holidays', [...holidays, newHoliday].sort()); setNewHoliday('') }}>Add</Button>
            </div>
          </div>
        </Card>
        <Card>
          <CardHeader title="Blackout windows" subtitle="Daily or one-off unavailable time ranges" />
          <div className="space-y-2 p-4">
            {blackout.map((b, i) => (
              <div key={i} className="flex items-center justify-between rounded-lg bg-ink-800/60 px-3 py-1.5 text-xs">
                <span>{b.date ?? 'daily'} {b.start}–{b.end}</span>
                <button className="text-red-300 hover:text-red-200 cursor-pointer"
                  onClick={() => set('blackout_windows', blackout.filter((_, j) => j !== i))}>Remove</button>
              </div>
            ))}
            <div className="grid grid-cols-4 gap-2">
              <Input type="date" value={newBlackout.date} onChange={(e) => setNewBlackout({ ...newBlackout, date: e.target.value })} />
              <Input type="time" value={newBlackout.start} onChange={(e) => setNewBlackout({ ...newBlackout, start: e.target.value })} />
              <Input type="time" value={newBlackout.end} onChange={(e) => setNewBlackout({ ...newBlackout, end: e.target.value })} />
              <Button size="sm" variant="secondary" disabled={!newBlackout.date}
                onClick={() => { set('blackout_windows', [...blackout, { ...newBlackout }]); }}>Add</Button>
            </div>
            <p className="text-[11px] text-ink-400">Leave date empty-style entries unsupported — each blackout needs a date. Clear the date field pattern is not supported; add one per date.</p>
          </div>
        </Card>
        <Card>
          <CardHeader title="Calendar providers" />
          <div className="space-y-2 p-4">
            {(providers?.providers ?? []).map((p) => (
              <div key={p.name} className="flex items-center justify-between rounded-lg bg-ink-800/60 px-3 py-2 text-xs">
                <span className="font-medium">{titleCase(p.name)}</span>
                <Badge tone={p.configured ? 'mint' : 'neutral'}>{p.configured ? 'configured' : 'not configured'}</Badge>
              </div>
            ))}
            <p className="text-[11px] text-ink-400">{providers?.internal_engine ?? 'Internal engine always available'}</p>
          </div>
        </Card>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------- assistant tab

function AssistantTab() {
  const { data, isLoading } = useQuery({
    queryKey: ['assistant-dashboard'],
    queryFn: () => api.get<AssistantDashboard>('/appointments/dashboard'),
    refetchInterval: 30000,
  })
  const { data: pending } = useQuery({
    queryKey: ['appointments', 'proposed'],
    queryFn: () => api.get<{ items: Appointment[] }>('/appointments?status=proposed&page_size=20'),
  })
  const { data: resched } = useQuery({
    queryKey: ['appointments', 'rescheduling'],
    queryFn: () => api.get<{ items: Appointment[] }>('/appointments?status=rescheduling_requested&page_size=20'),
  })

  if (isLoading || !data) return <Skeleton className="h-64" />
  const cards: [string, number, string][] = [
    ['Upcoming demos', data.upcoming_demos, 'violet'],
    ["Today's meetings", data.today_meetings, 'blue'],
    ['Pending booking requests', data.pending_booking_requests, 'amber'],
    ['Rescheduling requests', data.rescheduling_requests, 'violet'],
    ['Cancellations (30d)', data.cancellations, 'red'],
    ['No-shows', data.no_shows, 'red'],
    ['Completed demos', data.completed_demos, 'mint'],
  ]
  const toneMap: Record<string, 'neutral' | 'violet' | 'mint' | 'amber' | 'red' | 'blue'> = {
    violet: 'violet', blue: 'blue', amber: 'amber', red: 'red', mint: 'mint', neutral: 'neutral',
  }
  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        {cards.map(([label, value, tone]) => (
          <Card key={label} className="p-4">
            <p className="text-[11px] uppercase tracking-wide text-ink-400">{label}</p>
            <p className="mt-1 text-2xl font-bold"><Badge tone={toneMap[tone]}>{value}</Badge></p>
          </Card>
        ))}
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader title="Pending booking requests" subtitle="Proposals awaiting a prospect's choice" />
          <div className="divide-y divide-ink-800">
            {(pending?.items ?? []).length === 0 && <p className="px-5 py-6 text-xs text-ink-400">None pending</p>}
            {(pending?.items ?? []).map((a) => (
              <div key={a.id} className="flex items-center justify-between px-5 py-3 text-sm">
                <div>
                  <p className="font-medium">{a.title}</p>
                  <p className="text-xs text-ink-400">{a.lead_business_name} · {a.proposed_slots?.length ?? 0} slots offered</p>
                </div>
                <Badge tone="blue">proposed</Badge>
              </div>
            ))}
          </div>
        </Card>
        <Card>
          <CardHeader title="Rescheduling requests" />
          <div className="divide-y divide-ink-800">
            {(resched?.items ?? []).length === 0 && <p className="px-5 py-6 text-xs text-ink-400">None pending</p>}
            {(resched?.items ?? []).map((a) => (
              <div key={a.id} className="flex items-center justify-between px-5 py-3 text-sm">
                <div>
                  <p className="font-medium">{a.title}</p>
                  <p className="text-xs text-ink-400">{a.lead_business_name} · {formatDateTime(a.start_at)}</p>
                </div>
                <Badge tone="violet">rescheduling</Badge>
              </div>
            ))}
          </div>
        </Card>
      </div>
      <Card>
        <CardHeader title="Calendar provider" subtitle="External sync status" />
        <div className="p-5 text-sm text-ink-300">
          Provider: <span className="font-medium">{data.calendar_provider}</span>
          {data.calendar_provider === 'internal' && <span className="ml-2 text-xs text-ink-400">(internal scheduling only — connect Google Calendar in Availability settings)</span>}
        </div>
      </Card>
    </div>
  )
}
