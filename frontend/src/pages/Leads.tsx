import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowUpDown, Pencil, Plus, Search, Trash2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api, ApiError, type Activity, type Lead } from '../lib/api'
import { PageHeader } from '../components/metrics'
import {
  Badge, Button, Card, ConfirmDialog, Drawer, EmptyState, Field, Input,
  Select, Skeleton, Textarea, useToast,
} from '../components/ui'
import { formatDate, formatDateTime, titleCase } from '../lib/utils'

const STAGES = ['discovered','researched','qualified','approval_pending','contacted','replied','interested','demo_scheduled','negotiation','won','lost','do_not_contact']
const QUALS = ['unqualified','researching','qualified','disqualified']

const STAGE_TONE: Record<string, 'neutral'|'violet'|'mint'|'amber'|'blue'|'red'> = {
  discovered: 'neutral', researched: 'blue', qualified: 'violet', approval_pending: 'amber',
  contacted: 'blue', replied: 'mint', interested: 'mint', demo_scheduled: 'violet',
  negotiation: 'amber', won: 'mint', lost: 'red', do_not_contact: 'red',
}

interface LeadForm {
  business_name: string; business_type: string; website: string; email: string; phone: string
  city: string; state: string; country: string; contact_person: string; source: string
  lead_score: number; qualification_status: string; notes: string; estimated_value: string; currency: string
}

const emptyForm: LeadForm = {
  business_name: '', business_type: 'cafe', website: '', email: '', phone: '',
  city: '', state: '', country: '', contact_person: '', source: 'manual',
  lead_score: 0, qualification_status: 'unqualified', notes: '', estimated_value: '0', currency: 'USD',
}

function toForm(l?: Lead | null): LeadForm {
  if (!l) return { ...emptyForm }
  return {
    business_name: l.business_name, business_type: l.business_type, website: l.website ?? '',
    email: l.email ?? '', phone: l.phone ?? '', city: l.city ?? '', state: l.state ?? '',
    country: l.country ?? '', contact_person: l.contact_person ?? '', source: l.source,
    lead_score: l.lead_score, qualification_status: l.qualification_status,
    notes: l.notes, estimated_value: String(l.estimated_value), currency: l.currency,
  }
}

