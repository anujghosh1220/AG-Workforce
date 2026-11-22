import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api, ApiError, type Lead, type PipelineStage } from '../lib/api'
import { PageHeader } from '../components/metrics'
import { Badge, Card, EmptyState, Skeleton, useToast } from '../components/ui'
import { formatMoney, titleCase } from '../lib/utils'

const STAGE_ORDER = [
  'discovered','researched','qualified','approval_pending','contacted','replied',
  'interested','demo_scheduled','negotiation','won','lost','do_not_contact',
]
const ACTIVE_STAGES = STAGE_ORDER.filter((s) => !['lost', 'do_not_contact'].includes(s))

export default function Pipeline() {
  const qc = useQueryClient()
  const toast = useToast()
  const [dragId, setDragId] = useState<string | null>(null)
  const [overStage, setOverStage] = useState<string | null>(null)

  const { data: stages, isLoading } = useQuery({
    queryKey: ['pipeline'],
    queryFn: () => api.get<PipelineStage[]>('/pipeline'),
  })
  const { data: leads } = useQuery({
    queryKey: ['leads-all'],
    queryFn: () => api.get<{ items: Lead[]; total: number }>('/leads?page_size=200&sort=lead_score&order=desc'),
  })

  const move = useMutation({
    mutationFn: ({ id, stage }: { id: string; stage: string }) =>
      api.patch<Lead>(`/leads/${id}/stage`, { stage }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['pipeline'] })
      qc.invalidateQueries({ queryKey: ['leads'] })
      qc.invalidateQueries({ queryKey: ['leads-all'] })
      qc.invalidateQueries({ queryKey: ['analytics'] })
    },
    onError: (e) => toast(e instanceof ApiError ? e.message : 'Move failed', 'error'),
  })

  const byStage = new Map<string, Lead[]>()
  for (const l of leads?.items ?? []) {
    const arr = byStage.get(l.stage) ?? []
    arr.push(l)
    byStage.set(l.stage, arr)
  }
  const stageMeta = new Map((stages ?? []).map((s) => [s.stage, s]))

  return (
    <div>
      <PageHeader title="Sales Pipeline" subtitle="Drag leads between stages — every move is logged" />
      {isLoading ? (
        <div className="flex gap-4 overflow-x-auto pb-4">{Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-96 w-64 shrink-0" />)}</div>
      ) : (
        <div className="flex gap-4 overflow-x-auto pb-4">
          {ACTIVE_STAGES.map((stage) => {
            const items = byStage.get(stage) ?? []
            const meta = stageMeta.get(stage)
            const isOver = overStage === stage
            return (
              <div
                key={stage}
                className={`flex w-64 shrink-0 flex-col rounded-xl border transition-colors ${isOver ? 'border-viol-500/60 bg-viol-500/5' : 'border-ink-700/50 bg-ink-900/40'}`}
                onDragOver={(e) => { e.preventDefault(); setOverStage(stage) }}
                onDragLeave={() => setOverStage((s) => (s === stage ? null : s))}
                onDrop={(e) => {
                  e.preventDefault()
                  setOverStage(null)
                  if (dragId) move.mutate({ id: dragId, stage })
                  setDragId(null)
                }}
              >
                <div className="flex items-center justify-between border-b border-ink-700/50 px-3 py-2.5">
                  <span className="text-xs font-semibold uppercase tracking-wider text-ink-300">{titleCase(stage)}</span>
                  <Badge tone="neutral">{meta?.count ?? items.length}</Badge>
                </div>
                <div className="flex-1 space-y-2 overflow-y-auto p-2" style={{ maxHeight: '65vh' }}>
                  {items.map((l) => (
                    <Card
                      key={l.id}
                      draggable
                      onDragStart={() => setDragId(l.id)}
                      className="cursor-grab p-3 transition-all hover:border-viol-500/40 active:cursor-grabbing"
                    >
                      <p className="text-sm font-medium leading-tight">{l.business_name}</p>
                      <p className="mt-0.5 truncate text-[11px] text-ink-400">{l.city || l.contact_person || l.email || '—'}</p>
                      <div className="mt-2 flex items-center justify-between">
                        <Badge tone={l.lead_score >= 70 ? 'mint' : l.lead_score >= 40 ? 'amber' : 'neutral'}>Score {l.lead_score}</Badge>
                        <span className="text-[11px] font-medium text-mint-300">{formatMoney(Number(l.estimated_value), l.currency)}</span>
                      </div>
                    </Card>
                  ))}
                  {items.length === 0 && (
                    <div className="rounded-lg border border-dashed border-ink-700/60 px-3 py-6 text-center text-[11px] text-ink-400">
                      Drop leads here
                    </div>
                  )}
                </div>
                {(meta?.total_value ?? 0) > 0 && (
                  <div className="border-t border-ink-700/50 px-3 py-2 text-[11px] text-ink-400">
                    {formatMoney(meta!.total_value)} in stage
                  </div>
                )}
              </div>
            )
          })}
          {/* closed stages summary */}
          <div className="flex w-56 shrink-0 flex-col gap-3">
            {['won', 'lost', 'do_not_contact'].map((stage) => {
              const items = byStage.get(stage) ?? []
              const meta = stageMeta.get(stage)
              return (
                <Card key={stage} className="p-4">
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-semibold uppercase tracking-wider text-ink-300">{titleCase(stage)}</span>
                    <Badge tone={stage === 'won' ? 'mint' : 'red'}>{meta?.count ?? items.length}</Badge>
                  </div>
                  <p className="mt-1 text-lg font-bold">{formatMoney(meta?.total_value ?? 0)}</p>
                  <p className="text-[10px] text-ink-400">total value</p>
                </Card>
              )
            })}
            {(leads?.total ?? 0) === 0 && (
              <EmptyState title="Pipeline is empty" hint="Add leads in the CRM to populate the pipeline." />
            )}
          </div>
        </div>
      )}
    </div>
  )
}
