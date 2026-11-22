import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api, ApiError } from '../lib/api'
import { PageHeader } from '../components/metrics'
import { Badge, Button, Card, CardHeader, Drawer, EmptyState, Field, Input, Skeleton, useToast } from '../components/ui'
import { cn, formatDateTime, titleCase } from '../lib/utils'

interface Workflow {
  id: string; workflow_type: string; objective: string; status: string
  autonomy_mode: string; correlation_id: string; step_count: number
  completed_steps: number; error: string | null; created_by: string
  started_at: string | null; finished_at: string | null; created_at: string
}
interface Step {
  id: string; step_key: string; agent_slug: string; task_type: string
  title: string; sequence: number; depends_on: string[]; status: string
  output: Record<string, unknown>; attempts: number; max_attempts: number
  error: string | null; approval_id: string | null
  started_at: string | null; finished_at: string | null
}
interface WfEvent { id: string; event_type: string; status: string; attempts: number; created_at: string; processed_at: string | null; payload: Record<string, unknown> }
interface Page<T> { items: T[]; total: number }
interface AgentCard { name: string; status: string; enabled: boolean; tasks: string[] }

const STATUS_TONE: Record<string, 'neutral' | 'violet' | 'mint' | 'amber' | 'red' | 'blue'> = {
  queued: 'blue', running: 'violet', paused: 'amber', waiting_approval: 'amber',
  completed: 'mint', failed: 'red', cancelled: 'neutral',
  pending: 'blue', ready: 'blue', awaiting_approval: 'amber', skipped: 'neutral',
}

export default function Workforce() {
  const [selected, setSelected] = useState<string | null>(null)
  const [createOpen, setCreateOpen] = useState(false)
  return (
    <div>
      <PageHeader title="Workforce Command Centre"
        subtitle="Orchestrated execution across all five AI employees"
        action={<Button size="sm" variant="primary" onClick={() => setCreateOpen(true)}>New workflow</Button>} />
      <AgentsStrip />
      <AutonomyBar />
      <div className="mt-5 grid gap-5 lg:grid-cols-3">
        <div className="lg:col-span-2">
          <WorkflowsList onSelect={setSelected} />
        </div>
        <EventFeed workflowId={selected} />
      </div>
      <WorkflowDrawer id={selected} onClose={() => setSelected(null)} />
      <CreateWorkflow open={createOpen} onClose={() => setCreateOpen(false)} />
    </div>
  )
}

function AgentsStrip() {
  const { data } = useQuery({
    queryKey: ['wf-plans'],
    queryFn: () => api.get<{ capabilities: Record<string, AgentCard> }>('/workflows/plans'),
    refetchInterval: 60000,
  })
  const caps = data?.capabilities ?? {}
  return (
    <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
      {Object.entries(caps).map(([slug, c]) => (
        <Card key={slug} className="p-3">
          <div className="flex items-center justify-between">
            <p className="truncate text-xs font-semibold">{c.name}</p>
            <Badge tone={c.enabled ? 'mint' : 'neutral'}>{c.status}</Badge>
          </div>
          <p className="mt-1 text-[10px] text-ink-400">{c.tasks.length} capabilities</p>
        </Card>
      ))}
    </div>
  )
}

function AutonomyBar() {
  const qc = useQueryClient()
  const toast = useToast()
  const { data } = useQuery({
    queryKey: ['orchestration-config'],
    queryFn: () => api.get<{ mode: string; agents: Record<string, { mode?: string }> }>('/workflows/config/orchestration'),
  })
  const save = useMutation({
    mutationFn: (mode: string) => api.put('/workflows/config/orchestration', { mode, agents: {} }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['orchestration-config'] }); toast('Autonomy mode updated', 'success') },
    onError: (e) => toast(e instanceof ApiError ? e.message : 'Failed', 'error'),
  })
  const modes = ['manual', 'hybrid', 'autonomous'] as const
  return (
    <Card className="mt-4 flex flex-wrap items-center gap-3 p-4">
      <p className="text-xs font-semibold uppercase tracking-wide text-ink-400">Autonomy mode</p>
      <div className="flex gap-1.5">
        {modes.map((m) => (
          <button key={m} disabled={save.isPending}
            onClick={() => save.mutate(m)}
            className={cn('rounded-full px-3 py-1 text-xs font-medium border cursor-pointer',
              data?.mode === m ? 'bg-viol-500/20 text-viol-300 border-viol-500/40' : 'bg-ink-800 text-ink-400 border-ink-700 hover:text-ink-200')}>
            {titleCase(m)}
          </button>
        ))}
      </div>
      <p className="ml-auto text-[11px] text-ink-400">
        Consent, approvals and permissions are never bypassed — even in autonomous mode.
      </p>
    </Card>
  )
}

