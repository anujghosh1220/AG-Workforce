import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../lib/api'
import type { Campaign, Eligibility, Lead, Page } from '../lib/api'
import { titleCase } from '../lib/utils'
import { Badge, Button, Card, EmptyState, Input, Skeleton } from '../components/ui'

export default function Campaigns() {
  const qc = useQueryClient()
  const [name, setName] = useState('')
  const [instructions, setInstructions] = useState('')
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [activeId, setActiveId] = useState<string | null>(null)
  const [leadQ, setLeadQ] = useState('')

  const campaigns = useQuery({ queryKey: ['campaigns'], queryFn: () => api.get<Page<Campaign>>('/campaigns?page_size=20') })
  const leads = useQuery({ queryKey: ['leads-eligible', leadQ],
    queryFn: () => api.get<Page<Lead>>(`/leads?page_size=100&q=${encodeURIComponent(leadQ)}&qualification_status=qualified`) })
  const eligibility = useQuery({ queryKey: ['eligibility', activeId],
    enabled: !!activeId,
    queryFn: () => api.get<Eligibility[]>(`/campaigns/${activeId}/eligibility`) })

  const create = useMutation({
    mutationFn: () => api.post<Campaign>('/campaigns', { name, lead_ids: [...selected], channel: 'email', instructions }),
    onSuccess: (c) => { setActiveId(c.id); setName(''); setSelected(new Set()); qc.invalidateQueries({ queryKey: ['campaigns'] }) },
  })
  const act = (path: string) => useMutation({
    mutationFn: () => api.post(`/campaigns/${activeId}${path}`, path === '/authorize' ? { confirm: true } : {}),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['campaigns'] }); qc.invalidateQueries({ queryKey: ['eligibility', activeId] }) },
  })
  const generate = act('/generate')
  const authorize = act('/authorize')
  const send = act('/send') as ReturnType<typeof useMutation<{status:string;stats:Record<string,number>}, Error, void>>

  const eligibleLeads = (leads.data?.items ?? []).filter(l => l.email || l.phone)
  const active = campaigns.data?.items.find(c => c.id === activeId)

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Sales Campaigns</h1>
        <p className="text-sm text-muted">Owner-authorized outreach to qualified prospects â€” every send passes compliance checks</p>
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        {/* new campaign */}
        <Card className="p-5">
          <h2 className="mb-3 font-semibold">New campaign</h2>
          <div className="space-y-3">
            <Input placeholder="Campaign name" value={name} onChange={e => setName(e.target.value)} />
            <Input placeholder="Instructions for personalization (optional)" value={instructions} onChange={e => setInstructions(e.target.value)} />
            <Input placeholder="Filter leadsâ€¦" value={leadQ} onChange={e => setLeadQ(e.target.value)} />
            <div className="max-h-56 space-y-1 overflow-y-auto rounded-lg border border-line p-2">
              {leads.isLoading ? <Skeleton className="h-8 w-full" /> : eligibleLeads.length === 0
                ? <p className="p-2 text-xs text-muted">No qualified leads with contact info. Import leads or run research first.</p>
                : eligibleLeads.map(l => (
                  <label key={l.id} className="flex cursor-pointer items-center gap-2 rounded p-1.5 text-sm hover:bg-surface2">
                    <input type="checkbox" checked={selected.has(l.id)} onChange={() => {
                      const n = new Set(selected); n.has(l.id) ? n.delete(l.id) : n.add(l.id); setSelected(n)
                    }} />
                    <span className="flex-1">{l.business_name}</span>
                    <span className="text-xs text-muted">{l.email || l.phone}</span>
                  </label>
                ))}
            </div>
            <Button className="w-full" disabled={!name || selected.size === 0 || create.isPending}
              onClick={() => create.mutate()}>
              {create.isPending ? 'Creatingâ€¦' : `Create campaign (${selected.size} leads)`}
            </Button>
          </div>
        </Card>

        {/* campaign detail / history */}
        <Card className="p-5">
          <h2 className="mb-3 font-semibold">Campaigns</h2>
          {campaigns.isLoading ? <Skeleton className="h-24 w-full" /> : (campaigns.data?.items.length ?? 0) === 0
            ? <EmptyState title="No campaigns yet" hint="Create one to start authorized outreach." />
            : <div className="space-y-2">
              {campaigns.data!.items.map(c => (
                <button key={c.id} onClick={() => setActiveId(c.id)}
                  className={`block w-full rounded-lg border p-3 text-left ${activeId === c.id ? 'border-violet' : 'border-line'} hover:bg-surface2/50`}>
                  <div className="flex items-center justify-between">
                    <p className="text-sm font-medium">{c.name}</p>
                    <Badge tone={c.status === 'completed' ? 'mint' : c.status === 'authorized' ? 'violet' : c.status === 'sending' ? 'amber' : 'neutral'}>{titleCase(c.status)}</Badge>
                  </div>
                  <p className="mt-1 text-xs text-muted">{c.lead_ids.length} leads Â· {c.stats?.drafted ?? 0} drafted Â· {c.stats?.sent ?? 0} sent Â· {c.stats?.blocked ?? 0} blocked</p>
                </button>
              ))}
            </div>}

          {active && (
            <div className="mt-4 space-y-3 border-t border-line pt-4">
              <p className="text-sm font-medium">{active.name}</p>
              {/* eligibility table */}
              {eligibility.data && (
                <div className="max-h-48 overflow-y-auto rounded-lg border border-line">
                  <table className="w-full text-xs">
                    <thead><tr className="border-b border-line text-left text-muted"><th className="p-2">Lead</th><th className="p-2">Contact</th><th className="p-2">Status</th></tr></thead>
                    <tbody>
                      {eligibility.data.map(e => (
                        <tr key={e.lead_id} className="border-b border-line/50">
                          <td className="p-2">{e.business_name}</td>
                          <td className="p-2">{e.contact_value ?? 'â€”'}</td>
                          <td className="p-2">{e.allowed ? <Badge tone="mint">eligible</Badge> : <Badge tone="amber">{e.reason ?? 'blocked'}</Badge>}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              <div className="flex flex-wrap gap-2">
                <Button variant="secondary" onClick={() => generate.mutate()} disabled={generate.isPending}>Generate drafts</Button>
                {active.status === 'draft' && <Button variant="secondary" onClick={() => authorize.mutate()} disabled={authorize.isPending}>Authorize (owner)</Button>}
                {active.status === 'authorized' && <Button onClick={() => send.mutate()} disabled={send.isPending}>Send campaign</Button>}
              </div>
              {send.data && <p className="text-xs text-muted">Result: {send.data.status} — {JSON.stringify(send.data.stats ?? {})}</p>}
            </div>
          )}
        </Card>
      </div>
    </div>
  )
}
