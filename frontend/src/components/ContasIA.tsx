import {
  useEffect,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent as ReactKeyboardEvent,
  type MouseEvent as ReactMouseEvent,
} from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Badge, Button, Card, EmptyState, ErrorText, Input, Select, Skeleton } from './ui'
import { api, ApiError } from '../lib/api'

/**
 * Contas de IA da empresa e os combos que revezam entre elas.
 *
 * Cada empresa é isolada por Team no roteador de IA (LiteLLM) — é isso que
 * garante que a conta de um cliente nunca atenda a chamada de outro. O
 * cliente não vê o roteador em si: ele vê contas e combos.
 *
 * Duas coisas explicam o formato da tela:
 *
 * 1. **Cada provedor conecta de um jeito.** Chave de API é um campo;
 *    assinatura (Claude, ChatGPT) manda autorizar no site do provedor e voltar
 *    com um código; e há provedor que mostra um código para digitar lá e fica
 *    sendo consultado até confirmar. Por isso é um provedor por vez, num
 *    modal, e não um formulário só tentando servir aos três.
 * 2. **Mais de uma conta do mesmo provedor é o objetivo, não acidente.** É daí
 *    que vem o revezamento que segura o limite de uso. Por isso prioridade e
 *    estratégia ficam visíveis.
 */

interface StatusRouter {
  provisionado: boolean
  provisionamento?: 'pending' | 'provisioning' | 'ready' | 'failed'
  provisionamento_erro?: string | null
  contas?: number
  combos?: number
}

type ModoConexao = 'apikey' | 'redirect' | 'device'

interface Provedor {
  id: string
  nome: string
  modo: ModoConexao
  prefixo: string
  nota?: string
}

interface Conta {
  id: string
  provider: string
  provider_nome: string
  auth_type: string
  label: string
  conectada: boolean
  situacao?: string
  ativa?: boolean
  prioridade?: number | null
  expira_em?: string | null
  ultimo_erro?: string | null
}

interface Modelo {
  id: string
  provider: string | null
  provider_nome: string | null
}

interface Combo {
  id: string
  name: string
  models: string[]
  ai_service_id: string | null
}

interface InicioOAuth {
  modo: ModoConexao
  auth_url: string | null
  user_code: string | null
  device_code: string | null
  redirect_uri: string | null
  code_verifier: string | null
  state: string | null
}

const SITUACAO: Record<string, { texto: string; ok: boolean }> = {
  active: { texto: 'Ativa', ok: true },
  unknown: { texto: 'Sem uso ainda', ok: true },
  unavailable: { texto: 'Indisponível', ok: false },
  ausente: { texto: 'Pendente no roteador', ok: false },
  armazenada: { texto: 'Conectada', ok: true },
}

