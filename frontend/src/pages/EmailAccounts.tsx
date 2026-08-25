import { useState, type ChangeEvent, type FormEvent } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Button, Card, EmptyState, ErrorText, Input, PageHeader, Table, TableSkeleton } from '../components/ui'
import { api, ApiError } from '../lib/api'

interface EmailAccount {
  id: string
  label: string
  email_address: string
  smtp_host: string
  smtp_port: number
  imap_host: string
  imap_port: number
  username: string
  use_tls: boolean
  updated_at: string
}

const EMPTY_FORM = {
  label: '',
  email_address: '',
  smtp_host: '',
  smtp_port: '587',
  imap_host: '',
  imap_port: '993',
  username: '',
  password: '',
  use_tls: 'true',
}

export default function EmailAccounts() {
  const qc = useQueryClient()
  const {
    data: accounts = [],
    isLoading,
    error: loadError,
  } = useQuery({
    queryKey: ['email-accounts'],
    queryFn: () => api<EmailAccount[]>('/email-accounts'),
  })

  const [form, setForm] = useState(EMPTY_FORM)
  const [error, setError] = useState('')

  const set = (key: keyof typeof form) => (e: ChangeEvent<HTMLInputElement>) =>
    setForm((f) => ({ ...f, [key]: e.target.value }))

  const create = useMutation({
    mutationFn: () =>
      api<EmailAccount>('/email-accounts', {
        method: 'POST',
        body: JSON.stringify({
          label: form.label,
          email_address: form.email_address,
          smtp_host: form.smtp_host,
          smtp_port: Number(form.smtp_port),
          imap_host: form.imap_host,
          imap_port: Number(form.imap_port),
          username: form.username,
          password: form.password,
          use_tls: form.use_tls === 'true',
        }),
      }),
    onSuccess: () => {
      setForm(EMPTY_FORM)
      qc.invalidateQueries({ queryKey: ['email-accounts'] })
    },
    onError: (err) => setError(err instanceof ApiError ? err.message : 'Falha ao salvar'),
  })

  const remove = useMutation({
    mutationFn: (id: string) => api(`/email-accounts/${id}`, { method: 'DELETE' }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['email-accounts'] }),
  })

  function onSubmit(e: FormEvent) {
    e.preventDefault()
    setError('')
    if (!form.label || !form.email_address || !form.smtp_host || !form.imap_host || !form.username || !form.password) {
      setError('Preencha todos os campos obrigatórios.')
      return
    }
    create.mutate()
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Contas de email"
        description="Contas SMTP/IMAP usadas pelos agentes para ler e enviar email (ver caixa de entrada, mandar email). A senha é gravada criptografada e nunca é exibida de volta."
      />

      <Card title="Nova conta">
        <form onSubmit={onSubmit} className="grid gap-4 lg:grid-cols-2">
          <Input label="Nome (label)" hint="Identifica a conta quando há mais de uma." value={form.label} onChange={set('label')} />
          <Input label="Endereço de email" type="email" value={form.email_address} onChange={set('email_address')} />
          <Input label="Servidor SMTP" hint="Ex.: smtp.gmail.com" value={form.smtp_host} onChange={set('smtp_host')} />
          <Input label="Porta SMTP" value={form.smtp_port} onChange={set('smtp_port')} />
          <Input label="Servidor IMAP" hint="Ex.: imap.gmail.com" value={form.imap_host} onChange={set('imap_host')} />
          <Input label="Porta IMAP" value={form.imap_port} onChange={set('imap_port')} />
          <Input label="Usuário" hint="Geralmente o próprio email." value={form.username} onChange={set('username')} />
          <Input
            label="Senha"
            hint="Para Gmail/Outlook, use uma senha de app, não a senha da conta."
            type="password"
            autoComplete="off"
            value={form.password}
            onChange={set('password')}
          />
          <div className="lg:col-span-2">
            <ErrorText>{error}</ErrorText>
          </div>
          <div className="lg:col-span-2">
            <Button type="submit" disabled={create.isPending}>
              {create.isPending ? 'Salvando…' : 'Adicionar conta'}
            </Button>
          </div>
        </form>
      </Card>

      <Card title="Contas cadastradas">
        {isLoading ? (
          <TableSkeleton columns={4} rows={2} />
        ) : loadError ? (
          <ErrorText>Não foi possível carregar as contas. Recarregue a página.</ErrorText>
        ) : accounts.length === 0 ? (
          <EmptyState
            title="Nenhuma conta cadastrada"
            description="Adicione uma conta acima para os agentes conseguirem ler e enviar email."
          />
        ) : (
          <Table headers={['Label', 'Email', 'SMTP', 'IMAP', '']}>
            {accounts.map((a) => (
              <tr key={a.id} className="transition hover:bg-[var(--brand-soft)]/40">
                <td className="px-4 py-4 text-[var(--text)]">{a.label}</td>
                <td className="px-4 py-4 text-[var(--text-muted)]">{a.email_address}</td>
                <td className="px-4 py-4 font-mono text-xs text-[var(--text-muted)]">
                  {a.smtp_host}:{a.smtp_port}
                </td>
                <td className="px-4 py-4 font-mono text-xs text-[var(--text-muted)]">
                  {a.imap_host}:{a.imap_port}
                </td>
                <td className="px-4 py-4 text-right">
                  <Button type="button" variant="danger" onClick={() => remove.mutate(a.id)}>
                    Remover
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
