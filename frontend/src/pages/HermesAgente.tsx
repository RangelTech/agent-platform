import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Badge, Button, Card, EmptyState, ErrorText, PageHeader, Table, TableSkeleton, Textarea } from '../components/ui'
import { api } from '../lib/api'

// Hermes agente (SPEC_HERMES_INTEGRADO_RIA_ATENDIMENTO.md): computadores que
// pareiam a extensão VS Code "Hermes by Rangel Tech" aparecem aqui como
// dispositivos, cada sessão Hermes deles vira uma linha com o nome do
// computador, e um comando enviado daqui chega na extensão em até ~4s (poll
// HTTP -- ver docs/remote-control.md no repo hermes-by-rangel-tech; o Relay
// em tempo real é Fase B/futuro, o contrato não muda quando ele chegar).

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

function DeviceList() {
  const { data: devices = [], isLoading, error } = useQuery({
    queryKey: ['hermes', 'devices'],
    queryFn: () => api<HermesDevice[]>('/hermes/devices'),
    refetchInterval: 10_000,
  })

  if (isLoading) return <TableSkeleton columns={3} />
  if (error) return <ErrorText>Não foi possível carregar os dispositivos pareados.</ErrorText>
  if (devices.length === 0) {
    return (
      <EmptyState
        title="Nenhum computador pareado ainda"
        description={'Na extensão Hermes by Rangel Tech, rode o comando "Hermes: Sign In to RIA Atendimento" com seu login desta plataforma.'}
      />
    )
  }
  return (
    <Table headers={['Computador', 'Plataforma', 'Status']}>
      {devices.map((d) => (
        <tr key={d.id} className="transition hover:bg-[var(--brand-soft)]">
          <td className="px-3 py-2 text-[var(--text)]">{d.name}</td>
          <td className="px-3 py-2 text-[var(--text-muted)]">{d.platform ?? '—'}</td>
          <td className="px-3 py-2">
            <Badge ok={d.status === 'connected'}>{d.status}</Badge>
          </td>
        </tr>
      ))}
    </Table>
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
  const { data: sessions = [], isLoading, error } = useQuery({
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
      <PageHeader
        title="Hermes agente"
        description="Computadores com a extensão Hermes by Rangel Tech pareados nesta empresa, suas sessões, e comandos remotos."
      />

      <Card title="Dispositivos pareados">
        <DeviceList />
      </Card>

      <Card title="Sessões">
        {isLoading ? (
          <TableSkeleton columns={4} />
        ) : error ? (
          <ErrorText>Não foi possível carregar as sessões.</ErrorText>
        ) : sessions.length === 0 ? (
          <EmptyState
            title="Nenhuma sessão ainda"
            description="Abra um chat no Hermes, na extensão, com um computador já pareado — ele aparece aqui."
          />
        ) : (
          <Table headers={['Computador', 'Sessão', 'Status', 'Última atividade', '']}>
            {sessions.map((s) => (
              <tr key={s.id} className="transition hover:bg-[var(--brand-soft)]">
                <td className="px-3 py-2 text-[var(--text)]">{s.device_name}</td>
                <td className="px-3 py-2 text-[var(--text-muted)]">
                  {s.title || s.workspace_path || s.id.slice(0, 8)}
                </td>
                <td className="px-3 py-2">
                  <Badge ok={SESSION_STATUS_OK.has(s.status)}>{s.status}</Badge>
                </td>
                <td className="px-3 py-2 text-[var(--text-muted)]">
                  {new Date(s.last_activity_at).toLocaleString('pt-BR')}
                </td>
                <td className="px-3 py-2 text-right">
                  <Button variant="ghost" onClick={() => setSelected(s.id)}>
                    Abrir
                  </Button>
                </td>
              </tr>
            ))}
          </Table>
        )}
      </Card>

      {openSession && <SessionDetail session={openSession} onClose={() => setSelected(null)} />}
    </div>
  )
}
