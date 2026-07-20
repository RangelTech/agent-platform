import { useQuery } from '@tanstack/react-query'
import { Card, Table } from '../components/ui'
import { api } from '../lib/api'

interface UsageSummary {
  totals: {
    calls: number
    prompt_tokens: number
    completion_tokens: number
    cost_usd: number
    avg_latency_ms: number
  }
  by_model: { provider: string; model: string; calls: number; tokens: number; cost_usd: number }[]
  by_day: { day: string; tokens: number; cost_usd: number }[]
}

interface FeedbackRow {
  rating: number
  comment: string
  message: string
  user: string
  created_at: string
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900/60 px-4 py-3">
      <p className="text-xs uppercase tracking-wide text-slate-500">{label}</p>
      <p className="mt-1 text-xl font-semibold text-slate-100">{value}</p>
    </div>
  )
}

export default function Usage() {
  const { data } = useQuery({
    queryKey: ['usage'],
    queryFn: () => api<UsageSummary>('/usage?days=30'),
  })
  const { data: feedback = [] } = useQuery({
    queryKey: ['feedback'],
    queryFn: () => api<FeedbackRow[]>('/feedback'),
  })

  const totals = data?.totals
  return (
    <div className="space-y-6">
      <div className="grid gap-3 sm:grid-cols-4">
        <Stat label="Chamadas (30d)" value={String(totals?.calls ?? '—')} />
        <Stat
          label="Tokens"
          value={totals ? `${((totals.prompt_tokens + totals.completion_tokens) / 1000).toFixed(1)}k` : '—'}
        />
        <Stat label="Custo estimado" value={totals ? `US$ ${totals.cost_usd.toFixed(4)}` : '—'} />
        <Stat label="Latência média" value={totals ? `${totals.avg_latency_ms} ms` : '—'} />
      </div>

      <Card title="Por modelo (30 dias)">
        <Table headers={['Provider', 'Modelo', 'Chamadas', 'Tokens', 'Custo (US$)']}>
          {(data?.by_model ?? []).map((r, i) => (
            <tr key={i} data-testid="usage-model-row">
              <td className="px-3 py-2 text-slate-400">{r.provider}</td>
              <td className="px-3 py-2 font-mono text-xs text-slate-300">{r.model}</td>
              <td className="px-3 py-2 text-slate-400">{r.calls}</td>
              <td className="px-3 py-2 text-slate-400">{r.tokens.toLocaleString('pt-BR')}</td>
              <td className="px-3 py-2 text-slate-400">{r.cost_usd.toFixed(4)}</td>
            </tr>
          ))}
        </Table>
      </Card>

      <Card title="Avaliações dos usuários">
        {feedback.length === 0 ? (
          <p className="text-sm text-slate-500">Nenhuma avaliação ainda.</p>
        ) : (
          <Table headers={['', 'Comentário', 'Resposta avaliada', 'Usuário', 'Quando']}>
            {feedback.map((f, i) => (
              <tr key={i} data-testid="feedback-row">
                <td className="px-3 py-2">{f.rating > 0 ? '👍' : '👎'}</td>
                <td className="px-3 py-2 text-slate-300">{f.comment || '—'}</td>
                <td className="px-3 py-2 text-slate-500">{f.message}</td>
                <td className="px-3 py-2 text-slate-400">{f.user}</td>
                <td className="px-3 py-2 text-slate-500">
                  {new Date(f.created_at).toLocaleString('pt-BR')}
                </td>
              </tr>
            ))}
          </Table>
        )}
      </Card>
    </div>
  )
}
