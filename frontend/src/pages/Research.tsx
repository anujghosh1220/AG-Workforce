import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, ApiError } from '../lib/api'
import type { Page, Provider, ResearchJob, ResearchResult } from '../lib/api'
import { titleCase } from '../lib/utils'
import { Badge, Button, Card, EmptyState, Input, Select, Skeleton } from '../components/ui'

const BIZ_TYPES = ['cafe', 'coffee_shop', 'bakery_cafe', 'dessert_cafe', 'tea_cafe', 'specialty_coffee', 'restaurant_cafe']

const EMPTY_FORM = {
  country: 'India', state: 'West Bengal', district: '', city: 'Siliguri', town: '', locality: '',
  postal_code: '', latitude: '', longitude: '', radius_m: 5000,
  business_types: ['cafe'] as string[], keywords: '', exclusions: '',
  max_results: 50, providers: [] as string[],
}

function useJobs() {
  return useQuery({ queryKey: ['research-jobs'], queryFn: () => api.get<Page<ResearchJob>>('/research/jobs?page_size=20') })
}
function useResults(params: Record<string, string | number>) {
  const qs = new URLSearchParams(Object.entries(params).map(([k, v]) => [k, String(v)])).toString()
  return useQuery({ queryKey: ['research-results', qs], queryFn: () => api.get<Page<ResearchResult>>(`/research/results?${qs}`) })
}

