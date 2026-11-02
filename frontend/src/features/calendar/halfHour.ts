// Its own module, so the reader's "Schedule a meeting" can use it without loading the quick-create
// parser (lib/quickadd.ts) with the first page.

/** The next round half-hour today (the `c` key without a focused slot). */
export function nextHalfHour(now: Date): Date {
  // Counted on the instant: setSeconds/setMinutes read the local wall time back, and in the repeated
  // autumn hour that gives the first pass, an hour in the past (01:10 CST became 01:30 CDT).
  const whole = now.getTime() - (now.getTime() % 60_000)
  const m = now.getMinutes()
  return new Date(whole + ((m < 30 ? 30 : 60) - m) * 60_000)
}
