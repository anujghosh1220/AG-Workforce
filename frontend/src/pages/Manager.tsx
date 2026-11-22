import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api, ApiError } from '../lib/api'
import { PageHeader } from '../components/metrics'
import { Badge, Button, Card, CardHeader, Skeleton, useToast } from '../components/ui'
import { cn, formatDateTime, titleCase } from '../lib/utils'

type Tab = 'overview' | 'agents' | 'funnel' | 'intelligence' | 'issues' | 'reports'

interface Metric { value: unknown; quality: string; note?: string }
interface AgentPerf { agent: { name: string; status: string; enabled: boolean; tasks_completed: number; current_workload: number }; metrics: Record<string, Metric> }
interface FunnelStage { stage: string; count: number; conversion_from_previous: number | null; dropoff_from_previous: number | null }
interface Issue { id: string; issue_type: string; severity: string; title: string; detail: string; agent_slug: string | null; evidence: Record<string, unknown>; suggested_resolution: string; status: string; last_detected_at: string }
interface Rec { id: string; title: string; explanation: string; expected_benefit: string; priority: string; confidence: string; status: string; supporting_data: Record<string, unknown> }
interface Report { id: string; report_type: string; period_start: string; period_end: string; payload: Record<string, unknown>; generated_at: string }
interface Insight { provider: string; observations: string[]; hypotheses: string[]; recommendations: string[]; data_gaps: string[] }
interface Page<T> { items: T[]; total: number }

const SEV_TONE: Record<string, 'neutral' | 'violet' | 'mint' | 'amber' | 'red' | 'blue'> = {
  informational: 'neutral', low: 'blue', medium: 'amber', high: 'red', critical: 'red',
}

export default function Manager() {
  const [tab, setTab] = useState<Tab>('overview')
  return (
    <div>
      <PageHeader title="Sales Manager" subtitle="Workforce supervision, funnel intelligence and management reporting" />
      <div className="mb-5 flex flex-wrap gap-2">
        {([['overview', 'Executive'], ['agents', 'Agents'], ['funnel', 'Funnel'], ['intelligence', 'Intelligence'], ['issues', 'Issues'], ['reports', 'Reports']] as [Tab, string][]).map(([k, l]) => (
          <button key={k} onClick={() => setTab(k)}
            className={cn('rounded-full px-4 py-1.5 text-xs font-medium border cursor-pointer',
              tab === k ? 'bg-viol-500/20 text-viol-300 border-viol-500/40' : 'bg-ink-800 text-ink-400 border-ink-700 hover:text-ink-200')}>
            {l}
          </button>
        ))}
      </div>
      {tab === 'overview' && <OverviewTab />}
      {tab === 'agents' && <AgentsTab />}
      {tab === 'funnel' && <FunnelTab />}
      {tab === 'intelligence' && <IntelligenceTab />}
      {tab === 'issues' && <IssuesTab />}
      {tab === 'reports' && <ReportsTab />}
    </div>
  )
}

function OverviewTab() {
  const { data, isLoading } = useQuery({ queryKey: ['manager-overview'], queryFn: () => api.get<Record<string, number | string>>('/manager/overview'), refetchInterval: 30000 })
  if (isLoading || !data) return <Skeleton className="h-64" />
  const cards: [string, unknown][] = [
    ['Total leads', data.total_leads], ['Qualified', data.qualified_prospects],
    ['Interested', data.interested_prospects], ['Upcoming demos', data.upcoming_demos],
    ['Completed demos', data.completed_demos], ['Customers', data.converted_customers],
    ['Revenue (won)', `$${Number(data.revenue_won ?? 0).toLocaleString()}`],
    ['Open issues', data.open_issues],
  ]
  return <div className="grid grid-cols-2 gap-3 md:grid-cols-4">{cards.map(([l, v]) => (
    <Card key={l} className="p-4"><p className="text-[11px] uppercase text-ink-400">{l}</p><p className="mt-1 text-2xl font-bold">{String(v)}</p></Card>
  ))}</div>
}

