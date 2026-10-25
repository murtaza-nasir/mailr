// ACT mail.archive, ACT mail.delete, ACT mail.delete_forever, ACT mail.important, ACT mail.mute, ACT mail.spam, ACT mail.not_spam, ACT mail.read, ACT mail.unread, ACT mail.star (the keyboard shortcuts)
import { useEffect, useRef } from 'react'
import { resetInboxFocus } from '../mail/inboxFocus'
import { convFromSlug } from '../../lib/pubid'
import { useNavigate } from 'react-router'
import { useQueryClient } from '@tanstack/react-query'
import { useActions } from '../../api/hooks'
import { currentFavorites } from '../layout/favorites'
import { useThemedSettings, useThemeId } from '../../themes/useUITheme'
import { favoritesForTheme } from '../../lib/favorites'
import type { ActionName } from '../../api/types'
import {
  feedKey, isAppShortcut, isTypingTarget, keyBelongsToTarget, keyStaysInSurface, scopeForPath, shadowedGlobal, shortcutHandler,
  type MatchState, type ShortcutAction,
} from '../../lib/shortcuts'
import { appHome } from '../../lib/routes'
import { openAskFromScreen } from '../ask/askStore'
import { openPaletteWith } from '../../themes/shared/paletteStore'
import { mailPath, baseView } from '../../lib/views'
import { useCompose } from '../../stores/compose'
import { useUI } from '../../stores/ui'
import { summariesFor } from '../mail/commands'
import { archiveKeyAction } from '../../lib/rowArchive'
import { qk } from '../../api/hooks'
import type { Label } from '../../api/types'
import { t } from '../../i18n'

function routeInfo(pathname: string, search: string) {
  const m = /^\/mail\/([^/]+)(?:\/([0-9A-Za-z]+))?/.exec(pathname)
  const q = new URLSearchParams(search).get('q') ?? ''
  return { view: m ? decodeURIComponent(m[1]) : 'inbox', convId: convFromSlug(m?.[2]), q }
}

