import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../lib/api'
import type { SystemStatus } from '../lib/api'
import type { ConversationDetail, Page } from '../lib/api'
import { titleCase } from '../lib/utils'
import { Badge, Button, Card, EmptyState, Input, Select, Skeleton } from '../components/ui'

const STATE_TONE: Record<string, 'mint'|'violet'|'amber'|'red'|'blue'|'neutral'> = {
  new:'blue', contacted:'violet', awaiting_reply:'amber', engaged:'violet',
  interested:'mint', demo_requested:'mint', demo_scheduled:'mint',
  not_interested:'neutral', follow_up_scheduled:'amber', opted_out:'red',
  human_handoff:'red', closed:'neutral',
}

export default function Conversations() {
  const qc = useQueryClient()
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [q, setQ] = useState('')
  const [state, setState] = useState('')
  const [needsHuman, setNeedsHuman] = useState('')
  const [reply, setReply] = useState('')
  const [suggestion, setSuggestion] = useState<{subject:string;body:string;needs_human_review:boolean;reason:string;provider:string}|null>(null)

  const params = new URLSearchParams({ page_size: '50' })
  if (q) params.set('q', q)
  if (state) params.set('state', state)
  if (needsHuman) params.set('needs_human', needsHuman)
  const sysStatus = useQuery({ queryKey: ['sys-status'], queryFn: () => api.get<SystemStatus>('/system/status'), staleTime: 60000 })
  const list = useQuery({ queryKey: ['conversations', params.toString()],
    queryFn: () => api.get<Page<ConversationDetail>>(`/conversations?${params}`) })
  const detail = useQuery({ queryKey: ['conv', selectedId],
    enabled: !!selectedId,
    queryFn: () => api.get<ConversationDetail>(`/conversations/${selectedId}`) })

  const suggest = useMutation({
    mutationFn: () => api.post<{subject:string;body:string;needs_human_review:boolean;reason:string;provider:string}>(`/conversations/${selectedId}/suggest`),
    onSuccess: (d) => { setSuggestion(d); setReply(d.body) },
  })
  const sendReply = useMutation({
    mutationFn: () => api.post<{sent:boolean;reason?:string}>(`/conversations/${selectedId}/send`, { subject: `Re: ${detail.data?.lead_business_name ?? 'conversation'}`, body: reply }),
    onSuccess: () => { setReply(''); qc.invalidateQueries({ queryKey: ['conv', selectedId] }); qc.invalidateQueries({ queryKey: ['conversations'] }) },
  })
  const draftReply = useMutation({
    mutationFn: () => api.post(`/conversations/${selectedId}/draft-reply`, { body: reply }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['approvals'] }),
  })
  const handoff = useMutation({
    mutationFn: () => api.post(`/conversations/${selectedId}/handoff`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['conv', selectedId] }),
  })
  const resume = useMutation({
    mutationFn: () => api.post(`/conversations/${selectedId}/resume-ai`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['conv', selectedId] }),
  })
  const addFollowUp = useMutation({
    mutationFn: () => api.post(`/conversations/${selectedId}/follow-ups`, { hours: 24, max_attempts: 3 }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['conv', selectedId] }),
  })

  const convs = list.data?.items ?? []
  const c = detail.data

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-bold">Unified Inbox</h1>
        <p className="text-sm text-muted">AI-managed prospect conversations across channels</p>
        {sysStatus.data && (
          <p className="mt-1 text-xs">
            <Badge tone={sysStatus.data.email.configured ? 'mint' : 'amber'}>Email {sysStatus.data.email.configured ? 'connected' : 'not configured'}</Badge>{' '}
            <Badge tone={sysStatus.data.llm.configured ? 'mint' : 'amber'}>AI {sysStatus.data.llm.configured ? sysStatus.data.llm.model : 'heuristic fallback'}</Badge>{' '}
            <Badge tone={sysStatus.data.workspace.inbound_configured ? 'mint' : 'amber'}>Inbound {sysStatus.data.workspace.inbound_configured ? 'routed' : 'not set'}</Badge>
          </p>
        )}
      </div>
      <div className="flex flex-wrap gap-2">
        <Input className="w-64" placeholder="Search business, email, city…" value={q} onChange={e => setQ(e.target.value)} />
        <Select value={state} onChange={e => setState(e.target.value)}>
          <option value="">All states</option>
          {['new','contacted','awaiting_reply','engaged','interested','demo_requested','demo_scheduled','not_interested','follow_up_scheduled','opted_out','human_handoff','closed'].map(s => <option key={s} value={s}>{titleCase(s)}</option>)}
        </Select>
        <Select value={needsHuman} onChange={e => setNeedsHuman(e.target.value)}>
          <option value="">All</option><option value="true">Needs human</option><option value="false">AI handling</option>
        </Select>
      </div>

      <div className="grid gap-4 lg:grid-cols-5">
        {/* conversation list */}
        <Card className="p-0 lg:col-span-2">
          {list.isLoading ? <div className="space-y-2 p-4">{[...Array(5)].map((_,i)=><Skeleton key={i} className="h-16 w-full"/>)}</div>
          : convs.length === 0 ? <EmptyState title="No conversations" hint="Conversations appear when prospects reply or outreach begins." />
          : convs.map(cv => (
            <button key={cv.id} onClick={() => setSelectedId(cv.id)}
              className={`block w-full border-b border-line p-3 text-left hover:bg-surface2/60 ${selectedId === cv.id ? 'bg-surface2' : ''}`}>
              <div className="flex items-center justify-between">
                <p className="text-sm font-medium">{cv.lead_business_name ?? 'Unknown lead'}</p>
                <div className="flex gap-1">
                  {cv.needs_human && <Badge tone="red">Human</Badge>}
                  <Badge tone={STATE_TONE[cv.state] ?? 'neutral'}>{titleCase(cv.state)}</Badge>
                </div>
              </div>
              <p className="mt-1 text-xs text-muted">{cv.channel} · {cv.last_intent ? titleCase(cv.last_intent) : 'no intent yet'} · {new Date(cv.updated_at).toLocaleDateString()}</p>
              {cv.summary && <p className="mt-1 line-clamp-2 text-xs text-muted">{cv.summary}</p>}
            </button>
          ))}
        </Card>

        {/* thread */}
        <Card className="p-0 lg:col-span-3">
          {!c ? <EmptyState title="Select a conversation" hint="Pick a thread on the left." /> : (
            <div className="flex h-[70vh] flex-col">
              <div className="flex items-center justify-between border-b border-line p-4">
                <div>
                  <p className="font-semibold">{c.lead_business_name ?? 'Conversation'}</p>
                  <p className="text-xs text-muted">{titleCase(c.state)}{c.last_intent ? ` · intent: ${titleCase(c.last_intent)}` : ''} · {c.channel}</p>
                  {c.summary && <p className="mt-1 text-xs text-muted italic">{c.summary}</p>}
                </div>
                <div className="flex gap-2">
                  {c.needs_human
                    ? <Button variant="secondary" onClick={() => resume.mutate()}>Resume AI</Button>
                    : <Button variant="secondary" onClick={() => handoff.mutate()}>Human handoff</Button>}
                </div>
              </div>
              <div className="flex-1 space-y-3 overflow-y-auto p-4">
                {c.messages.map(m => (
                  <div key={m.id} className={`max-w-[85%] rounded-xl p-3 text-sm ${m.direction === 'inbound' ? 'bg-surface2' : 'ml-auto bg-violet/15'}`}>
                    <div className="mb-1 flex items-center gap-2 text-xs text-muted">
                      <span>{m.direction === 'inbound' ? 'Prospect' : 'Us'}</span>
                      {m.is_ai_draft && <Badge tone="blue">AI draft</Badge>}
                      <Badge tone={m.status === 'sent' ? 'mint' : m.status === 'failed' ? 'red' : 'neutral'}>{titleCase(m.status)}</Badge>
                      <span>{m.created_at ? new Date(m.created_at).toLocaleString() : ''}</span>
                    </div>
                    {m.subject && <p className="mb-1 text-xs font-medium">{m.subject}</p>}
                    <p className="whitespace-pre-wrap">{m.body}</p>
                  </div>
                ))}
              </div>
              <div className="border-t border-line p-4">
                {suggestion && (
                  <div className="mb-3 rounded-lg border border-line bg-surface2/50 p-3 text-xs">
                    <div className="mb-1 flex items-center justify-between">
                      <span className="font-medium">AI suggestion ({suggestion.provider})</span>
                      {suggestion.needs_human_review && <Badge tone="amber">needs review</Badge>}
                    </div>
                    {suggestion.reason && <p className="text-muted">{suggestion.reason}</p>}
                  </div>
                )}
                <textarea className="mb-2 h-24 w-full rounded-lg border border-line bg-surface p-3 text-sm" placeholder="Type a reply or click Suggest…"
                  value={reply} onChange={e => setReply(e.target.value)} />
                <div className="flex flex-wrap gap-2">
                  <Button variant="secondary" onClick={() => suggest.mutate()} disabled={suggest.isPending}>Suggest reply</Button>
                  <Button variant="secondary" onClick={() => draftReply.mutate()} disabled={!reply.trim() || draftReply.isPending}>Create approval draft</Button>
                  <Button onClick={() => sendReply.mutate()} disabled={!reply.trim() || sendReply.isPending}>Send now</Button>
                  <Button variant="ghost" onClick={() => addFollowUp.mutate()}>+ Follow-up 24h</Button>
                </div>
                {sendReply.data && <p className={`mt-2 text-xs ${sendReply.data.sent ? 'text-mint' : 'text-red'}`}>{sendReply.data.sent ? 'Sent' : `Failed: ${sendReply.data.reason}`}</p>}
              </div>
            </div>
          )}
        </Card>
      </div>
    </div>
  )
}
