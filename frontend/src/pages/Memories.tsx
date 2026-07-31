import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Button, Card, EmptyState, ErrorText, PageHeader, Table, TableSkeleton } from '../components/ui'
import { api } from '../lib/api'

interface Memory {
  id: string
  content: string
  created_at: string
}

export default function Memories() {
  const qc = useQueryClient()
  const { data: memories = [], isLoading, error: loadError } = useQuery({
    queryKey: ['memories'],
    queryFn: () => api<Memory[]>('/memories'),
  })
  const remove = useMutation({
    mutationFn: (id: string) => api(`/memories/${id}`, { method: 'DELETE' }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['memories'] }),
  })

  return (
    <div className="space-y-6">
      <PageHeader title="Memórias" description="O que a plataforma aprendeu sobre você em conversas anteriores." />
      <Card>
        {isLoading ? (
          <TableSkeleton columns={3} />
        ) : loadError ? (
          <ErrorText>Não foi possível carregar as memórias. Recarregue a página ou tente novamente.</ErrorText>
        ) : memories.length === 0 ? (
          <EmptyState
            title="Nenhuma memória ainda"
            description="A plataforma grava fatos relevantes ao final das conversas; eles aparecem aqui."
          />
        ) : (
          <Table headers={['Memória', 'Aprendida em', '']}>
            {memories.map((m) => (
              <tr key={m.id} data-testid="memory-row" className="transition hover:bg-[var(--brand-soft)]">
                <td className="px-3 py-2 text-[var(--text)]">{m.content}</td>
                <td className="px-3 py-2 text-[var(--text-muted)]">
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
    </div>
  )
}