function WorkflowsList({ onSelect }: { onSelect: (id: string) => void }) {
  const [statusFilter, setStatusFilter] = useState('')
  const params = new URLSearchParams({ page_size: '50' })
  if (statusFilter) params.set('status', statusFilter)
  const { data, isLoading } = useQuery({
    queryKey: ['workflows', statusFilter],
    queryFn: () => api.get<Page<Workflow>>(`/workflows?${params}`),
    refetchInterval: 10000,
  })
  return (
    <Card>
      <CardHeader title="Workflows" subtitle="Persistent orchestrated executions"
        action={
          <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}
            className="rounded-lg border border-ink-600 bg-ink-800 px-2 py-1 text-xs text-ink-200">
            <option value="">All statuses</option>
            {['queued', 'running', 'paused', 'waiting_approval', 'completed', 'failed', 'cancelled'].map((s) =>
              <option key={s} value={s}>{titleCase(s)}</option>)}
          </select>
        } />
      {isLoading ? <div className="space-y-2 p-4">{Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-16" />)}</div>
        : !data?.items.length ? <EmptyState title="No workflows" hint="Create a café acquisition workflow to get started." />
        : <div className="divide-y divide-ink-800">
            {data.items.map((w) => (
              <button key={w.id} onClick={() => onSelect(w.id)}
                className="flex w-full items-center justify-between gap-3 px-5 py-3 text-left hover:bg-ink-800/40 cursor-pointer">
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium">{w.objective}</p>
                  <p className="text-xs text-ink-400">
                    {titleCase(w.workflow_type)} · {w.completed_steps}/{w.step_count} steps · {w.autonomy_mode} · {w.correlation_id}
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  {w.error && <span className="max-w-40 truncate text-[10px] text-red-300">{w.error}</span>}
                  <Badge tone={STATUS_TONE[w.status] ?? 'neutral'}>{titleCase(w.status)}</Badge>
                </div>
              </button>
            ))}
          </div>}
    </Card>
  )
}

function EventFeed({ workflowId }: { workflowId: string | null }) {
  const { data } = useQuery({
    queryKey: ['wf-events', workflowId],
    queryFn: () => workflowId
      ? api.get<WfEvent[]>(`/workflows/${workflowId}/events`)
      : api.get<Page<WfEvent>>('/workflows/events/all?page_size=30').then((p) => p.items),
    refetchInterval: 8000,
  })
  const events = (data ?? []) as WfEvent[]
  return (
    <Card className="max-h-[560px] overflow-y-auto">
      <CardHeader title="Event stream" subtitle={workflowId ? 'This workflow' : 'All workflows'} />
      <div className="divide-y divide-ink-800/60">
        {events.length === 0 && <p className="px-5 py-6 text-xs text-ink-400">No events yet.</p>}
        {events.slice(0, 60).map((e) => (
          <div key={e.id} className="px-4 py-2">
            <div className="flex items-center justify-between gap-2">
              <p className="truncate font-mono text-[11px] text-viol-300">{e.event_type}</p>
              <Badge tone={e.status === 'processed' ? 'mint' : e.status === 'failed' ? 'red' : 'amber'}>{e.status}</Badge>
            </div>
            <p className="text-[10px] text-ink-500">{formatDateTime(e.created_at)}</p>
          </div>
        ))}
      </div>
    </Card>
  )
}

