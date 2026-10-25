// A QR code drawn as one SVG path (uqr encodes it; nothing leaves the browser). Dark modules on a
// white quiet zone in every theme, so phone cameras read it in dark mode too.

import { encode } from 'uqr'
import { useMemo } from 'react'
import { t } from '../i18n'

export function QrCode({ text, size = 176, label }: { text: string; size?: number; label: string }) {
  const { path, n } = useMemo(() => {
    const qr = encode(text, { ecc: 'M', border: 2 })
    let d = ''
    qr.data.forEach((row, y) => row.forEach((on, x) => { if (on) d += t('components.qrCode.mH1v1h1z', { x, y }) }))
    return { path: d, n: qr.size }
  }, [text])
  return (
    <svg className="qr-code" role="img" aria-label={label} width={size} height={size} viewBox={`0 0 ${n} ${n}`} shapeRendering="crispEdges">
      <rect width={n} height={n} fill="#fff" />
      <path d={path} fill="#111" />
    </svg>
  )
}
