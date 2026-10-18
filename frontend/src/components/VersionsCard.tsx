import type { ReactNode } from 'react'
import { Badge, Button, Card, Table } from './ui'

export interface VersionRow {
  id: string
  number: number
  created_at: string
  notes?: string
}

/**
 * Lista de versões publicadas, compartilhada pelos dois tipos de agente.
 * A ação por linha (deploy no estruturado, restaurar no de skills) vem de
 * fora; a versão em produção mostra o selo e não oferece a ação.
 */
export function VersionsCard({
  versions,
  activeId,
  actionLabel,
  busy,
  onAction,
  extra,
  readOnly = false,
}: {
  versions: VersionRow[]
  activeId: string | null
  actionLabel: string
  busy?: boolean
  onAction: (versionId: string) => void
  /** Ação adicional por linha (ex.: abrir no rascunho). */
  extra?: (v: VersionRow) => ReactNode
  readOnly?: boolean
}) {
  const showNotes = versions.some((v) => v.notes)
  return (
    <Card title="Versões">
      <Table headers={showNotes ? ['Versão', 'Criada em', 'Notas', 'Status', ''] : ['Versão', 'Criada em', 'Status', '']}>
        {versions.map((v) => (
          <tr
            key={v.id}
            data-testid="version-row"
            data-active={activeId === v.id ? 'true' : 'false'}
            className="transition hover:bg-[var(--brand-soft)]"
          >
            <td className="px-3 py-2 text-[var(--text)]">v{v.number}</td>
            <td className="px-3 py-2 text-[var(--text-muted)]">{new Date(v.created_at).toLocaleString('pt-BR')}</td>
            {showNotes && <td className="px-3 py-2 text-[var(--text-muted)]">{v.notes}</td>}
            <td className="px-3 py-2">{activeId === v.id && <Badge ok>em produção</Badge>}</td>
            <td className="px-3 py-2 text-right">
              <div className="flex justify-end gap-2">
                {extra?.(v)}
                {!readOnly && activeId !== v.id && (
                  <Button
                    variant="ghost"
                    data-testid="version-action"
                    onClick={() => onAction(v.id)}
                    disabled={busy}
                  >
                    {actionLabel}
                  </Button>
                )}
              </div>
            </td>
          </tr>
        ))}
        {versions.length === 0 && (
          <tr>
            <td colSpan={5} className="px-3 py-4 text-sm text-[var(--text-faint)]">
              Nenhuma versão publicada ainda.
            </td>
          </tr>
        )}
      </Table>
    </Card>
  )
}