function WorkflowDrawer({ id, onClose }: { id: string | null; onClose: () => void }) {
  const qc = useQueryClient()
  const toast = useToast()
  const { data: wf } = useQuery({
    queryKey: ['workflow', id],
    queryFn: () => api.get<Workflow>(`/workflows/${id}`),
    enabled: !!id,
    refetchInterval: 5000,
  })
  const { data: steps } = useQuery({
    queryKey: ['workflow-steps', id],
    queryFn: () => api.get<Step[]>(`/workflows/${id}/steps`),
    enabled: !!id,
    refetchInterval: 5000,
  })
  const act = useMutation({
    mutationFn: (action: 'run' | 'pause' | 'resume' | 'cancel') =>
      api.post<Workflow>(`/workflows/${id}/${action}`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['workflow', id] }); qc.invalidateQueries({ queryKey: ['workflow-steps', id] }); qc.invalidateQueries({ queryKey: ['workflows'] }); toast('Updated', 'success') },
    onError: (e) => toast(e instanceof ApiError ? e.message : 'Action failed', 'error'),
  })
  if (!wf) return null
  const controls = (
    <div className="flex gap-2">
      {(wf.status === 'queued' || wf.status === 'paused') &&
        <Button size="sm" variant="primary" disabled={act.isPending} onClick={() => act.mutate('run')}>Run</Button>}
      {wf.status === 'running' && <Button size="sm" variant="secondary" onClick={() => act.mutate('pause')}>Pause</Button>}
      {wf.status === 'paused' && <Button size="sm" variant="success" onClick={() => act.mutate('resume')}>Resume</Button>}
      {!['completed', 'failed', 'cancelled'].includes(wf.status) &&
        <Button size="sm" variant="danger" onClick={() => act.mutate('cancel')}>Cancel</Button>}
    </div>
  )
  return (
    <Drawer open={!!id} onClose={onClose} title={wf.objective} width="max-w-3xl">
      <div className="space-y-4">
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone={STATUS_TONE[wf.status] ?? 'neutral'}>{titleCase(wf.status)}</Badge>
          <Badge tone="neutral">{titleCase(wf.workflow_type)}</Badge>
          <Badge tone="neutral">{wf.autonomy_mode}</Badge>
          <span className="text-[11px] text-ink-400">corr: {wf.correlation_id}</span>
          <span className="ml-auto">{controls}</span>
        </div>
        {wf.error && <p className="rounded-lg bg-red-500/10 px-3 py-2 text-xs text-red-300">{wf.error}</p>}
        <div>
          <p className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-ink-400">Execution timeline</p>
          <div className="space-y-1.5">
            {(steps ?? []).map((s) => (
              <div key={s.id} className="flex items-start gap-3 rounded-lg bg-ink-800/50 px-3 py-2">
                <div className="w-6 shrink-0 pt-0.5 text-center text-[10px] font-bold text-ink-400">{s.sequence}</div>
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <p className="text-xs font-medium">{s.title}</p>
                    <Badge tone="neutral">{s.agent_slug}</Badge>
                    <Badge tone={STATUS_TONE[s.status] ?? 'neutral'}>{titleCase(s.status)}</Badge>
                    {s.attempts > 1 && <span className="text-[10px] text-amber-300">attempt {s.attempts}/{s.max_attempts}</span>}
                  </div>
                  {s.depends_on?.length > 0 && <p className="text-[10px] text-ink-500">depends on: {s.depends_on.join(', ')}</p>}
                  {s.error && <p className="mt-0.5 text-[10px] text-red-300">{s.error}</p>}
                  {s.output && Object.keys(s.output).length > 0 && (
                    <pre className="mt-1 max-h-24 overflow-auto rounded bg-ink-900/60 p-1.5 text-[10px] text-ink-400">
                      {JSON.stringify(s.output, null, 1).slice(0, 600)}
                    </pre>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </Drawer>
  )
}

function CreateWorkflow({ open, onClose }: { open: boolean; onClose: () => void }) {
  const qc = useQueryClient()
  const toast = useToast()
  const [objective, setObjective] = useState('')
  const [type, setType] = useState('cafe_acquisition')
  const create = useMutation({
    mutationFn: () => api.post<Workflow>('/workflows', { objective, workflow_type: type }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['workflows'] }); onClose(); setObjective(''); toast('Workflow created — run it to start', 'success') },
    onError: (e) => toast(e instanceof ApiError ? e.message : 'Create failed', 'error'),
  })
  return (
    <Drawer open={open} onClose={onClose} title="New workflow">
      <form onSubmit={(e) => { e.preventDefault(); create.mutate() }} className="space-y-4">
        <Field label="Objective *">
          <Input required value={objective} onChange={(e) => setObjective(e.target.value)}
            placeholder="e.g. Acquire 20 qualified café leads in Pune and book demos" />
        </Field>
        <Field label="Workflow type">
          <select value={type} onChange={(e) => setType(e.target.value)}
            className="w-full rounded-lg border border-ink-600 bg-ink-800 px-3 py-2 text-sm text-ink-200">
            <option value="cafe_acquisition">Café acquisition (end-to-end)</option>
          </select>
        </Field>
        <p className="text-[11px] text-ink-400">
          The orchestrator will plan, assign and execute steps across the five employees.
          Sensitive steps pause for owner approval.
        </p>
        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={onClose}>Cancel</Button>
          <Button type="submit" variant="primary" disabled={create.isPending || objective.length < 3}>
            {create.isPending ? 'Creating…' : 'Create workflow'}
          </Button>
        </div>
      </form>
    </Drawer>
  )
}