function AgentsTab() {
  const { data, isLoading } = useQuery({ queryKey: ['manager-agents'], queryFn: () => api.get<Record<string, AgentPerf>>('/manager/agents/performance') })
  if (isLoading || !data) return <Skeleton className="h-64" />
  return (
    <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
      {Object.entries(data).map(([slug, p]) => (
        <Card key={slug} className="p-4">
          <div className="flex items-center justify-between">
            <p className="text-sm font-semibold">{p.agent.name}</p>
            <Badge tone={p.agent.enabled ? 'mint' : 'neutral'}>{p.agent.status}</Badge>
          </div>
          <p className="mt-0.5 text-[11px] text-ink-400">{slug} · {p.agent.tasks_completed} tasks done</p>
          <div className="mt-3 space-y-1">
            {Object.entries(p.metrics).filter(([, m]) => m && typeof m === 'object' && 'value' in m).slice(0, 8).map(([k, m]) => (
              <div key={k} className="flex justify-between text-xs">
                <span className="text-ink-400">{titleCase(k)}</span>
                <span className="text-ink-200">{m.value === null || m.value === undefined ? '—' : String(m.value)}
                  {m.quality === 'estimate' && <span className="ml-1 text-amber-400">~</span>}
                </span>
              </div>
            ))}
          </div>
        </Card>
      ))}
    </div>
  )
}

function FunnelTab() {
  const { data, isLoading } = useQuery({ queryKey: ['manager-funnel'], queryFn: () => api.get<{ stages: FunnelStage[]; revenue_won: number; definitions: Record<string, string> }>('/manager/funnel?days=90') })
  if (isLoading || !data) return <Skeleton className="h-64" />
  const max = Math.max(...data.stages.map((s) => s.count), 1)
  return (
    <Card>
      <CardHeader title="Sales funnel (90 days)" subtitle="Counts from real records — drafts and proposals excluded" />
      <div className="space-y-2 p-5">
        {data.stages.map((s) => (
          <div key={s.stage} className="flex items-center gap-3">
            <div className="w-44 shrink-0 text-right text-xs text-ink-300">{titleCase(s.stage)}</div>
            <div className="h-6 flex-1 rounded bg-ink-800/60">
              <div className="h-full rounded bg-gradient-to-r from-viol-500/70 to-mint-500/60 transition-all" style={{ width: `${(s.count / max) * 100}%` }} />
            </div>
            <div className="w-20 text-right text-xs text-ink-400">{s.count}</div>
            <div className="w-16 text-right text-xs text-viol-300">{s.conversion_from_previous !== null ? `${(s.conversion_from_previous * 100).toFixed(0)}%` : ''}</div>
          </div>
        ))}
        <p className="pt-2 text-xs text-ink-400">Revenue (won opportunities): <span className="text-ink-200">${data.revenue_won.toLocaleString()}</span></p>
      </div>
    </Card>
  )
}

function IntelligenceTab() {
  const qc = useQueryClient()
  const toast = useToast()
  const { data, isLoading } = useQuery({ queryKey: ['manager-intel'], queryFn: () => api.get<Insight>('/manager/intelligence') })
  const refresh = useMutation({ mutationFn: () => api.post<Insight>('/manager/intelligence/refresh'), onSuccess: () => qc.invalidateQueries({ queryKey: ['manager-intel'] }), onError: (e) => toast(e instanceof ApiError ? e.message : 'Failed', 'error') })
  if (isLoading || !data) return <Skeleton className="h-64" />
  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-xs text-ink-400">Provider: <Badge tone="neutral">{data.provider}</Badge></p>
        <Button size="sm" variant="secondary" disabled={refresh.isPending} onClick={() => refresh.mutate()}>{refresh.isPending ? 'Analyzing…' : 'Refresh analysis'}</Button>
      </div>
      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="p-4"><p className="mb-2 text-xs font-semibold uppercase text-mint-300">Observations</p>
          <ul className="list-disc space-y-1 pl-4 text-xs text-ink-300">{data.observations.map((o, i) => <li key={i}>{o}</li>)}</ul></Card>
        <Card className="p-4"><p className="mb-2 text-xs font-semibold uppercase text-amber-300">Hypotheses</p>
          <ul className="list-disc space-y-1 pl-4 text-xs text-ink-300">{data.hypotheses.length ? data.hypotheses.map((o, i) => <li key={i}>{o}</li>) : <li className="text-ink-400">None</li>}</ul></Card>
        <Card className="p-4"><p className="mb-2 text-xs font-semibold uppercase text-viol-300">Recommendations</p>
          <ul className="list-disc space-y-1 pl-4 text-xs text-ink-300">{data.recommendations.map((o, i) => <li key={i}>{o}</li>)}</ul></Card>
      </div>
      {data.data_gaps.length > 0 && <Card className="p-4"><p className="text-xs font-semibold uppercase text-ink-400">Data gaps</p><ul className="mt-1 list-disc pl-4 text-xs text-ink-400">{data.data_gaps.map((g, i) => <li key={i}>{g}</li>)}</ul></Card>}
      <RecsCard />
    </div>
  )
}

