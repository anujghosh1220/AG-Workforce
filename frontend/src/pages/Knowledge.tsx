import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { BookOpen, Plus } from 'lucide-react'
import { useState } from 'react'
import { api, ApiError, type Knowledge, type Product } from '../lib/api'
import { PageHeader } from '../components/metrics'
import {
  Button, Card, CardHeader, Drawer, EmptyState, Field, Input,
  Select, Skeleton, Textarea, useToast,
} from '../components/ui'
import { formatDate, titleCase } from '../lib/utils'

const CATEGORIES = ['overview','features','pricing','plans','trial_policy','faq','objection','demo_links','screenshots','other']

export default function Knowledge() {
  const qc = useQueryClient()
  const toast = useToast()
  const [category, setCategory] = useState('')
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [form, setForm] = useState({ category: 'features', title: '', content: '', product_id: '' })

  const params = new URLSearchParams({ page_size: '200' })
  if (category) params.set('category', category)

  const { data, isLoading } = useQuery({
    queryKey: ['knowledge', category],
    queryFn: () => api.get<{ items: Knowledge[]; total: number }>(`/knowledge?${params}`),
  })
  const { data: products } = useQuery({
    queryKey: ['products'],
    queryFn: () => api.get<{ items: Product[] }>('/products?page_size=50'),
  })

  const create = useMutation({
    mutationFn: () => api.post<Knowledge>('/knowledge', {
      category: form.category, title: form.title, content: form.content,
      product_id: form.product_id || null,
    }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['knowledge'] })
      setDrawerOpen(false)
      setForm({ category: 'features', title: '', content: '', product_id: '' })
      toast('Knowledge entry added', 'success')
    },
    onError: (e) => toast(e instanceof ApiError ? e.message : 'Create failed', 'error'),
  })

  const byCategory = new Map<string, Knowledge[]>()
  for (const k of data?.items ?? []) {
    const arr = byCategory.get(k.category) ?? []
    arr.push(k)
    byCategory.set(k.category, arr)
  }

  return (
    <div>
      <PageHeader
        title="Product Knowledge"
        subtitle="Configure what your AI employees know about your product — used for pitches, FAQs and objection handling in later phases"
        action={<Button variant="primary" onClick={() => setDrawerOpen(true)}><Plus className="h-4 w-4" /> Add entry</Button>}
      />

      <div className="mb-4 flex flex-wrap gap-2">
        <button onClick={() => setCategory('')} className={`rounded-full px-3 py-1.5 text-xs font-medium cursor-pointer ${!category ? 'bg-viol-500/20 text-viol-300 border border-viol-500/40' : 'bg-ink-800 text-ink-400 border border-ink-700'}`}>All</button>
        {CATEGORIES.map((c) => (
          <button key={c} onClick={() => setCategory(c)}
            className={`rounded-full px-3 py-1.5 text-xs font-medium cursor-pointer ${category === c ? 'bg-viol-500/20 text-viol-300 border border-viol-500/40' : 'bg-ink-800 text-ink-400 border border-ink-700 hover:text-ink-200'}`}>
            {titleCase(c)}
          </button>
        ))}
      </div>

      {isLoading ? (
        <div className="grid gap-4 md:grid-cols-2">{Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-36" />)}</div>
      ) : !data?.items.length ? (
        <EmptyState icon={<BookOpen className="h-5 w-5" />} title="No knowledge entries" hint="Add product features, pricing, FAQs and objection answers to arm your AI sales team." action={<Button size="sm" variant="primary" onClick={() => setDrawerOpen(true)}>Add entry</Button>} />
      ) : (
        <div className="stagger grid gap-4 md:grid-cols-2">
          {[...byCategory.entries()].map(([cat, items]) => (
            <Card key={cat}>
              <CardHeader title={titleCase(cat)} subtitle={`${items.length} entr${items.length === 1 ? 'y' : 'ies'}`} />
              <div className="divide-y divide-ink-800">
                {items.map((k) => (
                  <div key={k.id} className="px-5 py-3">
                    <p className="text-sm font-medium">{k.title}</p>
                    {k.content && <p className="mt-1 line-clamp-3 whitespace-pre-wrap text-xs text-ink-400">{k.content}</p>}
                    <p className="mt-1.5 text-[10px] text-ink-400/70">Updated {formatDate(k.updated_at)}</p>
                  </div>
                ))}
              </div>
            </Card>
          ))}
        </div>
      )}

      <Drawer open={drawerOpen} onClose={() => setDrawerOpen(false)} title="Add knowledge entry">
        <form onSubmit={(e) => { e.preventDefault(); create.mutate() }} className="space-y-4">
          <Field label="Category">
            <Select value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value })}>
              {CATEGORIES.map((c) => <option key={c} value={c}>{titleCase(c)}</option>)}
            </Select>
          </Field>
          <Field label="Product">
            <Select value={form.product_id} onChange={(e) => setForm({ ...form, product_id: e.target.value })}>
              <option value="">Workspace-level (no product)</option>
              {(products?.items ?? []).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </Select>
          </Field>
          <Field label="Title *"><Input required value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} /></Field>
          <Field label="Content"><Textarea className="min-h-40" value={form.content} onChange={(e) => setForm({ ...form, content: e.target.value })} placeholder="Features, pricing details, FAQ answers, objection responses…" /></Field>
          <div className="flex justify-end gap-2 pt-2">
            <Button type="button" variant="ghost" onClick={() => setDrawerOpen(false)}>Cancel</Button>
            <Button type="submit" variant="primary" disabled={create.isPending}>{create.isPending ? 'Saving…' : 'Add entry'}</Button>
          </div>
        </form>
      </Drawer>
    </div>
  )
}
