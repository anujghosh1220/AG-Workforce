import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { api, ApiError } from '../lib/api'
import { PageHeader } from '../components/metrics'
import { Badge, Card, CardHeader, EmptyState, Skeleton } from '../components/ui'
import { formatDateTime } from '../lib/utils'

// ---- types mirroring backend responses ----
interface LlmUsage {
  workspace_id: string
  window_days: number
  calls: number
  failed_calls: number
  tokens_prompt: number
  tokens_completion: number
  note: string
  by_model: { provider: string; model: string; calls: number; tokens_prompt: number; tokens_completion: number }[]
  by_agent: { agent: string; calls: number }[]
  daily: { date: string; calls: number }[]
}
interface WorkerHealth {
  worker_id: string
  worker_type: string
  status: string
  stale: boolean
  last_heartbeat_at: string | null
  host: string | null
  pid: number | null
  started_at: string | null
  last_job_name: string | null
  consecutive_failures: number
  jobs_completed: number
  jobs_failed: number
}
interface WorkerList { workers: WorkerHealth[]; counts: Record<string, number>; stale_after_seconds: number; checked_at: string }
interface WaTemplate {
  id: string; name: string; channel: string; language: string
  is_active: boolean; provider_template_id: string | null
  provider_status: string; provider_category: string | null
  rejection_reason: string | null; last_synced_at: string | null
}
interface SystemStatus {
  email: { provider: string; configured: boolean; from_address_set: boolean; webhook_secret_set: boolean }
  llm: { provider: string; configured: boolean; model: string; fallback: string }
  workspace: { inbound_email: string | null; inbound_configured: boolean }
  calendar: { providers: { name: string; configured: boolean }[]; internal_scheduling: string }
  rate_limit: Record<string, unknown>
  workers: WorkerList
  environment: string
}

const STATUS_TONE: Record<string, 'neutral' | 'violet' | 'mint' | 'amber' | 'red' | 'blue'> = {
  running: 'mint', idle: 'blue', stopped: 'neutral', failed: 'red',
  approved: 'mint', pending: 'amber', rejected: 'red', paused: 'amber',
  disabled: 'neutral', draft: 'neutral',
}

