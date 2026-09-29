import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Badge, Button, Card, EmptyState, ErrorText, PageHeader, Table, TableSkeleton, Textarea } from '../components/ui'
import { SurfaceSwitcher } from '../components/SurfaceSwitcher'
import { api } from '../lib/api'

// Versão publicada real (GitHub Releases do repo hermes-vscode, workflow
// release.yml a partir de uma tag vX.Y.Z) -- spec seção 7.3. Atualizar
// junto com um novo corte de release; não há endpoint pra descobrir isso
// automaticamente sem dar ao RIA acesso à API do GitHub.
const HERMES_RELEASE = {
  version: '0.1.1',
  vsixUrl: 'https://github.com/LucasRangelSSouza/hermes-vscode/releases/download/v0.1.1/hermes-by-rangel-tech-0.1.1.vsix',
  sha256Url: 'https://github.com/LucasRangelSSouza/hermes-vscode/releases/download/v0.1.1/hermes-by-rangel-tech-0.1.1.vsix.sha256',
  notesUrl: 'https://github.com/LucasRangelSSouza/hermes-vscode/releases/tag/v0.1.1',
}

// Hermes agente (SPEC_HERMES_INTEGRADO_RIA_ATENDIMENTO.md, seção 6.2):
// computadores que pareiam a extensão VS Code "Hermes by Rangel Tech"
// aparecem aqui como a linha de primeiro nível de uma árvore; cada sessão
// Hermes deles é uma linha filha. Um comando enviado daqui chega na
// extensão em até ~4s (poll HTTP -- ver docs/remote-control.md no repo
// hermes-by-rangel-tech; o Relay em tempo real já existe em produção,
// mas esta tela ainda consome o mesmo contrato HTTP por simplicidade).

interface HermesDevice {
  id: string
  name: string
  platform: string | null
  status: string
  last_seen_at: string | null
}

interface HermesSession {
  id: string
  device_id: string
  device_name: string
  device_status: string
  title: string | null
  workspace_path: string | null
  status: string
  last_activity_at: string
}

interface HermesEvent {
  id: string
  sequence: number
  type: string
  payload: Record<string, unknown>
  occurred_at: string
}

interface HermesCommand {
  id: string
  status: string
  payload: { text?: string }
  error: string | null
  created_at: string
}

const SESSION_STATUS_OK = new Set(['idle', 'completed'])
const COMMAND_TERMINAL_OK = new Set(['completed'])
// A sessão pode receber comando; só isso ganha o traço azul de
// disponibilidade (seção 6.2 — "o mesmo traço não deve fingir que uma
// sessão desconectada está disponível").
const SESSION_AVAILABLE = new Set(['idle', 'running', 'waiting_approval'])

function eventText(event: HermesEvent): string {
  switch (event.type) {
    case 'agent_message_chunk':
      return typeof event.payload.text === 'string' ? event.payload.text : ''
    case 'tool_call':
      return `🔧 ${event.payload.title ?? ''} (${event.payload.status ?? ''})`
    case 'error':
      return `⚠️ ${event.payload.message ?? ''}`
    case 'done':
      return '— fim do turno —'
    default:
      return JSON.stringify(event.payload)
  }
}

/** Bolinha verde/vermelha de presença do dispositivo (seção 6.2) —
 * cinza pra revogado, nunca verde quando não está de fato conectado. */
function PresenceDot({ status }: { status: string }) {
  const color =
    status === 'connected' ? 'bg-emerald-500' :
    status === 'revoked' ? 'bg-[var(--text-faint)]' :
    'bg-red-500'
  return <span className={`inline-block h-2 w-2 shrink-0 rounded-full ${color}`} aria-hidden />
}

function sessionStatusMeta(status: string): { label: string; dot: string; pulse?: boolean } {
  switch (status) {
    case 'running': return { label: 'executando', dot: 'bg-blue-500', pulse: true }
    case 'waiting_approval': return { label: 'aguardando aprovação', dot: 'bg-amber-500' }
    case 'idle': return { label: 'ociosa', dot: 'bg-emerald-500' }
    case 'completed': return { label: 'concluída', dot: 'bg-[var(--text-faint)]' }
    case 'failed': return { label: 'erro', dot: 'bg-red-500' }
    case 'ended': return { label: 'encerrada', dot: 'bg-[var(--text-faint)]' }
    default: return { label: status, dot: 'bg-[var(--text-faint)]' }
  }
}

