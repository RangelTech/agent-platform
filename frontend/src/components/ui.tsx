import type { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode, SelectHTMLAttributes } from 'react'

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
