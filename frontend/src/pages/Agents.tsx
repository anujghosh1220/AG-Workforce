import { useQuery } from '@tanstack/react-query'
import { Activity, Bot, Gauge, Pause, Play, Timer } from 'lucide-react'
import { Link } from 'react-router-dom'
import { api, type Agent } from '../lib/api'
import { PageHeader } from '../components/metrics'
import { Badge, Card, EmptyState, Skeleton } from '../components/ui'
import { formatDateTime, titleCase } from '../lib/utils'

const STATUS_TONE: Record<string, 'mint' | 'neutral' | 'amber' | 'red'> = {
  active: 'mint', idle: 'neutral', paused: 'amber', error: 'red',
}

function AgentAvatar() {
  return (
    <div className="relative flex h-14 w-14 items-center justify-center rounded-2xl bg-gradient-to-br from-viol-500/25 to-mint-500/15 text-viol-300 ring-1 ring-viol-500/30">
      <Bot className="h-6 w-6" />
      <span className="absolute -bottom-1 -right-1 h-3 w-3 rounded-full border-2 border-ink-850 bg-mint-400" />
    </div>
  )
}

export default function Agents() {
  const { data, isLoading } = useQuery({
    queryKey: ['agents'],
    queryFn: () => api.get<{ items: Agent[] }>('/agents?page_size=50'),
  })

  return (
    <div>
      <PageHeader title="AI Employees" subtitle="Your configurable digital workforce — Phase 1 management layer" />
      {isLoading ? (
        <div className="grid gap-5 sm:grid-cols-2 xl:grid-cols-3">{Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} className="h-64" />)}</div>
      ) : !data?.items.length ? (
        <EmptyState title="No AI employees" hint="Agents are seeded when you create a workspace." />
      ) : (
        <div className="stagger grid gap-5 sm:grid-cols-2 xl:grid-cols-3">
          {data.items.map((a) => (
            <Link key={a.id} to={`/agents/${a.id}`} className="group">
              <Card className="h-full p-5 transition-all duration-300 hover:-translate-y-1 hover:border-viol-500/40 hover:shadow-[0_16px_50px_-16px_rgba(139,92,246,0.35)]">
                <div className="flex items-start justify-between">
                  <AgentAvatar />
                  <Badge tone={STATUS_TONE[a.status] ?? 'neutral'}>{titleCase(a.status)}</Badge>
                </div>
                <h3 className="mt-4 text-base font-semibold">{a.name}</h3>
                <p className="text-xs text-viol-300/80">{a.title}</p>
                <p className="mt-2 line-clamp-2 text-xs text-ink-400">{a.description}</p>
                <div className="mt-4 grid grid-cols-3 gap-2 border-t border-ink-700/50 pt-4 text-center">
                  <div>
                    <p className="flex items-center justify-center gap-1 text-sm font-semibold"><Gauge className="h-3 w-3 text-ink-400" />{a.current_workload}</p>
                    <p className="text-[10px] uppercase text-ink-400">Workload</p>
                  </div>
                  <div>
                    <p className="flex items-center justify-center gap-1 text-sm font-semibold"><Activity className="h-3 w-3 text-ink-400" />{a.tasks_completed}</p>
                    <p className="text-[10px] uppercase text-ink-400">Completed</p>
                  </div>
                  <div>
                    <p className="flex items-center justify-center gap-1 text-sm font-semibold"><Timer className="h-3 w-3 text-ink-400" />{a.last_activity_at ? formatDateTime(a.last_activity_at) : '—'}</p>
                    <p className="text-[10px] uppercase text-ink-400">Last active</p>
                  </div>
                </div>
                <div className="mt-3 flex items-center gap-1.5 text-[11px] text-ink-400">
                  {a.enabled ? <Play className="h-3 w-3 text-mint-400" /> : <Pause className="h-3 w-3 text-amber-400" />}
                  {a.enabled ? 'Enabled' : 'Disabled'} · {a.permissions?.requires_approval ? 'approval-gated' : 'autonomous'}
                </div>
              </Card>
            </Link>
          ))}
        </div>
      )}
    </div>
  )
}