export function useGlobalShortcuts() {
  // The reading pane as the active theme applies it (Office always shows one, Focus never).
  const settings = useThemedSettings()
  const nav = useNavigate()
  const { run } = useActions()
  const qc = useQueryClient()
  const themeId = useRef('classic')
  themeId.current = useThemeId()
  const state = useRef<MatchState>({ pending: [] })
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented) return
      if (isTypingTarget(e.target)) return
      // A focused link, button or other control keeps its own keys: Enter and Space activate it,
      // Tab moves on, arrows move within it (lib/shortcuts keyBelongsToTarget).
      if (keyBelongsToTarget(e.target, e)) return
      // In a compose window, Ambrose's chat or a non-modal dialog, letters are never app keys.
      if (keyStaysInSurface(e.target, e)) return
      // Leave keys alone while a dialog or menu is open (they handle their own keys).
      // A dialog, a modal drawer, a menu or the ask card has the keys. (Other aria-modal
      // surfaces, such as the event detail and the palette, take app keys on purpose.)
      if (document.querySelector('.scrim, .popover, .ask-card, [data-modal-drawer]')) return
      const ui = useUI.getState()
      if (!settings.keyboard_shortcuts && e.key !== 'Escape') return
      // Each app has its own keys (lib/shortcuts scopes); Settings has only the global ones.
      const scope = scopeForPath(window.location.pathname)
      const r = feedKey(state.current, e, scope)
      state.current = r.state
      if (timer.current) clearTimeout(timer.current)
      if (r.state.pending.length) timer.current = setTimeout(() => (state.current = { pending: [] }), 1500)
      if (!r.action) {
        if (r.handled) e.preventDefault()
        return
      }
      if (isAppShortcut(r.action)) {
        // The calendar and tasks apps run their own keys (useAppShortcuts). Without a handler
        // the key falls back to the global binding it shadows, else to the browser.
        const h = shortcutHandler(scope, r.action)
        if (h) {
          if (h(r.arg) !== false) e.preventDefault()
          return
        }
        const g = shadowedGlobal(r.keys)
        if (!g || isAppShortcut(g.action)) return
        e.preventDefault()
        handle(g.action, ui)
        return
      }
      // false: nothing to act on, so the key stays the browser's (Enter with no row to open).
      if (handle(r.action, ui, r.arg) !== false) e.preventDefault()
    }

    const handle = (action: ShortcutAction, ui: ReturnType<typeof useUI.getState>, arg?: string): boolean | void => {
      // Read the live URL: router state can lag a fast key sequence (u then j).
      const { view, convId, q } = routeInfo(window.location.pathname, window.location.search)
      const ids = ui.visibleIds
      // The keyboard cursor counts only while its row is in the list (a removed row's id must
      // never be acted on again).
      const cur = convId ?? (ui.focusedId != null && ids.includes(ui.focusedId) ? ui.focusedId : null)
      const targets = ui.selected.size ? Array.from(ui.selected) : cur ? [cur] : []
      const idx = cur ? ids.indexOf(cur) : -1
      const goTo = (id: number | undefined) => {
        if (!id) return
        if (convId) nav(mailPath(view, id, q || undefined))
        else ui.setFocused(id)
      }
      const act = (a: ActionName, leaves = false) => {
        if (!targets.length) return
        const next = idx >= 0 ? (ids[idx + 1] ?? ids[idx - 1]) : undefined
        const from = a === 'archive' ? useUI.getState().contextLabelId : null
        void run({ ids: targets, action: a, ...(from ? { from_label_id: from } : {}) }, {
          silent: a === 'star' || a === 'unstar',
          onDone: () => {
            ui.clearSelection()
            // In the list, keep the cursor moving: the next row takes the removed row's place.
            if (leaves && !convId && cur && targets.includes(cur) && next) useUI.getState().setFocused(next)
            // An open conversation that left the view: follow the auto-advance setting like the
            // toolbar buttons do (default: back to the list; or on to the newer/older one).
            if (leaves && convId && !useUI.getState().paneVisible) {
              const mode = settings.auto_advance ?? 'list'
              const after = mode === 'older' ? ids[idx + 1] : mode === 'newer' ? ids[idx - 1] : undefined
              nav(mailPath(view, idx >= 0 && after ? after : null, q || undefined))
            }
          },
        })
      }
      // e, [ and ]: what the row's button shows (lib/rowArchive.ts), so a row outside the Inbox
      // moves back to it instead of answering "Nothing to archive".
      const archiveAction = (on: number[]) => archiveKeyAction(summariesFor(qc, on), view,
        new Map((qc.getQueryData<Label[]>(qk.labels) ?? []).map((l) => [l.id, l])),
        { contextLabelId: useUI.getState().contextLabelId, removesCurrent: settings.archive_removes_current_label ?? true })
      const selectWhere = (fn: (id: number) => boolean) => ui.setSelected(new Set(ids.filter(fn)))

      switch (action) {
        case 'compose':
          useCompose.getState().open()
          break
        case 'search':
          window.dispatchEvent(new Event('mailr:focus-search'))
          break
        case 'older':
          goTo(idx < 0 ? ids[0] : ids[idx + 1])
          break
        case 'newer':
          goTo(idx < 0 ? ids[0] : ids[idx - 1])
          break
        case 'open':
          if (convId || !ui.focusedId || !ids.includes(ui.focusedId)) return false
          nav(mailPath(view, ui.focusedId, q || undefined))
          break
        case 'backToList':
          if (convId) nav(mailPath(view, null, q || undefined))
          break
        case 'archive': {
          // ACT mail.move: on rows outside the Inbox, e moves them back (Move to Inbox).
          const a = archiveAction(targets)
          act(a, a === 'archive' || baseView(view) === 'archive')
          break
        }
        case 'delete':
          act(baseView(view) === 'trash' ? 'delete_forever' : 'trash', true)
          break
        case 'spam':
          act(baseView(view) === 'spam' ? 'not_spam' : 'spam', true)
          break
        case 'markRead':
          act('read')
          break
        case 'markUnread':
          act('unread', true)
          break
        case 'star': {
          // Read the star from the cached conversations (as the toolbar's Star does): the visible
          // rows' copy is not refreshed while a conversation is open without the list, so a second
          // press sent "star" again and nothing changed.
          const items = summariesFor(qc, targets)
          const anyUnstarred = !items.length || items.some((c) => !c.starred)
          act(anyUnstarred ? 'star' : 'unstar')
          break
        }
        case 'markImportant':
          act('important')
          break
        case 'markNotImportant':
          act('not_important')
          break
        case 'mute':
          act('mute', true)
          break
        case 'select':
          if (ui.focusedId) ui.toggleSelected(ui.focusedId)
          break
        case 'label':
        case 'move':
        case 'snooze':
          if (convId && settings.reading_pane === 'none') {
            window.dispatchEvent(new CustomEvent('mailr:conv-menu', { detail: action }))
          } else if (targets.length) {
            if (!ui.selected.size && cur) ui.setSelected(new Set([cur]))
            ui.setShortcutMenu(action)
          }
          break
        case 'createFrom':
          // The open conversation's Create from email menu.
          if (convId) window.dispatchEvent(new CustomEvent('mailr:conv-menu', { detail: 'create' }))
          break
        case 'reply':
          window.dispatchEvent(new CustomEvent('mailr:reply', { detail: 'reply' }))
          break
        case 'replyAll':
          window.dispatchEvent(new CustomEvent('mailr:reply', { detail: 'reply_all' }))
          break
        case 'forward':
          window.dispatchEvent(new CustomEvent('mailr:reply', { detail: 'forward' }))
          break
        case 'nextMessage':
        case 'prevMessage':
          window.dispatchEvent(new CustomEvent('mailr:message-nav', { detail: action }))
          break
        case 'undo': {
          // z replaces the toast that offered this Undo, as its button does.
          const fn = ui.lastUndo
          if (!fn) break
          for (const tt of ui.toasts) if (tt.onAction === fn) ui.dismissToast(tt.id)
          fn()
          break
        }
        case 'help':
          ui.setHelp(true)
          break
        case 'escape':
          if (ui.helpOpen) ui.setHelp(false)
          else if (ui.selected.size) ui.clearSelection()
          else if (convId && settings.reading_pane !== 'none') nav(mailPath(view, null, q || undefined))
          break
        case 'goInbox':
          resetInboxFocus() // the Inbox from the keyboard: all sections again (features/mail/inboxFocus.ts)
          nav(mailPath('inbox'))
          break
        case 'goStarred':
          nav(mailPath('starred'))
          break
        case 'goSent':
          nav(mailPath('sent'))
          break
        case 'goDrafts':
          nav(mailPath('drafts'))
          break
        case 'goAll':
          nav(mailPath('all'))
          break
        case 'goSnoozed':
          nav(mailPath('snoozed'))
          break
        case 'goImportant':
          nav(mailPath('important'))
          break
        case 'goFavorite': {
          // g 1 … g 9: the favorites in the order the theme draws them (Classic
          // leaves out the folders it shows below Favorites).
          const view = favoritesForTheme(currentFavorites(qc), themeId.current)[Number(arg) - 1]
          if (view) nav(mailPath(view))
          break
        }
        case 'goTasks':
          nav(appHome('tasks'))
          break
        case 'goCalendar':
          nav(appHome('calendar'))
          break
        case 'goNotes':
          nav(appHome('notes'))
          break
        case 'goRecordings':
          // Without a Speakr account the app shows how to connect one.
          nav(appHome('recordings'))
          break
        case 'selectAll':
          selectWhere(() => true)
          break
        case 'selectNone':
          ui.clearSelection()
          ui.setAllMatching(null)
          break
        case 'selectSection':
          // The mail list knows the sections (MailList, mailr:select-section).
          window.dispatchEvent(new Event('mailr:select-section'))
          break
        case 'selectRead':
          selectWhere((id) => !ui.visibleMeta[id]?.unread)
          break
        case 'selectUnread':
          selectWhere((id) => !!ui.visibleMeta[id]?.unread)
          break
        case 'selectStarred':
          selectWhere((id) => !!ui.visibleMeta[id]?.starred)
          break
        case 'selectUnstarred':
          selectWhere((id) => !ui.visibleMeta[id]?.starred)
          break
        case 'archivePrev':
        case 'archiveNext': {
          if (!convId) break
          const other = action === 'archiveNext' ? ids[idx + 1] : ids[idx - 1]
          const a = archiveAction([convId])
          const from = a === 'archive' ? useUI.getState().contextLabelId : null
          void run({ ids: [convId], action: a, ...(from ? { from_label_id: from } : {}) },
            { onDone: () => nav(mailPath(view, other ?? null, q || undefined)) })
          break
        }
        case 'settings':
          nav('/settings')
          break
        case 'toggleAgent':
          ui.setPanel(ui.panel === 'agent' ? null : 'agent')
          break
        case 'rerunAssistant':
          if (ui.panel === 'agent') window.dispatchEvent(new Event('mailr:assistant-rerun'))
          break
        case 'quickNote':
          // ACT note.quick @web.palette (Shift+N): the palette on "New note: ".
          openPaletteWith(t('shortcuts.useGlobalShortcuts.newNote'))
          break
        case 'askAbout':
          // ACT agent.ask_about @web.ask (Shift+A): the ask capture about what is open.
          openAskFromScreen()
          break
        case 'send':
          break
      }
    }

    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('keydown', onKey)
      if (timer.current) clearTimeout(timer.current)
    }
  }, [settings.keyboard_shortcuts, settings.reading_pane, settings.auto_advance, settings.archive_removes_current_label, nav, run, qc])
}
