// Its own module, so the reader's "Schedule a meeting" can use it without loading the quick-create
// parser (lib/quickadd.ts) with the first page.

/** The next round half-hour today (the `c` key without a focused slot). */
export function nextHalfHour(now: Date): Date {
  const t = new Date(now.getTime())
  t.setSeconds(0, 0)
  const m = t.getMinutes()
  t.setMinutes(m < 30 ? 30 : 60)
  return t
}
