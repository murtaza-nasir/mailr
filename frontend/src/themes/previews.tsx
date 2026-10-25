import type { ReactNode } from 'react'
import { t } from '../i18n'

// Picker thumbnails: a 160×100 sketch of each theme's layout. Colours come from --tp-* custom
// properties set per preview class in themes.css (light and dark), so a thumbnail shows its own
// theme's palette whatever theme is active.

const W = 160
const H = 100

function Frame({ cls, children, label }: { cls: string; children: ReactNode; label: string }) {
  return (
    <svg className={`theme-preview ${cls}`} viewBox={`0 0 ${W} ${H}`} role="img" aria-label={label}>
      <rect width={W} height={H} rx="6" fill="var(--tp-bg)" />
      {children}
    </svg>
  )
}

/** Horizontal text line placeholder. */
function L({ x, y, w, h = 2.5, c = 'var(--tp-line)', r = 1.25 }: { x: number; y: number; w: number; h?: number; c?: string; r?: number }) {
  return <rect x={x} y={y} width={w} height={h} rx={r} fill={c} />
}

export function ClassicPreview() {
  return (
    <Frame cls="tp-classic" label={t('themes.previews.classicLayoutPreview')}>
      <L x={36} y={6} w={70} h={8} r={4} c="var(--tp-input)" />
      <rect x={6} y={20} width={24} height={9} rx={4} fill="var(--tp-accent-soft)" />
      <rect x={4} y={34} width={30} height={5} rx={2.5} fill="var(--tp-accent-soft)" />
      {[43, 50, 57, 64].map((y) => (
        <L key={y} x={8} y={y + 1} w={20} />
      ))}
      <rect x={38} y={20} width={110} height={76} rx={6} fill="var(--tp-surface)" />
      {[0, 1, 2, 3, 4, 5, 6, 7].map((i) => (
        <g key={i}>
          <L x={44} y={31 + i * 8} w={18} c={i < 3 ? 'var(--tp-strong)' : 'var(--tp-line)'} />
          <L x={66} y={31 + i * 8} w={60 - (i % 3) * 8} />
          <L x={136} y={31 + i * 8} w={8} />
        </g>
      ))}
      <rect x={38} y={24} width={110} height={1} fill="var(--tp-divider)" />
      <circle cx={154} cy={26} r={2} fill="var(--tp-line)" />
      <circle cx={154} cy={34} r={2} fill="var(--tp-line)" />
    </Frame>
  )
}

export function OfficePreview() {
  return (
    <Frame cls="tp-office" label={t('themes.previews.workspaceLayoutPreview')}>
      <path d={`M0 6a6 6 0 0 1 6-6h${W - 12}a6 6 0 0 1 6 6v6H0z`} fill="var(--tp-accent)" />
      <L x={56} y={4} w={48} h={4} r={1} c="var(--tp-on-accent)" />
      <rect x={0} y={12} width={W} height={11} fill="var(--tp-surface)" />
      {[4, 22, 38, 54, 70, 86].map((x, i) => (
        <g key={x}>
          <rect x={x} y={14.5} width={5} height={5} rx={1} fill={i === 0 ? 'var(--tp-accent)' : 'var(--tp-line)'} />
          <L x={x + 7} y={16} w={8} h={2} />
        </g>
      ))}
      <rect x={0} y={24} width={10} height={76} fill="var(--tp-rail)" />
      <rect x={10} y={24} width={32} height={76} fill="var(--tp-pane)" />
      <rect x={12} y={30} width={28} height={5} rx={1} fill="var(--tp-accent-soft)" />
      {[39, 45, 51, 57, 63, 69].map((y) => (
        <L key={y} x={15} y={y} w={18} h={2} />
      ))}
      <rect x={43} y={25} width={46} height={75} fill="var(--tp-surface)" />
      <L x={46} y={28} w={12} h={2} c="var(--tp-accent)" />
      <L x={62} y={28} w={10} h={2} />
      {[0, 1, 2, 3, 4, 5].map((i) => (
        <g key={i}>
          {i === 1 && <rect x={43} y={33 + i * 11} width={46} height={11} fill="var(--tp-accent-soft)" />}
          {i === 1 && <rect x={43} y={33 + i * 11} width={1.5} height={11} fill="var(--tp-accent)" />}
          <L x={47} y={35 + i * 11} w={20} h={2} c="var(--tp-strong)" />
          <L x={47} y={39.5 + i * 11} w={34} h={1.8} />
        </g>
      ))}
      <rect x={90} y={25} width={70} height={75} fill="var(--tp-surface)" />
      <rect x={90} y={25} width={30} height={7} fill="var(--tp-pane)" />
      <rect x={90} y={31} width={30} height={1.2} fill="var(--tp-accent)" />
      <L x={93} y={27.5} w={20} h={2} />
      <L x={123} y={27.5} w={20} h={2} />
      <L x={95} y={38} w={44} h={3.5} c="var(--tp-strong)" />
      <circle cx={98} cy={49} r={3.5} fill="var(--tp-line)" />
      <L x={104} y={47} w={26} h={2} c="var(--tp-strong)" />
      {[56, 61, 66, 71, 76].map((y, i) => (
        <L key={y} x={95} y={y} w={56 - i * 4} h={1.8} />
      ))}
    </Frame>
  )
}

