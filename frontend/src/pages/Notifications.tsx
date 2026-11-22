import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Bell, CheckCheck } from 'lucide-react'
import { api, ApiError, type Notification } from '../lib/api'
import { PageHeader } from '../components/metrics'
import { Badge, Button, Card, EmptyState, Skeleton, useToast } from '../components/ui'
import { formatDateTime, titleCase } from '../lib/utils'

const TYPE_TONE: Record<string, 'violet' | 'mint' | 'amber' | 'blue' | 'red' | 'neutral'> = {
  new_qualified_lead: 'violet', new_reply: 'blue', interested_prospect: 'mint',
  demo_request: 'violet', pending_approval: 'amber', upcoming_meeting: 'blue',
  won_deal: 'mint', agent_error: 'red', system: 'neutral',
}

export default function NotificationsPage() {
  const qc = useQueryClient()
  const toast = useToast()

  const { data, isLoading } = useQuery({
    queryKey: ['notifications'],
    queryFn: () => api.get<{ items: Notification[]; total: number }>('/notifications?page_size=100'),
  })

  const markRead = useMutation({
    mutationFn: (id: string) => api.patch<Notification>(`/notifications/${id}/read`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['notifications'] }),
    onError: (e) => toast(e instanceof ApiError ? e.message : 'Failed', 'error'),
  })
  const markAll = useMutation({
    mutationFn: () => api.post<{ updated: number }>('/notifications/read-all'),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['notifications'] }); toast('All marked read', 'success') },
  })

  return (
    <div>
      <PageHeader
        title="Notifications"
        subtitle="Events from your AI workforce and pipeline"
        action={
          <Button variant="secondary" size="sm" onClick={() => markAll.mutate()} disabled={markAll.isPending}>
            <CheckCheck className="h-4 w-4" /> Mark all read
          </Button>
        }
      />
      {isLoading ? (
        <div className="space-y-2">{Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-16" />)}</div>
      ) : !data?.items.length ? (
        <EmptyState icon={<Bell className="h-5 w-5" />} title="No notifications" hint="Events like qualified leads, replies and approvals will appear here." />
      ) : (
        <div className="stagger space-y-2">
          {data.items.map((n) => (
            <Card key={n.id} className={`flex items-start gap-4 p-4 transition-colors ${!n.is_read ? 'border-viol-500/30 bg-viol-500/5' : ''}`}>
              <div className={`mt-0.5 h-2 w-2 shrink-0 rounded-full ${n.is_read ? 'bg-ink-600' : 'bg-viol-400'}`} />
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <p className={`text-sm ${n.is_read ? 'text-ink-300' : 'font-semibold text-ink-100'}`}>{n.title}</p>
                  <Badge tone={TYPE_TONE[n.type] ?? 'neutral'}>{titleCase(n.type)}</Badge>
                </div>
                {n.body && <p className="mt-0.5 text-xs text-ink-400">{n.body}</p>}
                <p className="mt-1 text-[10px] text-ink-400/70">{formatDateTime(n.created_at)}</p>
              </div>
              {!n.is_read && (
                <Button size="sm" variant="ghost" onClick={() => markRead.mutate(n.id)} disabled={markRead.isPending}>Mark read</Button>
              )}
            </Card>
          ))}
        </div>
      )}
    </div>
  )
}
