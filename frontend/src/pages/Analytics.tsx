import { useQuery } from '@tanstack/react-query'
import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, Cell,
  Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'
import { api, type AnalyticsOverview } from '../lib/api'
import { PageHeader } from '../components/metrics'
import { Badge, Card, CardHeader, EmptyState, Skeleton } from '../components/ui'
import { titleCase } from '../lib/utils'

const COLORS = ['#8b5cf6', '#34d399', '#38bdf8', '#fbbf24', '#f472b6', '#fb7185', '#4ade80', '#22d3ee', '#e879f9', '#f87171', '#64748b']

function pct(v: number | null) {
  return v === null ? '—' : `${(v * 100).toFixed(1)}%`
}

export default function Analytics() {
  const { data: a, isLoading } = useQuery({
    queryKey: ['analytics'],
    queryFn: () => api.get<AnalyticsOverview>('/analytics/overview'),
  })

  if (isLoading) return <div className="space-y-6"><Skeleton className="h-10 w-56" /><div className="grid grid-cols-2 gap-4 md:grid-cols-4">{Array.from({ length: 8 }).map((_, i) => <Skeleton key={i} className="h-24" />)}</div><Skeleton className="h-80" /></div>

  if (!a) return <EmptyState title="No analytics" hint="Analytics appear once records exist." />

  const stageData = Object.entries(a.leads_by_stage).map(([k, v]) => ({ name: titleCase(k), value: v }))
  const sourceData = Object.entries(a.leads_by_source).map(([k, v]) => ({ name: titleCase(k), value: v }))
  const taskData = Object.entries(a.tasks_by_status).map(([k, v]) => ({ name: titleCase(k), value: v }))
  const msgSeries = mergeSeries(a.leads_over_time, a.messages_over_time)

  return (
    <div className="space-y-6">
      <PageHeader title="Analytics" subtitle="Metrics computed from your actual records" />

      <div className="stagger grid grid-cols-2 gap-4 md:grid-cols-4">
        {[
          ['Response rate', pct(a.response_rate), 'Replied / sent'],
          ['Qualification rate', pct(a.qualification_rate), 'Qualified / total leads'],
          ['Sales conversion', pct(a.conversion_rate), 'Won / total leads'],
          ['Pending approvals', String(a.pending_approvals), 'Awaiting review'],
        ].map(([label, value, hint]) => (
          <Card key={label} className="p-5">
            <p className="text-xs font-medium uppercase tracking-wider text-ink-400">{label}</p>
            <p className="mt-2 text-3xl font-bold tracking-tight">{value}</p>
            <p className="mt-1 text-[11px] text-ink-400">{hint}</p>
          </Card>
        ))}
      </div>

      {(a.demos_booked !== undefined || a.demos_requested !== undefined) && (
        <Card>
          <CardHeader title="Demo & appointment funnel" subtitle="Computed from appointment records" />
          <div className="grid grid-cols-2 gap-3 p-5 text-center sm:grid-cols-4 lg:grid-cols-8">
            {([
              ['Requested', a.demos_requested ?? 0], ['Proposed', a.demos_proposed ?? 0],
              ['Booked', a.demos_booked ?? 0], ['Completed', a.demos_completed ?? 0],
              ['Cancelled', a.demos_cancelled ?? 0], ['No-shows', a.demo_no_shows ?? 0],
              ['Attendance', pct(a.demo_attendance_rate ?? null)],
              ['Demo→customer', pct(a.demo_to_customer_rate ?? null)],
            ] as [string, string | number][]).map(([l, v]) => (
              <div key={l} className="rounded-xl bg-ink-800/60 p-4">
                <p className="text-xl font-bold">{v}</p>
                <p className="mt-1 text-[11px] text-ink-400">{l}</p>
              </div>
            ))}
          </div>
        </Card>
      )}

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader title="Lead acquisition" subtitle="Last 30 days" />
          <div className="h-64 p-4">
            {msgSeries.length === 0 ? <EmptyState title="No data" /> : (
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={msgSeries}>
                  <defs>
                    <linearGradient id="gLeads" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#8b5cf6" stopOpacity={0.4} /><stop offset="100%" stopColor="#8b5cf6" stopOpacity={0} /></linearGradient>
                    <linearGradient id="gMsgs" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#34d399" stopOpacity={0.4} /><stop offset="100%" stopColor="#34d399" stopOpacity={0} /></linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke="#2a2a3d" vertical={false} />
                  <XAxis dataKey="date" tick={{ fill: '#6b6b85', fontSize: 10 }} tickLine={false} axisLine={false} />
                  <YAxis tick={{ fill: '#6b6b85', fontSize: 11 }} tickLine={false} axisLine={false} allowDecimals={false} />
                  <Tooltip contentStyle={{ background: '#171722', border: '1px solid #2a2a3d', borderRadius: 10, fontSize: 12 }} />
                  <Area type="monotone" dataKey="leads" name="Leads" stroke="#8b5cf6" fill="url(#gLeads)" strokeWidth={2} />
                  <Area type="monotone" dataKey="messages" name="Messages" stroke="#34d399" fill="url(#gMsgs)" strokeWidth={2} />
                </AreaChart>
              </ResponsiveContainer>
            )}
          </div>
        </Card>

        <Card>
          <CardHeader title="Outreach performance" subtitle="Sent vs inbound messages" />
          <div className="grid grid-cols-2 gap-3 p-5 text-center sm:grid-cols-5">
            {[['Emails sent', a.emails_sent], ['WhatsApp sent', a.whatsapp_sent], ['Messages received', a.messages_received], ['Interested', a.interested_prospects], ['Meetings booked', a.meetings_booked]].map(([l, v]) => (
              <div key={l as string} className="rounded-xl bg-ink-800/60 p-4">
                <p className="text-2xl font-bold">{v as number}</p>
                <p className="mt-1 text-[11px] text-ink-400">{l}</p>
              </div>
            ))}
          </div>
          <div className="border-t border-ink-700/50 px-5 py-4">
            <p className="mb-2 text-xs font-medium text-ink-300">Leads by source</p>
            {sourceData.length === 0 ? <p className="text-xs text-ink-400">No source data yet.</p> : (
              <ResponsiveContainer width="100%" height={180}>
                <PieChart>
                  <Pie data={sourceData} dataKey="value" nameKey="name" innerRadius={35} outerRadius={65} paddingAngle={3}>
                    {sourceData.map((_, i) => <Cell key={i} fill={COLORS[i % COLORS.length]} />)}
                  </Pie>
                  <Tooltip contentStyle={{ background: '#171722', border: '1px solid #2a2a3d', borderRadius: 10, fontSize: 12 }} />
                </PieChart>
              </ResponsiveContainer>
            )}
          </div>
        </Card>
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader title="Leads by stage" subtitle="Pipeline distribution" />
          <div className="h-64 p-4">
            {stageData.length === 0 ? <EmptyState title="No leads yet" /> : (
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={stageData} layout="vertical" margin={{ left: 10 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#2a2a3d" horizontal={false} />
                  <XAxis type="number" tick={{ fill: '#6b6b85', fontSize: 11 }} tickLine={false} axisLine={false} allowDecimals={false} />
                  <YAxis type="category" dataKey="name" tick={{ fill: '#9a9ab2', fontSize: 10 }} tickLine={false} axisLine={false} width={110} />
                  <Tooltip contentStyle={{ background: '#171722', border: '1px solid #2a2a3d', borderRadius: 10, fontSize: 12 }} />
                  <Bar dataKey="value" name="Leads" radius={[0, 6, 6, 0]}>
                    {stageData.map((_, i) => <Cell key={i} fill={COLORS[i % COLORS.length]} />)}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            )}
          </div>
        </Card>

        <Card>
          <CardHeader title="Agent productivity" subtitle="Execution runs and token usage" />
          <div className="p-5">
            {a.agent_activity.length === 0 ? (
              <EmptyState title="No agent runs" hint="Runs are recorded when agents execute tasks. Phase 1 records structural runs only." />
            ) : (
              <div className="space-y-3">
                {a.agent_activity.map((r) => (
                  <div key={r.status} className="flex items-center justify-between rounded-lg bg-ink-800/50 px-4 py-3">
                    <div>
                      <p className="text-sm font-medium">{titleCase(r.status)} runs</p>
                      <p className="text-[11px] text-ink-400">{r.tokens.toLocaleString()} tokens used</p>
                    </div>
                    <p className="text-xl font-bold">{r.runs}</p>
                  </div>
                ))}
              </div>
            )}
            <div className="mt-4 border-t border-ink-700/50 pt-4">
              <p className="mb-2 text-xs font-medium text-ink-300">Tasks by status</p>
              {taskData.length === 0 ? <p className="text-xs text-ink-400">No tasks yet.</p> : (
                <div className="flex flex-wrap gap-2">
                  {taskData.map((t) => <Badge key={t.name} tone="neutral">{t.name}: {t.value}</Badge>)}
                </div>
              )}
            </div>
          </div>
        </Card>
      </div>
    </div>
  )
}

function mergeSeries(leads: { date: string; count: number }[], msgs: { date: string; count: number }[]) {
  const map = new Map<string, { date: string; leads: number; messages: number }>()
  for (const l of leads) map.set(l.date, { date: l.date, leads: l.count, messages: 0 })
  for (const m of msgs) {
    const e = map.get(m.date) ?? { date: m.date, leads: 0, messages: 0 }
    e.messages = m.count
    map.set(m.date, e)
  }
  return [...map.values()].sort((a, b) => a.date.localeCompare(b.date))
}
