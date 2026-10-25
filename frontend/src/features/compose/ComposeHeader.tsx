import type { Address, Identity } from '../../api/types'
import { RecipientField } from './RecipientField'
import { t } from '../../i18n'

interface Props {
  identities: Identity[]
  identity: Identity | undefined
  onIdentity: (id: number) => void
  to: Address[]
  cc: Address[]
  bcc: Address[]
  onTo: (v: Address[]) => void
  onCc: (v: Address[]) => void
  onBcc: (v: Address[]) => void
  showCc: boolean
  showBcc: boolean
  onShowCc: () => void
  onShowBcc: () => void
  /** Focus the To field when the form opens (a new window with no recipients yet). */
  autoFocusTo: boolean
  /** Enter in a recipient field with nothing typed: send. */
  onSubmit: () => void
  /** Only a compose window has a Subject field; an inline reply keeps the conversation's. */
  showSubject: boolean
  subject: string
  onSubject: (s: string) => void
}

/** The compose form's header: From (with several sending addresses), To, Cc, Bcc and Subject. */
export function ComposeHeader({ identities, identity, onIdentity, to, cc, bcc, onTo, onCc, onBcc, showCc, showBcc, onShowCc, onShowBcc, autoFocusTo, onSubmit, showSubject, subject, onSubject }: Props) {
  return (
    <>
      {identities.length > 1 && (
        <div className="compose-field">
          <span className="lbl">{t('common.from')}</span>
          <select className="plain" value={identity?.id ?? ''} onChange={(e) => onIdentity(Number(e.target.value))} aria-label={t('common.from')}>
            {identities.map((i) => (
              <option key={i.id} value={i.id}>
                {i.name ? `${i.name} <${i.email}>` : i.email}
              </option>
            ))}
          </select>
        </div>
      )}
      <RecipientField
        label={t('common.to')}
        value={to}
        onChange={onTo}
        autoFocus={autoFocusTo}
        onSubmit={onSubmit}
        trailing={
          <span className="cc-links">
            {!showCc && (
              // Out of the Tab path (To, then Subject, then the text); Ctrl + Shift + C adds Cc.
              <button type="button" tabIndex={-1} aria-keyshortcuts="Control+Shift+C" data-tip={t('compose.composeHeader.addCc')} onClick={(e) => { e.stopPropagation(); onShowCc() }}>
                {t('common.cc')}
              </button>
            )}
            {!showBcc && (
              <button type="button" tabIndex={-1} aria-keyshortcuts="Control+Shift+B" data-tip={t('compose.composeHeader.addBcc')} onClick={(e) => { e.stopPropagation(); onShowBcc() }}>
                {t('common.bcc')}
              </button>
            )}
          </span>
        }
      />
      {showCc && <RecipientField label={t('common.cc')} value={cc} onChange={onCc} autoFocus={cc.length === 0} onSubmit={onSubmit} />}
      {showBcc && <RecipientField label={t('common.bcc')} value={bcc} onChange={onBcc} autoFocus={bcc.length === 0} onSubmit={onSubmit} />}
      {showSubject && (
        <div className="compose-field">
          <input className="plain" dir="auto" value={subject} placeholder={t('common.subject')} aria-label={t('common.subject')} onChange={(e) => onSubject(e.target.value)} />
        </div>
      )}
    </>
  )
}
