import { describe, expect, it } from 'vitest'
import { effectiveHidden, hiddenCalendars, VisibilityIntents } from './visibilityIntents'

/** A promise the test settles by hand. */
function gate<T = void>() {
  let open!: (v: T) => void
  const p = new Promise<T>((r) => (open = r))
  return { p, open }
}
const tick = () => new Promise((r) => setTimeout(r, 0))

describe('calendar visibility intents (calendar-views e2e race, 10 Oct 2026)', () => {
  it('shows the choice at once and keeps it over a stale refetch until its own refetch lands', async () => {
    const v = new VisibilityIntents()
    let server = true // the server still says hidden
    const shown = () => effectiveHidden(server ? ['p'] : [], v.getSnapshot()).has('p')
    const save = gate()
    const refetch = gate()
    void v.set('p', false, () => save.p.then(() => { server = false }), () => refetch.p)
    // The same event: the box is checked before anything comes back.
    expect(shown()).toBe(false)
    // A refetch from the last toggle lands with the old state: the box stays as clicked.
    server = true
    expect(shown()).toBe(false)
    save.open()
    await tick()
    expect(v.getSnapshot().get('p')).toBe(false)
    refetch.open()
    await tick()
    expect(v.getSnapshot().size).toBe(0)
    expect(shown()).toBe(false)
  })

  it('saves one calendar in click order, skips a save a newer click overtook, and the last click wins', async () => {
    const v = new VisibilityIntents()
    const sent: boolean[] = []
    const first = gate()
    const saveOf = (hide: boolean, wait?: Promise<void>) => async () => {
      sent.push(hide)
      await wait
    }
    void v.set('p', true, saveOf(true, first.p), async () => undefined)
    await tick()
    void v.set('p', false, saveOf(false), async () => undefined)
    void v.set('p', true, saveOf(true), async () => undefined)
    const last = v.set('p', false, saveOf(false), async () => undefined)
    expect(v.getSnapshot().get('p')).toBe(false)
    await tick()
    // Only the first save is out; the next ones wait for it.
    expect(sent).toEqual([true])
    first.open()
    await last
    // The two middle clicks were overtaken while they waited: only the last one is sent.
    expect(sent).toEqual([true, false])
    await tick()
    expect(v.getSnapshot().size).toBe(0)
  })

  it('a refetch that ends after a newer click does not drop the newer choice', async () => {
    const v = new VisibilityIntents()
    const refetch = gate()
    const second = gate()
    await v.set('p', true, async () => undefined, () => refetch.p)
    void v.set('p', false, () => second.p, async () => undefined)
    refetch.open()
    await tick()
    expect(v.getSnapshot().get('p')).toBe(false)
    second.open()
    await tick()
    await tick()
    expect(v.getSnapshot().size).toBe(0)
  })

  it('a failed save lets the choice go once the refetch shows the server state', async () => {
    const v = new VisibilityIntents()
    await v.set('p', true, () => Promise.reject(new Error('offline')), async () => undefined)
    await tick()
    expect(v.getSnapshot().size).toBe(0)
  })

  it('tells subscribers about each change', () => {
    const v = new VisibilityIntents()
    let n = 0
    const stop = v.subscribe(() => n++)
    void v.set('a', true, () => new Promise(() => {}), async () => undefined)
    expect(n).toBe(1)
    stop()
    void v.set('b', true, () => new Promise(() => {}), async () => undefined)
    expect(n).toBe(1)
  })

  it('a save given `after` waits for it (Show only hides the others first)', async () => {
    const v = new VisibilityIntents()
    const order: string[] = []
    const others = gate()
    const first = v.set('w', true, async () => { await others.p; order.push('hide w') }, async () => undefined)
    void v.set('p', false, async () => { order.push('show p') }, async () => undefined, first)
    // Both show at once.
    expect([...v.getSnapshot()]).toEqual([['w', true], ['p', false]])
    await tick()
    expect(order).toEqual([])
    others.open()
    await tick()
    await tick()
    expect(order).toEqual(['hide w', 'show p'])
  })

  it('one hidden set for every surface: device list, server flags, pending choices over both', () => {
    const cols = [{ id: 'a', url: 'A', hidden: true }, { id: 'b', url: 'B' }, { id: 'c', url: 'C' }]
    expect([...hiddenCalendars(cols, ['C'], new Map())].sort()).toEqual(['A', 'C'])
    expect([...hiddenCalendars(cols, ['C'], new Map([['A', false], ['B', true]]))].sort()).toEqual(['B', 'C'])
  })
})
