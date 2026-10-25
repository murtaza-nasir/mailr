import { Mail, PenLine, X } from 'lucide-react'
import { useCallback, useEffect, useRef, type KeyboardEvent } from 'react'
import { useNavigate } from 'react-router'
import { useConversation } from '../../api/hooks'
import { ErrorBoundary } from '../../components/bits'
import { ComposeForm } from '../../features/compose/LazyComposeForm'
import { LazyConversationView as ConversationView } from '../../features/conversation/LazyConversationView'
import { useMailRoute } from '../../features/layout/mailboxes'
import { mailPath } from '../../lib/views'
import { useCompose, type ComposeWindow } from '../../stores/compose'
import { composeClosers, useOfficeTabs } from './tabs'
import { t as tr } from '../../i18n'
import { arrowStep } from '../../lib/rtl'

/**
 * Workspace's reading area: a tab strip over the reading pane. The routed conversation shows in a
 * preview tab that the next selection replaces; Ctrl/⌘-click, middle-click or double-click keeps
 * a conversation in its own tab. New messages, replies popped out and drafts open as tabs too.
 */
export function OfficeReader({ hidden }: { hidden?: boolean }) {
  const nav = useNavigate()
  const { view, convId, q, isMail } = useMailRoute()
  const pinned = useOfficeTabs((s) => s.pinned)
  const activeCompose = useOfficeTabs((s) => s.activeCompose)
  const titles = useOfficeTabs((s) => s.titles)
  const { unpin, setActiveCompose, retitle, pin } = useOfficeTabs.getState()
  const windows = useCompose((s) => s.windows)
  const conv = useConversation(isMail ? convId : null).data?.conversation
  const routedTitle = conv && conv.id === convId ? conv.subject || tr('common.noSubject') : null

  // A newly opened compose window becomes the active tab; a closed one hands back to the conversation.
  const seen = useRef<Set<string>>(new Set(windows.map((w) => w.key)))
  useEffect(() => {
    const fresh = windows.filter((w) => !seen.current.has(w.key))
    seen.current = new Set(windows.map((w) => w.key))
    if (fresh.length) setActiveCompose(fresh[fresh.length - 1].key)
    else if (activeCompose && !windows.some((w) => w.key === activeCompose)) setActiveCompose(null)
  }, [windows, activeCompose, setActiveCompose])

  // Selecting a conversation in the list shows it (and leaves any compose tab).
  useEffect(() => {
    if (convId) setActiveCompose(null)
  }, [convId, setActiveCompose])

  // Keep pinned titles current (subjects can change on first load).
  useEffect(() => {
    if (convId && routedTitle) retitle(convId, routedTitle)
  }, [convId, routedTitle, retitle])

  const composeActive = !!activeCompose && windows.some((w) => w.key === activeCompose)
  const previewOpen = !!convId && isMail && !pinned.some((t) => t.id === convId)

  const openConv = (id: number, v: string, tq: string) => {
    setActiveCompose(null)
    nav(mailPath(v, id, tq || undefined))
  }
  const closeConv = (id: number) => {
    const rest = pinned.filter((t) => t.id !== id)
    unpin(id)
    if (id === convId) {
      const next = rest[rest.length - 1]
      if (next) openConv(next.id, next.view, next.q)
      else nav(mailPath(view, null, q || undefined))
    }
  }
  const closePreview = () => nav(mailPath(view, null, q || undefined))
  const closeCompose = (key: string) => {
    const fn = composeClosers.get(key)
    if (fn) void fn()
    else useCompose.getState().close(key)
  }

  const tabKeys = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return
    const tabs = Array.from(e.currentTarget.querySelectorAll<HTMLElement>('[role="tab"]'))
    const i = tabs.indexOf(document.activeElement as HTMLElement)
    if (i < 0) return
    e.preventDefault()
    tabs[(i + arrowStep(e.key) + tabs.length) % tabs.length]?.focus()
  }

  const any = previewOpen || pinned.length > 0 || windows.length > 0

  return (
    <section className={`o-reader no-print${hidden ? ' hidden' : ''}`} aria-label={tr('themes.office.officeReader.readingPane')}>
      {any && (
        <div className="o-tabs" role="tablist" aria-label={tr('themes.office.officeReader.openItems')} onKeyDown={tabKeys}>
          {previewOpen && (
            <div className={`o-tab preview${!composeActive ? ' active' : ''}`} title={tr('themes.office.officeReader.previewDoubleClickToKeep')}>
              <button type="button" role="tab" aria-selected={!composeActive} className="o-tab-main" onClick={() => setActiveCompose(null)} onDoubleClick={() => conv && pin({ id: conv.id, title: routedTitle ?? '', view, q })}>
                <Mail />
                <span className="o-tab-title">{routedTitle ?? tr('common.loading2')}</span>
              </button>
              <button type="button" className="icon-btn xs" aria-label={tr('themes.office.officeReader.closePreview')} onClick={closePreview}>
                <X />
              </button>
            </div>
          )}
          {pinned.map((t) => {
            const active = !composeActive && t.id === convId
            return (
              <div key={t.id} className={`o-tab${active ? ' active' : ''}`}>
                <button type="button" role="tab" aria-selected={active} className="o-tab-main" onClick={() => openConv(t.id, t.view, t.q)} onAuxClick={(e) => e.button === 1 && closeConv(t.id)}>
                  <Mail />
                  <span className="o-tab-title">{t.title}</span>
                </button>
                <button type="button" className="icon-btn xs" aria-label={tr('themes.office.officeReader.close', { title: t.title })} onClick={() => closeConv(t.id)}>
                  <X />
                </button>
              </div>
            )
          })}
          {windows.map((w) => {
            const active = activeCompose === w.key
            const title = titles[w.key]?.trim() || w.payload.subject?.trim() || (w.payload.in_reply_to_message_id ? tr('common.reply') : tr('themes.office.officeReader.newMessage'))
            return (
              <div key={w.key} className={`o-tab compose${active ? ' active' : ''}`}>
                <button type="button" role="tab" aria-selected={active} className="o-tab-main" onClick={() => setActiveCompose(w.key)} onAuxClick={(e) => e.button === 1 && closeCompose(w.key)}>
                  <PenLine />
                  <span className="o-tab-title">{title}</span>
                </button>
                <button type="button" className="icon-btn xs" aria-label={tr('themes.office.officeReader.saveAndClose', { title })} data-tip={tr('themes.office.officeReader.saveClose')} onClick={() => closeCompose(w.key)}>
                  <X />
                </button>
              </div>
            )
          })}
        </div>
      )}
      <div className="o-panels">
        {!composeActive &&
          (convId && isMail ? (
            <ErrorBoundary key={convId}>
              <ConversationView id={convId} view={view} q={q} pane />
            </ErrorBoundary>
          ) : (
            <div className="reader-empty o-empty">
              <div>
                <Mail size={48} strokeWidth={1.25} />
                <p className="o-empty-title">{tr('themes.office.officeReader.chooseAConversationToRead')}</p>
                <p className="o-empty-hint">{tr('themes.office.officeReader.ctrlClickMiddleClickOr')}</p>
              </div>
            </div>
          ))}
        {windows.map((w) => (
          <ComposeTab key={w.key} win={w} active={composeActive && activeCompose === w.key} />
        ))}
      </div>
    </section>
  )
}

