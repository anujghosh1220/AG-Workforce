import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api, ApiError, type Agent, type Lead, type TaskItem } from '../lib/api'
import { PageHeader } from '../components/metrics'
import {
  Badge, Button, Card, Drawer, EmptyState, Field, Input, Select,
  Skeleton, Textarea, useToast,
} from '../components/ui'
import { formatDate, titleCase } from '../lib/utils'

const PRIORITY_TONE: Record<string, 'neutral' | 'blue' | 'amber' | 'red'> = {
  low: 'neutral', medium: 'blue', high: 'amber', urgent: 'red',
}

export default function Tasks() {
  const qc = useQueryClient()
  const toast = useToast()
  const [statusFilter, setStatusFilter] = useState('')
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [form, setForm] = useState({
    title: '', description: '', assignee_type: 'agent', assignee_agent_id: '',
    priority: 'medium', due_date: '', lead_id: '',
  })

  const params = new URLSearchParams({ page_size: '100' })
  if (statusFilter) params.set('status', statusFilter)

  const { data, isLoading } = useQuery({
    queryKey: ['tasks', statusFilter],
    queryFn: () => api.get<{ items: TaskItem[]; total: number }>(`/tasks?${params}`),
  })
  const { data: agents } = useQuery({
    queryKey: ['agents'],
    queryFn: () => api.get<{ items: Agent[] }>('/agents?page_size=50'),
  })
  const { data: leads } = useQuery({
    queryKey: ['leads-options'],
    queryFn: () => api.get<{ items: Lead[] }>('/leads?page_size=100'),
  })

  const create = useMutation({
    mutationFn: () => api.post<TaskItem>('/tasks', {
      title: form.title,
      description: form.description,
      assignee_type: form.assignee_type,
      assignee_agent_id: form.assignee_type === 'agent' && form.assignee_agent_id ? form.assignee_agent_id : null,
      priority: form.priority,
      due_date: form.due_date ? new Date(form.due_date).toISOString() : null,
      lead_id: form.lead_id || null,
    }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['tasks'] })
      setDrawerOpen(false)
      setForm({ title: '', description: '', assignee_type: 'agent', assignee_agent_id: '', priority: 'medium', due_date: '', lead_id: '' })
      toast('Task created', 'success')
    },
    onError: (e) => toast(e instanceof ApiError ? e.message : 'Create failed', 'error'),
  })

  const update = useMutation({
    mutationFn: ({ id, patch }: { id: string; patch: Record<string, unknown> }) => api.patch<TaskItem>(`/tasks/${id}`, patch),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['tasks'] })
      qc.invalidateQueries({ queryKey: ['analytics'] })
      toast('Task updated', 'success')
    },
    onError: (e) => toast(e instanceof ApiError ? e.message : 'Update failed', 'error'),
  })

  return (
    <div>
      <PageHeader
        title="Tasks"
        subtitle="Assign work to AI employees or teammates"
        action={<Button variant="primary" onClick={() => setDrawerOpen(true)}>New task</Button>}
      />

      <div className="mb-4 flex gap-2">
        {['', 'pending', 'in_progress', 'blocked', 'completed', 'cancelled'].map((s) => (
          <button
            key={s || 'all'}
            onClick={() => setStatusFilter(s)}
            className={`rounded-full px-3 py-1.5 text-xs font-medium transition-colors cursor-pointer ${statusFilter === s ? 'bg-viol-500/20 text-viol-300 border border-viol-500/40' : 'bg-ink-800 text-ink-400 border border-ink-700 hover:text-ink-200'}`}
          >
            {s ? titleCase(s) : 'All'}
          </button>
        ))}
      </div>

      {isLoading ? (
        <div className="space-y-3">{Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-20" />)}</div>
      ) : !data?.items.length ? (
        <EmptyState title="No tasks" hint="Create a task and assign it to an AI employee or a teammate." action={<Button size="sm" variant="primary" onClick={() => setDrawerOpen(true)}>New task</Button>} />
      ) : (
        <div className="stagger space-y-3">
          {data.items.map((t) => (
            <Card key={t.id} className="flex flex-wrap items-center gap-4 p-4">
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <p className="text-sm font-semibold">{t.title}</p>
                  <Badge tone={PRIORITY_TONE[t.priority]}>{titleCase(t.priority)}</Badge>
                  <Badge tone="neutral">{titleCase(t.status)}</Badge>
                </div>
                {t.description && <p className="mt-1 line-clamp-1 text-xs text-ink-400">{t.description}</p>}
                <div className="mt-1.5 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-ink-400">
                  <span>Assignee: <span className="text-ink-200">{t.assignee_name ?? 'Unassigned'}</span></span>
                  {t.lead_business_name && <span>Lead: <span className="text-ink-200">{t.lead_business_name}</span></span>}
                  {t.due_date && <span>Due: <span className="text-ink-200">{formatDate(t.due_date)}</span></span>}
                  <span>Created {formatDate(t.created_at)}</span>
                </div>
              </div>
              <div className="flex gap-2">
                {t.status !== 'completed' && t.status !== 'cancelled' && (
                  <>
                    {t.status === 'pending' && <Button size="sm" variant="secondary" onClick={() => update.mutate({ id: t.id, patch: { status: 'in_progress' } })}>Start</Button>}
                    <Button size="sm" variant="success" onClick={() => update.mutate({ id: t.id, patch: { status: 'completed' } })}>Complete</Button>
                  </>
                )}
                {t.status !== 'cancelled' && t.status !== 'completed' && (
                  <Button size="sm" variant="ghost" onClick={() => update.mutate({ id: t.id, patch: { status: 'cancelled' } })}>Cancel</Button>
                )}
              </div>
            </Card>
          ))}
        </div>
      )}

      <Drawer open={drawerOpen} onClose={() => setDrawerOpen(false)} title="New task">
        <form
          onSubmit={(e) => { e.preventDefault(); create.mutate() }}
          className="space-y-4"
        >
          <Field label="Title *"><Input required value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} /></Field>
          <Field label="Description"><Textarea value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} /></Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Assignee type">
              <Select value={form.assignee_type} onChange={(e) => setForm({ ...form, assignee_type: e.target.value })}>
                <option value="agent">AI employee</option>
                <option value="human">Human</option>
              </Select>
            </Field>
            <Field label="Priority">
              <Select value={form.priority} onChange={(e) => setForm({ ...form, priority: e.target.value })}>
                {['low', 'medium', 'high', 'urgent'].map((p) => <option key={p} value={p}>{titleCase(p)}</option>)}
              </Select>
            </Field>
          </div>
          {form.assignee_type === 'agent' && (
            <Field label="AI employee">
              <Select value={form.assignee_agent_id} onChange={(e) => setForm({ ...form, assignee_agent_id: e.target.value })}>
                <option value="">Select agent…</option>
                {(agents?.items ?? []).map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
              </Select>
            </Field>
          )}
          <div className="grid grid-cols-2 gap-3">
            <Field label="Due date"><Input type="datetime-local" value={form.due_date} onChange={(e) => setForm({ ...form, due_date: e.target.value })} /></Field>
            <Field label="Related lead">
              <Select value={form.lead_id} onChange={(e) => setForm({ ...form, lead_id: e.target.value })}>
                <option value="">None</option>
                {(leads?.items ?? []).map((l) => <option key={l.id} value={l.id}>{l.business_name}</option>)}
              </Select>
            </Field>
          </div>
          <div className="flex justify-end gap-2 pt-2">
            <Button type="button" variant="ghost" onClick={() => setDrawerOpen(false)}>Cancel</Button>
            <Button type="submit" variant="primary" disabled={create.isPending}>{create.isPending ? 'Creating…' : 'Create task'}</Button>
          </div>
        </form>
      </Drawer>
    </div>
  )
}
