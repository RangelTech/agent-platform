import type { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode } from 'react'

export function Button({
  variant = 'primary',
  className = '',
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: 'primary' | 'ghost' | 'danger' }) {
  const styles = {
    primary: 'bg-[var(--brand)] text-white hover:opacity-90 disabled:opacity-50',
    ghost: 'bg-[var(--border)] text-[var(--text)] hover:opacity-80',
    danger: 'bg-rose-700 text-white hover:bg-rose-600',
  }[variant]
  return (
    <button
      {...props}
      className={`rounded-md px-3 py-2 text-sm font-medium transition disabled:cursor-not-allowed ${styles} ${className}`}
    />
  )
}

export function Input({
  label,
  className = '',
  ...props
}: InputHTMLAttributes<HTMLInputElement> & { label?: string }) {
  return (
    <label className="block">
      {label && <span className="mb-1 block text-sm text-[var(--text-muted)]">{label}</span>}
      <input
        {...props}
        className={`w-full rounded-md border border-[var(--border)] bg-[var(--surface-solid)] px-3 py-2 text-sm text-[var(--text)] outline-none placeholder:text-[var(--text-faint)] focus:border-[var(--brand)] ${className}`}
      />
    </label>
  )
}

export function Select({
  label,
  children,
  ...props
}: React.SelectHTMLAttributes<HTMLSelectElement> & { label?: string }) {
  return (
    <label className="block">
      {label && <span className="mb-1 block text-sm text-[var(--text-muted)]">{label}</span>}
      <select
        {...props}
        className="w-full rounded-md border border-[var(--border)] bg-[var(--surface-solid)] px-3 py-2 text-sm text-[var(--text)] outline-none focus:border-[var(--brand)]"
      >
        {children}
      </select>
    </label>
  )
}

export function Card({ title, actions, children }: { title?: string; actions?: ReactNode; children: ReactNode }) {
  return (
    <section className="rounded-lg border border-[var(--border)] bg-[var(--surface)]">
      {(title || actions) && (
        <header className="flex items-center justify-between border-b border-[var(--border)] px-4 py-3">
          {title && <h2 className="text-sm font-semibold text-[var(--text)]">{title}</h2>}
          {actions}
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  )
}

export function Table({ headers, children }: { headers: string[]; children: ReactNode }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-sm">
        <thead>
          <tr className="border-b border-[var(--border)] text-xs uppercase tracking-wide text-[var(--text-muted)]">
            {headers.map((h) => (
              <th key={h} className="px-3 py-2 font-medium">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-[var(--border)]">{children}</tbody>
      </table>
    </div>
  )
}

export function Badge({ ok, children }: { ok: boolean; children: ReactNode }) {
  return (
    <span
      className={`rounded-full px-2 py-0.5 text-xs ${ok ? 'bg-emerald-950 text-emerald-300' : 'bg-slate-800 text-slate-400'}`}
    >
      {children}
    </span>
  )
}

export function ErrorText({ children }: { children: ReactNode }) {
  if (!children) return null
  return (
    <p role="alert" className="text-sm text-rose-400">
      {children}
    </p>
  )
}