function DeviceRow({
  device, sessions, expanded, onToggle, selectedId, onSelectSession, onRevoke, revoking,
}: {
  device: HermesDevice
  sessions: HermesSession[]
  expanded: boolean
  onToggle: () => void
  selectedId: string | null
  onSelectSession: (id: string) => void
  onRevoke: (id: string) => void
  revoking: boolean
}) {
  return (
    <div className="overflow-hidden rounded-[16px] border border-[var(--border)]">
      <button
        type="button"
        onClick={onToggle}
        className="flex w-full items-center gap-2 bg-[var(--surface-soft)] px-3 py-2.5 text-left transition hover:bg-[var(--brand-soft)]"
      >
        <span className={`text-[var(--text-faint)] transition-transform ${expanded ? 'rotate-90' : ''}`}>▸</span>
        <PresenceDot status={device.status} />
        <span className="flex-1 truncate text-sm font-medium text-[var(--text)]">{device.name}</span>
        <span className="text-xs text-[var(--text-faint)]">{device.platform ?? '—'}</span>
        <span className="text-xs text-[var(--text-muted)]">
          {sessions.length > 0 ? `${sessions.length} sessão${sessions.length > 1 ? 'ões' : ''}` : 'sem sessões'}
        </span>
        {device.status !== 'revoked' && (
          <Button
            variant="ghost"
            onClick={(e) => { e.stopPropagation(); onRevoke(device.id) }}
            disabled={revoking}
          >
            Revogar
          </Button>
        )}
      </button>

      {expanded && (
        <div className="divide-y divide-[var(--border)]">
          {sessions.length === 0 ? (
            <p className="px-6 py-3 text-xs text-[var(--text-faint)]">
              Nenhuma sessão neste computador ainda.
            </p>
          ) : (
            sessions.map((s) => {
              const meta = sessionStatusMeta(s.status)
              const available = SESSION_AVAILABLE.has(s.status) && device.status === 'connected'
              const isSelected = s.id === selectedId
              return (
                <button
                  key={s.id}
                  type="button"
                  onClick={() => onSelectSession(s.id)}
                  className={`flex w-full items-center gap-2 border-l-[3px] py-2.5 pl-6 pr-3 text-left transition ${
                    isSelected
                      ? 'border-l-[var(--brand)] bg-[var(--brand-soft)]'
                      : available
                        ? 'border-l-[var(--brand)]/50 hover:bg-[var(--surface-elevated)]'
                        : 'border-l-transparent opacity-70 hover:bg-[var(--surface-elevated)]'
                  }`}
                >
                  <span className={`inline-block h-1.5 w-1.5 shrink-0 rounded-full ${meta.dot} ${meta.pulse ? 'animate-pulse' : ''}`} aria-hidden />
                  <span className={`flex-1 truncate text-sm ${isSelected ? 'font-medium text-[var(--text)]' : 'text-[var(--text-muted)]'}`}>
                    {s.title || s.workspace_path || s.id.slice(0, 8)}
                  </span>
                  <span className="text-xs text-[var(--text-faint)]">{meta.label}</span>
                </button>
              )
            })
          )}
        </div>
      )}
    </div>
  )
}

