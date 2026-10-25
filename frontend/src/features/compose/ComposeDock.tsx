import { Maximize2, Minimize2, Minus, X } from 'lucide-react'
import { useCallback, useRef, useState } from 'react'
import { useCompose, type ComposeWindow as Win } from '../../stores/compose'
import { ErrorBoundary } from '../../components/bits'
import { ComposeForm } from './LazyComposeForm'
import { t } from '../../i18n'

export function ComposeDock() {
  const windows = useCompose((s) => s.windows)
  const maximized = windows.find((w) => w.mode === 'maximized')
  return (
    <div className="compose-dock no-print" role="group" aria-label={t('compose.composeDock.composeWindows')}>
      {maximized && <div className="compose-max-scrim" onClick={() => useCompose.getState().setMode(maximized.key, 'normal')} />}
      {windows.map((w) => (
        <ComposeWindow key={w.key} win={w} />
      ))}
    </div>
  )
}

function ComposeWindow({ win }: { win: Win }) {
  const { update, close, setMode } = useCompose.getState()
  const [subject, setSubject] = useState(win.payload.subject)
  const closeFn = useRef<(() => Promise<void>) | null>(null)
  const register = useCallback((fn: () => Promise<void>) => {
    closeFn.current = fn
  }, [])
  const title = subject.trim() || (win.payload.in_reply_to_message_id ? t('common.reply') : t('compose.composeDock.newMessage'))
  const minimized = win.mode === 'minimized'

  return (
    <section className={`compose-win ${win.mode}`} role="dialog" aria-label={title}>
      <header className="compose-head" onClick={() => setMode(win.key, minimized ? 'normal' : 'minimized')}>
        <span className="title">{title}</span>
        <button type="button" className="icon-btn" aria-label={minimized ? t('common.restore') : t('compose.composeDock.minimize')} data-tip={minimized ? t('common.restore') : t('compose.composeDock.minimize')} onClick={(e) => { e.stopPropagation(); setMode(win.key, minimized ? 'normal' : 'minimized') }}>
          <Minus />
        </button>
        <button
          type="button"
          className="icon-btn"
          aria-label={win.mode === 'maximized' ? t('compose.composeDock.exitFullScreen') : t('compose.composeDock.fullScreen')}
          data-tip={win.mode === 'maximized' ? t('compose.composeDock.exitFullScreen') : t('compose.composeDock.fullScreen')}
          onClick={(e) => {
            e.stopPropagation()
            setMode(win.key, win.mode === 'maximized' ? 'normal' : 'maximized')
          }}
        >
          {win.mode === 'maximized' ? <Minimize2 /> : <Maximize2 />}
        </button>
        <button
          type="button"
          className="icon-btn"
          aria-label={t('compose.composeDock.saveClose')}
          data-tip={t('compose.composeDock.saveClose')}
          onClick={(e) => {
            e.stopPropagation()
            if (closeFn.current) void closeFn.current()
            else close(win.key)
          }}
        >
          <X />
        </button>
      </header>
      <div style={{ display: minimized ? 'none' : 'flex', flex: 1, minHeight: 0, flexDirection: 'column' }}>
        <ErrorBoundary>
          <ComposeForm
            initial={win.payload}
            draftId={win.draftId}
            conversationId={win.conversationId}
            variant="window"
            assist={win.assist}
            autoFocusBody={!!win.payload.in_reply_to_message_id}
            onDraftId={(id) => update(win.key, { draftId: id })}
            onSubjectChange={setSubject}
            onClose={() => close(win.key)}
            registerClose={register}
            liveKey={win.key}
            localId={win.localId}
            restored={win.restored}
            initialVersion={win.version}
            onFocusRequest={() => setMode(win.key, win.mode === 'minimized' ? 'normal' : win.mode)}
          />
        </ErrorBoundary>
      </div>
    </section>
  )
}
