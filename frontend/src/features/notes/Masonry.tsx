// The grid's columns (masonry, by width): as many columns as fit the width, the
// cards dealt left to right in their order, so the reading order and the keys' order (j/k) agree.

import { Children, useEffect, useRef, useState, type ReactNode } from 'react'

export function Masonry({ children, min, gap, list, label }: { children: ReactNode; min: number; gap: number; list: boolean; label?: string }) {
  const ref = useRef<HTMLDivElement>(null)
  const [cols, setCols] = useState(1)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    const measure = () => setCols(list ? 1 : Math.max(1, Math.floor((el.clientWidth + gap) / (min + gap))))
    measure()
    const ro = new ResizeObserver(measure)
    ro.observe(el)
    return () => ro.disconnect()
  }, [min, gap, list])
  const items = Children.toArray(children)
  const columns: ReactNode[][] = Array.from({ length: cols }, () => [])
  items.forEach((c, i) => columns[i % cols].push(c))
  return (
    <div ref={ref} className="nt-grid" aria-label={label} style={{ gap }}>
      {columns.map((c, i) => <div key={i} className="nt-col" style={{ gap }}>{c}</div>)}
    </div>
  )
}
