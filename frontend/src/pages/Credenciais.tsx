import { useState, type ReactElement } from 'react'
import { Badge, PageHeader } from '../components/ui'
import { EmailAccountsPanel } from '../components/credenciais/EmailAccountsPanel'
import { GoogleAccountsPanel } from '../components/credenciais/GoogleAccountsPanel'
import { MicrosoftAccountsPanel } from '../components/credenciais/MicrosoftAccountsPanel'
import { useAuth } from '../lib/auth'

type TipoCredencial = 'email' | 'google' | 'microsoft'

interface TipoDef {
  id: TipoCredencial
  nome: string
  descricao: string
  resource: string
  Panel: () => ReactElement
}

const TIPOS: TipoDef[] = [
  {
    id: 'email',
    nome: 'Email',
    descricao: 'SMTP/IMAP para ler e enviar email',
    resource: 'email_accounts',
    Panel: EmailAccountsPanel,
  },
  {
    id: 'google',
    nome: 'Google',
    descricao: 'Calendar e Sheets',
    resource: 'google_accounts',
    Panel: GoogleAccountsPanel,
  },
  {
    id: 'microsoft',
    nome: 'Microsoft',
    descricao: 'Outlook e Teams',
    resource: 'microsoft_accounts',
    Panel: MicrosoftAccountsPanel,
  },
]

/** Tela "Credenciais" unificada (produto-08 §10) -- substitui as antigas
 * `/contas-de-email` e `/contas-google` separadas. Mesmo padrão visual de
 * "Serviços de IA"/`ContasIA`: grid de cards por tipo, clica pra conectar.
 * Diferente de Contas de IA, aqui não existe o conceito de combo -- conecta
 * e já fica disponível pras tools do tenant. Catálogo extensível: o próximo
 * tipo (Microsoft, produto-08 §12) entra como card novo em `TIPOS`, sem
 * tela nova. */
export default function Credenciais() {
  const { can } = useAuth()

  const tiposVisiveis = TIPOS.filter((t) => can(t.resource, 'view'))
  const [ativo, setAtivo] = useState<TipoCredencial | null>(tiposVisiveis[0]?.id ?? null)

  const tipoAtivo = tiposVisiveis.find((t) => t.id === ativo)

  return (
    <div className="space-y-6">
      <PageHeader
        title="Credenciais"
        description="Contas externas que os agentes usam para trabalhar em nome da empresa — email, agenda, planilhas e outras integrações."
      />

      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
        {tiposVisiveis.map((t) => (
          <button
            key={t.id}
            type="button"
            data-testid={`credencial-tipo-${t.id}`}
            onClick={() => setAtivo(t.id)}
            className={`flex min-h-16 flex-col justify-center gap-1 rounded-2xl border px-4 py-3 text-left transition ${
              ativo === t.id
                ? 'border-[var(--brand)] bg-[var(--brand-soft)]'
                : 'border-[var(--border)] bg-[var(--surface-soft)] hover:border-[var(--brand)] hover:bg-[var(--brand-soft)]'
            }`}
          >
            <span className="text-sm font-medium text-[var(--text)]">{t.nome}</span>
            <span className="text-xs text-[var(--text-muted)]">{t.descricao}</span>
          </button>
        ))}
      </div>

      {tiposVisiveis.length === 0 ? (
        <Badge ok={false}>Sem permissão para ver credenciais</Badge>
      ) : tipoAtivo ? (
        <tipoAtivo.Panel />
      ) : null}
    </div>
  )
}
