import { Chip, Input } from './ui'
import { parseFrontmatter, patchFrontmatter } from '../lib/frontmatter'
import type { ToolCatalogItem } from '../lib/skills'

/**
 * Formulário do cabeçalho de uma skill. Cada alteração reescreve o YAML do
 * arquivo (só as chaves que o formulário conhece) e devolve o texto inteiro.
 */
export function FrontmatterForm({
  content,
  tools,
  readOnly,
  onChange,
}: {
  content: string
  tools: ToolCatalogItem[]
  readOnly?: boolean
  onChange: (next: string) => void
}) {
  const fm = parseFrontmatter(content)

  if (fm.hasBlock && !fm.ok) {
    return (
      <p
        data-testid="fm-broken"
        role="alert"
        className="rounded-2xl border border-[var(--danger)]/30 bg-[var(--danger-soft)] px-4 py-3 text-sm text-[var(--danger)]"
      >
        O cabeçalho desta skill não foi fechado (falta a segunda linha <code>---</code>). Corrija no editor para
        voltar a usar o formulário.
      </p>
    )
  }

  const toggleTool = (id: string) =>
    onChange(
      patchFrontmatter(content, {
        tools: fm.tools.includes(id) ? fm.tools.filter((t) => t !== id) : [...fm.tools, id],
      }),
    )

  return (
    <div data-testid="frontmatter-form" className="space-y-3 rounded-2xl border border-[var(--border)] bg-[var(--surface-soft)] p-4">
      <div className="grid gap-3 sm:grid-cols-2">
        <Input
          label="Nome da skill"
          hint="Minúsculas, números, ponto, hífen e sublinhado."
          name="fm-name"
          data-testid="fm-name"
          disabled={readOnly}
          value={fm.name}
          onChange={(e) => onChange(patchFrontmatter(content, { name: e.target.value }))}
        />
        <Input
          label="Versão"
          name="fm-version"
          data-testid="fm-version"
          disabled={readOnly}
          value={fm.version}
          placeholder="1.0.0"
          onChange={(e) => onChange(patchFrontmatter(content, { version: e.target.value }))}
        />
      </div>
      <Input
        label="Quando usar"
        hint="Uma frase: o agente lê isto para decidir quando aplicar a skill."
        name="fm-description"
        data-testid="fm-description"
        disabled={readOnly}
        value={fm.description}
        onChange={(e) => onChange(patchFrontmatter(content, { description: e.target.value }))}
      />
      {tools.length > 0 && (
        <div>
          <span className="mb-1.5 block text-[11px] font-semibold uppercase tracking-[0.18em] text-[var(--text-muted)]">
            Ferramentas desta skill
          </span>
          <div className="flex flex-wrap gap-2" data-testid="fm-tools">
            {tools.map((t) => (
              <Chip
                key={t.id}
                testId="fm-tool"
                selected={fm.tools.includes(t.id)}
                title={t.description}
                disabled={readOnly}
                onClick={() => toggleTool(t.id)}
              >
                {t.label}
              </Chip>
            ))}
          </div>
          <p className="mt-1.5 text-xs text-[var(--text-faint)]">
            A skill só pode usar ferramentas liberadas no painel Manifesto do agente.
          </p>
        </div>
      )}
    </div>
  )
}