function RecsCard() {
  const qc = useQueryClient()
  const toast = useToast()
  const { data } = useQuery({ queryKey: ['manager-recs'], queryFn: () => api.get<Page<Rec>>('/manager/recommendations?status=new&page_size=20') })
  const act = useMutation({
    mutationFn: ({ id, action }: { id: string; action: 'accept' | 'status' }) =>
      action === 'accept' ? api.post(`/manager/recommendations/${id}/accept`) : api.post(`/manager/recommendations/${id}/status`, { status: 'dismissed' }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['manager-recs'] }); toast('Done', 'success') },
    onError: (e) => toast(e instanceof ApiError ? e.message : 'Failed', 'error'),
  })
  return (
    <Card>
      <CardHeader title="Recommendations" subtitle="Evidence-based suggestions — never guaranteed outcomes" />
      <div className="divide-y divide-ink-800">
        {(data?.items ?? []).length === 0 && <p className="px-5 py-6 text-xs text-ink-400">No open recommendations.</p>}
        {(data?.items ?? []).map((r) => (
          <div key={r.id} className="flex flex-wrap items-start justify-between gap-3 px-5 py-3">
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <p className="text-sm font-medium">{r.title}</p>
                <Badge tone="neutral">{r.confidence} confidence</Badge>
                <Badge tone="blue">{titleCase(r.priority)}</Badge>
              </div>
              <p className="mt-1 text-xs text-ink-400">{r.explanation}</p>
              {r.expected_benefit && <p className="mt-0.5 text-xs text-mint-300">Benefit: {r.expected_benefit}</p>}
            </div>
            <div className="flex gap-2">
              <Button size="sm" variant="primary" disabled={act.isPending} onClick={() => act.mutate({ id: r.id, action: 'accept' })}>Accept</Button>
              <Button size="sm" variant="ghost" disabled={act.isPending} onClick={() => act.mutate({ id: r.id, action: 'status' })}>Dismiss</Button>
            </div>
          </div>
        ))}
      </div>
    </Card>
  )
}

