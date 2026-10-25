import { useMemo } from 'react'
import { encodeQr, qrPath } from './qr'

/** A QR code for `value`. Always dark on white, also in dark mode, so every camera reads it. */
export function QrCode({ value, size = 184, label }: { value: string; size?: number; label: string }) {
  const q = useMemo(() => encodeQr(value, 'M'), [value])
  const n = q.size + 8
  return (
    <svg className="ob-qr" width={size} height={size} viewBox={`0 0 ${n} ${n}`} role="img" aria-label={label} data-value={value} shapeRendering="crispEdges">
      <rect width={n} height={n} fill="#ffffff" />
      <path d={qrPath(q)} fill="#111111" />
    </svg>
  )
}