function DeviceSessionTree({ selectedId, onSelectSession }: { selectedId: string | null; onSelectSession: (id: string) => void }) {
  const qc = useQueryClient()
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set())

  const { data: devices = [], isLoading: devicesLoading, error: devicesError } = useQuery({
    queryKey: ['hermes', 'devices'],
    queryFn: () => api<HermesDevice[]>('/hermes/devices'),
    refetchInterval: 10_000,
  })
  const { data: sessions = [], isLoading: sessionsLoading, error: sessionsError } = useQuery({
    queryKey: ['hermes', 'sessions'],
    queryFn: () => api<HermesSession[]>('/hermes/sessions'),
    refetchInterval: 5_000,
  })
  const revoke = useMutation({
    mutationFn: (deviceId: string) => api(`/hermes/devices/${deviceId}/revoke`, { method: 'POST' }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['hermes', 'devices'] }),
  })

  const sessionsByDevice = useMemo(() => {
    const map = new Map<string, HermesSession[]>()
    for (const s of sessions) {
      const arr = map.get(s.device_id) ?? []
      arr.push(s)
      map.set(s.device_id, arr)
    }
    for (const arr of map.values()) {
      arr.sort((a, b) => new Date(b.last_activity_at).getTime() - new Date(a.last_activity_at).getTime())
    }
    return map
  }, [sessions])

  // Ordenado por disponibilidade e atividade recente (seção 6.2): conectado
  // com sessão ativa primeiro, depois conectado sem sessão, depois o resto.
  const orderedDevices = useMemo(() => {
    return [...devices].sort((a, b) => {
      const aActive = a.status === 'connected' && (sessionsByDevice.get(a.id)?.length ?? 0) > 0
      const bActive = b.status === 'connected' && (sessionsByDevice.get(b.id)?.length ?? 0) > 0
      if (aActive !== bActive) return aActive ? -1 : 1
      if ((a.status === 'connected') !== (b.status === 'connected')) return a.status === 'connected' ? -1 : 1
      return a.name.localeCompare(b.name)
    })
  }, [devices, sessionsByDevice])

  if (devicesLoading || sessionsLoading) return <TableSkeleton columns={3} />
  if (devicesError || sessionsError) return <ErrorText>Não foi possível carregar dispositivos e sessões.</ErrorText>
  if (devices.length === 0) {
    return (
      <EmptyState
        title="Nenhum computador pareado ainda"
        description={'Na extensão Hermes by Rangel Tech, rode o comando "Hermes: Sign In to RIA Atendimento" com seu login desta plataforma.'}
      />
    )
  }

  return (
    <div className="space-y-2">
      {orderedDevices.map((d) => (
        <DeviceRow
          key={d.id}
          device={d}
          sessions={sessionsByDevice.get(d.id) ?? []}
          expanded={!collapsed.has(d.id)}
          onToggle={() => setCollapsed((prev) => {
            const next = new Set(prev)
            if (next.has(d.id)) next.delete(d.id); else next.add(d.id)
            return next
          })}
          selectedId={selectedId}
          onSelectSession={onSelectSession}
          onRevoke={(id) => revoke.mutate(id)}
          revoking={revoke.isPending}
        />
      ))}
    </div>
  )
}

function SessionDetail({ session, onClose }: { session: HermesSession; onClose: () => void }) {
  const qc = useQueryClient()
  const [text, setText] = useState('')

  const { data: events = [], error: eventsError } = useQuery({
    queryKey: ['hermes', 'session-events', session.id],
    queryFn: () => api<HermesEvent[]>(`/hermes/sessions/${session.id}/events?limit=500`),
    refetchInterval: 3_000,
  })
  const { data: commands = [] } = useQuery({
    queryKey: ['hermes', 'session-commands', session.id],
    queryFn: () => api<HermesCommand[]>(`/hermes/sessions/${session.id}/commands`),
    refetchInterval: 3_000,
  })

  const send = useMutation({
    mutationFn: (instructionText: string) =>
      api<HermesCommand>(`/hermes/sessions/${session.id}/commands`, {
        method: 'POST',
        body: JSON.stringify({
          idempotency_key: `web-${Date.now()}-${Math.random().toString(36).slice(2)}`,
          payload: { text: instructionText },
        }),
      }),
    onSuccess: () => {
      setText('')
      qc.invalidateQueries({ queryKey: ['hermes', 'session-commands', session.id] })
    },
  })

  return (
    <Card
      title={`${session.device_name} — ${session.title || session.workspace_path || session.id.slice(0, 8)}`}
      actions={<Button variant="ghost" onClick={onClose}>Fechar</Button>}
    >
      <div className="space-y-4">
        <div className="flex items-center gap-2 text-sm text-[var(--text-muted)]">
          <span>Sessão:</span>
          <Badge ok={SESSION_STATUS_OK.has(session.status)}>{session.status}</Badge>
          <span>· Dispositivo:</span>
          <Badge ok={session.device_status === 'connected'}>{session.device_status}</Badge>
        </div>

        <div className="max-h-80 space-y-1 overflow-y-auto rounded-[16px] border border-[var(--border)] bg-[var(--surface-soft)] p-3 text-sm">
          {eventsError ? (
            <ErrorText>Não foi possível carregar os eventos desta sessão.</ErrorText>
          ) : events.length === 0 ? (
            <p className="text-[var(--text-muted)]">Nenhum evento ainda.</p>
          ) : (
            events.map((e) => (
              <p key={e.id} className="whitespace-pre-wrap text-[var(--text)]">
                {eventText(e)}
              </p>
            ))
          )}
        </div>

        <div className="space-y-2">
          <Textarea
            value={text}
            onChange={(ev) => setText(ev.target.value)}
            placeholder="Instrução para o Hermes remoto, ex.: gere uma tela de cadastro em HTML/CSS estilizada"
            rows={3}
          />
          <div className="flex items-center justify-between">
            {send.isError && <ErrorText>Não foi possível enviar o comando.</ErrorText>}
            <div className="ml-auto">
              <Button
                onClick={() => text.trim() && send.mutate(text.trim())}
                disabled={send.isPending || !text.trim()}
              >
                {send.isPending ? 'Enviando…' : 'Enviar comando'}
              </Button>
            </div>
          </div>
        </div>

        {commands.length > 0 && (
          <div>
            <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-[var(--text-faint)]">
              Comandos enviados
            </p>
            <Table headers={['Instrução', 'Status', 'Enviado em']}>
              {commands.map((c) => (
                <tr key={c.id}>
                  <td className="px-3 py-2 text-[var(--text)]">{c.payload.text ?? '—'}</td>
                  <td className="px-3 py-2">
                    <Badge ok={COMMAND_TERMINAL_OK.has(c.status)}>{c.status}</Badge>
                  </td>
                  <td className="px-3 py-2 text-[var(--text-muted)]">
                    {new Date(c.created_at).toLocaleTimeString('pt-BR')}
                  </td>
                </tr>
              ))}
            </Table>
          </div>
        )}
      </div>
    </Card>
  )
}

