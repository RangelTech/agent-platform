import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { Badge, Button, Card, ErrorText, Input, Table } from '../components/ui'
import { api, listProfiles, type Profile } from '../lib/api'

const RESOURCES: { key: string; label: string }[] = [
  { key: 'templates', label: 'Templates' },
  { key: 'ai_services', label: 'Serviços de IA' },
  { key: 'datasources', label: 'Fontes de dados' },
  { key: 'files', label: 'Arquivos' },
  { key: 'secrets', label: 'Segredos' },
  { key: 'users', label: 'Usuários' },
  { key: 'user_profiles', label: 'Perfis' },
  { key: 'integrations', label: 'Integrações' },
  { key: 'chats', label: 'Chat' },
  { key: 'usage', label: 'Consumo' },
]
const ACTIONS: { key: string; label: string }[] = [
  { key: 'view', label: 'Ver' },
  { key: 'create', label: 'Criar' },
  { key: 'edit', label: 'Editar' },
  { key: 'delete', label: 'Excluir' },
]

function PermissionGrid({
  permissions,
  onChange,
}: {
  permissions: Record<string, string[]>
  onChange: (p: Record<string, string[]>) => void
}) {
  function toggle(resource: string, action: string) {
    const current = permissions[resource] ?? []
    const next = current.includes(action)
      ? current.filter((a) => a !== action)
      : [...current, action]
    onChange({ ...permissions, [resource]: next })
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-xs uppercase tracking-wide text-[var(--text-muted)]">
            <th className="px-2 py-1 text-left">Recurso</th>
            {ACTIONS.map((a) => (
              <th key={a.key} className="px-2 py-1">
                {a.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {RESOURCES.map((r) => (
            <tr key={r.key} className="border-t border-[var(--border)]">
              <td className="px-2 py-1.5 text-[var(--text)]">{r.label}</td>
              {ACTIONS.map((a) => (
                <td key={a.key} className="px-2 py-1.5 text-center">
                  <input
                    type="checkbox"
                    data-testid={`perm-${r.key}-${a.key}`}
                    checked={(permissions[r.key] ?? []).includes(a.key)}
                    onChange={() => toggle(r.key, a.key)}
                    className="h-4 w-4 accent-[var(--brand)]"
                  />
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export default function Profiles() {
  const qc = useQueryClient()
  const { data: profiles = [], isLoading } = useQuery({
    queryKey: ['profiles'],
    queryFn: listProfiles,
  })

  const [editing, setEditing] = useState<Profile | null>(null)
  const [creating, setCreating] = useState(false)
  const [name, setName] = useState('')
  const [permissions, setPermissions] = useState<Record<string, string[]>>({})
  const [error, setError] = useState('')

  function openCreate() {
    setCreating(true)
    setEditing(null)
    setName('')
    setPermissions({})
  }
  function openEdit(profile: Profile) {
    setEditing(profile)
    setCreating(false)
    setName(profile.name)
    setPermissions(profile.permissions)
  }
  function close() {
    setEditing(null)
    setCreating(false)
    setError('')
  }

  const save = useMutation({
    mutationFn: () =>
      creating
        ? api('/user-profiles', {
            method: 'POST',
            body: JSON.stringify({ name, permissions }),
          })
        : api(`/user-profiles/${editing!.id}`, {
            method: 'PUT',
            body: JSON.stringify({ name, permissions }),
          }),
    onSuccess: () => {
      close()
      qc.invalidateQueries({ queryKey: ['profiles'] })
    },
    onError: (e: Error) => setError(e.message),
  })

  function onSubmit(e: FormEvent) {
    e.preventDefault()
    save.mutate()
  }

  if (creating || editing) {
    return (
      <Card title={creating ? 'Novo perfil' : `Editar perfil: ${editing?.name}`}>
        <form onSubmit={onSubmit} className="space-y-4">
          <div className="max-w-sm">
            <Input
              label="Nome do perfil"
              required
              name="profile-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </div>
          <PermissionGrid permissions={permissions} onChange={setPermissions} />
          <div className="flex items-center gap-3">
            <Button type="submit" disabled={save.isPending}>
              Salvar perfil
            </Button>
            <Button type="button" variant="ghost" onClick={close}>
              Cancelar
            </Button>
            <ErrorText>{error}</ErrorText>
          </div>
        </form>
      </Card>
    )
  }

  return (
    <Card
      title="Perfis de permissão"
      actions={
        <Button variant="ghost" onClick={openCreate}>
          + Novo perfil
        </Button>
      }
    >
      {isLoading ? (
        <p className="text-sm text-[var(--text-muted)]">Carregando…</p>
      ) : (
        <Table headers={['Perfil', 'Permissões', 'Status', '']}>
          {profiles.map((p) => (
            <tr key={p.id} data-testid="profile-row">
              <td className="px-3 py-2 text-[var(--text)]">{p.name}</td>
              <td className="px-3 py-2">
                <div className="flex flex-wrap gap-1">
                  {Object.entries(p.permissions)
                    .filter(([, actions]) => actions.length > 0)
                    .map(([resource, actions]) => (
                      <span
                        key={resource}
                        className="rounded bg-[var(--border)] px-2 py-0.5 text-xs text-[var(--text-muted)]"
                      >
                        {resource}: {actions.join(', ')}
                      </span>
                    ))}
                </div>
              </td>
              <td className="px-3 py-2">
                <Badge ok={p.is_active}>{p.is_active ? 'Ativo' : 'Inativo'}</Badge>
              </td>
              <td className="px-3 py-2 text-right">
                {p.tenant_id && (
                  <Button variant="ghost" onClick={() => openEdit(p)}>
                    Editar
                  </Button>
                )}
              </td>
            </tr>
          ))}
        </Table>
      )}
    </Card>
  )
}