export function ContasIA() {
  const qc = useQueryClient()
  const [erro, setErro] = useState('')
  const [provedorAberto, setProvedorAberto] = useState<Provedor | null>(null)

  const { data: status, isLoading } = useQuery({
    queryKey: ['ai-router-status'],
    queryFn: () => api<StatusRouter>('/ai-router/status'),
    // Enquanto a conexão está sendo feita em background, a tela se
    // atualiza sozinha em vez de exigir um F5 manual do admin.
    refetchInterval: (query) =>
      query.state.data?.provisionamento === 'provisioning' ? 5000 : false,
  })
  const provisionado = status?.provisionado ?? false

  const { data: catalogo = [] } = useQuery({
    queryKey: ['ai-router-catalogo'],
    queryFn: () => api<Provedor[]>('/ai-router/catalogo'),
    enabled: provisionado,
  })
  const { data: contas = [] } = useQuery({
    queryKey: ['ai-router-contas'],
    queryFn: () => api<Conta[]>('/ai-router/contas'),
    enabled: provisionado,
  })
  const { data: modelos = [] } = useQuery({
    queryKey: ['ai-router-modelos'],
    queryFn: () => api<Modelo[]>('/ai-router/modelos'),
    enabled: provisionado,
  })
  const { data: combos = [] } = useQuery({
    queryKey: ['ai-router-combos'],
    queryFn: () => api<Combo[]>('/ai-router/combos'),
    enabled: provisionado,
  })
  const { data: estrategias = {} } = useQuery({
    queryKey: ['ai-router-estrategia'],
    queryFn: () => api<Record<string, string>>('/ai-router/estrategia'),
    enabled: provisionado,
  })

  const recarregar = () => {
    qc.invalidateQueries({ queryKey: ['ai-router-contas'] })
    qc.invalidateQueries({ queryKey: ['ai-router-modelos'] })
    qc.invalidateQueries({ queryKey: ['ai-router-status'] })
  }

  const removerConta = useMutation({
    mutationFn: (id: string) => api(`/ai-router/contas/${id}`, { method: 'DELETE' }),
    onSuccess: recarregar,
    onError: (e) => setErro(e instanceof ApiError ? e.message : 'Falha ao remover conta'),
  })

  const sincronizar = useMutation({
    mutationFn: () => api<{ novas: number }>('/ai-router/sincronizar', { method: 'POST' }),
    onSuccess: recarregar,
    onError: (e) => setErro(e instanceof ApiError ? e.message : 'Falha ao sincronizar'),
  })

  const trocarEstrategia = useMutation({
    mutationFn: (v: { provider: string; estrategia: string }) =>
      api('/ai-router/estrategia', { method: 'PUT', body: JSON.stringify(v) }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['ai-router-estrategia'] }),
    onError: (e) => setErro(e instanceof ApiError ? e.message : 'Falha ao mudar revezamento'),
  })

  if (isLoading) return <Skeleton className="h-40" />

  if (!provisionado) {
    const etapa = status?.provisionamento ?? 'pending'
    const textos: Record<string, { title: string; description: string }> = {
      provisioning: {
        title: 'Conectando sua empresa ao roteador de IA…',
        description:
          'Isso leva só alguns instantes. Volte a esta tela em breve — ela atualiza sozinha.',
      },
      failed: {
        title: 'Não foi possível conectar sua empresa ao roteador de IA',
        description:
          status?.provisionamento_erro
            ? `Peça ao administrador da plataforma para tentar novamente. Detalhe: ${status.provisionamento_erro.slice(0, 200)}`
            : 'Peça ao administrador da plataforma para tentar novamente.',
      },
      pending: {
        title: 'Sua empresa ainda não está conectada ao roteador de IA',
        description:
          'Cada empresa atende com as próprias contas, isoladas das demais. Peça ao administrador da plataforma para conectar esta empresa.',
      },
    }
    const { title, description } = textos[etapa] ?? textos.pending
    return (
      <Card title="Contas de IA da empresa">
        <EmptyState title={title} description={description} />
      </Card>
    )
  }

  // O revezamento só muda alguma coisa onde há mais de uma conta do mesmo
  // provedor — então só aí a escolha aparece.
  const comRevezamento = [...new Set(contas.map((c) => c.provider))].filter(
    (p) => contas.filter((c) => c.provider === p).length > 1,
  )

  return (
    <div className="space-y-4">
      <Card
        title="Contas de IA da empresa"
        actions={
          <Button variant="ghost" onClick={() => sincronizar.mutate()}>
            {sincronizar.isPending ? 'Sincronizando…' : 'Sincronizar'}
          </Button>
        }
      >
        <div className="space-y-6">
          <div>
            <p className="mb-3 text-sm text-[var(--text-muted)]">
              Conecte as contas que a empresa já paga. Assinaturas (Claude, ChatGPT, Copilot)
              autorizam pelo site do provedor; as demais pedem a chave de API.
            </p>
            <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
              {catalogo.map((p) => {
                const quantas = contas.filter((c) => c.provider === p.id).length
                return (
                  <button
                    key={p.id}
                    type="button"
                    data-testid={`provedor-${p.id}`}
                    onClick={() => {
                      setErro('')
                      setProvedorAberto(p)
                    }}
                    className="flex min-h-14 items-center justify-between gap-2 rounded-2xl border border-[var(--border)] bg-[var(--surface-soft)] px-4 py-3 text-left transition hover:border-[var(--brand)] hover:bg-[var(--brand-soft)]"
                  >
                    <span className="min-w-0">
                      <span className="block truncate text-sm font-medium text-[var(--text)]">
                        {p.nome}
                      </span>
                      <span className="block text-xs text-[var(--text-muted)]">
                        {p.modo === 'apikey' ? 'chave de API' : 'assinatura'}
                      </span>
                    </span>
                    {quantas > 0 && <Badge ok>{quantas}</Badge>}
                  </button>
                )
              })}
            </div>
          </div>

          {erro && <ErrorText>{erro}</ErrorText>}

          {contas.length === 0 ? (
            <EmptyState
              title="Nenhuma conta conectada"
              description="Escolha um provedor acima para conectar a primeira conta."
            />
          ) : (
            <ul className="space-y-2" data-testid="lista-contas">
              {contas.map((c) => {
                const s = SITUACAO[c.situacao ?? 'unknown'] ?? SITUACAO.unknown
                return (
                  <li
                    key={c.id}
                    className="flex flex-wrap items-center justify-between gap-3 rounded-2xl bg-[var(--surface-soft)] px-4 py-3"
                  >
                    <div className="min-w-0">
                      <p className="truncate text-sm font-medium text-[var(--text)]">{c.label}</p>
                      <p className="text-xs text-[var(--text-muted)]">
                        {c.provider_nome} ·{' '}
                        {c.auth_type === 'apikey' ? 'chave de API' : 'assinatura'}
                        {c.prioridade ? ` · prioridade ${c.prioridade}` : ''}
                      </p>
                      {c.ultimo_erro && (
                        <p className="mt-1 text-xs text-[var(--danger)]">{c.ultimo_erro}</p>
                      )}
                    </div>
                    <div className="flex items-center gap-2">
                      <Badge ok={s.ok}>{s.texto}</Badge>
                      <Button variant="danger" onClick={() => removerConta.mutate(c.id)}>
                        Remover
                      </Button>
                    </div>
                  </li>
                )
              })}
            </ul>
          )}

          {comRevezamento.length > 0 && (
            <div className="rounded-2xl border border-[var(--border)] p-4">
              <p className="text-sm font-medium text-[var(--text)]">Revezamento entre contas</p>
              <p className="mb-3 text-xs text-[var(--text-muted)]">
                Alternar a cada chamada distribui o limite entre as contas. Só quando falhar
                mantém uma conta até ela parar de responder.
              </p>
              <div className="space-y-2">
                {comRevezamento.map((p) => (
                  <div key={p} className="flex flex-wrap items-center justify-between gap-3">
                    <span className="text-sm text-[var(--text)]">
                      {contas.find((c) => c.provider === p)?.provider_nome ?? p}
                    </span>
                    <Select
                      value={estrategias[p] ?? 'fallback'}
                      onChange={(e) =>
                        trocarEstrategia.mutate({ provider: p, estrategia: e.target.value })
                      }
                      className="max-w-[16rem]"
                    >
                      <option value="round-robin">Alternar a cada chamada</option>
                      <option value="fallback">Só quando falhar</option>
                    </Select>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </Card>

      <CombosCard modelos={modelos} combos={combos} />

      {provedorAberto && (
        <ModalConexao
          provedor={provedorAberto}
          onFechar={() => setProvedorAberto(null)}
          onPronto={() => {
            setProvedorAberto(null)
            recarregar()
          }}
        />
      )}
    </div>
  )
}

/**
 * Conexão de uma conta, um provedor por vez.
 *
 * O fluxo de assinatura tem duas etapas separadas por uma ida ao site do
 * provedor. O `code_verifier` devolvido na primeira etapa volta na segunda:
 * é um verificador PKCE de uso único, feito exatamente para trafegar assim,
 * e por isso vive só no estado deste componente — não é persistido.
 */
function ModalConexao({
  provedor,
  onFechar,
  onPronto,
}: {
  provedor: Provedor
  onFechar: () => void
  onPronto: () => void
}) {
  const [label, setLabel] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [codigo, setCodigo] = useState('')
  const [inicio, setInicio] = useState<InicioOAuth | null>(null)
  const [erro, setErro] = useState('')
  const [aguardando, setAguardando] = useState(false)
  const cancelado = useRef(false)

  // Claude e Codex: o client_id público deles só aceita redirect_uri
  // fixo/loopback (console.anthropic.com e localhost:1455) -- não dá pra
  // apontar pro nosso domínio como os outros provedores redirect fazem.
  // Em vez de pedir pra colar código na mão, abrimos um navegador de
  // verdade no nosso lado (serviço `oauth-browser`) e espelhamos a tela
  // aqui: o admin loga normal, a gente captura o código sozinho.
  const usaNavegadorEspelhado = provedor.id === 'claude' || provedor.id === 'codex'
  const [frame, setFrame] = useState('')
  const [navegadorAberto, setNavegadorAberto] = useState(false)
  const wsRef = useRef<WebSocket | null>(null)
  const NAV_W = 1280
  const NAV_H = 800

  useEffect(
    () => () => {
      cancelado.current = true
      wsRef.current?.close()
    },
    [],
  )

  const conectarChave = useMutation({
    mutationFn: () =>
      api('/ai-router/contas', {
        method: 'POST',
        body: JSON.stringify({
          provider: provedor.id,
          api_key: apiKey,
          label: label || provedor.nome,
        }),
      }),
    onSuccess: onPronto,
    onError: (e) => setErro(e instanceof ApiError ? e.message : 'Falha ao conectar'),
  })

  const iniciar = useMutation({
    mutationFn: () =>
      api<InicioOAuth>('/ai-router/contas/oauth/iniciar', {
        method: 'POST',
        body: JSON.stringify({ provider: provedor.id }),
      }),
    onSuccess: (dados) => {
      setInicio(dados)
      if (dados.auth_url) window.open(dados.auth_url, '_blank', 'noopener')
      if (dados.modo === 'device') void aguardarConfirmacao(dados)
    },
    onError: (e) => setErro(e instanceof ApiError ? e.message : 'Falha ao iniciar autorização'),
  })

  const iniciarNavegador = useMutation({
    mutationFn: () =>
      api<{
        ws_url: string
        redirect_uri: string
        code_verifier: string
        state: string
      }>('/ai-router/contas/oauth/navegador/iniciar', {
        method: 'POST',
        body: JSON.stringify({ provider: provedor.id }),
      }),
    onSuccess: (dados) => {
      setInicio({
        modo: 'redirect',
        auth_url: null,
        user_code: null,
        device_code: null,
        redirect_uri: dados.redirect_uri,
        code_verifier: dados.code_verifier,
        state: dados.state,
      })
      setNavegadorAberto(true)
      const ws = new WebSocket(dados.ws_url)
      wsRef.current = ws
      ws.onmessage = (evento) => {
        const msg = JSON.parse(evento.data) as
          | { type: 'frame'; data: string }
          | { type: 'done'; code: string; state: string }
          | { type: 'erro'; mensagem: string }
        if (msg.type === 'frame') setFrame(`data:image/jpeg;base64,${msg.data}`)
        else if (msg.type === 'done') {
          setCodigo(provedor.id === 'claude' ? `${msg.code}#${msg.state}` : msg.code)
        } else if (msg.type === 'erro') setErro(msg.mensagem)
      }
      ws.onerror = () => setErro('Falha na conexão com o navegador remoto')
    },
    onError: (e) => setErro(e instanceof ApiError ? e.message : 'Falha ao abrir navegador remoto'),
  })

  const enviarMouseRemoto = (e: ReactMouseEvent<HTMLImageElement>, clique: boolean) => {
    const ws = wsRef.current
    if (!ws || ws.readyState !== WebSocket.OPEN) return
    const rect = e.currentTarget.getBoundingClientRect()
    const x = Math.round(((e.clientX - rect.left) / rect.width) * NAV_W)
    const y = Math.round(((e.clientY - rect.top) / rect.height) * NAV_H)
    ws.send(JSON.stringify({ type: 'mouse', x, y, click: clique }))
  }

  const enviarTeclaRemota = (e: ReactKeyboardEvent) => {
    const ws = wsRef.current
    if (!ws || ws.readyState !== WebSocket.OPEN) return
    e.preventDefault()
    if (e.key.length === 1) ws.send(JSON.stringify({ type: 'key', text: e.key }))
    else ws.send(JSON.stringify({ type: 'key', key: e.key }))
  }

  const concluir = useMutation({
    mutationFn: () =>
      api<{ pendente: boolean }>('/ai-router/contas/oauth/concluir', {
        method: 'POST',
        body: JSON.stringify({
          provider: provedor.id,
          label: label || provedor.nome,
          code: codigo,
          redirect_uri: inicio?.redirect_uri,
          code_verifier: inicio?.code_verifier,
          state: inicio?.state,
        }),
      }),
    onSuccess: onPronto,
    onError: (e) => setErro(e instanceof ApiError ? e.message : 'Falha ao concluir'),
  })

  // A aba de callback (produto-08, /oauth/callback) repassa o código pra cá
  // via postMessage assim que o provedor redireciona — sem isso o cliente
  // teria que copiar a URL na mão da outra aba e colar aqui.
  useEffect(() => {
    function aoReceberMensagem(evento: MessageEvent) {
      if (evento.origin !== window.location.origin) return
      const corpo = evento.data as { type?: string; dados?: { code?: string; error?: string } }
      if (corpo?.type !== 'oauth_callback' || !corpo.dados) return
      if (corpo.dados.error) {
        setErro(corpo.dados.error)
        return
      }
      if (corpo.dados.code) setCodigo(corpo.dados.code)
    }
    window.addEventListener('message', aoReceberMensagem)
    return () => window.removeEventListener('message', aoReceberMensagem)
  }, [])

  // Assim que o código chega (mensagem ou colado na mão), conclui sozinho —
  // fluxo redirect só precisa do clique inicial de "Abrir autorização".
  useEffect(() => {
    if (codigo && inicio && inicio.modo !== 'device' && !concluir.isPending) {
      concluir.mutate()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [codigo])

  /**
   * Fluxo device: o cliente digita o código no site do provedor e nós
   * perguntamos até ele confirmar. Sem isso ele teria que apertar um botão
   * sem saber quando — e apertar cedo demais parece erro.
   */
  async function aguardarConfirmacao(dados: InicioOAuth) {
    setAguardando(true)
    cancelado.current = false
    for (let tentativa = 0; tentativa < 60 && !cancelado.current; tentativa += 1) {
      await new Promise((r) => setTimeout(r, 5000))
      if (cancelado.current) return
      try {
        const r = await api<{ pendente: boolean; erro?: string | null }>(
          '/ai-router/contas/oauth/concluir',
          {
            method: 'POST',
            body: JSON.stringify({
              provider: provedor.id,
              label: label || provedor.nome,
              device_code: dados.device_code,
              code_verifier: dados.code_verifier,
            }),
          },
        )
        if (!r.pendente) {
          if (r.erro) setErro(r.erro)
          else {
            onPronto()
            return
          }
          break
        }
      } catch (e) {
        setErro(e instanceof ApiError ? e.message : 'Falha ao confirmar')
        break
      }
    }
    setAguardando(false)
  }

  const enviar = (e: FormEvent) => {
    e.preventDefault()
    setErro('')
    if (provedor.modo === 'apikey') conectarChave.mutate()
    else if (!inicio) {
      if (usaNavegadorEspelhado) iniciarNavegador.mutate()
      else iniciar.mutate()
    } else concluir.mutate()
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/60 p-4"
      role="dialog"
      aria-modal="true"
    >
      <form
        onSubmit={enviar}
        data-testid="modal-conexao"
        className="max-h-[90dvh] w-full max-w-lg space-y-4 overflow-y-auto rounded-[28px] border border-[var(--border)] bg-[var(--surface-solid)] p-6 shadow-2xl"
      >
        <div>
          <h3 className="text-base font-semibold text-[var(--text)]">
            Conectar {provedor.nome}
          </h3>
          {provedor.nota && (
            <p className="mt-1 text-sm text-[var(--text-muted)]">{provedor.nota}</p>
          )}
        </div>

        <Input
          label="Apelido"
          hint="Como esta conta aparece para o seu time."
          value={label}
          onChange={(e) => setLabel(e.target.value)}
          placeholder={provedor.nome}
        />

        {provedor.modo === 'apikey' && (
          <Input
            label="Chave de API"
            hint="Fica guardada cifrada e nunca é exibida de volta."
            type="password"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            required
          />
        )}

        {provedor.modo === 'redirect' && inicio && navegadorAberto && (
          <div className="space-y-2">
            <p className="text-sm font-medium text-[var(--text)]">
              Faça login normalmente na tela abaixo
            </p>
            <div
              tabIndex={0}
              onKeyDown={enviarTeclaRemota}
              className="overflow-hidden rounded-2xl border border-[var(--border)] outline-none focus:ring-2 focus:ring-[var(--brand)]"
            >
              {frame ? (
                <img
                  src={frame}
                  alt="Tela de login do provedor"
                  className="w-full cursor-pointer select-none"
                  onClick={(e) => enviarMouseRemoto(e, true)}
                  onMouseMove={(e) => enviarMouseRemoto(e, false)}
                />
              ) : (
                <div className="flex h-64 items-center justify-center text-sm text-[var(--text-muted)]">
                  Abrindo navegador…
                </div>
              )}
            </div>
            <p className="text-xs text-[var(--text-muted)]">
              Clique na tela pra focar e digitar. Fecha sozinha ao concluir o login.
            </p>
          </div>
        )}

        {provedor.modo === 'redirect' && inicio && !navegadorAberto && (
          <div className="space-y-3">
            <div className="rounded-2xl bg-[var(--surface-soft)] p-3">
              <p className="text-sm font-medium text-[var(--text)]">
                1. Autorize no site do provedor
              </p>
              <p className="mt-1 text-xs text-[var(--text-muted)]">
                A janela abriu em outra aba. Se não abriu,{' '}
                <a
                  className="underline"
                  href={inicio.auth_url ?? '#'}
                  target="_blank"
                  rel="noreferrer"
                >
                  clique aqui
                </a>
                .
              </p>
            </div>
            <Input
              label="2. Cole o que o provedor devolveu"
              hint="Pode colar a URL inteira da barra de endereços — extraímos o código."
              value={codigo}
              onChange={(e) => setCodigo(e.target.value)}
              required
            />
          </div>
        )}

        {provedor.modo === 'device' && inicio && (
          <div className="space-y-2 rounded-2xl bg-[var(--surface-soft)] p-4 text-center">
            <p className="text-sm font-medium text-[var(--text)]">
              Digite este código no site do provedor
            </p>
            <p className="font-mono text-2xl tracking-[0.3em] text-[var(--text)]">
              {inicio.user_code ?? '—'}
            </p>
            <p className="text-xs text-[var(--text-muted)]">
              {aguardando
                ? 'Esperando você confirmar… esta janela fecha sozinha.'
                : 'Confirme no site para concluir.'}
            </p>
          </div>
        )}

        {erro && <ErrorText>{erro}</ErrorText>}

        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={onFechar}>
            Cancelar
          </Button>
          {!(provedor.modo === 'device' && inicio) && !navegadorAberto && (
            <Button
              type="submit"
              disabled={
                iniciar.isPending ||
                iniciarNavegador.isPending ||
                concluir.isPending ||
                conectarChave.isPending
              }
            >
              {provedor.modo === 'apikey'
                ? 'Conectar'
                : inicio
                  ? 'Concluir'
                  : 'Abrir autorização'}
            </Button>
          )}
        </div>
      </form>
    </div>
  )
}

/** Combos: quais modelos revezam entre si e viram um serviço de IA. */
function CombosCard({ modelos, combos }: { modelos: Modelo[]; combos: Combo[] }) {
  const qc = useQueryClient()
  const [nome, setNome] = useState('')
  const [escolhidos, setEscolhidos] = useState<string[]>([])
  const [erro, setErro] = useState('')

  const criar = useMutation({
    mutationFn: () =>
      api('/ai-router/combos', {
        method: 'POST',
        body: JSON.stringify({ name: nome, models: escolhidos }),
      }),
    onSuccess: () => {
      setNome('')
      setEscolhidos([])
      setErro('')
      qc.invalidateQueries({ queryKey: ['ai-router-combos'] })
      qc.invalidateQueries({ queryKey: ['ai-services'] })
    },
    onError: (e) => setErro(e instanceof ApiError ? e.message : 'Falha ao criar combo'),
  })

  const remover = useMutation({
    mutationFn: (id: string) => api(`/ai-router/combos/${id}`, { method: 'DELETE' }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['ai-router-combos'] })
      qc.invalidateQueries({ queryKey: ['ai-services'] })
    },
  })

  // Agrupar por provedor deixa visível que um combo bom mistura contas
  // diferentes — é isso que faz o limite de uma não derrubar o atendimento.
  const porProvedor = modelos.reduce<Record<string, Modelo[]>>((acc, m) => {
    const chave = m.provider_nome ?? m.provider ?? 'Outros'
    acc[chave] = [...(acc[chave] ?? []), m]
    return acc
  }, {})

  return (
    <Card title="Combos">
      <div className="space-y-5">
        <p className="text-sm text-[var(--text-muted)]">
          Um combo reveza entre os modelos escolhidos e vira um serviço de IA, pronto para
          escolher no template.
        </p>

        {modelos.length === 0 ? (
          <EmptyState
            title="Conecte uma conta primeiro"
            description="Os modelos de um combo vêm das contas conectadas acima."
          />
        ) : (
          <div className="space-y-3">
            <Input
              label="Nome do combo"
              value={nome}
              onChange={(e) => setNome(e.target.value)}
              placeholder="Ex.: Produção"
              className="max-w-sm"
            />

            <div className="max-h-72 space-y-4 overflow-y-auto rounded-2xl border border-[var(--border)] p-4">
              {Object.entries(porProvedor).map(([grupo, lista]) => (
                <div key={grupo}>
                  <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-[var(--text-muted)]">
                    {grupo}
                  </p>
                  <div className="grid gap-1 sm:grid-cols-2">
                    {lista.map((m) => (
                      <label key={m.id} className="flex items-center gap-2 text-sm">
                        <input
                          type="checkbox"
                          checked={escolhidos.includes(m.id)}
                          onChange={(e) =>
                            setEscolhidos((atual) =>
                              e.target.checked
                                ? [...atual, m.id]
                                : atual.filter((x) => x !== m.id),
                            )
                          }
                        />
                        <span className="truncate font-mono text-xs text-[var(--text)]">
                          {m.id}
                        </span>
                      </label>
                    ))}
                  </div>
                </div>
              ))}
            </div>

            {erro && <ErrorText>{erro}</ErrorText>}

            <Button
              onClick={() => criar.mutate()}
              disabled={!nome || escolhidos.length === 0 || criar.isPending}
            >
              {criar.isPending ? 'Criando…' : `Criar combo (${escolhidos.length})`}
            </Button>
          </div>
        )}

        {combos.length > 0 && (
          <ul className="space-y-2" data-testid="lista-combos">
            {combos.map((c) => (
              <li
                key={c.id}
                className="flex flex-wrap items-center justify-between gap-3 rounded-2xl bg-[var(--surface-soft)] px-4 py-3"
              >
                <div className="min-w-0">
                  <p className="text-sm font-medium text-[var(--text)]">{c.name}</p>
                  <p className="truncate font-mono text-xs text-[var(--text-muted)]">
                    {c.models.join(' · ')}
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  <Badge ok>serviço de IA publicado</Badge>
                  <Button variant="danger" onClick={() => remover.mutate(c.id)}>
                    Remover
                  </Button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </Card>
  )
}
