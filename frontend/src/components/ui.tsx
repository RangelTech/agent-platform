import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type ButtonHTMLAttributes,
  type InputHTMLAttributes,
  type ReactNode,
  type SelectHTMLAttributes,
} from 'react'
import { createPortal } from 'react-dom'

function cx(...values: Array<string | false | null | undefined>) {
  return values.filter(Boolean).join(' ')
}

type FieldProps = {
  label?: string
  hint?: string
  error?: string
  className?: string
}

function FieldShell({
  label,
  hint,
  error,
  className = '',
  children,
}: FieldProps & { children: ReactNode }) {
  return (
    <label className={cx('block space-y-2', className)}>
      {(label || hint) && (
        <div className="space-y-1">
          {label && (
            <span className="block text-[11px] font-semibold uppercase tracking-[0.18em] text-[var(--text-muted)]">
              {label}
            </span>
          )}
          {hint && <span className="block text-sm leading-5 text-[var(--text-faint)]">{hint}</span>}
        </div>
      )}
      {children}
      {error && <p className="text-sm text-[var(--danger)]">{error}</p>}
    </label>
  )
}

export function PageHeader({
  title,
  description,
  actions,
}: {
  title: string
  description?: string
  actions?: ReactNode
}) {
  return (
    <div className="flex flex-col gap-4 rounded-[28px] border border-[var(--border)] bg-[linear-gradient(180deg,color-mix(in_srgb,var(--surface)_92%,white_8%),var(--surface))] px-6 py-6 shadow-[0_24px_80px_-44px_rgba(15,23,42,0.45)] sm:flex-row sm:items-start sm:justify-between">
      <div className="max-w-2xl">
        <div className="inline-flex items-center rounded-full border border-[var(--border)] bg-[var(--surface-solid)] px-3 py-1 text-[11px] font-semibold uppercase tracking-[0.18em] text-[var(--text-faint)]">
          Painel administrativo
        </div>
        <h1 className="mt-4 text-2xl font-semibold tracking-tight text-[var(--text)] sm:text-[30px]">{title}</h1>
        {description && <p className="mt-2 max-w-2xl text-sm leading-6 text-[var(--text-muted)] sm:text-[15px]">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-3">{actions}</div>}
    </div>
  )
}

export function Button({
  variant = 'primary',
  className = '',
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: 'primary' | 'ghost' | 'danger' }) {
  const styles = {
    primary:
      'border border-transparent bg-[var(--brand)] text-white shadow-[0_18px_40px_-18px_var(--brand)] hover:-translate-y-0.5 hover:opacity-95 disabled:opacity-50 disabled:shadow-none',
    ghost:
      'border border-[var(--border)] bg-[color-mix(in_srgb,var(--surface-solid)_82%,transparent)] text-[var(--text)] hover:border-[var(--brand)] hover:bg-[var(--brand-soft)]',
    danger:
      'border border-[color-mix(in_srgb,var(--danger)_28%,transparent)] bg-[var(--danger-soft)] text-[var(--danger)] hover:border-[color-mix(in_srgb,var(--danger)_45%,transparent)] hover:bg-[color-mix(in_srgb,var(--danger)_22%,transparent)]',
  }[variant]
  return (
    <button
      {...props}
      className={cx(
        'inline-flex min-h-11 items-center justify-center gap-2 rounded-full px-4 py-2.5 text-sm font-medium transition duration-200 disabled:cursor-not-allowed',
        styles,
        className,
      )}
    />
  )
}

export function Input({
  label,
  hint,
  error,
  className = '',
  ...props
}: InputHTMLAttributes<HTMLInputElement> & FieldProps) {
  return (
    <FieldShell label={label} hint={hint} error={error} className={className}>
      <input
        {...props}
        className={cx(
          'w-full rounded-2xl border border-[var(--border)] bg-[color-mix(in_srgb,var(--surface-solid)_88%,transparent)] px-4 py-3 text-sm text-[var(--text)] outline-none transition placeholder:text-[var(--text-faint)] focus:border-[var(--brand)] focus:ring-2 focus:ring-[var(--brand-soft)]',
          error && 'border-[color-mix(in_srgb,var(--danger)_45%,transparent)] focus:border-[var(--danger)] focus:ring-[var(--danger-soft)]',
        )}
      />
    </FieldShell>
  )
}

export function Select({
  label,
  hint,
  error,
  children,
  className = '',
  ...props
}: SelectHTMLAttributes<HTMLSelectElement> & FieldProps) {
  return (
    <FieldShell label={label} hint={hint} error={error} className={className}>
      <select
        {...props}
        className={cx(
          'w-full rounded-2xl border border-[var(--border)] bg-[color-mix(in_srgb,var(--surface-solid)_88%,transparent)] px-4 py-3 text-sm text-[var(--text)] outline-none transition focus:border-[var(--brand)] focus:ring-2 focus:ring-[var(--brand-soft)]',
          error && 'border-[color-mix(in_srgb,var(--danger)_45%,transparent)] focus:border-[var(--danger)] focus:ring-[var(--danger-soft)]',
        )}
      >
        {children}
      </select>
    </FieldShell>
  )
}

export function Textarea({
  label,
  hint,
  error,
  className = '',
  ...props
}: React.TextareaHTMLAttributes<HTMLTextAreaElement> & FieldProps) {
  return (
    <FieldShell label={label} hint={hint} error={error} className={className}>
      <textarea
        {...props}
        className={cx(
          'w-full rounded-2xl border border-[var(--border)] bg-[color-mix(in_srgb,var(--surface-solid)_88%,transparent)] px-4 py-3 text-sm text-[var(--text)] outline-none transition placeholder:text-[var(--text-faint)] focus:border-[var(--brand)] focus:ring-2 focus:ring-[var(--brand-soft)]',
          error && 'border-[color-mix(in_srgb,var(--danger)_45%,transparent)] focus:border-[var(--danger)] focus:ring-[var(--danger-soft)]',
        )}
      />
    </FieldShell>
  )
}

export function Card({ title, actions, children }: { title?: string; actions?: ReactNode; children: ReactNode }) {
  return (
    <section className="overflow-hidden rounded-[28px] border border-[var(--border)] bg-[linear-gradient(180deg,color-mix(in_srgb,var(--surface)_94%,white_6%),var(--surface))] shadow-[0_28px_90px_-48px_rgba(15,23,42,0.5)] backdrop-blur-sm">
      {(title || actions) && (
        <header className="flex flex-col gap-3 border-b border-[var(--border)] px-6 py-5 sm:flex-row sm:items-center sm:justify-between">
          {title && <h2 className="text-base font-semibold tracking-tight text-[var(--text)]">{title}</h2>}
          {actions}
        </header>
      )}
      <div className="p-6">{children}</div>
    </section>
  )
}

export function SectionIntro({
  eyebrow,
  title,
  description,
}: {
  eyebrow?: string
  title: string
  description?: string
}) {
  return (
    <div className="space-y-2">
      {eyebrow && <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-[var(--text-faint)]">{eyebrow}</p>}
      <h3 className="text-lg font-semibold tracking-tight text-[var(--text)]">{title}</h3>
      {description && <p className="max-w-2xl text-sm leading-6 text-[var(--text-muted)]">{description}</p>}
    </div>
  )
}

export function StatCard({ label, value, meta }: { label: string; value: string; meta?: string }) {
  return (
    <div className="rounded-[24px] border border-[var(--border)] bg-[var(--surface-solid)] p-5 shadow-[0_16px_40px_-32px_rgba(15,23,42,0.45)]">
      <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-[var(--text-faint)]">{label}</p>
      <p className="mt-3 text-2xl font-semibold tracking-tight text-[var(--text)]">{value}</p>
      {meta && <p className="mt-2 text-sm text-[var(--text-muted)]">{meta}</p>}
    </div>
  )
}

export function Table({ headers, children }: { headers: string[]; children: ReactNode }) {
  return (
    <div className="overflow-hidden rounded-[24px] border border-[var(--border)] bg-[var(--surface-solid)]">
      <div className="overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead>
            <tr className="border-b border-[var(--border)] bg-[color-mix(in_srgb,var(--surface)_78%,transparent)] text-[11px] uppercase tracking-[0.18em] text-[var(--text-faint)]">
              {headers.map((h) => (
                <th key={h} className="px-4 py-3 font-semibold">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-[var(--border)]">{children}</tbody>
        </table>
      </div>
    </div>
  )
}

export function Badge({ ok, children }: { ok: boolean; children: ReactNode }) {
  return (
    <span
      className={cx(
        'inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium',
        ok ? 'bg-[var(--success-soft)] text-[var(--success)]' : 'bg-[var(--border)] text-[var(--text-muted)]',
      )}
    >
      <span className={cx('h-1.5 w-1.5 rounded-full', ok ? 'bg-[var(--success)]' : 'bg-[var(--text-faint)]')} />
      {children}
    </span>
  )
}

export function ErrorText({ children }: { children: ReactNode }) {
  if (!children) return null
  return (
    <p
      role="alert"
      className="rounded-2xl border border-[color-mix(in_srgb,var(--danger)_22%,transparent)] bg-[var(--danger-soft)] px-4 py-3 text-sm text-[var(--danger)]"
    >
      {children}
    </p>
  )
}

/**
 * Loading placeholder. Every list/table screen uses these instead of a bare
 * "Carregando…" so the layout does not jump when the data lands.
 */
export function Skeleton({ className = '' }: { className?: string }) {
  return (
    <div
      aria-hidden="true"
      className={cx('animate-pulse rounded-2xl bg-[var(--surface-soft)]', className)}
    />
  )
}

export function TableSkeleton({ rows = 4, columns = 4 }: { rows?: number; columns?: number }) {
  return (
    <div
      role="status"
      aria-live="polite"
      aria-busy="true"
      data-testid="table-skeleton"
      className="space-y-3 rounded-[24px] border border-[var(--border)] bg-[var(--surface-solid)] p-4"
    >
      <span className="sr-only">Carregando dados…</span>
      {Array.from({ length: rows }).map((_, row) => (
        <div key={row} className="grid gap-3" style={{ gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))` }}>
          {Array.from({ length: columns }).map((_, col) => (
            <Skeleton key={col} className={cx('h-4', row === 0 && 'h-3 opacity-70')} />
          ))}
        </div>
      ))}
    </div>
  )
}

/**
 * Empty state. Never render an empty table body: say what is missing and give
 * the next action when there is one.
 */
export function EmptyState({
  title,
  description,
  action,
  icon = '◇',
}: {
  title: string
  description?: string
  action?: ReactNode
  icon?: ReactNode
}) {
  return (
    <div
      data-testid="empty-state"
      className="flex flex-col items-center gap-3 rounded-[24px] border border-dashed border-[var(--border-strong)] bg-[var(--surface-soft)] px-6 py-10 text-center"
    >
      <span aria-hidden="true" className="text-xl text-[var(--text-faint)]">
        {icon}
      </span>
      <p className="text-sm font-medium text-[var(--text)]">{title}</p>
      {description && <p className="max-w-md text-sm leading-6 text-[var(--text-muted)]">{description}</p>}
      {action && <div className="mt-1">{action}</div>}
    </div>
  )
}

/* ------------------------------------------------------------------ */
/* Tabs / Segmented                                                    */
/* ------------------------------------------------------------------ */

export interface TabItem {
  id: string
  label: ReactNode
  testId?: string
  /** Contador opcional ao lado do rótulo. */
  count?: number
}

/**
 * Abas com semântica de teclado (setas, Home, End). O chamador renderiza o
 * conteúdo do painel conforme `value`.
 */
export function Tabs({
  items,
  value,
  onChange,
  label,
  className = '',
  testId,
}: {
  items: TabItem[]
  value: string
  onChange: (id: string) => void
  label: string
  className?: string
  testId?: string
}) {
  const refs = useRef<Record<string, HTMLButtonElement | null>>({})
  function onKey(e: React.KeyboardEvent) {
    const idx = items.findIndex((i) => i.id === value)
    let next = -1
    if (e.key === 'ArrowRight') next = (idx + 1) % items.length
    else if (e.key === 'ArrowLeft') next = (idx - 1 + items.length) % items.length
    else if (e.key === 'Home') next = 0
    else if (e.key === 'End') next = items.length - 1
    if (next < 0) return
    e.preventDefault()
    const target = items[next]
    onChange(target.id)
    refs.current[target.id]?.focus()
  }
  return (
    <div
      role="tablist"
      aria-label={label}
      data-testid={testId}
      onKeyDown={onKey}
      className={cx('flex flex-wrap gap-1 border-b border-[var(--border)]', className)}
    >
      {items.map((item) => {
        const active = item.id === value
        return (
          <button
            key={item.id}
            ref={(node) => {
              refs.current[item.id] = node
            }}
            role="tab"
            type="button"
            aria-selected={active}
            tabIndex={active ? 0 : -1}
            data-testid={item.testId}
            onClick={() => onChange(item.id)}
            className={cx(
              '-mb-px inline-flex min-h-11 items-center gap-2 border-b-2 px-4 py-2 text-sm font-medium transition',
              active
                ? 'border-[var(--brand)] text-[var(--text)]'
                : 'border-transparent text-[var(--text-muted)] hover:text-[var(--text)]',
            )}
          >
            {item.label}
            {item.count !== undefined && (
              <span className="rounded-full bg-[var(--surface-soft)] px-2 py-0.5 text-xs text-[var(--text-muted)]">
                {item.count}
              </span>
            )}
          </button>
        )
      })}
    </div>
  )
}

export interface SegmentItem {
  id: string
  label: ReactNode
  testId?: string
}

/** Grupo de opções exclusivas em formato de pílulas (filtros). */
export function Segmented({
  items,
  value,
  onChange,
  label,
  testId,
  className = '',
}: {
  items: SegmentItem[]
  value: string
  onChange: (id: string) => void
  label: string
  testId?: string
  className?: string
}) {
  return (
    <div
      role="radiogroup"
      aria-label={label}
      data-testid={testId}
      className={cx(
        'inline-flex flex-wrap gap-1 rounded-full border border-[var(--border)] bg-[var(--surface-soft)] p-1',
        className,
      )}
    >
      {items.map((item) => {
        const active = item.id === value
        return (
          <button
            key={item.id}
            type="button"
            role="radio"
            aria-checked={active}
            data-testid={item.testId}
            onClick={() => onChange(item.id)}
            className={cx(
              'min-h-9 rounded-full px-4 py-1.5 text-sm font-medium transition',
              active
                ? 'bg-[var(--brand)] text-white shadow-sm'
                : 'text-[var(--text-muted)] hover:text-[var(--text)]',
            )}
          >
            {item.label}
          </button>
        )
      })}
    </div>
  )
}

/* ------------------------------------------------------------------ */
/* Modal                                                               */
/* ------------------------------------------------------------------ */

const FOCUSABLE =
  'a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])'

/**
 * Diálogo modal acessível: foco preso, Esc fecha, devolve o foco a quem abriu.
 * Renderiza num portal para escapar de overflow/transform das páginas.
 */
export function Modal({
  open,
  onClose,
  title,
  description,
  children,
  footer,
  testId = 'modal',
  size = 'md',
}: {
  open: boolean
  onClose: () => void
  title: string
  description?: string
  children: ReactNode
  footer?: ReactNode
  testId?: string
  size?: 'md' | 'lg'
}) {
  const panelRef = useRef<HTMLDivElement>(null)
  const titleId = useId()
  const onCloseRef = useRef(onClose)
  useEffect(() => {
    onCloseRef.current = onClose
  })

  useEffect(() => {
    if (!open) return
    const opener = document.activeElement as HTMLElement | null
    const panel = panelRef.current
    const focusables = () => Array.from(panel?.querySelectorAll<HTMLElement>(FOCUSABLE) ?? [])
    // Foco inicial: primeiro campo de formulário, senão o primeiro foco possível.
    const first = panel?.querySelector<HTMLElement>('input,select,textarea') ?? focusables()[0]
    first?.focus()
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') {
        e.stopPropagation()
        onCloseRef.current()
        return
      }
      if (e.key !== 'Tab') return
      const list = focusables()
      if (list.length === 0) return
      const head = list[0]
      const tail = list[list.length - 1]
      if (e.shiftKey && document.activeElement === head) {
        e.preventDefault()
        tail.focus()
      } else if (!e.shiftKey && document.activeElement === tail) {
        e.preventDefault()
        head.focus()
      }
    }
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('keydown', onKey)
      opener?.focus?.()
    }
  }, [open])

  if (!open) return null
  return createPortal(
    <div
      className="fixed inset-0 z-[60] flex items-end justify-center bg-[rgba(2,6,23,0.6)] p-0 backdrop-blur-sm sm:items-center sm:p-4"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose()
      }}
    >
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        data-testid={testId}
        className={cx(
          'flex max-h-[92dvh] w-full flex-col overflow-hidden rounded-t-[28px] border border-[var(--border)] bg-[var(--surface-solid)] text-[var(--text)] shadow-[0_40px_120px_-40px_rgba(2,6,23,0.8)] sm:rounded-[28px]',
          size === 'lg' ? 'sm:max-w-3xl' : 'sm:max-w-lg',
        )}
      >
        <header className="flex items-start justify-between gap-4 border-b border-[var(--border)] px-6 py-5">
          <div className="min-w-0">
            <h2 id={titleId} className="text-lg font-semibold tracking-tight">
              {title}
            </h2>
            {description && <p className="mt-1 text-sm leading-6 text-[var(--text-muted)]">{description}</p>}
          </div>
          <button
            type="button"
            aria-label="Fechar"
            data-testid={`${testId}-close`}
            onClick={onClose}
            className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full border border-[var(--border)] text-[var(--text-muted)] transition hover:text-[var(--text)]"
          >
            ✕
          </button>
        </header>
        <div className="min-h-0 flex-1 overflow-y-auto px-6 py-5">{children}</div>
        {footer && (
          <footer className="flex flex-wrap items-center justify-end gap-3 border-t border-[var(--border)] px-6 py-4">
            {footer}
          </footer>
        )}
      </div>
    </div>,
    document.body,
  )
}

/** Confirmação simples (excluir, parar, despublicar). */
export function ConfirmDialog({
  open,
  title,
  message,
  confirmLabel = 'Confirmar',
  danger = false,
  busy = false,
  onConfirm,
  onClose,
  testId = 'confirm-dialog',
}: {
  open: boolean
  title: string
  message: ReactNode
  confirmLabel?: string
  danger?: boolean
  busy?: boolean
  onConfirm: () => void
  onClose: () => void
  testId?: string
}) {
  return (
    <Modal
      open={open}
      onClose={onClose}
      title={title}
      testId={testId}
      footer={
        <>
          <Button type="button" variant="ghost" onClick={onClose} data-testid={`${testId}-cancel`}>
            Cancelar
          </Button>
          <Button
            type="button"
            variant={danger ? 'danger' : 'primary'}
            disabled={busy}
            onClick={onConfirm}
            data-testid={`${testId}-confirm`}
          >
            {confirmLabel}
          </Button>
        </>
      }
    >
      <div className="text-sm leading-6 text-[var(--text-muted)]">{message}</div>
    </Modal>
  )
}

/* ------------------------------------------------------------------ */
/* Toast                                                               */
/* ------------------------------------------------------------------ */

type ToastTone = 'success' | 'error' | 'info'
interface ToastItem {
  id: number
  tone: ToastTone
  message: string
}

const ToastContext = createContext<((message: string, tone?: ToastTone) => void) | null>(null)

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([])
  const counter = useRef(0)
  const push = useCallback((message: string, tone: ToastTone = 'success') => {
    const id = ++counter.current
    setItems((list) => [...list, { id, tone, message }])
    window.setTimeout(() => setItems((list) => list.filter((t) => t.id !== id)), tone === 'error' ? 7000 : 4000)
  }, [])
  const tones: Record<ToastTone, string> = {
    success: 'border-[var(--success)] bg-[var(--surface-solid)] text-[var(--text)]',
    error: 'border-[var(--danger)] bg-[var(--surface-solid)] text-[var(--text)]',
    info: 'border-[var(--info)] bg-[var(--surface-solid)] text-[var(--text)]',
  }
  return (
    <ToastContext.Provider value={push}>
      {children}
      <div
        aria-live="polite"
        data-testid="toast-region"
        className="pointer-events-none fixed inset-x-4 bottom-4 z-[70] flex flex-col items-center gap-2 sm:left-auto sm:right-4 sm:items-end"
      >
        {items.map((t) => (
          <div
            key={t.id}
            role={t.tone === 'error' ? 'alert' : 'status'}
            data-testid="toast"
            data-tone={t.tone}
            className={cx(
              'pointer-events-auto max-w-sm rounded-2xl border px-4 py-3 text-sm shadow-[0_20px_60px_-24px_rgba(2,6,23,0.7)]',
              tones[t.tone],
            )}
          >
            {t.message}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  )
}

export function useToast() {
  const push = useContext(ToastContext)
  return useMemo(() => push ?? (() => undefined), [push])
}

/* ------------------------------------------------------------------ */
/* Pequenos blocos reutilizados                                        */
/* ------------------------------------------------------------------ */

/** Pílula selecionável (ferramentas, filtros). */
export function Chip({
  selected,
  onClick,
  children,
  title,
  testId,
  disabled,
}: {
  selected: boolean
  onClick: () => void
  children: ReactNode
  title?: string
  testId?: string
  disabled?: boolean
}) {
  return (
    <button
      type="button"
      role="checkbox"
      aria-checked={selected}
      title={title}
      disabled={disabled}
      data-testid={testId}
      onClick={onClick}
      className={cx(
        'inline-flex min-h-9 items-center gap-1.5 rounded-full border px-3 py-1 text-xs transition disabled:cursor-not-allowed disabled:opacity-60',
        selected
          ? 'border-[var(--brand)] bg-[var(--brand-soft)] text-[var(--text)]'
          : 'border-[var(--border)] bg-[var(--surface-solid)] text-[var(--text-muted)] hover:border-[var(--brand)]',
      )}
    >
      {children}
    </button>
  )
}

/** Barra de uso (CPU, memória, disco). */
export function Meter({
  label,
  value,
  max,
  text,
  testId,
}: {
  label: string
  value: number
  max: number
  text: string
  testId?: string
}) {
  const pct = max > 0 ? Math.min(100, Math.round((value / max) * 100)) : 0
  const tone = pct >= 90 ? 'bg-[var(--danger)]' : pct >= 70 ? 'bg-[var(--info)]' : 'bg-[var(--success)]'
  return (
    <div data-testid={testId} className="space-y-2">
      <div className="flex items-baseline justify-between gap-3 text-sm">
        <span className="font-medium text-[var(--text)]">{label}</span>
        <span className="text-[var(--text-muted)]">{text}</span>
      </div>
      <div
        role="progressbar"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={pct}
        className="h-2.5 overflow-hidden rounded-full bg-[var(--surface-soft)]"
      >
        <div className={cx('h-full rounded-full transition-all', tone)} style={{ width: `${pct}%` }} />
      </div>
    </div>
  )
}
