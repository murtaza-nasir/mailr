// mail.translate in the reader: the message's text in English, in place of the original, with
// "Show original". Offered from the message menu, and as a bar when the server says so (offer_translate:
// the body is in a language the user did not list as their own, core/langid.py).
// ACT mail.translate @web.reader

import { useMutation } from '@tanstack/react-query'
import { Languages } from 'lucide-react'
import { api } from '../../api/client'
import { Spinner } from '../../components/bits'
import { Markdown } from '../rail/assistant/LazyMarkdown'
import { t as tr } from '../../i18n'

export interface TranslationResult {
  status: 'ready' | 'unavailable' | 'empty'
  source: string | null
  to: string
  text: string
  reason?: string
  truncated?: boolean
}

export function useTranslate(messageId: number) {
  return useMutation({
    mutationKey: ['translate', messageId],
    mutationFn: () => api.post<TranslationResult>(`/messages/${messageId}/translate`, { to: 'English' }),
  })
}

/** The bar that offers a translation, or shows its state and the way back to the original. */
export function TranslateBar({ t, showing, offer, onShow }: {
  t: ReturnType<typeof useTranslate>; showing: boolean; offer: boolean; onShow: (on: boolean) => void
}) {
  const r = t.data
  if (t.isPending) return <div className="translate-bar" role="status"><Spinner small />{' '}{tr('conversation.translate.translating')}</div>
  if (t.isError) return <div className="translate-bar error" role="alert">{tr('conversation.translate.couldNotTranslateThisMessage')}{' '}<button type="button" className="btn text sm" onClick={() => t.mutate()}>{tr('common.tryAgain')}</button></div>
  if (r && r.status !== 'ready') return <div className="translate-bar" role="status">{r.reason ?? tr('conversation.translate.nothingToTranslate')}</div>
  if (r && showing) {
    return (
      <div className="translate-bar" role="status">
        <Languages size={16} />
        <span>{tr('conversation.translate.translatedTo', { value: r.source ? tr('conversation.translate.from', { source: r.source }) : '', to: r.to, value2: r.truncated ? tr('conversation.translate.theFirstPart') : '' })}</span>
        <button type="button" className="btn text sm" onClick={() => onShow(false)}>{tr('conversation.translate.showOriginal')}</button>
      </div>
    )
  }
  if (r) {
    return (
      <div className="translate-bar">
        <Languages size={16} />
        <button type="button" className="btn text sm" onClick={() => onShow(true)}>{tr('conversation.translate.showTranslation')}</button>
      </div>
    )
  }
  if (!offer) return null
  return (
    <div className="translate-bar">
      <Languages size={16} />
      <span>{tr('conversation.translate.thisMessageMayNotBe')}</span>
      <button type="button" className="btn text sm" onClick={() => { onShow(true); t.mutate() }}>{tr('conversation.translate.translateToEnglish')}</button>
    </div>
  )
}

export function TranslatedText({ r }: { r: TranslationResult }) {
  return <div className="translated-text"><Markdown text={r.text} /></div>
}