export default function Leads() {
  const qc = useQueryClient()
  const toast = useToast()

  const [page, setPage] = useState(1)
  const [q, setQ] = useState('')
  const [debouncedQ, setDebouncedQ] = useState('')
  const [stage, setStage] = useState('')
  const [qual, setQual] = useState('')
  const [sort, setSort] = useState('created_at')
  const [order, setOrder] = useState<'asc' | 'desc'>('desc')
  const [selected, setSelected] = useState<Set<string>>(new Set())

  const [drawerOpen, setDrawerOpen] = useState(false)
  const [editing, setEditing] = useState<Lead | null>(null)
  const [form, setForm] = useState<LeadForm>(emptyForm)
  const [detail, setDetail] = useState<Lead | null>(null)
  const [deleting, setDeleting] = useState<Lead | null>(null)

  // debounce search
  useEffect(() => {
    const t = setTimeout(() => { setDebouncedQ(q); setPage(1) }, 350)
    return () => clearTimeout(t)
  }, [q])

  const params = new URLSearchParams({ page: String(page), page_size: '15', sort, order })
  if (debouncedQ) params.set('q', debouncedQ)
  if (stage) params.set('stage', stage)
  if (qual) params.set('qualification_status', qual)

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['leads', params.toString()],
    queryFn: () => api.get<{ items: Lead[]; total: number; pages: number; page: number }>(`/leads?${params}`),
  })

  const { data: activities } = useQuery({
    queryKey: ['lead-activities', detail?.id],
    queryFn: () => api.get<{ items: Activity[] }>(`/activities?lead_id=${detail!.id}&page_size=30`),
    enabled: !!detail,
  })

  const save = useMutation({
    mutationFn: () => editing
      ? api.patch<Lead>(`/leads/${editing.id}`, { ...form, estimated_value: form.estimated_value || '0' })
      : api.post<Lead>('/leads', { ...form, estimated_value: form.estimated_value || '0' }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['leads'] })
      qc.invalidateQueries({ queryKey: ['analytics'] })
      setDrawerOpen(false); setEditing(null); setForm(emptyForm)
      toast(editing ? 'Lead updated' : 'Lead created', 'success')
    },
    onError: (e) => toast(e instanceof ApiError ? e.message : 'Save failed', 'error'),
  })

  const del = useMutation({
    mutationFn: (id: string) => api.delete(`/leads/${id}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['leads'] })
      setDeleting(null)
      toast('Lead deleted', 'success')
    },
    onError: (e) => toast(e instanceof ApiError ? e.message : 'Delete failed', 'error'),
  })

  const setStageMut = useMutation({
    mutationFn: ({ id, stage }: { id: string; stage: string }) =>
      api.patch<Lead>(`/leads/${id}/stage`, { stage }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['leads'] })
      qc.invalidateQueries({ queryKey: ['pipeline'] })
      qc.invalidateQueries({ queryKey: ['analytics'] })
      toast('Stage updated', 'success')
    },
    onError: (e) => toast(e instanceof ApiError ? e.message : 'Stage update failed', 'error'),
  })

  const bulkStage = useMutation({
    mutationFn: (stage: string) =>
      api.post<{ updated: number }>('/pipeline/bulk-stage', { lead_ids: [...selected], stage }),
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ['leads'] })
      setSelected(new Set())
      toast(`${r.updated} lead(s) moved`, 'success')
    },
    onError: (e) => toast(e instanceof ApiError ? e.message : 'Bulk update failed', 'error'),
  })

  const toggle = (id: string) => setSelected((s) => {
    const n = new Set(s)
    if (n.has(id)) n.delete(id); else n.add(id)
    return n
  })
  const toggleSort = (col: string) => {
    if (sort === col) setOrder(order === 'asc' ? 'desc' : 'asc')
    else { setSort(col); setOrder('desc') }
    setPage(1)
  }

  const submit = (e: React.FormEvent) => { e.preventDefault(); save.mutate() }

  return (
    <div>
      <PageHeader
        title="Leads / CRM"
        subtitle={`${data?.total ?? 0} cafés in your pipeline`}
        action={<Button variant="primary" onClick={() => { setEditing(null); setForm(emptyForm); setDrawerOpen(true) }}><Plus className="h-4 w-4" /> Add lead</Button>}
      />

      {/* toolbar */}
      <Card className="mb-4 flex flex-wrap items-center gap-3 p-3">
        <div className="relative min-w-52 flex-1">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-400" />
          <Input className="pl-9" placeholder="Search name, email, phone, city…" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        <Select className="w-44" value={stage} onChange={(e) => { setStage(e.target.value); setPage(1) }}>
          <option value="">All stages</option>
          {STAGES.map((s) => <option key={s} value={s}>{titleCase(s)}</option>)}
        </Select>
        <Select className="w-44" value={qual} onChange={(e) => { setQual(e.target.value); setPage(1) }}>
          <option value="">All qualification</option>
          {QUALS.map((s) => <option key={s} value={s}>{titleCase(s)}</option>)}
        </Select>
        {selected.size > 0 && (
          <div className="flex items-center gap-2 animate-fade-in">
            <span className="text-xs text-ink-300">{selected.size} selected</span>
            <Select className="w-40" defaultValue="" onChange={(e) => { if (e.target.value) bulkStage.mutate(e.target.value); e.target.value = '' }}>
              <option value="">Move to stage…</option>
              {STAGES.map((s) => <option key={s} value={s}>{titleCase(s)}</option>)}
            </Select>
          </div>
        )}
      </Card>

      {/* table */}
      <Card className="overflow-hidden">
        {isLoading ? (
          <div className="space-y-3 p-5">{Array.from({ length: 8 }).map((_, i) => <Skeleton key={i} className="h-12" />)}</div>
        ) : error ? (
          <div className="p-5"><EmptyState title="Failed to load leads" hint="Check your connection and try again." action={<Button size="sm" onClick={() => refetch()}>Retry</Button>} /></div>
        ) : !data?.items.length ? (
          <div className="p-5"><EmptyState title="No leads found" hint="Add your first café lead or adjust your filters." action={<Button size="sm" variant="primary" onClick={() => setDrawerOpen(true)}><Plus className="h-4 w-4" /> Add lead</Button>} /></div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-ink-700/60 text-left text-[11px] uppercase tracking-wider text-ink-400">
                  <th className="w-10 px-4 py-3"><input type="checkbox" className="accent-viol-500" checked={selected.size === data.items.length && data.items.length > 0} onChange={(e) => setSelected(e.target.checked ? new Set(data.items.map((l) => l.id)) : new Set())} /></th>
                  {[['business_name', 'Business'], ['lead_score', 'Score'], ['qualification_status', 'Qualification'], ['stage', 'Stage'], ['estimated_value', 'Value'], ['last_contacted_at', 'Last contact'], ['created_at', 'Created']].map(([col, label]) => (
                    <th key={col} className="cursor-pointer select-none px-4 py-3 hover:text-ink-200" onClick={() => toggleSort(col)}>
                      <span className="inline-flex items-center gap-1">{label} <ArrowUpDown className="h-3 w-3" /></span>
                    </th>
                  ))}
                  <th className="px-4 py-3" />
                </tr>
              </thead>
              <tbody className="divide-y divide-ink-800">
                {data.items.map((l) => (
                  <tr key={l.id} className="group transition-colors hover:bg-ink-800/40">
                    <td className="px-4 py-3"><input type="checkbox" className="accent-viol-500" checked={selected.has(l.id)} onChange={() => toggle(l.id)} /></td>
                    <td className="px-4 py-3">
                      <button className="text-left font-medium text-ink-100 hover:text-viol-300" onClick={() => setDetail(l)}>{l.business_name}</button>
                      <p className="text-[11px] text-ink-400">{l.contact_person || l.email || '—'}</p>
                    </td>
                    <td className="px-4 py-3"><Badge tone={l.lead_score >= 70 ? 'mint' : l.lead_score >= 40 ? 'amber' : 'neutral'}>{l.lead_score}</Badge></td>
                    <td className="px-4 py-3"><Badge tone={l.qualification_status === 'qualified' ? 'violet' : 'neutral'}>{titleCase(l.qualification_status)}</Badge></td>
                    <td className="px-4 py-3">
                      <select
                        className="rounded-md border border-ink-600/60 bg-ink-900 px-2 py-1 text-[11px] text-ink-200 focus:border-viol-500/60 focus:outline-none cursor-pointer"
                        value={l.stage}
                        onChange={(e) => setStageMut.mutate({ id: l.id, stage: e.target.value })}
                      >
                        {STAGES.map((s) => <option key={s} value={s}>{titleCase(s)}</option>)}
                      </select>
                    </td>
                    <td className="px-4 py-3 text-ink-300">{l.estimated_value}</td>
                    <td className="px-4 py-3 text-xs text-ink-400">{formatDate(l.last_contacted_at)}</td>
                    <td className="px-4 py-3 text-xs text-ink-400">{formatDate(l.created_at)}</td>
                    <td className="px-4 py-3">
                      <div className="flex justify-end gap-1 opacity-0 transition-opacity group-hover:opacity-100">
                        <button className="rounded-md p-1.5 text-ink-400 hover:bg-ink-700 hover:text-ink-100 cursor-pointer" onClick={() => { setEditing(l); setForm(toForm(l)); setDrawerOpen(true) }} title="Edit"><Pencil className="h-3.5 w-3.5" /></button>
                        <button className="rounded-md p-1.5 text-ink-400 hover:bg-red-500/15 hover:text-red-300 cursor-pointer" onClick={() => setDeleting(l)} title="Delete"><Trash2 className="h-3.5 w-3.5" /></button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {data && data.pages > 1 && (
          <div className="flex items-center justify-between border-t border-ink-700/60 px-4 py-3 text-xs text-ink-400">
            <span>Page {data.page} of {data.pages} · {data.total} leads</span>
            <div className="flex gap-2">
              <Button size="sm" variant="secondary" disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous</Button>
              <Button size="sm" variant="secondary" disabled={page >= data.pages} onClick={() => setPage(page + 1)}>Next</Button>
            </div>
          </div>
        )}
      </Card>

      {/* create / edit drawer */}
      <Drawer open={drawerOpen} onClose={() => setDrawerOpen(false)} title={editing ? 'Edit lead' : 'New lead'}>
        <form onSubmit={submit} className="space-y-4">
          <Field label="Business name *"><Input required value={form.business_name} onChange={(e) => setForm({ ...form, business_name: e.target.value })} /></Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Business type"><Input value={form.business_type} onChange={(e) => setForm({ ...form, business_type: e.target.value })} /></Field>
            <Field label="Source"><Input value={form.source} onChange={(e) => setForm({ ...form, source: e.target.value })} /></Field>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Website"><Input value={form.website} onChange={(e) => setForm({ ...form, website: e.target.value })} placeholder="https://…" /></Field>
            <Field label="Email"><Input type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} /></Field>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Phone"><Input value={form.phone} onChange={(e) => setForm({ ...form, phone: e.target.value })} /></Field>
            <Field label="Contact person"><Input value={form.contact_person} onChange={(e) => setForm({ ...form, contact_person: e.target.value })} /></Field>
          </div>
          <div className="grid grid-cols-3 gap-3">
            <Field label="City"><Input value={form.city} onChange={(e) => setForm({ ...form, city: e.target.value })} /></Field>
            <Field label="State"><Input value={form.state} onChange={(e) => setForm({ ...form, state: e.target.value })} /></Field>
            <Field label="Country"><Input value={form.country} onChange={(e) => setForm({ ...form, country: e.target.value })} /></Field>
          </div>
          <div className="grid grid-cols-3 gap-3">
            <Field label="Lead score (0-100)"><Input type="number" min={0} max={100} value={form.lead_score} onChange={(e) => setForm({ ...form, lead_score: Number(e.target.value) })} /></Field>
            <Field label="Qualification">
              <Select value={form.qualification_status} onChange={(e) => setForm({ ...form, qualification_status: e.target.value })}>
                {QUALS.map((s) => <option key={s} value={s}>{titleCase(s)}</option>)}
              </Select>
            </Field>
            <Field label="Est. value"><Input type="number" step="0.01" min={0} value={form.estimated_value} onChange={(e) => setForm({ ...form, estimated_value: e.target.value })} /></Field>
          </div>
          <Field label="Notes"><Textarea value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} /></Field>
          <div className="flex justify-end gap-2 pt-2">
            <Button type="button" variant="ghost" onClick={() => setDrawerOpen(false)}>Cancel</Button>
            <Button type="submit" variant="primary" disabled={save.isPending}>{save.isPending ? 'Saving…' : editing ? 'Save changes' : 'Create lead'}</Button>
          </div>
        </form>
      </Drawer>

      {/* detail drawer */}
      <Drawer open={!!detail} onClose={() => setDetail(null)} title={detail?.business_name ?? 'Lead'}>
        {detail && (
          <div className="space-y-5">
            <div className="flex flex-wrap gap-2">
              <Badge tone={STAGE_TONE[detail.stage] ?? 'neutral'}>{titleCase(detail.stage)}</Badge>
              <Badge tone="violet">Score {detail.lead_score}</Badge>
              <Badge>{titleCase(detail.qualification_status)}</Badge>
            </div>
            <div className="grid grid-cols-2 gap-3 text-sm">
              {[
                ['Type', detail.business_type], ['Website', detail.website], ['Email', detail.email],
                ['Phone', detail.phone], ['Contact', detail.contact_person],
                ['Location', [detail.city, detail.state, detail.country].filter(Boolean).join(', ') || null],
                ['Source', detail.source], ['Value', detail.estimated_value],
                ['Created', formatDate(detail.created_at)], ['Last contact', formatDate(detail.last_contacted_at)],
              ].map(([k, v]) => (
                <div key={k} className="rounded-lg bg-ink-800/60 px-3 py-2">
                  <p className="text-[10px] uppercase text-ink-400">{k}</p>
                  <p className="mt-0.5 truncate text-ink-200">{v || '—'}</p>
                </div>
              ))}
            </div>
            {detail.notes && (
              <div className="rounded-lg border border-ink-700/60 bg-ink-800/40 p-3">
                <p className="text-[10px] uppercase text-ink-400 mb-1">Notes</p>
                <p className="whitespace-pre-wrap text-sm text-ink-200">{detail.notes}</p>
              </div>
            )}
            <div>
              <h4 className="mb-2 text-xs font-semibold uppercase tracking-wider text-ink-400">Activity history</h4>
              <div className="space-y-2">
                {(activities?.items ?? []).map((act) => (
                  <div key={act.id} className="flex gap-3 rounded-lg bg-ink-800/40 px-3 py-2">
                    <div className="mt-0.5 h-1.5 w-1.5 shrink-0 rounded-full bg-viol-400" />
                    <div className="min-w-0">
                      <p className="text-xs text-ink-200">{act.description || act.action}</p>
                      <p className="text-[10px] text-ink-400">{titleCase(act.actor_type)} · {formatDateTime(act.created_at)}</p>
                    </div>
                  </div>
                ))}
                {!activities?.items.length && <p className="text-xs text-ink-400">No recorded activity.</p>}
              </div>
            </div>
            <div className="flex gap-2 border-t border-ink-700/60 pt-4">
              <Button size="sm" variant="secondary" onClick={() => { setEditing(detail); setForm(toForm(detail)); setDetail(null); setDrawerOpen(true) }}><Pencil className="h-3.5 w-3.5" /> Edit</Button>
              <Button size="sm" variant="danger" onClick={() => { setDeleting(detail); setDetail(null) }}><Trash2 className="h-3.5 w-3.5" /> Delete</Button>
            </div>
          </div>
        )}
      </Drawer>

      <ConfirmDialog
        open={!!deleting}
        title="Delete lead?"
        message={`This will permanently delete '${deleting?.business_name}' and its contacts, conversations and activities. This cannot be undone.`}
        confirmLabel="Delete"
        danger
        busy={del.isPending}
        onConfirm={() => deleting && del.mutate(deleting.id)}
        onCancel={() => setDeleting(null)}
      />
    </div>
  )
}