export default function HermesAgente() {
  const [selected, setSelected] = useState<string | null>(null)
  const { data: sessions = [] } = useQuery({
    queryKey: ['hermes', 'sessions'],
    queryFn: () => api<HermesSession[]>('/hermes/sessions'),
    refetchInterval: 5_000,
  })

  // Keep the open session in sync as the list refreshes, but don't force one
  // open on load — the person picks which conversation to watch.
  useEffect(() => {
    if (selected && !sessions.some((s) => s.id === selected)) setSelected(null)
  }, [sessions, selected])

  const openSession = sessions.find((s) => s.id === selected) ?? null

  return (
    <div className="space-y-8">
      <SurfaceSwitcher current="hermes" />
      <PageHeader
        title="Hermes agente"
        description="Computadores com a extensão Hermes by Rangel Tech pareados nesta empresa, suas sessões, e comandos remotos."
      />

      <Card title="Hermes by Rangel Tech">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
          <div className="space-y-1">
            <p className="text-sm text-[var(--text)]">
              Extensão para VS Code, versão <span className="font-semibold">{HERMES_RELEASE.version}</span>.
            </p>
            <p className="text-sm text-[var(--text-muted)]">
              Instale, abra o Hermes na barra lateral do VS Code e entre com seu e-mail e senha desta plataforma — o computador aparece aqui.
            </p>
            <p className="text-xs text-[var(--text-faint)]">
              <a href={HERMES_RELEASE.notesUrl} target="_blank" rel="noreferrer" className="underline hover:text-[var(--text)]">
                Notas de lançamento
              </a>
              {' · '}
              <a href={HERMES_RELEASE.sha256Url} target="_blank" rel="noreferrer" className="underline hover:text-[var(--text)]">
                checksum SHA-256
              </a>
            </p>
          </div>
          <a href={HERMES_RELEASE.vsixUrl} download>
            <Button>Baixar extensão (.vsix)</Button>
          </a>
        </div>
        <div className="mt-4 space-y-1 border-t border-[var(--border)] pt-4 text-xs text-[var(--text-muted)]">
          <p><span className="font-semibold text-[var(--text)]">1.</span> Baixe o arquivo .vsix acima.</p>
          <p><span className="font-semibold text-[var(--text)]">2.</span> No VS Code: Extensões → menu "..." → Instalar a partir de VSIX.</p>
          <p><span className="font-semibold text-[var(--text)]">3.</span> Abra o ícone do Hermes na barra lateral e entre com seu e-mail e senha desta plataforma.</p>
          <p><span className="font-semibold text-[var(--text)]">4.</span> O computador e suas sessões aparecem logo abaixo.</p>
        </div>
      </Card>

      <Card title="Computadores e sessões">
        <DeviceSessionTree selectedId={selected} onSelectSession={setSelected} />
      </Card>

      {openSession && <SessionDetail session={openSession} onClose={() => setSelected(null)} />}
    </div>
  )
}
