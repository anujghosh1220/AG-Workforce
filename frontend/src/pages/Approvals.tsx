import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, Mail, MessageCircle, Plus, X } from 'lucide-react'
import { useState } from 'react'
import { api, ApiError, type Agent, type Approval, type Lead } from '../lib/api'
import { PageHeader } from '../components/metrics'
import {
  Badge, Button, Card, Drawer, EmptyState, Field, Input, Select,
  Skeleton, Textarea, useToast,
} from '../components/ui'
import { formatDateTime, titleCase } from '../lib/utils'

const ACTION_TYPES = ['send_email', 'send_whatsapp', 'schedule_meeting', 'offer_discount', 'change_pricing']
const ACTION_ICON: Record<string, typeof Mail> = {
  send_email: Mail, send_whatsapp: MessageCircle, schedule_meeting: Check,
  offer_discount: Check, change_pricing: Check,
}
const STATUS_TONE: Record<string, 'amber' | 'mint' | 'red' | 'neutral'> = {
  pending: 'amber', approved: 'mint', rejected: 'red', expired: 'neutral', cancelled: 'neutral',
}

export default function Approvals() {
  const qc = useQueryClient()
  const toast = useToast()
  const [statusFilter, setStatusFilter] = useState('pending')
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [form, setForm] = useState({ action_type: 'send_email', title: '', payload: '', lead_id: '' })

  const params = new URLSearchParams({ page_size: '100' })
  if (statusFilter) params.set('status', statusFilter)

  const { data, isLoading } = useQuery({
    queryKey: ['approvals', statusFilter],
    queryFn: () => api.get<{ items: Approval[]; total: number }>(`/approvals?${params}`),
  })
  const { data: leads } = useQuery({
    queryKey: ['leads-options'],
    queryFn: () => api.get<{ items: Lead[] }>('/leads?page_size=100'),
  })
  const { data: agents } = useQuery({
    queryKey: ['agents'],
    queryFn: () => api.get<{ items: Agent[] }>('/agents?page_size=50'),
  })

  const review = useMutation({
    mutationFn: ({ id, status }: { id: string; status: string }) =>
      api.patch<Approval>(`/approvals/${id}`, { status }),
    onSuccess: (_, v) => {
      qc.invalidateQueries({ queryKey: ['approvals'] })
      qc.invalidateQueries({ queryKey: ['approvals-pending'] })
      qc.invalidateQueries({ queryKey: ['notifications'] })
      toast(`Approval ${v.status}`, 'success')
    },
    onError: (e) => toast(e instanceof ApiError ? e.message : 'Review failed', 'error'),
  })

  const create = useMutation({
    mutationFn: () => api.post<Approval>('/approvals', {
      action_type: form.action_type,
      title: form.title,
      payload: form.payload ? JSON.parse(form.payload) : {},
      lead_id: form.lead_id || null,
      requested_by_agent_id: agents?.items[0]?.id ?? null,
    }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['approvals'] })
      setDrawerOpen(false)
      setForm({ action_type: 'send_email', title: '', payload: '', lead_id: '' })
      toast('Approval request created', 'success')
    },
    onError: (e) => toast(e instanceof ApiError ? e.message : 'Create failed', 'error'),
  })

  return (
    <div>
      <PageHeader
        title="Approvals"
        subtitle="Human-in-the-loop gate for sensitive AI actions. Approval records never execute external actions directly."
        action={<Button variant="primary" onClick={() => setDrawerOpen(true)}><Plus className="h-4 w-4" /> Request approval</Button>}
      />

      <div className="mb-4 flex gap-2">
        {['pending', 'approved', 'rejected', 'expired', 'cancelled', ''].map((s) => (
          <button key={s || 'all'} onClick={() => setStatusFilter(s)}
            className={`rounded-full px-3 py-1.5 text-xs font-medium transition-colors cursor-pointer ${statusFilter === s ? 'bg-viol-500/20 text-viol-300 border border-viol-500/40' : 'bg-ink-800 text-ink-400 border border-ink-700 hover:text-ink-200'}`}>
            {s ? titleCase(s) : 'All'}
          </button>
        ))}
      </div>

      {isLoading ? (
        <div className="space-y-3">{Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} className="h-24" />)}</div>
      ) : !data?.items.length ? (
        <EmptyState title="No approvals" hint={statusFilter === 'pending' ? 'Nothing awaiting your review.' : 'No approval records match this filter.'} />
      ) : (
        <div className="stagger space-y-3">
          {data.items.map((a) => {
            const Icon = ACTION_ICON[a.action_type] ?? Check
            return (
              <Card key={a.id} className="p-4">
                <div className="flex flex-wrap items-start gap-4">
                  <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-amber-500/10 text-amber-300">
                    <Icon className="h-5 w-5" />
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <p className="text-sm font-semibold">{a.title}</p>
                      <Badge tone="violet">{titleCase(a.action_type)}</Badge>
                      {a.risk_level && (
                        <Badge tone={a.risk_level === 'high' ? 'red' : a.risk_level === 'medium' ? 'amber' : 'mint'}>
                          {titleCase(a.risk_level)} risk
                        </Badge>
                      )}
                      <Badge tone={STATUS_TONE[a.status]}>{titleCase(a.status)}</Badge>
                    </div>
                    <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-ink-400">
                      {a.lead_business_name && <span>Lead: <span className="text-ink-200">{a.lead_business_name}</span></span>}
                      {a.requester_name && <span>Requested by: <span className="text-ink-200">{a.requester_name}</span></span>}
                      <span>{formatDateTime(a.created_at)}</span>
                      {a.decided_at && <span>Decided {formatDateTime(a.decided_at)}</span>}
                    </div>
                    {Object.keys(a.payload ?? {}).length > 0 && (
                      <pre className="mt-2 max-h-32 overflow-auto rounded-lg bg-ink-900 p-2 text-[11px] text-ink-300">{JSON.stringify(a.payload, null, 2)}</pre>
                    )}
                  </div>
                  {a.status === 'pending' && (
                    <div className="flex gap-2">
                      <Button size="sm" variant="success" onClick={() => review.mutate({ id: a.id, status: 'approved' })} disabled={review.isPending}>
                        <Check className="h-3.5 w-3.5" /> Approve
                      </Button>
                      <Button size="sm" variant="danger" onClick={() => review.mutate({ id: a.id, status: 'rejected' })} disabled={review.isPending}>
                        <X className="h-3.5 w-3.5" /> Reject
                      </Button>
                    </div>
                  )}
                </div>
              </Card>
            )
          })}
        </div>
      )}

      <Drawer open={drawerOpen} onClose={() => setDrawerOpen(false)} title="Request approval">
        <form onSubmit={(e) => { e.preventDefault(); create.mutate() }} className="space-y-4">
          <Field label="Action type">
            <Select value={form.action_type} onChange={(e) => setForm({ ...form, action_type: e.target.value })}>
              {ACTION_TYPES.map((t) => <option key={t} value={t}>{titleCase(t)}</option>)}
            </Select>
          </Field>
          <Field label="Title *"><Input required value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} placeholder="e.g. Send intro email to Sunrise Café" /></Field>
          <Field label="Related lead">
            <Select value={form.lead_id} onChange={(e) => setForm({ ...form, lead_id: e.target.value })}>
              <option value="">None</option>
              {(leads?.items ?? []).map((l) => <option key={l.id} value={l.id}>{l.business_name}</option>)}
            </Select>
          </Field>
          <Field label="Payload (JSON)" hint="Details the future action executor will need — e.g. {&quot;to&quot;: &quot;...&quot;, &quot;subject&quot;: &quot;...&quot;}" >
            <Textarea className="font-mono text-xs" value={form.payload} onChange={(e) => setForm({ ...form, payload: e.target.value })} placeholder='{"to": "owner@cafe.com"}' />
          </Field>
          <p className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-[11px] text-amber-200">
            Creating this record does not send anything. Execution happens only through the secure action service in a later phase.
          </p>
          <div className="flex justify-end gap-2 pt-2">
            <Button type="button" variant="ghost" onClick={() => setDrawerOpen(false)}>Cancel</Button>
            <Button type="submit" variant="primary" disabled={create.isPending}>{create.isPending ? 'Creating…' : 'Create request'}</Button>
          </div>
        </form>
      </Drawer>
    </div>
  )
}