function PanelError({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const msg = error instanceof ApiError && error.status === 403
    ? 'You do not have permission to view this (admin role required).'
    : error instanceof Error ? error.message : 'Failed to load'
  return (
    <div className="rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-300">
      {msg}
      {onRetry && <button className="ml-2 underline" onClick={onRetry}>Retry</button>}
    </div>
  )
}

function Stat({ label, value, hint }: { label: string; value: string | number; hint?: string }) {
  return (
    <div className="rounded-lg bg-ink-800/60 px-3 py-2">
      <div className="text-[11px] uppercase tracking-wide text-ink-400">{label}</div>
      <div className="text-lg font-semibold text-ink-100">{value}</div>
      {hint && <div className="text-[11px] text-ink-500">{hint}</div>}
    </div>
  )
}

export default function Operations() {
  const [days, setDays] = useState(30)
  return (
    <div>
      <PageHeader title="Operations"
        subtitle="LLM usage, worker health, WhatsApp templates and integration status" />

      <div className="grid gap-5 xl:grid-cols-2">
        <LlmUsagePanel days={days} setDays={setDays} />
        <WorkerPanel />
        <WhatsAppPanel />
        <SystemStatusPanel />
      </div>
    </div>
  )
}

// ---------------------------------------------------------- LLM usage

function LlmUsagePanel({ days, setDays }: { days: number; setDays: (n: number) => void }) {
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['llm-usage', days],
    queryFn: () => api.get<LlmUsage>(`/analytics/llm-usage?days=${days}`),
    refetchInterval: 60_000,
  })
  return (
    <Card>
      <CardHeader title="LLM Usage" subtitle="Actual provider calls metered per workspace"
        action={
          <select value={days} onChange={(e) => setDays(Number(e.target.value))}
            className="rounded-md border border-ink-700 bg-ink-900 px-2 py-1 text-xs text-ink-200">
            <option value={7}>7 days</option>
            <option value={30}>30 days</option>
            <option value={90}>90 days</option>
          </select>
        } />
      <div className="p-4">
        {isLoading && <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">{[0,1,2,3].map(i => <Skeleton key={i} className="h-16" />)}</div>}
        {error && <PanelError error={error} onRetry={() => refetch()} />}
        {data && (
          <>
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
              <Stat label="Calls" value={data.calls} />
              <Stat label="Failed" value={data.failed_calls} hint={data.calls ? `${Math.round(data.failed_calls / data.calls * 100)}%` : undefined} />
              <Stat label="Prompt tokens" value={data.tokens_prompt || 'n/a'} hint={data.tokens_prompt ? undefined : 'provider did not report'} />
              <Stat label="Completion tokens" value={data.tokens_completion || 'n/a'} />
            </div>
            {data.calls === 0 ? (
              <EmptyState title="No LLM calls yet" hint="Usage appears here once agents make real provider calls." />
            ) : (
              <div className="mt-4 space-y-4">
                {data.by_model.length > 0 && (
                  <div>
                    <div className="mb-1 text-xs font-medium text-ink-300">By model</div>
                    <table className="w-full text-xs">
                      <thead><tr className="text-left text-ink-500"><th className="py-1">Provider / Model</th><th>Calls</th><th>Tokens</th></tr></thead>
                      <tbody>
                        {data.by_model.map((m, i) => (
                          <tr key={i} className="border-t border-ink-800">
                            <td className="py-1.5">{m.provider} · {m.model}</td>
                            <td>{m.calls}</td>
                            <td>{m.tokens_prompt + m.tokens_completion || 'n/a'}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
                {data.by_agent.length > 0 && (
                  <div>
                    <div className="mb-1 text-xs font-medium text-ink-300">By agent</div>
                    <div className="flex flex-wrap gap-1.5">
                      {data.by_agent.map((a, i) => (
                        <Badge key={i} tone="blue">{a.agent}: {a.calls}</Badge>
                      ))}
                    </div>
                  </div>
                )}
                {data.daily.length > 0 && (
                  <div>
                    <div className="mb-1 text-xs font-medium text-ink-300">Daily calls</div>
                    <div className="flex h-16 items-end gap-0.5">
                      {data.daily.slice(-30).map((d, i) => {
                        const max = Math.max(...data.daily.map(x => x.calls), 1)
                        return <div key={i} title={`${d.date}: ${d.calls}`}
                          className="flex-1 rounded-sm bg-violet-500/70"
                          style={{ height: `${Math.max(4, d.calls / max * 100)}%` }} />
                      })}
                    </div>
                  </div>
                )}
              </div>
            )}
          </>
        )}
      </div>
    </Card>
  )
}

// ---------------------------------------------------------- Workers

function WorkerPanel() {
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['system-status'],
    queryFn: () => api.get<SystemStatus>('/system/status'),
    refetchInterval: 30_000,
  })
  const workers = data?.workers?.workers ?? []
  return (
    <Card>
      <CardHeader title="Worker Health" subtitle="Background job processors"
        action={<Badge tone={data?.workers?.counts?.failed ? 'red' : data?.workers?.counts?.stale ? 'amber' : 'mint'}>
          {data ? `${data.workers.counts?.healthy ?? 0} healthy` : '…'}
        </Badge>} />
      <div className="p-4">
        {isLoading && <Skeleton className="h-24" />}
        {error && <PanelError error={error} onRetry={() => refetch()} />}
        {data && workers.length === 0 && (
          <EmptyState title="No worker heartbeats" hint="Workers report here when running (AGW_DISABLE_WORKER=1 disables them)." />
        )}
        {data && workers.length > 0 && (
          <ul className="space-y-2">
            {workers.map((w) => (
              <li key={w.worker_id} className="flex items-center justify-between rounded-lg bg-ink-800/60 px-3 py-2 text-sm">
                <div>
                  <div className="font-medium text-ink-200">{w.worker_type}</div>
                  <div className="text-[11px] text-ink-500">
                    {w.last_job_name ? `job: ${w.last_job_name}` : 'idle'}
                    {w.last_heartbeat_at ? ` · beat ${formatDateTime(w.last_heartbeat_at)}` : ''}
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  {w.consecutive_failures > 0 && <Badge tone="amber">{w.consecutive_failures} fail</Badge>}
                  {w.stale && <Badge tone="red">stale</Badge>}
                  <Badge tone={STATUS_TONE[w.status] ?? 'neutral'}>{w.status}</Badge>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </Card>
  )
}

// ---------------------------------------------------------- WhatsApp

function WhatsAppPanel() {
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['wa-templates'],
    queryFn: () => api.get<WaTemplate[]>('/whatsapp/templates'),
    retry: false,
  })
  const approved = data?.filter(t => t.provider_status === 'approved').length ?? 0
  return (
    <Card>
      <CardHeader title="WhatsApp Templates" subtitle="Meta Cloud API template status"
        action={data ? <Badge tone="blue">{approved}/{data.length} approved</Badge> : undefined} />
      <div className="p-4">
        {isLoading && <Skeleton className="h-24" />}
        {error && <PanelError error={error} onRetry={() => refetch()} />}
        {data && data.length === 0 && (
          <EmptyState title="No templates synced" hint="Sync templates from Meta via POST /whatsapp/templates/sync (admin)." />
        )}
        {data && data.length > 0 && (
          <ul className="max-h-64 space-y-2 overflow-y-auto">
            {data.map((t) => (
              <li key={t.id} className="rounded-lg bg-ink-800/60 px-3 py-2 text-sm">
                <div className="flex items-center justify-between">
                  <span className="font-medium text-ink-200">{t.name}</span>
                  <Badge tone={STATUS_TONE[t.provider_status] ?? 'neutral'}>{t.provider_status}</Badge>
                </div>
                <div className="mt-0.5 text-[11px] text-ink-500">
                  {t.language} · {t.provider_category ?? '—'}
                  {t.last_synced_at ? ` · synced ${formatDateTime(t.last_synced_at)}` : ' · never synced'}
                </div>
                {t.rejection_reason && <div className="mt-0.5 text-[11px] text-red-400">{t.rejection_reason}</div>}
              </li>
            ))}
          </ul>
        )}
      </div>
    </Card>
  )
}

// ---------------------------------------------------------- System status

function SystemStatusPanel() {
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['system-status'],
    queryFn: () => api.get<SystemStatus>('/system/status'),
  })
  return (
    <Card>
      <CardHeader title="Integrations" subtitle="Provider configuration state" />
      <div className="p-4">
        {isLoading && <Skeleton className="h-24" />}
        {error && <PanelError error={error} onRetry={() => refetch()} />}
        {data && (
          <div className="space-y-2 text-sm">
            <StatusRow label="Email (Resend)" ok={data.email.configured}
              detail={`${data.email.provider}${data.email.configured ? '' : ' — not configured'}`} />
            <StatusRow label="LLM" ok={data.llm.configured}
              detail={`${data.llm.provider} · ${data.llm.model}`} />
            <StatusRow label="Inbound email" ok={data.workspace.inbound_configured}
              detail={data.workspace.inbound_email ?? 'not set — set in workspace settings'} />
            {data.calendar.providers.map((p) => (
              <StatusRow key={p.name} label={`Calendar: ${p.name}`} ok={p.configured}
                detail={p.configured ? 'configured' : 'not configured'} />
            ))}
            <StatusRow label="Environment" ok={data.environment !== 'development'}
              detail={data.environment} />
          </div>
        )}
      </div>
    </Card>
  )
}

function StatusRow({ label, ok, detail }: { label: string; ok: boolean; detail: string }) {
  return (
    <div className="flex items-center justify-between rounded-lg bg-ink-800/60 px-3 py-2">
      <span className="text-ink-300">{label}</span>
      <span className="flex items-center gap-2 text-xs">
        <span className="text-ink-500">{detail}</span>
        <Badge tone={ok ? 'mint' : 'amber'}>{ok ? 'connected' : 'off'}</Badge>
      </span>
    </div>
  )
}