function IssuesTab() {
  const qc = useQueryClient()
  const toast = useToast()
  const { data } = useQuery({ queryKey: ['manager-issues'], queryFn: () => api.get<Page<Issue>>('/manager/issues?page_size=100') })
  const act = useMutation({
    mutationFn: ({ id, action }: { id: string; action: string }) => api.post(`/manager/issues/${id}/${action}`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['manager-issues'] }); toast('Updated', 'success') },
    onError: (e) => toast(e instanceof ApiError ? e.message : 'Failed', 'error'),
  })
  const open = (data?.items ?? []).filter((i) => i.status === 'open' || i.status === 'acknowledged')
  const closed = (data?.items ?? []).filter((i) => !open.includes(i))
  return (
    <div className="space-y-4">
      <Card>
        <CardHeader title="Open issues" subtitle="Detected bottlenecks and alerts" />
        <div className="divide-y divide-ink-800">
          {open.length === 0 && <p className="px-5 py-6 text-xs text-ink-400">No open issues.</p>}
          {open.map((i) => (
            <div key={i.id} className="flex flex-wrap items-start justify-between gap-3 px-5 py-3">
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <p className="text-sm font-medium">{i.title}</p>
                  <Badge tone={SEV_TONE[i.severity]}>{i.severity}</Badge>
                  {i.agent_slug && <Badge tone="neutral">{i.agent_slug}</Badge>}
                </div>
                <p className="mt-1 text-xs text-ink-400">{i.detail}</p>
                {i.suggested_resolution && <p className="mt-0.5 text-xs text-viol-300">→ {i.suggested_resolution}</p>}
              </div>
              <div className="flex gap-2">
                {i.status === 'open' && <Button size="sm" variant="secondary" onClick={() => act.mutate({ id: i.id, action: 'acknowledge' })}>Ack</Button>}
                <Button size="sm" variant="success" onClick={() => act.mutate({ id: i.id, action: 'resolve' })}>Resolve</Button>
                <Button size="sm" variant="ghost" onClick={() => act.mutate({ id: i.id, action: 'dismiss' })}>Dismiss</Button>
              </div>
            </div>
          ))}
        </div>
      </Card>
      {closed.length > 0 && (
        <Card>
          <CardHeader title="Resolved / dismissed" />
          <div className="divide-y divide-ink-800">
            {closed.slice(0, 20).map((i) => (
              <div key={i.id} className="flex items-center justify-between px-5 py-2.5 text-xs">
                <span className="text-ink-400">{i.title}</span>
                <Badge tone="neutral">{i.status}</Badge>
              </div>
            ))}
          </div>
        </Card>
      )}
    </div>
  )
}

function ReportsTab() {
  const qc = useQueryClient()
  const toast = useToast()
  const { data } = useQuery({ queryKey: ['manager-reports'], queryFn: () => api.get<Page<Report>>('/manager/reports?page_size=30') })
  const gen = useMutation({
    mutationFn: (t: 'daily' | 'weekly') => api.post<Report>('/manager/reports/generate', { report_type: t }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['manager-reports'] }); toast('Report generated', 'success') },
    onError: (e) => toast(e instanceof ApiError ? e.message : 'Failed', 'error'),
  })
  const [open, setOpen] = useState<string | null>(null)
  return (
    <Card>
      <CardHeader title="Management reports" subtitle="Generated from real records; external delivery disabled until configured" action={
        <div className="flex gap-2">
          <Button size="sm" variant="secondary" disabled={gen.isPending} onClick={() => gen.mutate('daily')}>Daily</Button>
          <Button size="sm" variant="primary" disabled={gen.isPending} onClick={() => gen.mutate('weekly')}>Weekly</Button>
        </div>
      } />
      <div className="divide-y divide-ink-800">
        {(data?.items ?? []).length === 0 && <p className="px-5 py-6 text-xs text-ink-400">No reports yet — generate one or wait for the background worker.</p>}
        {(data?.items ?? []).map((r) => (
          <div key={r.id}>
            <button onClick={() => setOpen(open === r.id ? null : r.id)} className="flex w-full items-center justify-between px-5 py-3 text-left hover:bg-ink-800/40 cursor-pointer">
              <div>
                <p className="text-sm font-medium capitalize">{r.report_type} report</p>
                <p className="text-xs text-ink-400">{formatDateTime(r.period_start)} → {formatDateTime(r.period_end)} · generated {formatDateTime(r.generated_at)}</p>
              </div>
              <Badge tone={r.report_type === 'weekly' ? 'violet' : 'blue'}>{r.report_type}</Badge>
            </button>
            {open === r.id && (
              <div className="grid grid-cols-2 gap-2 px-5 pb-4 text-xs sm:grid-cols-4">
                {Object.entries(r.payload).filter(([k]) => typeof (r.payload as Record<string, unknown>)[k] === 'number').map(([k, v]) => (
                  <div key={k} className="rounded-lg bg-ink-800/60 px-3 py-2">
                    <p className="text-ink-400">{titleCase(k)}</p><p className="text-sm font-semibold">{String(v)}</p>
                  </div>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>
    </Card>
  )
}
