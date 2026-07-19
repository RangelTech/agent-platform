import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Button, Card, Table } from '../components/ui'
import { api } from '../lib/api'

interface Memory {
  id: string
  content: string
  created_at: string
}

export default function Memories() {
  const qc = useQueryClient()
  const { data: memories = [], isLoading } = useQuery({
    queryKey: ['memories'],
    queryFn: () => api<Memory[]>('/memories'),
  })
  const remove = useMutation({
    mutationFn: (id: string) => api(`/memories/${id}`, { method: 'DELETE' }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['memories'] }),
  })

  return (
    <Card title="O que a plataforma lembra sobre você">
      <p className="mb-4 text-sm text-slate-400">
        Fatos aprendidos nas suas conversas, usados para personalizar as respostas.
        Você pode apagar qualquer um.
      </p>
      {isLoading ? (
        <p className="text-sm text-slate-400">Carregando…</p>
      ) : memories.length === 0 ? (
        <p className="text-sm text-slate-500">Nenhuma memória ainda.</p>
      ) : (
        <Table headers={['Memória', 'Aprendida em', '']}>
          {memories.map((m) => (
            <tr key={m.id} data-testid="memory-row">
              <td className="px-3 py-2 text-slate-200">{m.content}</td>
              <td className="px-3 py-2 text-slate-400">
                {new Date(m.created_at).toLocaleDateString('pt-BR')}
              </td>
              <td className="px-3 py-2 text-right">
                <Button variant="ghost" onClick={() => remove.mutate(m.id)}>
                  Apagar
                </Button>
              </td>
            </tr>
          ))}
        </Table>
      )}
    </Card>
  )
}
