// The calendars' visibility while a change is being saved. The person's last choice for each
// calendar is held here from the click until its save has settled and a refetch that started after
// it has landed, and it overrides the server's copy in that time. A refetch that read the old state,
// a save still on its way, or this device's own copy can no longer flip the checkbox back.
// The saves of one calendar go one after another, so the server ends on the last choice.

interface Intent {
  hidden: boolean
  seq: number
}

export class VisibilityIntents {
  private intents = new Map<string, Intent>()
  private chains = new Map<string, Promise<void>>()
  private seq = 0
  private snapshot: ReadonlyMap<string, boolean> = new Map()
  private listeners = new Set<() => void>()

  subscribe = (l: () => void) => {
    this.listeners.add(l)
    return () => {
      this.listeners.delete(l)
    }
  }

  /** The pending choices by calendar URL (hidden or shown); a new object after each change. */
  getSnapshot = (): ReadonlyMap<string, boolean> => this.snapshot

  private publish() {
    this.snapshot = new Map([...this.intents].map(([url, i]) => [url, i.hidden]))
    for (const l of this.listeners) l()
  }

  /**
   * Record a choice at once (the checkbox shows it in the same event) and queue its save behind
   * any earlier save of the same calendar. A save that a newer choice overtook while it waited is
   * not sent. Resolves once this choice is saved (or skipped). `refresh` refetches the calendars;
   * the choice is let go when it resolves, unless a newer choice came in the meantime. A save
   * also waits for `after` when given (Show only: the other calendars are hidden first).
   */
  set(url: string, hidden: boolean, save: () => Promise<void>, refresh: () => Promise<unknown>, after?: Promise<unknown>): Promise<void> {
    const seq = ++this.seq
    this.intents.set(url, { hidden, seq })
    this.publish()
    const latest = () => this.intents.get(url)?.seq === seq
    const prev = Promise.all([this.chains.get(url), after?.catch(() => undefined)])
    const saved = prev.then(async () => {
      if (!latest()) return false
      await save().catch(() => undefined)
      return true
    })
    const chain = saved.then(() => undefined)
    this.chains.set(url, chain)
    void chain.then(() => {
      if (this.chains.get(url) === chain) this.chains.delete(url)
    })
    void saved.then(async (sent) => {
      if (!sent || !latest()) return
      await refresh().catch(() => undefined)
      if (!latest()) return
      this.intents.delete(url)
      this.publish()
    })
    return chain
  }
}

/** The hidden calendars by URL: this device's list, the server's flags, and the pending choices over both. */
export function hiddenCalendars(collections: readonly { url?: string | null; id: string; hidden?: boolean }[], hiddenLocal: readonly string[], pending: ReadonlyMap<string, boolean>): Set<string> {
  return effectiveHidden([...hiddenLocal, ...collections.filter((c) => c.hidden).map((c) => c.url ?? c.id)], pending)
}

/** The hidden calendars on screen: the stored ones, with each pending choice in place of its calendar's. */
export function effectiveHidden(stored: Iterable<string>, pending: ReadonlyMap<string, boolean>): Set<string> {
  const out = new Set(stored)
  for (const [url, hidden] of pending) {
    if (hidden) out.add(url)
    else out.delete(url)
  }
  return out
}

/** The one set of pending choices in this tab (the left column and the "all hidden" banner share it). */
export const visibilityIntents = new VisibilityIntents()
