import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, Bot, ShieldCheck, Wrench } from 'lucide-react'
import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, ApiError, type Agent } from '../lib/api'
import { PageHeader } from '../components/metrics'
import { Badge, Button, Card, CardHeader, Field, Select, Skeleton, Textarea, useToast } from '../components/ui'
import { formatDateTime, titleCase } from '../lib/utils'

export default function AgentDetail() {
  const { id } = useParams<{ id: string }>()
  const qc = useQueryClient()
  const toast = useToast()
  const [editing, setEditing] = useState(false)
  const [form, setForm] = useState({ status: '', enabled: true, description: '', system_prompt: '' })

  const { data: agent, isLoading } = useQuery({
    queryKey: ['agent', id],
    queryFn: () => api.get<Agent>(`/agents/${id}`),
  })
  const { data: runs } = useQuery({
    queryKey: ['agent-runs', id],
    queryFn: () => api.get<{ items: { id: string; status: string; created_at: string; tokens_prompt: number; tokens_completion: number; error: string | null }[] }>(`/agents/${id}/runs?page_size=20`),
  })

  const save = useMutation({
    mutationFn: () => api.patch<Agent>(`/agents/${id}`, {
      status: form.status || undefined,
      enabled: form.enabled,
      description: form.description || undefined,
      system_prompt: form.system_prompt || undefined,
    }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['agent', id] })
      qc.invalidateQueries({ queryKey: ['agents'] })
      setEditing(false)
      toast('Agent updated', 'success')
    },
    onError: (e) => toast(e instanceof ApiError ? e.message : 'Update failed', 'error'),
  })

  if (isLoading || !agent) return <Skeleton className="h-96" />

  return (
    <div>
      <Link to="/agents" className="mb-4 inline-flex items-center gap-1.5 text-xs text-ink-400 hover:text-ink-100">
        <ArrowLeft className="h-3.5 w-3.5" /> Back to employees
      </Link>
      <PageHeader
        title={agent.name}
        subtitle={agent.title}
        action={
          <Button variant="secondary" size="sm" onClick={() => {
            if (!editing) {
              setForm({ status: agent.status, enabled: agent.enabled, description: agent.description, system_prompt: agent.system_prompt })
            }
            setEditing(!editing)
          }}>{editing ? 'Cancel' : 'Configure'}</Button>
        }
      />

      <div className="grid gap-6 lg:grid-cols-3">
        <div className="space-y-6 lg:col-span-2">
          <Card className="p-5">
            <div className="flex items-start gap-4">
              <div className="flex h-16 w-16 items-center justify-center rounded-2xl bg-gradient-to-br from-viol-500/25 to-mint-500/15 text-viol-300">
                <Bot className="h-8 w-8" />
              </div>
              <div className="flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge tone={agent.status === 'error' ? 'red' : agent.status === 'active' ? 'mint' : 'neutral'}>{titleCase(agent.status)}</Badge>
                  <Badge tone={agent.enabled ? 'mint' : 'amber'}>{agent.enabled ? 'Enabled' : 'Disabled'}</Badge>
                </div>
                <p className="mt-3 text-sm leading-relaxed text-ink-300">{agent.description}</p>
              </div>
            </div>
            <div className="mt-5 grid grid-cols-3 gap-3 border-t border-ink-700/50 pt-4 text-center">
              {[['Workload', agent.current_workload], ['Tasks done', agent.tasks_completed], ['Runs', runs?.items.length ?? 0]].map(([l, v]) => (
                <div key={l as string}><p className="text-xl font-bold">{v as number}</p><p className="text-[10px] uppercase text-ink-400">{l}</p></div>
              ))}
            </div>
          </Card>

          {editing && (
            <Card className="p-5 animate-fade-up">
              <h3 className="mb-4 text-sm font-semibold">Configuration</h3>
              <div className="grid gap-4 sm:grid-cols-2">
                <Field label="Status">
                  <Select value={form.status} onChange={(e) => setForm({ ...form, status: e.target.value })}>
                    {['active', 'idle', 'paused', 'error'].map((s) => <option key={s} value={s}>{titleCase(s)}</option>)}
                  </Select>
                </Field>
                <Field label="Enabled">
                  <Select value={String(form.enabled)} onChange={(e) => setForm({ ...form, enabled: e.target.value === 'true' })}>
                    <option value="true">Yes</option><option value="false">No</option>
                  </Select>
                </Field>
              </div>
              <div className="mt-4 space-y-4">
                <Field label="Description"><Textarea value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} /></Field>
                <Field label="System prompt"><Textarea className="min-h-32 font-mono text-xs" value={form.system_prompt} onChange={(e) => setForm({ ...form, system_prompt: e.target.value })} /></Field>
              </div>
              <div className="mt-4 flex justify-end gap-2">
                <Button variant="primary" size="sm" onClick={() => save.mutate()} disabled={save.isPending}>Save changes</Button>
              </div>
            </Card>
          )}

          <Card>
            <CardHeader title="Execution History" subtitle="Recorded agent runs (Phase 1 structural runs)" />
            <div className="divide-y divide-ink-800">
              {(runs?.items ?? []).length === 0 && (
                <p className="px-5 py-8 text-center text-xs text-ink-400">No runs recorded yet. Autonomous execution arrives in a later phase.</p>
              )}
              {runs?.items.map((r) => (
                <div key={r.id} className="flex items-center justify-between px-5 py-3 text-xs">
                  <div>
                    <Badge tone={r.status === 'succeeded' ? 'mint' : r.status === 'failed' ? 'red' : r.status === 'awaiting_approval' ? 'amber' : 'blue'}>{titleCase(r.status)}</Badge>
                    {r.error && <p className="mt-1 text-red-300">{r.error}</p>}
                  </div>
                  <div className="text-right text-ink-400">
                    <p>{r.tokens_prompt + r.tokens_completion} tokens</p>
                    <p>{formatDateTime(r.created_at)}</p>
                  </div>
                </div>
              ))}
            </div>
          </Card>
        </div>

        <div className="space-y-6">
          <Card className="p-5">
            <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold"><Wrench className="h-4 w-4 text-ink-400" /> Tools</h3>
            {(agent.tools ?? []).length === 0 ? (
              <p className="text-xs text-ink-400">No tools assigned. Tools are wired in when integrations go live.</p>
            ) : (
              <ul className="space-y-1.5">{(agent.tools as string[]).map((t) => <li key={t} className="text-xs text-ink-300">· {t}</li>)}</ul>
            )}
          </Card>
          <Card className="p-5">
            <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold"><ShieldCheck className="h-4 w-4 text-ink-400" /> Permissions</h3>
            <pre className="overflow-x-auto rounded-lg bg-ink-900 p-3 text-[11px] text-ink-300">{JSON.stringify(agent.permissions, null, 2)}</pre>
          </Card>
        </div>
      </div>
    </div>
  )
}
