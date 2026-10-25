// Settings → You → Region: the language. The account setting `language` holds
// the choice (null: follow the browser); this browser keeps a copy so the right words load before
// the first paint. A change saves at once and reloads the page, so every string changes together;
// the toast with Undo shows after the reload.
// ACT settings.region @web.settings

import { useQueryClient } from '@tanstack/react-query'
import { useEffect } from 'react'
import { api } from '../../api/client'
import { qk, useSettings } from '../../api/hooks'
import type { Me } from '../../api/types'
import { browserLanguage, language, languages, prepare, PSEUDO, PSEUDO_RTL, switchLanguage, syncLanguageSetting, t, wantedLanguage } from '../../i18n'
import { toastError } from '../../stores/ui'
import { SetRow } from './common'
import { savedToast } from './save'

const UNDO_KEY = 'mailr.language.undo'

// Names Intl writes differently from the way the language menus of the major apps do
// (Intl: "简体中文"); the same names as Android's per-app language list.
const NATIVE_NAMES: Record<string, string> = { 'zh-Hans': '中文（简体）' } // i18n-ignore: a language's own name

/** "Deutsch", "English", "Português (Brasil)": each language in its own words. */
export function languageName(code: string): string {
  if (code === PSEUDO) return t('settings.language.pseudo')
  if (code === PSEUDO_RTL) return t('settings.language.pseudoRtl')
  if (NATIVE_NAMES[code]) return NATIVE_NAMES[code]
  try {
    const n = new Intl.DisplayNames([code], { type: 'language' }).of(code)
    return n ? n[0].toLocaleUpperCase(code) + n.slice(1) : code
  } catch {
    return code
  }
}

function session(): Storage | null {
  try {
    return window.sessionStorage
  } catch {
    return null
  }
}

/** Save the language for the account, then show it (a reload when the words change). */
async function chooseLanguage(qc: ReturnType<typeof useQueryClient>, code: string | null, prev: string | null, isUndo: boolean): Promise<void> {
  try {
    await api.patch<unknown>('/me/settings', { language: code })
  } catch (e) {
    toastError(e, t('common.notSaved'))
    return
  }
  const me = qc.getQueryData<Me>(qk.me)
  if (me) qc.setQueryData<Me>(qk.me, { ...me, settings: { ...me.settings, language: code } })
  if (wantedLanguage(code) === language()) {
    // Same words (Automatic → the browser's own language): no reload.
    syncLanguageSetting(code)
    savedToast(isUndo ? t('common.changeUndone') : t('common.saved2'), isUndo ? undefined : () => void chooseLanguage(qc, prev, code, true))
    return
  }
  try {
    session()?.setItem(UNDO_KEY, JSON.stringify(isUndo ? { undone: true } : { prev }))
  } catch {
    /* ignore */
  }
  void switchLanguage(code)
}

/** The Language row of Settings → You → Region. */
export function LanguageRow() {
  const s = useSettings()
  const qc = useQueryClient()
  const value = s.language ?? ''
  return (
    <SetRow k={t('settings.language.label')} sub={t('settings.language.sub')}>
      <select className="select" style={{ maxWidth: 320 }} aria-label={t('settings.language.aria')} value={value} data-testid="language-select"
        onChange={(e) => void chooseLanguage(qc, e.target.value || null, s.language ?? null, false)}>
        <option value="">{t('settings.language.automatic', { language: languageName(browserLanguage()) })}</option>
        {languages().map((c) => <option key={c} value={c}>{languageName(c)}</option>)}
      </select>
    </SetRow>
  )
}

/** In the signed-in shell: follow the account's language on this browser (a reload when another
 *  device changed it), and show the Undo toast of a change that reloaded the page. */
export function useLanguageSync(): void {
  const s = useSettings()
  const qc = useQueryClient()
  const setting = s.language
  useEffect(() => {
    const raw = session()?.getItem(UNDO_KEY)
    if (!raw) return
    session()?.removeItem(UNDO_KEY)
    try {
      const note = JSON.parse(raw) as { prev?: string | null; undone?: boolean }
      if (note.undone) savedToast(t('common.changeUndone'))
      else savedToast(t('settings.language.changed'), () => void chooseLanguage(qc, note.prev ?? null, language(), true))
    } catch {
      /* ignore */
    }
  }, [qc])
  useEffect(() => {
    if (!syncLanguageSetting(setting)) return
    // Once per tab and language, so a language that cannot load never reloads in a loop.
    const mark = `mailr.language.synced:${wantedLanguage(setting)}`
    if (session()?.getItem(mark)) return
    session()?.setItem(mark, '1')
    void prepare(wantedLanguage(setting)).then(() => location.reload())
  }, [setting])
}