export default function Research() {
  const qc = useQueryClient()
  const [form, setForm] = useState({ ...EMPTY_FORM })
  const [tab, setTab] = useState<'results' | 'history'>('results')
  const [filters, setFilters] = useState({ q: '', status: '', min_score: '', page: 1 })
  const [selected, setSelected] = useState<ResearchResult | null>(null)
  const [checked, setChecked] = useState<Set<string>>(new Set())

  const providers = useQuery({ queryKey: ['providers'], queryFn: () => api.get<Provider[]>('/research/providers') })
  const jobs = useJobs()
  const results = useResults({
    page: filters.page, page_size: 25, q: filters.q, status: filters.status,
    min_score: filters.min_score,
    ...(jobs.data?.items.find(j => j.status === 'running' || j.status === 'queued') ? {} : {}),
  })

  const createJob = useMutation({
    mutationFn: () => api.post<ResearchJob>('/research/jobs', {
      ...form,
      latitude: form.latitude ? Number(form.latitude) : null,
      longitude: form.longitude ? Number(form.longitude) : null,
      keywords: form.keywords.split(',').map(s => s.trim()).filter(Boolean),
      exclusions: form.exclusions.split(',').map(s => s.trim()).filter(Boolean),
      providers: form.providers.length ? form.providers : null,
    }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['research-jobs'] }); setTab('results') },
  })
  const cancelJob = useMutation({
    mutationFn: (id: string) => api.post(`/research/jobs/${id}/cancel`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['research-jobs'] }),
  })
  const importOne = useMutation({
    mutationFn: (id: string) => api.post(`/research/results/${id}/import`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['research-results'] }); qc.invalidateQueries({ queryKey: ['leads'] }) },
  })
  const bulkImport = useMutation({
    mutationFn: (ids: string[]) => api.post<{ imported: number; skipped: number }>('/research/results/bulk-import', ids),
    onSuccess: () => { setChecked(new Set()); qc.invalidateQueries({ queryKey: ['research-results'] }); qc.invalidateQueries({ queryKey: ['leads'] }) },
  })
  const csvUpload = useMutation({
    mutationFn: async (file: File) => {
      const fd = new FormData(); fd.append('file', file)
      const res = await fetch('/api/v1/research/import-csv', { method: 'POST', headers: { Authorization: `Bearer ${localStorage.getItem('agw_token')}` }, body: fd })
      if (!res.ok) throw new ApiError(res.status, await res.text())
      return res.json()
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['leads'] }),
  })

  const activeJob = jobs.data?.items.find(j => j.status === 'running' || j.status === 'queued')
  const set = (k: keyof typeof form, v: unknown) => setForm(f => ({ ...f, [k]: v }))
  const toggleType = (t: string) => set('business_types', form.business_types.includes(t) ? form.business_types.filter(x => x !== t) : [...form.business_types, t])
  const toggleProvider = (n: string) => set('providers', form.providers.includes(n) ? form.providers.filter(x => x !== n) : [...form.providers, n])

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Lead Research</h1>
          <p className="text-sm text-muted">AI Lead Researcher — discover, verify and qualify café prospects</p>
        </div>
        <div className="flex gap-2">
          <Button variant="secondary" onClick={() => document.getElementById('csv-input')?.click()}>Import CSV</Button>
          <input id="csv-input" type="file" accept=".csv" className="hidden" onChange={e => e.target.files?.[0] && csvUpload.mutate(e.target.files[0])} />
        </div>
      </div>
      {csvUpload.data && <p className="text-sm text-mint">CSV import: {csvUpload.data.created} created, {csvUpload.data.skipped} skipped</p>}
      {csvUpload.error && <p className="text-sm text-red">{(csvUpload.error as Error).message}</p>}

      <div className="grid gap-6 lg:grid-cols-3">
        {/* config */}
        <Card className="p-5 lg:col-span-1">
          <h2 className="mb-4 font-semibold">Research configuration</h2>
          <div className="space-y-3">
            <div className="grid grid-cols-2 gap-2">
              <Field label="Country"><Input value={form.country} onChange={e => set('country', e.target.value)} /></Field>
              <Field label="State"><Input value={form.state} onChange={e => set('state', e.target.value)} /></Field>
              <Field label="District"><Input value={form.district} onChange={e => set('district', e.target.value)} /></Field>
              <Field label="City"><Input value={form.city} onChange={e => set('city', e.target.value)} /></Field>
              <Field label="Town"><Input value={form.town} onChange={e => set('town', e.target.value)} /></Field>
              <Field label="Locality"><Input value={form.locality} onChange={e => set('locality', e.target.value)} /></Field>
              <Field label="Postal code"><Input value={form.postal_code} onChange={e => set('postal_code', e.target.value)} /></Field>
              <Field label="Radius (m)"><Input type="number" value={form.radius_m} onChange={e => set('radius_m', Number(e.target.value))} /></Field>
            </div>
            <Field label="Business types">
              <div className="flex flex-wrap gap-1.5">
                {BIZ_TYPES.map(t => (
                  <button key={t} onClick={() => toggleType(t)}
                    className={`rounded-full px-2.5 py-1 text-xs ${form.business_types.includes(t) ? 'bg-violet text-white' : 'bg-surface2 text-muted'}`}>
                    {titleCase(t)}
                  </button>
                ))}
              </div>
            </Field>
            <Field label="Keywords (comma separated)"><Input value={form.keywords} onChange={e => set('keywords', e.target.value)} placeholder="specialty coffee, third wave" /></Field>
            <Field label="Exclusions"><Input value={form.exclusions} onChange={e => set('exclusions', e.target.value)} placeholder="franchise, hotel" /></Field>
            <Field label="Max results"><Input type="number" value={form.max_results} onChange={e => set('max_results', Number(e.target.value))} /></Field>
            <Field label="Data sources">
              <div className="space-y-1">
                {(providers.data ?? []).map(p => (
                  <label key={p.name} className="flex items-center gap-2 text-sm">
                    <input type="checkbox" checked={form.providers.includes(p.name)} onChange={() => toggleProvider(p.name)} disabled={!p.configured} />
                    <span className={p.configured ? '' : 'text-muted'}>{p.name === 'openstreetmap' ? 'OpenStreetMap (Overpass)' : 'Google Places'}
                      {!p.configured && <span className="ml-1 text-xs text-amber">— API key not configured</span>}
                    </span>
                  </label>
                ))}
              </div>
            </Field>
            <Button className="w-full" disabled={createJob.isPending || !!activeJob || form.business_types.length === 0}
              onClick={() => createJob.mutate()}>
              {createJob.isPending ? 'Starting…' : activeJob ? 'Job already running' : 'Start research'}
            </Button>
            {createJob.error && <p className="text-xs text-red">{(createJob.error as Error).message}</p>}
          </div>
        </Card>

        {/* status + results */}
        <div className="space-y-6 lg:col-span-2">
          {activeJob && (
            <Card className="p-5">
              <div className="mb-2 flex items-center justify-between">
                <h2 className="font-semibold">Active job</h2>
                <div className="flex items-center gap-2">
                  <Badge tone={activeJob.status === 'running' ? 'amber' : 'violet'}>{titleCase(activeJob.status)}</Badge>
                  <Button variant="ghost" onClick={() => cancelJob.mutate(activeJob.id)}>Cancel</Button>
                </div>
              </div>
              <div className="h-2 overflow-hidden rounded bg-surface2"><div className="h-full bg-violet transition-all" style={{ width: `${activeJob.progress}%` }} /></div>
              <p className="mt-2 text-xs text-muted">{activeJob.progress}% — refreshes automatically</p>
            </Card>
          )}

          <Card className="p-5">
            <div className="mb-4 flex items-center justify-between">
              <div className="flex gap-1 rounded-lg bg-surface2 p-1">
                {(['results', 'history'] as const).map(t => (
                  <button key={t} onClick={() => setTab(t)} className={`rounded-md px-3 py-1 text-sm ${tab === t ? 'bg-surface text-white' : 'text-muted'}`}>{titleCase(t)}</button>
                ))}
              </div>
              {tab === 'results' && checked.size > 0 && (
                <Button variant="secondary" onClick={() => bulkImport.mutate([...checked])}>Import {checked.size} selected</Button>
              )}
            </div>

            {tab === 'results' ? (
              <>
                <div className="mb-3 flex flex-wrap gap-2">
                  <Input className="w-56" placeholder="Search name, city, email…" value={filters.q}
                    onChange={e => setFilters(f => ({ ...f, q: e.target.value, page: 1 }))} />
                  <Select value={filters.status} onChange={e => setFilters(f => ({ ...f, status: e.target.value, page: 1 }))}>
                    <option value="">All statuses</option>
                    {['researched', 'duplicate', 'imported', 'pending', 'failed'].map(s => <option key={s} value={s}>{titleCase(s)}</option>)}
                  </Select>
                  <Select value={filters.min_score} onChange={e => setFilters(f => ({ ...f, min_score: e.target.value, page: 1 }))}>
                    <option value="">Any score</option>
                    <option value="80">80+ high</option><option value="60">60+ good</option><option value="40">40+</option>
                  </Select>
                </div>
                {results.isLoading ? <div className='space-y-2'>{[...Array(6)].map((_,i)=><Skeleton key={i} className='h-10 w-full'/>)}</div> : (results.data?.items.length ?? 0) === 0 ? (
                  <EmptyState title="No research results yet" hint="Configure a search above and start a research job." />
                ) : (
                  <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                      <thead><tr className="border-b border-line text-left text-xs text-muted">
                        <th className="p-2"></th><th className="p-2">Business</th><th className="p-2">Location</th>
                        <th className="p-2">Contact</th><th className="p-2">Score</th><th className="p-2">Status</th><th className="p-2">Source</th><th className="p-2"></th>
                      </tr></thead>
                      <tbody>
                        {results.data!.items.map(r => (
                          <tr key={r.id} className="border-b border-line/50 hover:bg-surface2/50">
                            <td className="p-2"><input type="checkbox" checked={checked.has(r.id)} onChange={() => {
                              const n = new Set(checked); n.has(r.id) ? n.delete(r.id) : n.add(r.id); setChecked(n)
                            }} /></td>
                            <td className="p-2">
                              <button className="font-medium text-violet hover:underline" onClick={() => setSelected(r)}>{r.business_name}</button>
                              <div className="text-xs text-muted">{titleCase(r.business_category)}</div>
                            </td>
                            <td className="p-2 text-xs">{[r.city, r.state].filter(Boolean).join(', ') || '—'}</td>
                            <td className="p-2 text-xs">{r.email || r.phone || '—'}</td>
                            <td className="p-2"><Badge tone={r.score >= 80 ? 'mint' : r.score >= 60 ? 'violet' : r.score >= 40 ? 'amber' : 'red'}>{r.score}</Badge></td>
                            <td className="p-2"><Badge tone={r.status === 'duplicate' ? 'amber' : r.status === 'imported' ? 'mint' : 'neutral'}>{titleCase(r.status)}</Badge></td>
                            <td className="p-2 text-xs text-muted">{r.source_provider}</td>
                            <td className="p-2">{!r.lead_id && r.status !== 'duplicate' && <Button variant="ghost" onClick={() => importOne.mutate(r.id)}>Import</Button>}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                    <div className="mt-3 flex items-center justify-between text-xs text-muted">
                      <span>{results.data!.total} results</span>
                      <div className="flex gap-2">
                        <Button variant="ghost" disabled={filters.page <= 1} onClick={() => setFilters(f => ({ ...f, page: f.page - 1 }))}>Prev</Button>
                        <Button variant="ghost" disabled={filters.page * 25 >= results.data!.total} onClick={() => setFilters(f => ({ ...f, page: f.page + 1 }))}>Next</Button>
                      </div>
                    </div>
                  </div>
                )}
              </>
            ) : (
              <div className="space-y-2">
                {(jobs.data?.items ?? []).length === 0 && <EmptyState title="No research jobs yet" hint="Start your first research job." />}
                {(jobs.data?.items ?? []).map(j => (
                  <div key={j.id} className="flex items-center justify-between rounded-lg border border-line p-3">
                    <div>
                      <p className="text-sm font-medium">{[j.config.city, j.config.state, j.config.country].filter(Boolean).join(', ') || 'Area search'}</p>
                      <p className="text-xs text-muted">{new Date(j.created_at).toLocaleString()} · {j.stats?.researched ?? 0} researched · {j.stats?.qualified ?? 0} qualified</p>
                    </div>
                    <div className="flex items-center gap-2">
                      <Badge tone={j.status === 'completed' ? 'mint' : j.status === 'failed' ? 'red' : j.status === 'running' ? 'amber' : 'neutral'}>{titleCase(j.status)}</Badge>
                      {(j.status === 'queued' || j.status === 'running') && <Button variant="ghost" onClick={() => cancelJob.mutate(j.id)}>Cancel</Button>}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </Card>
        </div>
      </div>

      {/* detail drawer */}
      {selected && (
        <div className="fixed inset-0 z-50 flex justify-end bg-black/60" onClick={() => setSelected(null)}>
          <div className="h-full w-full max-w-lg overflow-y-auto border-l border-line bg-surface p-6" onClick={e => e.stopPropagation()}>
            <div className="mb-4 flex items-start justify-between">
              <div>
                <h2 className="text-xl font-bold">{selected.business_name}</h2>
                <p className="text-sm text-muted">{titleCase(selected.business_category)} · {selected.source_provider}</p>
              </div>
              <Button variant="ghost" onClick={() => setSelected(null)}>✕</Button>
            </div>
            <div className="mb-4 flex items-center gap-2">
              <Badge tone={selected.score >= 80 ? 'mint' : selected.score >= 60 ? 'violet' : 'amber'}>Score {selected.score} · {titleCase(selected.priority)}</Badge>
              {selected.duplicate_reason && <Badge tone="amber">Possible duplicate</Badge>}
            </div>
            <Section title="Business">
              <Row k="Address" v={selected.address} /><Row k="City/State" v={[selected.city, selected.state, selected.country].filter(Boolean).join(', ')} />
              <Row k="Website" v={selected.website} link /><Row k="Phone" v={selected.phone} /><Row k="Email" v={selected.email} />
              <Row k="Instagram" v={selected.instagram} link /><Row k="Facebook" v={selected.facebook} link />
            </Section>
            {selected.duplicate_reason && <p className="mb-4 rounded-lg bg-amber/10 p-3 text-xs text-amber">⚠ {selected.duplicate_reason}</p>}
            <Section title="Score explanation">
              <pre className="whitespace-pre-wrap text-xs text-muted">{selected.score_explanation || 'No explanation recorded'}</pre>
              {selected.score_breakdown && Object.keys(selected.score_breakdown).length > 0 && (
                <div className="mt-2 space-y-1">
                  {Object.entries(selected.score_breakdown).map(([k, v]) => (
                    <div key={k} className="flex justify-between text-xs"><span className="text-muted">{titleCase(k)}</span><span>{v.points}/{v.max} — {v.reason}</span></div>
                  ))}
                </div>
              )}
              {selected.missing_info?.length > 0 && <p className="mt-2 text-xs text-amber">Missing: {selected.missing_info.join(', ')}</p>}
            </Section>
            {selected.evidence?.length > 0 && (
              <Section title="Evidence">
                {selected.evidence.map((e, i) => (
                  <p key={i} className="text-xs text-muted">• {titleCase(e.field)}: {e.value} <span className="text-mint">({e.source})</span></p>
                ))}
              </Section>
            )}
            {selected.research_notes && <p className="mt-3 text-xs text-muted">Notes: {selected.research_notes}</p>}
            {!selected.lead_id && selected.status !== 'duplicate' && (
              <Button className="mt-4 w-full" onClick={() => importOne.mutate(selected.id)}>Import to CRM</Button>
            )}
            {selected.lead_id && <p className="mt-4 text-sm text-mint">✓ Imported to CRM</p>}
          </div>
        </div>
      )}
    </div>
  )
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <label className="block"><span className="mb-1 block text-xs text-muted">{label}</span>{children}</label>
}
function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return <div className="mb-4"><h3 className="mb-2 text-sm font-semibold">{title}</h3>{children}</div>
}
function Row({ k, v, link }: { k: string; v?: string | null; link?: boolean }) {
  if (!v) return null
  return <p className="text-sm"><span className="text-muted">{k}: </span>{link ? <a className="text-violet hover:underline" href={v.startsWith('http') ? v : `https://${v}`} target="_blank" rel="noreferrer">{v}</a> : v}</p>
}
