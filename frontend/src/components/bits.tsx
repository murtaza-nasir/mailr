import { X } from 'lucide-react'
import { Component, Fragment, useState, type CSSProperties, type ErrorInfo, type ReactNode } from 'react'
import type { Address, Label, Participant } from '../api/types'
import { avatarColor, displayName, initials, labelLeaf } from '../lib/format'
import { chipStyle } from '../lib/labels'
import { avatarUrl, knownMissing, markMissing, useLocalPicture, useSenderPictures } from '../lib/avatars'
import { reloadApp, reloadForChunkError } from '../lib/appUpdate'
import { isChunkLoadError } from '../lib/netErrors'
import { t } from '../i18n'

export function Avatar({ who, size = 'md', src }: { who: Address | Participant | null | undefined; size?: 'md' | 'sm' | 'xs'; src?: string | null }) {
  const key = who?.email ?? '?'
  const lookups = useSenderPictures()
  // A user of this server (you, your family): their picture, whether or not lookups are on.
  const local = useLocalPicture(src ? null : who?.email)
  const url = src ?? (who?.email && local ? `${avatarUrl(who.email)}?v=${local}` : lookups && who?.email ? avatarUrl(who.email) : null)
  const [broken, setBroken] = useState<string | null>(null)
  const show = !!url && broken !== url && !knownMissing(url)
  return (
    <span className={`avatar ${size === 'md' ? '' : size}`} style={{ background: avatarColor(key) }} aria-hidden="true" title={who ? displayName(who) : undefined}>
      {initials(who)}
      {show && (
        <img
          src={url}
          alt=""
          loading="lazy"
          decoding="async"
          referrerPolicy="no-referrer"
          onError={() => {
            markMissing(url)
            setBroken(url)
          }}
        />
      )}
    </span>
  )
}

export function LabelChip({ label, onRemove, large, full }: { label: Label; onRemove?: () => void; large?: boolean; full?: boolean }) {
  return (
    <span className={`label-chip${large ? ' lg' : ''}`} dir="auto" style={chipStyle(label.color)} title={label.name}>
      {full ? label.name : labelLeaf(label.name)}
      {onRemove && (
        <button
          type="button"
          aria-label={t('components.bits.removeLabel', { labelName: label.name })}
          onClick={(e) => {
            e.stopPropagation()
            onRemove()
          }}
        >
          <X />
        </button>
      )}
    </span>
  )
}

export function Skeleton({ width, height = 14, style }: { width?: number | string; height?: number; style?: CSSProperties }) {
  return <span className="skeleton" style={{ width, height, ...style }} />
}

export function Spinner({ small }: { small?: boolean }) {
  return <span className={`spinner${small ? ' sm' : ''}`} role="progressbar" aria-label={t('common.loading')} />
}

export function Switch({ checked, onChange, label, disabled }: { checked: boolean; onChange: (v: boolean) => void; label: string; disabled?: boolean }) {
  return (
    <button type="button" role="switch" className="switch" aria-checked={checked} aria-label={label} disabled={disabled} onClick={() => onChange(!checked)} />
  )
}

/** A list that could not be loaded: say so, with Retry, never "nothing here", which made
 *  people recreate filters they still had. Use it before a list's empty state. */
export function LoadFailed({ what, q }: { what: string; q: { refetch: () => unknown; isFetching?: boolean } }) {
  return (
    <div className="load-failed" role="alert" data-testid="load-failed">
      <span>{t('components.bits.couldntLoad', { what })}</span>
      <button type="button" className="btn text sm" disabled={q.isFetching} onClick={() => void q.refetch()}>
        {t('common.retry')}
      </button>
    </div>
  )
}

/** True when a query failed and has nothing to show (a failed refetch keeps the old list). */
export const failedEmpty = (q: { isError: boolean; data: unknown }) => q.isError && q.data === undefined

export function EmptyState({ icon, title, children }: { icon?: ReactNode; title: string; children?: ReactNode }) {
  return (
    <div className="empty">
      {icon}
      <h3>{title}</h3>
      {children}
    </div>
  )
}

