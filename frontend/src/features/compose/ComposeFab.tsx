// The floating Compose button the shells draw over the mail list (phones, and Classic everywhere).
// On a phone it is the round icon button, so it never sits over a row's text; it carries its label
// only while the list shows no rows (an empty list has room for it).
import { Pencil } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useCompose } from '../../stores/compose'
import { t } from '../../i18n'

export function ComposeFab() {
  const [rows, setRows] = useState(true)
  useEffect(() => {
    // Whether a list on screen has rows, checked once per frame after the page changes.
    let frame = 0
    const look = () => {
      frame = 0
      setRows(!!document.querySelector('.list-scroll [role="row"]'))
    }
    const watch = new MutationObserver(() => {
      if (!frame) frame = requestAnimationFrame(look)
    })
    watch.observe(document.body, { childList: true, subtree: true })
    look()
    return () => {
      watch.disconnect()
      if (frame) cancelAnimationFrame(frame)
    }
  }, [])
  const cls = `compose-fab no-print${rows ? '' : ' roomy'}`
  return (
    <button type="button" className={cls} onClick={() => useCompose.getState().open()} aria-label={t('common.compose')} data-tip={rows ? t('common.composeC') : undefined}>
      <Pencil /> <span className="fab-label">{t('common.compose')}</span>
    </button>
  )
}
