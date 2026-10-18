import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import {
  Badge,
  Button,
  Card,
  ConfirmDialog,
  EmptyState,
  ErrorText,
  Input,
  Meter,
  PageHeader,
  Select,
  Skeleton,
  Table,
  useToast,
} from '../components/ui'
import { useAuth } from '../lib/auth'
import {
  addDomain,
  environmentAction,
  getEnvironment,
  removeDomain,
  STATE_LABEL,
  unpublishApp,
  type EnvApp,
  type EnvDomain,
  type EnvState,
} from '../lib/environment'

const APP_STATUS: Record<EnvApp['status'], string> = { online: 'No ar', offline: 'Fora do ar', waking: 'Acordando' }
const DOMAIN_STATUS: Record<EnvDomain['status'], string> = {
  pending_verification: 'Aguardando verificação',
  active: 'Ativo',
  removed: 'Removido',
}

const fmtDate = (v: string | null) => (v ? new Date(v).toLocaleString('pt-BR') : 'Nunca')
const gb = (n: number) => `${n.toLocaleString('pt-BR', { maximumFractionDigits: 1 })} GB`

function hostOf(url: string) {
  try {
    return new URL(url).host
  } catch {
    return url
  }
}

export default function Environment() {
  const qc = useQueryClient()
  const toast = useToast()
  const { can } = useAuth()
  const canManage = can('environment', 'manage')
  const { data, isLoading, error } = useQuery({
    queryKey: ['environment'],
    queryFn: getEnvironment,
    // Enquanto sobe ou acorda, acompanha sem o usuário recarregar.
    refetchInterval: (q) => (q.state.data?.state === 'starting' ? 3000 : false),
  })
  const [actionError, setActionError] = useState('')
  const [confirmStop, setConfirmStop] = useState(false)
  const [unpublish, setUnpublish] = useState<EnvApp | null>(null)
  const [dropDomain, setDropDomain] = useState<EnvDomain | null>(null)
  const [host, setHost] = useState('')
  const [appId, setAppId] = useState('')
  const [hostError, setHostError] = useState('')

  const refresh = () => qc.invalidateQueries({ queryKey: ['environment'] })
  const fail = (e: Error) => setActionError(e.message)

  const act = useMutation({
    mutationFn: (a: 'start' | 'stop' | 'backup') => environmentAction(a),
    onSuccess: (_r, a) => {
      setActionError('')
      toast({ start: 'Ambiente iniciando.', stop: 'Ambiente parado.', backup: 'Backup iniciado.' }[a])
      refresh()
    },
    onError: fail,
  })
  const addDom = useMutation({
    mutationFn: () => addDomain(host.trim().toLowerCase(), appId),
    onSuccess: () => {
      setActionError('')
      setHost('')
      toast('Domínio adicionado. Siga as instruções de DNS abaixo.')
      refresh()
    },
    onError: (e: Error) => setHostError(e.message),
  })
  const delDom = useMutation({
    mutationFn: (id: string) => removeDomain(id),
    onSuccess: () => {
      setDropDomain(null)
      toast('Domínio removido.')
      refresh()
    },
    onError: (e: Error) => {
      setDropDomain(null)
      fail(e)
    },
  })
  const delApp = useMutation({
    mutationFn: (id: string) => unpublishApp(id),
    onSuccess: () => {
      setUnpublish(null)
      toast('Aplicação despublicada.')
      refresh()
    },
    onError: (e: Error) => {
      setUnpublish(null)
      fail(e)
    },
  })

  function onAddDomain(e: FormEvent) {
    e.preventDefault()
    setHostError('')
    const h = host.trim().toLowerCase()
    if (!/^([a-z0-9]([a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}$/.test(h)) {
      setHostError('Informe um domínio válido, como app.suaempresa.com.br.')
      return
    }
    if (!appId && (data?.apps.length ?? 0) > 0) {
      setHostError('Escolha a aplicação que vai responder neste domínio.')
      return
    }
    addDom.mutate()
  }

  if (isLoading) {
    return (
      <div className="space-y-6">
        <PageHeader title="Ambiente do agente" />
        <Skeleton className="h-40" />
      </div>
    )
  }
  if (error || !data) {
    return (
      <div className="space-y-6">
        <PageHeader title="Ambiente do agente" />
        <ErrorText>{error instanceof Error ? error.message : 'Não foi possível carregar o ambiente.'}</ErrorText>
      </div>
    )
  }
  if (!data.enabled || data.state === 'disabled') {
    return (
      <div className="space-y-6" data-testid="environment-page">
        <PageHeader title="Ambiente do agente" description="O servidor onde o agente instala e publica aplicações para a sua empresa." />
        <EmptyState
          title="Ambiente do agente não contratado"
          description="Sua empresa ainda não tem um ambiente. Fale com o suporte para habilitar."
          icon="◇"
        />
        <p data-testid="env-disabled" className="sr-only">
          Ambiente não contratado
        </p>
      </div>
    )
  }

  const state: EnvState = data.state
  const running = state === 'running' || state === 'degraded'
  const tone = state === 'running'
  const u = data.usage

  return (
    <div className="space-y-6" data-testid="environment-page">
      <PageHeader
        title="Ambiente do agente"
        description="O servidor onde o agente instala e publica aplicações para a sua empresa."
        actions={
          canManage && (
            <>
              <Button
                type="button"
                data-testid="env-start"
                disabled={running || state === 'starting' || state === 'suspended' || act.isPending}
                onClick={() => act.mutate('start')}
              >
                Iniciar
              </Button>
              <Button
                type="button"
                variant="ghost"
                data-testid="env-stop"
                disabled={!running || act.isPending}
                onClick={() => setConfirmStop(true)}
              >
                Parar
              </Button>
              <Button
                type="button"
                variant="ghost"
                data-testid="env-backup"
                disabled={state === 'suspended' || act.isPending}
                onClick={() => act.mutate('backup')}
              >
                Fazer backup
              </Button>
            </>
          )
        }
      />
      <ErrorText>{actionError}</ErrorText>

      <Card title="Situação">
        <div className="flex flex-wrap items-center gap-x-8 gap-y-3 text-sm">
          <span className="flex items-center gap-2">
            <span className="text-[var(--text-muted)]">Estado</span>
            <span data-testid="env-state" data-state={state}>
              <Badge ok={tone}>{STATE_LABEL[state]}</Badge>
            </span>
          </span>
          {data.plan && (
            <span className="flex items-center gap-2">
              <span className="text-[var(--text-muted)]">Plano</span>
              <span data-testid="env-plan" className="font-medium text-[var(--text)]">
                {data.plan}
              </span>
            </span>
          )}
          <span className="flex items-center gap-2">
            <span className="text-[var(--text-muted)]">Último backup</span>
            <span data-testid="env-last-backup" className="font-medium text-[var(--text)]">
              {fmtDate(data.last_backup_at)}
            </span>
          </span>
        </div>
        {state === 'suspended' && (
          <p className="mt-4 rounded-2xl bg-[var(--danger-soft)] px-4 py-3 text-sm text-[var(--danger)]">
            O ambiente está suspenso. Fale com o suporte para reativar.
          </p>
        )}
      </Card>

      <Card title="Consumo">
        <div className="grid gap-5 sm:grid-cols-2">
          <Meter testId="env-usage-cpu" label="Processador" value={u.cpu_pct} max={100} text={`${Math.round(u.cpu_pct)}%`} />
          <Meter
            testId="env-usage-ram"
            label="Memória"
            value={u.ram_mb}
            max={u.ram_limit_mb}
            text={`${Math.round(u.ram_mb)} de ${Math.round(u.ram_limit_mb)} MB`}
          />
          <Meter testId="env-usage-disk" label="Disco" value={u.disk_gb} max={u.disk_limit_gb} text={`${gb(u.disk_gb)} de ${gb(u.disk_limit_gb)}`} />
          <div data-testid="env-usage-egress" className="flex items-baseline justify-between gap-3 text-sm">
            <span className="font-medium text-[var(--text)]">Tráfego de saída (mês)</span>
            <span className="text-[var(--text-muted)]">{gb(u.egress_gb)}</span>
          </div>
        </div>
      </Card>

      <Card title="Aplicações publicadas">
        {data.apps.length === 0 ? (
          <EmptyState title="Nenhuma aplicação publicada" description="Quando o agente publicar algo, o endereço aparece aqui." />
        ) : (
          <Table headers={['Aplicação', 'Endereço', 'Situação', '']}>
            {data.apps.map((a) => (
              <tr key={a.id} data-testid="env-app-row" data-status={a.status}>
                <td className="px-3 py-2 font-medium text-[var(--text)]">{a.name}</td>
                <td className="px-3 py-2">
                  <a
                    href={a.url}
                    target="_blank"
                    rel="noreferrer noopener"
                    data-testid="env-app-url"
                    className="break-all text-[var(--brand)] underline"
                  >
                    {a.url}
                  </a>
                </td>
                <td className="px-3 py-2">
                  <Badge ok={a.status === 'online'}>{APP_STATUS[a.status]}</Badge>
                </td>
                <td className="px-3 py-2 text-right">
                  {canManage && (
                    <Button type="button" variant="ghost" data-testid="env-app-unpublish" onClick={() => setUnpublish(a)}>
                      Despublicar
                    </Button>
                  )}
                </td>
              </tr>
            ))}
          </Table>
        )}
      </Card>

      <Card title="Domínios próprios">
        <div className="space-y-4">
          {data.domains.filter((d) => d.status !== 'removed').length === 0 ? (
            <EmptyState title="Nenhum domínio próprio" description="Aponte um domínio da sua empresa para uma das aplicações publicadas." />
          ) : (
            <ul className="space-y-3">
              {data.domains
                .filter((d) => d.status !== 'removed')
                .map((d) => (
                  <li
                    key={d.id}
                    data-testid="env-domain-row"
                    data-status={d.status}
                    className="rounded-[24px] border border-[var(--border)] bg-[var(--surface-solid)] p-4"
                  >
                    <div className="flex flex-wrap items-center justify-between gap-3">
                      <span className="break-all font-medium text-[var(--text)]">{d.host}</span>
                      <span className="flex items-center gap-2">
                        <Badge ok={d.status === 'active'}>{DOMAIN_STATUS[d.status]}</Badge>
                        {canManage && (
                          <Button type="button" variant="ghost" data-testid="env-domain-remove" onClick={() => setDropDomain(d)}>
                            Remover
                          </Button>
                        )}
                      </span>
                    </div>
                    {d.status === 'pending_verification' && (
                      <div data-testid="env-domain-instructions" className="mt-3 space-y-2 text-sm text-[var(--text-muted)]">
                        <p>No painel de DNS do seu domínio, crie estes registros:</p>
                        <dl className="grid gap-2 rounded-2xl bg-[var(--surface-soft)] p-3 font-mono text-xs sm:grid-cols-[auto_1fr]">
                          <dt className="text-[var(--text-faint)]">TXT nome</dt>
                          <dd data-testid="env-txt-name" className="break-all text-[var(--text)]">
                            {d.txt_name}
                          </dd>
                          <dt className="text-[var(--text-faint)]">TXT valor</dt>
                          <dd data-testid="env-txt-value" className="break-all text-[var(--text)]">
                            {d.txt_value}
                          </dd>
                          <dt className="text-[var(--text-faint)]">CNAME</dt>
                          <dd data-testid="env-cname" className="break-all text-[var(--text)]">
                            {d.host} → {hostOf(data.apps[0]?.url ?? '') || 'endereço da aplicação'}
                          </dd>
                        </dl>
                        <p>A verificação pode levar alguns minutos depois de salvar o DNS.</p>
                      </div>
                    )}
                  </li>
                ))}
            </ul>
          )}

          {canManage && (
            <form onSubmit={onAddDomain} noValidate className="grid gap-3 sm:grid-cols-[1fr_1fr_auto] sm:items-end" data-testid="env-domain-form">
              <Input
                label="Domínio"
                name="env-domain-host"
                data-testid="env-domain-host"
                placeholder="app.suaempresa.com.br"
                value={host}
                error={hostError}
                onChange={(e) => setHost(e.target.value)}
              />
              <Select label="Aplicação" name="env-domain-app" data-testid="env-domain-app" value={appId} onChange={(e) => setAppId(e.target.value)}>
                <option value="">Escolha…</option>
                {data.apps.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.name}
                  </option>
                ))}
              </Select>
              <Button type="submit" data-testid="env-domain-add" disabled={addDom.isPending}>
                Adicionar domínio
              </Button>
            </form>
          )}
        </div>
      </Card>

      <ConfirmDialog
        open={confirmStop}
        title="Parar o ambiente"
        message="As aplicações publicadas ficam fora do ar até você iniciar de novo. Os arquivos são mantidos."
        confirmLabel="Parar"
        danger
        busy={act.isPending}
        onClose={() => setConfirmStop(false)}
        onConfirm={() => {
          setConfirmStop(false)
          act.mutate('stop')
        }}
        testId="env-stop-dialog"
      />
      <ConfirmDialog
        open={unpublish !== null}
        title="Despublicar aplicação"
        message={`Tirar “${unpublish?.name}” do ar? O endereço deixa de responder.`}
        confirmLabel="Despublicar"
        danger
        busy={delApp.isPending}
        onClose={() => setUnpublish(null)}
        onConfirm={() => unpublish && delApp.mutate(unpublish.id)}
        testId="env-unpublish-dialog"
      />
      <ConfirmDialog
        open={dropDomain !== null}
        title="Remover domínio"
        message={`Remover ${dropDomain?.host}? Você pode adicioná-lo de novo depois.`}
        confirmLabel="Remover"
        danger
        busy={delDom.isPending}
        onClose={() => setDropDomain(null)}
        onConfirm={() => dropDomain && delDom.mutate(dropDomain.id)}
        testId="env-domain-dialog"
      />
    </div>
  )
}
