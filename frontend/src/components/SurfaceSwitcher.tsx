import { useNavigate } from 'react-router-dom'

// SPEC_HERMES_INTEGRADO_RIA_ATENDIMENTO.md secao 6.1: uma unica escolha
// muda a natureza da area de trabalho (referencia ChatGPT/Codex), sem
// criar uma navegacao paralela. So aparece pra quem tem permissao Hermes
// -- pra todo mundo mais, a tela de Conversas continua exatamente igual.
export function SurfaceSwitcher({ current }: { current: 'chat' | 'hermes' }) {
  const navigate = useNavigate()
  return (
    <select
      value={current}
      onChange={(e) => navigate(e.target.value === 'hermes' ? '/hermes-agente' : '/chat')}
      aria-label="Selecionar superfície"
      data-testid="surface-switcher"
      className="mb-3 w-full rounded-2xl border border-[var(--border)] bg-[var(--surface-soft)] px-3 py-2 text-xs font-semibold uppercase tracking-[0.14em] text-[var(--text-muted)] outline-none transition hover:border-[var(--brand)] hover:text-[var(--text)] focus:border-[var(--brand)]"
    >
      <option value="chat">Conversas</option>
      <option value="hermes">Hermes agente</option>
    </select>
  )
}