export function FocusPreview() {
  return (
    <Frame cls="tp-focus" label={t('themes.previews.focusLayoutPreview')}>
      <L x={10} y={9} w={14} h={3} c="var(--tp-strong)" />
      <L x={60} y={9} w={10} h={2.5} c="var(--tp-strong)" />
      <rect x={60} y={13} width={10} height={1} fill="var(--tp-accent)" />
      <L x={75} y={9} w={10} h={2.5} />
      <L x={90} y={9} w={10} h={2.5} />
      <rect x={128} y={6} width={22} height={8} rx={2} fill="none" stroke="var(--tp-line)" strokeWidth={0.8} />
      <L x={132} y={9} w={14} h={2} />
      {[0, 1, 2, 3, 4, 5].map((i) => (
        <g key={i}>
          <circle cx={36} cy={28 + i * 12} r={1.4} fill={i < 2 ? 'var(--tp-accent)' : 'none'} />
          <L x={42} y={26.5 + i * 12} w={22} h={3} c={i < 2 ? 'var(--tp-strong)' : 'var(--tp-line)'} />
          <L x={70} y={26.5 + i * 12} w={46 - (i % 2) * 10} h={3} c="var(--tp-line)" />
          <L x={118} y={26.5 + i * 12} w={8} h={3} c="var(--tp-line)" />
        </g>
      ))}
      <L x={58} y={94} w={44} h={2} c="var(--tp-line)" />
    </Frame>
  )
}

