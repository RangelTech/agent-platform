import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { Badge, Button, Card, ErrorText, Input, Table } from '../components/ui'
import { createTenant, listTenants, updateTenant } from '../lib/api'

export default function Tenants() {
  const qc = useQueryClient()
  const { data: tenants = [], isLoading } = useQuery({
    queryKey: ['tenants'],
    queryFn: listTenants,
  })
  const [name, setName] = useState('')
  const [key, setKey] = useState('')
  const [adminName, setAdminName] = useState('')
  const [adminEmail, setAdminEmail] = useState('')
  const [adminPassword, setAdminPassword] = useState('')
  const [error, setError] = useState('')

  const create = useMutation({
    mutationFn: () =>
      createTenant({
        name,
        tenant_key: key,
        admin_name: adminName || null,
        admin_email: adminEmail || null,
        admin_password: adminPassword || null,
      } as never),
    onSuccess: () => {
      setName('')
      setKey('')
      setAdminName('')
      setAdminEmail('')
      setAdminPassword('')
      setError('')
      qc.invalidateQueries({ queryKey: ['tenants'] })
    },
    onError: (e: Error) => setError(e.message),
  })

  const toggle = useMutation({
    mutationFn: ({ id, is_active }: { id: string; is_active: boolean }) =>
      updateTenant(id, { is_active }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['tenants'] }),
  })

  function onSubmit(e: FormEvent) {
    e.preventDefault()
    create.mutate()
  }

  return (
    <div className="space-y-6">
      <Card title="Nova empresa">
        <form onSubmit={onSubmit} className="grid gap-3 sm:grid-cols-3 sm:items-end">
          <Input
            label="Nome"
            required
            value={name}
            name="tenant-name"
            onChange={(e) => setName(e.target.value)}
          />
          <Input
            label="Chave (identificador)"
            required
            pattern="[a-z0-9][a-z0-9-]*"
            title="Minúsculas, números e hífen"
            value={key}
            name="tenant-key"
            onChange={(e) => setKey(e.target.value)}
          />
          <Input
            label="Nome do admin inicial"
            name="admin-name"
            value={adminName}
            onChange={(e) => setAdminName(e.target.value)}
          />
          <Input
            label="E-mail do admin"
            type="email"
            name="admin-email"
            value={adminEmail}
            onChange={(e) => setAdminEmail(e.target.value)}
          />
          <Input
            label="Senha do admin (mín. 8)"
            type="password"
            name="admin-password"
            value={adminPassword}
            onChange={(e) => setAdminPassword(e.target.value)}
          />
          <Button type="submit" disabled={create.isPending}>
            Criar empresa
          </Button>
          <div className="sm:col-span-3">
            <ErrorText>{error}</ErrorText>
          </div>
        </form>
      </Card>

      <Card title="Empresas">
        {isLoading ? (
          <p className="text-sm text-slate-400">Carregando…</p>
        ) : (
          <Table headers={['Nome', 'Chave', 'Status', '']}>
            {tenants.map((t) => (
              <tr key={t.id} data-testid="tenant-row">
                <td className="px-3 py-2 text-slate-200">{t.name}</td>
                <td className="px-3 py-2 font-mono text-xs text-slate-400">{t.tenant_key}</td>
                <td className="px-3 py-2">
                  <Badge ok={t.is_active}>{t.is_active ? 'Ativa' : 'Inativa'}</Badge>
                </td>
                <td className="px-3 py-2 text-right">
                  <Button
                    variant="ghost"
                    onClick={() => toggle.mutate({ id: t.id, is_active: !t.is_active })}
                  >
                    {t.is_active ? 'Desativar' : 'Ativar'}
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
