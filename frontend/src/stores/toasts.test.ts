// Toasts with an action (Undo) and errors stay at least 10 s; the timers stop while the
// toasts are hovered or focused and run on afterwards.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { toastDuration, useUI } from './ui'

describe('toasts', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => {
    useUI.getState().pauseToasts(false)
    useUI.setState({ toasts: [] })
    vi.useRealTimers()
  })

  it('give errors and actions at least 10 s', () => {
    expect(toastDuration({})).toBe(6000)
    expect(toastDuration({ error: true })).toBe(10_000)
    expect(toastDuration({ actionLabel: 'Undo' })).toBe(10_000)
    expect(toastDuration({ actionLabel: 'Undo', duration: 4000 })).toBe(10_000)
    expect(toastDuration({ duration: 0, actionLabel: 'Undo' })).toBe(0)
  })

  it('pause while hovered, then run the time that was left', () => {
    const ui = useUI.getState()
    const id = ui.toast({ message: 'Archived', actionLabel: 'Undo', onAction: () => undefined })
    vi.advanceTimersByTime(4000)
    ui.pauseToasts(true)
    vi.advanceTimersByTime(60_000)
    expect(useUI.getState().toasts.map((t) => t.id)).toContain(id)
    ui.pauseToasts(false)
    vi.advanceTimersByTime(5900)
    expect(useUI.getState().toasts.map((t) => t.id)).toContain(id)
    vi.advanceTimersByTime(200)
    expect(useUI.getState().toasts.map((t) => t.id)).not.toContain(id)
  })

  it('unpause when the last toast goes, so the next one expires', () => {
    const ui = useUI.getState()
    const a = ui.toast({ message: 'One' })
    ui.pauseToasts(true)
    ui.dismissToast(a)
    const b = ui.toast({ message: 'Two' })
    vi.advanceTimersByTime(6100)
    expect(useUI.getState().toasts.map((t) => t.id)).not.toContain(b)
  })

  it('call onGone once when a toast leaves: its time, Dismiss, or pushed out by newer ones', () => {
    const ui = useUI.getState()
    const gone: string[] = []
    ui.toast({ message: 'Timed', onGone: () => gone.push('timed') })
    vi.advanceTimersByTime(6100)
    expect(gone).toEqual(['timed'])
    const d = ui.toast({ message: 'Dismissed', onGone: () => gone.push('dismissed') })
    ui.dismissToast(d)
    ui.dismissToast(d)
    expect(gone).toEqual(['timed', 'dismissed'])
    ui.toast({ message: 'Oldest', onGone: () => gone.push('oldest') })
    ui.toast({ message: 'Two' })
    ui.toast({ message: 'Three' })
    ui.toast({ message: 'Four' })
    expect(gone).toEqual(['timed', 'dismissed', 'oldest'])
    vi.advanceTimersByTime(60_000)
    expect(gone).toEqual(['timed', 'dismissed', 'oldest'])
  })
})