/** The importance marker: a small chevron tag, filled when the conversation is important. */
export function ImportanceMarker({ on, onToggle, title }: { on: boolean; onToggle?: () => void; title?: string }) {
  const tip = title ?? (on ? t('components.bits.importantClickToMarkAs') : t('components.bits.clickToTeachMailrThis'))
  return (
    <button
      type="button"
      className={`importance${on ? ' on' : ''}`}
      aria-pressed={on}
      aria-label={on ? t('components.bits.markedImportant') : t('common.markImportant')}
      data-tip={tip}
      onClick={(e) => {
        e.stopPropagation()
        onToggle?.()
      }}
    >
      <svg viewBox="0 0 18 12" aria-hidden="true">
        <path d="M1 1h10.5L16.5 6 11.5 11H1l4.5-5z" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round" />
      </svg>
    </button>
  )
}

/** The mailr mark: stack-check B. */
/** The mailr mark: the paper-fold M.
 *  One fixed colour in every theme, paired with Speakr's apricot S. */
export function Logo({ size = 24 }: { size?: number }) {
  return <img className="logo-mark" src="/icon-64.png" srcSet="/icon-64.png 1x, /icon-128.png 2x" alt="" aria-hidden="true"
    draggable={false} width={size} height={size} />
}

interface EBState {
  error: Error | null
}

export class ErrorBoundary extends Component<{ children: ReactNode; fallback?: ReactNode }, EBState> {
  state: EBState = { error: null }
  static getDerivedStateFromError(error: Error): EBState {
    return { error }
  }
  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('UI error', error, info.componentStack)
    // A lazy chunk from an older build is gone after a deploy: reload once.
    if (isChunkLoadError(error)) reloadForChunkError()
  }
  render() {
    if (this.state.error && isChunkLoadError(this.state.error)) {
      return (
        <div className="empty" role="alert">
          <h3>{t('components.bits.aNewVersionOfMailr')}</h3>
          <p style={{ maxWidth: 480 }}>{t('components.bits.thisPartOfMailrChanged')}</p>
          <button type="button" className="btn filled" onClick={reloadApp}>
            {t('components.bits.reload')}
          </button>
        </div>
      )
    }
    if (this.state.error) {
      return (
        this.props.fallback ?? (
          <div className="empty" role="alert">
            <h3>{t('components.bits.somethingWentWrong')}</h3>
            <p style={{ maxWidth: 480 }}>{this.state.error.message}</p>
            <button type="button" className="btn tonal" onClick={() => this.setState({ error: null })}>
              {t('common.tryAgain')}
            </button>
          </div>
        )
      )
    }
    return this.props.children
  }
}

type ItemKind = 'event' | 'task' | 'invite' | 'items'
const ITEM_TEXT: Record<ItemKind, () => string> = {
  event: () => t('components.bits.couldntShowEvent'),
  task: () => t('components.bits.couldntShowTask'),
  invite: () => t('components.bits.couldntShowInvite'),
  items: () => t('components.bits.couldntShowItems'),
}

/**
 * Contains a failure to one record (9 Oct 2026: one event with a zone the browser refused took
 * the whole screen down). The fallback is one small line, "Couldn't show this event", with the
 * error in its tooltip; the error and the component stack go to the console. `compact` is for
 * chips and rows (no buttons); otherwise "Try again" and, with `onClose`, "Close" are offered.
 * Give a `key` per record so the next record starts clean.
 */
export class ItemBoundary extends Component<{ children: ReactNode; what: ItemKind; compact?: boolean; onClose?: () => void }, EBState> {
  state: EBState = { error: null }
  static getDerivedStateFromError(error: Error): EBState {
    return { error }
  }
  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error(`Couldn't show this ${this.props.what}`, error, info.componentStack) // i18n-ignore: console only
    if (isChunkLoadError(error)) reloadForChunkError()
  }
  render() {
    const { error } = this.state
    if (!error) return this.props.children
    const text = ITEM_TEXT[this.props.what]()
    if (this.props.compact) {
      return <span className="item-error compact" role="alert" title={error.message} data-testid="item-error">{text}</span>
    }
    return (
      <div className="item-error" role="alert" data-testid="item-error">
        <span title={error.message}>{text}</span>
        <button type="button" className="btn text sm" onClick={() => this.setState({ error: null })}>{t('common.tryAgain')}</button>
        {this.props.onClose && <button type="button" className="btn text sm" onClick={this.props.onClose}>{t('common.close')}</button>}
      </div>
    )
  }
}

/** A list of names or addresses joined into one line, each in its own direction (an Arabic name
 *  beside an English one keeps its commas in place). */
export function BidiList({ items, sep = ', ' }: { items: string[]; sep?: string }) {
  return <>{items.map((x, i) => <Fragment key={i}>{i > 0 && sep}<bdi>{x}</bdi></Fragment>)}</>
}
