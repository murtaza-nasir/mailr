// "Show only this section" (MailList.tsx focus): kept per tab for the browser session. Going to the
// Inbox from the navigation (the sidebar's Inbox, its shortcut) starts again from all sections.

const PREFIX = 'mailr.inbox.focus.'
export const FOCUS_RESET = 'mailr:inbox-focus-reset'

export function focusKey(layout: string | undefined, tab: string | undefined): string {
  return `${PREFIX}${layout ?? ''}.${tab ?? ''}`
}

/** Every tab back to all of its sections. */
export function resetInboxFocus(): void {
  try {
    for (let i = sessionStorage.length - 1; i >= 0; i--) {
      const k = sessionStorage.key(i)
      if (k?.startsWith(PREFIX)) sessionStorage.removeItem(k)
    }
  } catch {
    /* private mode */
  }
  window.dispatchEvent(new Event(FOCUS_RESET))
}
