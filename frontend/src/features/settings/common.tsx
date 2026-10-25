import type { ReactNode } from 'react'

export function SetRow({ k, sub, children, id }: { k: ReactNode; sub?: ReactNode; children: ReactNode; id?: string }) {
  return (
    <div className="set-row" id={id}>
      <div className="k">
        {k}
        {sub && <span className="sub">{sub}</span>}
      </div>
      <div className="v">{children}</div>
    </div>
  )
}

export function Section({ title, desc, children, id, actions }: { title: string; desc?: ReactNode; children: ReactNode; id?: string; actions?: ReactNode }) {
  return (
    <section className="set-section" id={id}>
      <div className="row" style={{ alignItems: 'flex-end', marginBottom: 4 }}>
        <h2 style={{ flex: 1 }}>{title}</h2>
        {actions}
      </div>
      {desc && <p>{desc}</p>}
      {children}
    </section>
  )
}

export function Radio<T extends string | number>({ name, value, current, onChange, label, desc, disabled }: { name: string; value: T; current: T; onChange: (v: T) => void; label: ReactNode; desc?: ReactNode; disabled?: boolean }) {
  return (
    <label className="choice" style={disabled ? { opacity: 0.5 } : undefined}>
      <input type="radio" className="radio" name={name} checked={current === value} disabled={disabled} onChange={() => onChange(value)} />
      <span>
        {label}
        {desc && <div className="desc">{desc}</div>}
      </span>
    </label>
  )
}

export function Check({ checked, onChange, label, desc, disabled }: { checked: boolean; onChange: (v: boolean) => void; label: ReactNode; desc?: ReactNode; disabled?: boolean }) {
  return (
    <label className="choice" style={disabled ? { opacity: 0.5 } : undefined}>
      <input type="checkbox" className="checkbox" checked={checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)} />
      <span>
        {label}
        {desc && <div className="desc">{desc}</div>}
      </span>
    </label>
  )
}
