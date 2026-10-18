import { lazy, Suspense } from 'react'

const MonacoInner = lazy(() => import('./MonacoInner'))

/** Linguagem do Monaco a partir da extensão do arquivo. */
export function languageFor(path: string): string {
  if (path.endsWith('.py')) return 'python'
  if (path.endsWith('.sh')) return 'shell'
  if (path.endsWith('.js')) return 'javascript'
  if (path.endsWith('.yaml') || path.endsWith('.yml')) return 'yaml'
  return 'markdown'
}

/**
 * Editor de texto do pacote do agente (Monaco, carregado sob demanda).
 * `data-testid="skill-editor"` envolve o componente; o conteúdo é lido pelas
 * demais áreas da tela (formulário, prévia), nunca do DOM do Monaco.
 */
export function MarkdownEditor({
  path,
  value,
  onChange,
  readOnly = false,
  height = '28rem',
  testId = 'skill-editor',
}: {
  path: string
  value: string
  onChange: (value: string) => void
  readOnly?: boolean
  height?: string
  testId?: string
}) {
  return (
    <div
      data-testid={testId}
      data-path={path}
      className="overflow-hidden rounded-2xl border border-[var(--border)] bg-[var(--surface-soft)] focus-within:ring-2 focus-within:ring-[var(--brand-soft)]"
    >
      <Suspense
        fallback={
          <div
            style={{ height }}
            className="flex items-center justify-center text-sm text-[var(--text-muted)]"
            data-testid="editor-loading"
          >
            Carregando editor…
          </div>
        }
      >
        <MonacoInner
          value={value}
          language={languageFor(path)}
          readOnly={readOnly}
          ariaLabel={`Editor de ${path}`}
          modelPath={path}
          height={height}
          onChange={onChange}
        />
      </Suspense>
    </div>
  )
}
