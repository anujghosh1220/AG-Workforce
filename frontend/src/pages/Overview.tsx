import { useQuery } from '@tanstack/react-query'
import {
  ArrowRight, Bot, Building2, CalendarCheck, Mail, MessageCircle,
  Target, TrendingUp, Users, Wallet,
} from 'lucide-react'
import { Link } from 'react-router-dom'
import {
  Bar, BarChart, CartesianGrid, Cell, Pie, PieChart, ResponsiveContainer,
  Tooltip, XAxis, YAxis,
} from 'recharts'
import { api, type Activity, type Agent, type AnalyticsOverview, type Approval } from '../lib/api'
import { MetricCard, PageHeader } from '../components/metrics'
import { Badge, Card, CardHeader, EmptyState, Skeleton } from '../components/ui'
import { formatDateTime, formatMoney, titleCase } from '../lib/utils'

const STAGE_COLORS = ['#8b5cf6', '#a78bfa', '#34d399', '#fbbf24', '#38bdf8', '#f472b6', '#fb7185', '#4ade80', '#22d3ee', '#e879f9', '#f87171', '#64748b']

export default function Overview() {
  const { data: analytics, isLoading } = useQuery({
    queryKey: ['analytics'],
    queryFn: () => api.get<AnalyticsOverview>('/analytics/overview'),
  })
  const { data: agents } = useQuery({
    queryKey: ['agents'],
    queryFn: () => api.get<{ items: Agent[] }>('/agents?page_size=10'),
  })
  const { data: activities } = useQuery({
    queryKey: ['activities'],
    queryFn: () => api.get<{ items: Activity[] }>('/activities?page_size=8'),
  })
  const { data: approvals } = useQuery({
    queryKey: ['approvals-pending'],
    queryFn: () => api.get<{ items: Approval[]; total: number }>('/approvals?status=pending&page_size=5'),
  })

  if (isLoading) {
    return (
      <div className="space-y-6">
        <Skeleton className="h-10 w-64" />
        <div className="grid grid-cols-2 gap-4 md:grid-cols-4">{Array.from({ length: 8 }).map((_, i) => <Skeleton key={i} className="h-28" />)}</div>
        <Skeleton className="h-72" />
      </div>
    )
  }

  const a = analytics
  const funnel = a ? [
    { name: 'Discovered', value: a.leads_by_stage['discovered'] ?? 0 },
    { name: 'Researched', value: a.leads_by_stage['researched'] ?? 0 },
    { name: 'Qualified', value: a.qualified_leads },
    { name: 'Interested', value: a.interested_prospects },
    { name: 'Demo', value: a.demo_requests },
    { name: 'Won', value: a.won_deals },
  ] : []



  return (
    <div className="space-y-6">
      <PageHeader title="Command Center" subtitle="Real-time view of your AI workforce operations" />

      {/* metrics */}
      <div className="stagger grid grid-cols-2 gap-4 md:grid-cols-4">
        <MetricCard label="Total Leads" value={a?.total_leads ?? 0} icon={<Building2 className="h-5 w-5" />} accent="violet" />
        <MetricCard label="Qualified" value={a?.qualified_leads ?? 0} icon={<Target className="h-5 w-5" />} accent="mint" />
        <MetricCard label="Pipeline Value" value={Math.round(a?.pipeline_value ?? 0)} icon={<Wallet className="h-5 w-5" />} accent="amber"
          hint={a ? formatMoney(a.pipeline_value) : undefined} />
        <MetricCard label="Won Deals" value={a?.won_deals ?? 0} icon={<TrendingUp className="h-5 w-5" />} accent="blue"
          hint={a ? formatMoney(a.won_value) : undefined} />
        <MetricCard label="Emails Sent" value={a?.emails_sent ?? 0} icon={<Mail className="h-5 w-5" />} />
        <MetricCard label="WhatsApp Sent" value={a?.whatsapp_sent ?? 0} icon={<MessageCircle className="h-5 w-5" />} accent="mint" />
        <MetricCard label="Interested" value={a?.interested_prospects ?? 0} icon={<Users className="h-5 w-5" />} accent="amber" />
        <MetricCard label="Demo Requests" value={a?.demo_requests ?? 0} icon={<CalendarCheck className="h-5 w-5" />} accent="blue" />
      </div>

      <div className="grid gap-6 lg:grid-cols-3">
        {/* charts */}
        <Card className="lg:col-span-2">
          <CardHeader title="Lead Acquisition" subtitle="Leads created over the last 30 days" />
          <div className="h-64 p-4">
            {(a?.leads_over_time.length ?? 0) === 0 ? (
              <EmptyState title="No lead data yet" hint="Create leads in the CRM to see acquisition trends." />
            ) : (
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={a!.leads_over_time}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#2a2a3d" vertical={false} />
                  <XAxis dataKey="date" tick={{ fill: '#6b6b85', fontSize: 11 }} tickLine={false} axisLine={false} />
                  <YAxis tick={{ fill: '#6b6b85', fontSize: 11 }} tickLine={false} axisLine={false} allowDecimals={false} />
                  <Tooltip contentStyle={{ background: '#171722', border: '1px solid #2a2a3d', borderRadius: 10, fontSize: 12 }} />
                  <Bar dataKey="count" name="Leads" fill="#8b5cf6" radius={[6, 6, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            )}
          </div>
        </Card>

        <Card>
          <CardHeader title="Conversion Funnel" subtitle="Lead → Won" />
          <div className="h-64 p-4">
            {funnel.every((f) => f.value === 0) ? (
              <EmptyState title="No funnel data" hint="Funnel populates as leads progress through stages." />
            ) : (
              <ResponsiveContainer width="100%" height="100%">
                <PieChart>
                  <Pie data={funnel} dataKey="value" nameKey="name" innerRadius={45} outerRadius={75} paddingAngle={3}>
                    {funnel.map((_, i) => <Cell key={i} fill={STAGE_COLORS[i % STAGE_COLORS.length]} />)}
                  </Pie>
                  <Tooltip contentStyle={{ background: '#171722', border: '1px solid #2a2a3d', borderRadius: 10, fontSize: 12 }} />
                </PieChart>
              </ResponsiveContainer>
            )}
          </div>
          <div className="flex flex-wrap gap-x-4 gap-y-1 px-5 pb-4">
            {funnel.map((f, i) => (
              <span key={f.name} className="flex items-center gap-1.5 text-[11px] text-ink-300">
                <span className="h-2 w-2 rounded-full" style={{ background: STAGE_COLORS[i % STAGE_COLORS.length] }} />
                {f.name} · {f.value}
              </span>
            ))}
          </div>
        </Card>
      </div>

      <div className="grid gap-6 lg:grid-cols-3">
        {/* AI employees */}
        <Card className="lg:col-span-1">
          <CardHeader title="AI Employees" subtitle="Workforce status" action={<Link to="/agents" className="text-xs text-viol-300 hover:text-viol-400 flex items-center gap-1">View all <ArrowRight className="h-3 w-3" /></Link>} />
          <div className="divide-y divide-ink-800">
            {(agents?.items ?? []).slice(0, 5).map((ag) => (
              <Link key={ag.id} to={`/agents/${ag.id}`} className="flex items-center gap-3 px-5 py-3 hover:bg-ink-800/50 transition-colors">
                <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-viol-500/15 text-viol-300">
                  <Bot className="h-4 w-4" />
                </div>
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium">{ag.name}</p>
                  <p className="truncate text-[11px] text-ink-400">{ag.title}</p>
                </div>
                <Badge tone={ag.status === 'error' ? 'red' : ag.status === 'active' ? 'mint' : 'neutral'}>{titleCase(ag.status)}</Badge>
              </Link>
            ))}
            {!agents?.items.length && <p className="px-5 py-8 text-center text-xs text-ink-400">No agents configured</p>}
          </div>
        </Card>

        {/* pending approvals */}
        <Card className="lg:col-span-1">
          <CardHeader title="Pending Approvals" subtitle="Actions awaiting your review" action={<Link to="/approvals" className="text-xs text-viol-300 hover:text-viol-400 flex items-center gap-1">View all <ArrowRight className="h-3 w-3" /></Link>} />
          <div className="divide-y divide-ink-800">
            {(approvals?.items ?? []).length === 0 && (
              <p className="px-5 py-8 text-center text-xs text-ink-400">No pending approvals — you're all caught up.</p>
            )}
            {approvals?.items.map((ap) => (
              <Link key={ap.id} to="/approvals" className="block px-5 py-3 hover:bg-ink-800/50 transition-colors">
                <p className="truncate text-sm font-medium">{ap.title}</p>
                <p className="mt-0.5 text-[11px] text-ink-400">{titleCase(ap.action_type)} · {formatDateTime(ap.created_at)}</p>
              </Link>
            ))}
          </div>
        </Card>

        {/* recent activity */}
        <Card className="lg:col-span-1">
          <CardHeader title="Recent Activity" subtitle="Latest events across the workspace" />
          <div className="max-h-[420px] divide-y divide-ink-800 overflow-y-auto">
            {(activities?.items ?? []).length === 0 && (
              <p className="px-5 py-8 text-center text-xs text-ink-400">No activity yet. Create a lead to get started.</p>
            )}
            {activities?.items.map((act) => (
              <div key={act.id} className="px-5 py-3">
                <div className="flex items-center justify-between gap-2">
                  <Badge tone={act.actor_type === 'agent' ? 'violet' : act.actor_type === 'system' ? 'amber' : 'blue'}>{titleCase(act.actor_type)}</Badge>
                  <span className="text-[10px] text-ink-400">{formatDateTime(act.created_at)}</span>
                </div>
                <p className="mt-1.5 text-xs text-ink-200">{act.description || act.action}</p>
              </div>
            ))}
          </div>
        </Card>
      </div>
    </div>
  )
}