/** One compose window drawn as a tab page. Stays mounted while other tabs show. */
function ComposeTab({ win, active }: { win: ComposeWindow; active: boolean }) {
  const { update, close } = useCompose.getState()
  const setTitle = useOfficeTabs((s) => s.setTitle)
  const register = useCallback((fn: () => Promise<void>) => void composeClosers.set(win.key, fn), [win.key])
  const onSubject = useCallback((s: string) => setTitle(win.key, s), [win.key, setTitle])
  useEffect(() => () => void composeClosers.delete(win.key), [win.key])
  // A tab mounts hidden, so autofocus misses it: put the caret in when it is shown.
  const ref = useRef<HTMLDivElement>(null)
  const reply = !!win.payload.in_reply_to_message_id
  useEffect(() => {
    if (!active) return
    const t = setTimeout(() => {
      const el = ref.current
      if (!el || el.contains(document.activeElement)) return
      const target = el.querySelector<HTMLElement>(reply ? '.ProseMirror' : '.recips input') ?? el.querySelector<HTMLElement>('.ProseMirror')
      target?.focus()
    }, 0)
    return () => clearTimeout(t)
  }, [active, reply])
  return (
    <div ref={ref} className="o-compose" hidden={!active} role="tabpanel">
      <ErrorBoundary>
        <ComposeForm
          initial={win.payload}
          draftId={win.draftId}
          conversationId={win.conversationId}
          variant="window"
          assist={win.assist}
          autoFocusBody={!!win.payload.in_reply_to_message_id}
          onDraftId={(id) => update(win.key, { draftId: id })}
          onSubjectChange={onSubject}
          onClose={() => close(win.key)}
          registerClose={register}
          liveKey={win.key}
          localId={win.localId}
          restored={win.restored}
          initialVersion={win.version}
          onFocusRequest={() => useOfficeTabs.getState().setActiveCompose(win.key)}
        />
      </ErrorBoundary>
    </div>
  )
}
