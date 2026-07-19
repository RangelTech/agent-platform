import { useQuery } from '@tanstack/react-query'
import { Badge, Card, Table } from '../components/ui'
import { listProfiles } from '../lib/api'

export default function Profiles() {
  const { data: profiles = [], isLoading } = useQuery({
    queryKey: ['profiles'],
    queryFn: listProfiles,
  })

  return (
    <Card title="Perfis de permissão">
      {isLoading ? (
        <p className="text-sm text-slate-400">Carregando…</p>
      ) : (
        <Table headers={['Perfil', 'Permissões', 'Status']}>
          {profiles.map((p) => (
            <tr key={p.id} data-testid="profile-row">
              <td className="px-3 py-2 text-slate-200">{p.name}</td>
              <td className="px-3 py-2">
                <div className="flex flex-wrap gap-1">
                  {Object.entries(p.permissions).map(([resource, actions]) => (
                    <span
                      key={resource}
                      className="rounded bg-slate-800 px-2 py-0.5 text-xs text-slate-300"
                    >
                      {resource}: {actions.join(', ')}
                    </span>
                  ))}
                </div>
              </td>
              <td className="px-3 py-2">
                <Badge ok={p.is_active}>{p.is_active ? 'Ativo' : 'Inativo'}</Badge>
              </td>
            </tr>
          ))}
        </Table>
      )}
    </Card>
  )
}