export function DeskPreview() {
  return (
    <Frame cls="tp-desk" label={t('themes.previews.deskLayoutPreview')}>
      <rect x={0} y={0} width={W} height={12} rx={6} fill="var(--tp-pane)" />
      <rect x={0} y={6} width={W} height={6} fill="var(--tp-pane)" />
      {[4, 16, 28, 44, 56, 68, 84, 96].map((x) => (
        <rect key={x} x={x} y={3.5} width={8} height={5} rx={1} fill="var(--tp-line)" />
      ))}
      <rect x={118} y={3} width={38} height={6} rx={1} fill="var(--tp-surface)" stroke="var(--tp-divider)" strokeWidth={0.6} />
      <rect x={0} y={12} width={36} height={82} fill="var(--tp-pane)" />
      {[16, 22, 28, 34, 40, 46, 52, 58].map((y, i) => (
        <g key={y}>
          {i === 1 && <rect x={1} y={y - 1.5} width={34} height={5.5} fill="var(--tp-accent)" />}
          <L x={5} y={y} w={16} h={2} c={i === 1 ? 'var(--tp-on-accent)' : 'var(--tp-line)'} />
          {i < 4 && <L x={28} y={y} w={4} h={2} c={i === 1 ? 'var(--tp-on-accent)' : 'var(--tp-strong)'} />}
        </g>
      ))}
      <rect x={37} y={12} width={123} height={46} fill="var(--tp-surface)" />
      <rect x={37} y={12} width={123} height={5} fill="var(--tp-head)" />
      {[40, 48, 82, 136].map((x) => (
        <L key={x} x={x} y={13.5} w={x === 82 ? 20 : 6} h={1.8} c="var(--tp-strong)" />
      ))}
      {[0, 1, 2, 3, 4, 5, 6, 7].map((i) => (
        <g key={i}>
          {i === 2 && <rect x={37} y={18.5 + i * 4.8} width={123} height={4.8} fill="var(--tp-accent-soft)" />}
          {i % 2 === 1 && i !== 2 && <rect x={37} y={18.5 + i * 4.8} width={123} height={4.8} fill="var(--tp-stripe)" />}
          <L x={48} y={19.8 + i * 4.8} w={26} h={1.8} c={i < 3 ? 'var(--tp-strong)' : 'var(--tp-line)'} />
          <L x={82} y={19.8 + i * 4.8} w={44 - (i % 3) * 6} h={1.8} c={i < 3 ? 'var(--tp-strong)' : 'var(--tp-line)'} />
          <L x={136} y={19.8 + i * 4.8} w={18} h={1.8} />
        </g>
      ))}
      <rect x={37} y={58} width={123} height={1} fill="var(--tp-divider)" />
      <rect x={37} y={59} width={123} height={35} fill="var(--tp-surface)" />
      <L x={42} y={63} w={60} h={2.5} c="var(--tp-strong)" />
      <L x={42} y={68} w={36} h={1.8} />
      {[74, 78, 82, 86].map((y, i) => (
        <L key={y} x={42} y={y} w={100 - i * 12} h={1.6} />
      ))}
      <path d={`M0 94h${W}v0a6 6 0 0 1-6 6H6a6 6 0 0 1-6-6z`} fill="var(--tp-pane)" />
      <L x={4} y={96} w={30} h={1.6} />
      <L x={126} y={96} w={30} h={1.6} />
    </Frame>
  )
}

export function CardsPreview() {
  return (
    <Frame cls="tp-cards" label={t('themes.previews.cardsLayoutPreview')}>
      <rect x={4} y={6} width={14} height={14} rx={5} fill="var(--tp-accent-soft)" />
      {[28, 40, 52, 64].map((y, i) => (
        <g key={y}>
          {i === 0 && <rect x={4} y={y - 2} width={14} height={7} rx={3.5} fill="var(--tp-accent-soft)" />}
          <circle cx={11} cy={y + 1.5} r={2} fill={i === 0 ? 'var(--tp-accent)' : 'var(--tp-line)'} />
        </g>
      ))}
      <rect x={24} y={6} width={62} height={10} rx={5} fill="var(--tp-input)" />
      <L x={26} y={21} w={16} h={2.5} c="var(--tp-strong)" />
      {[0, 1, 2].map((i) => (
        <g key={i}>
          <rect x={24} y={26 + i * 16} width={62} height={14} rx={5} fill="var(--tp-surface)" />
          <circle cx={31} cy={33 + i * 16} r={4} fill={['var(--tp-a1)', 'var(--tp-a2)', 'var(--tp-a3)'][i]} />
          <L x={38} y={30 + i * 16} w={24} h={2.2} c="var(--tp-strong)" />
          <L x={38} y={35 + i * 16} w={40} h={1.8} />
        </g>
      ))}
      <rect x={24} y={75} width={62} height={20} rx={5} fill="var(--tp-accent-soft)" />
      <circle cx={31} cy={85} r={3.5} fill="var(--tp-a2)" />
      <circle cx={35} cy={85} r={3.5} fill="var(--tp-a3)" stroke="var(--tp-accent-soft)" strokeWidth={1} />
      <L x={42} y={83.5} w={30} h={2.2} c="var(--tp-strong)" />
      <rect x={90} y={6} width={66} height={89} rx={8} fill="var(--tp-surface)" />
      <L x={96} y={14} w={44} h={3.5} c="var(--tp-strong)" />
      <circle cx={100} cy={27} r={4} fill="var(--tp-a1)" />
      <L x={107} y={25.5} w={24} h={2.2} c="var(--tp-strong)" />
      {[37, 42, 47, 52, 57].map((y, i) => (
        <L key={y} x={96} y={y} w={52 - i * 5} h={1.8} />
      ))}
      <rect x={126} y={80} width={24} height={10} rx={4} fill="var(--tp-accent)" />
    </Frame>
  )
}
