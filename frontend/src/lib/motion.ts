// Short motion for actions that move mail.
//
// Rows are found by their data-id inside the message list, so every theme gets the same motion
// without its Row knowing. The Web Animations API is used (not classes) so a re-render during
// the animation cannot cut it short. The caller removes the rows from the list when animateOut
// resolves; the rows below then slide up (height collapse in sections, a transform transition
// on the virtualised row wrappers, see list.css .v-row). A section's last rows fade in place
// instead, and its empty state (one row tall) takes their place: nothing below moves.

import type { ActionRequest } from '../api/types'

type Kind = 'archive' | 'delete' | 'move' | 'snooze'

// About 120 ms from click to the next row in place: fast enough to delete
// row after row by clicking. Exit and gap close overlap; virtual rows glide in list.css (.rows-moving).
const EXIT_MS = 70
const COLLAPSE_MS = 70
const OVERLAP_MS = 20
const STAGGER_MS = 8
const UNDO_MS = 120
const MAX_ANIMATED = 30

/** 'smooth', or 'auto' when the user asked for reduced motion. */
export function scrollBehavior(): ScrollBehavior {
  return reducedMotion() ? 'auto' : 'smooth'
}

export function reducedMotion(): boolean {
  try {
    return window.matchMedia('(prefers-reduced-motion: reduce)').matches
  } catch {
    return false
  }
}

function kindOf(action: ActionRequest['action']): Kind {
  if (action === 'archive') return 'archive'
  if (action === 'trash' || action === 'delete_forever' || action === 'spam') return 'delete'
  if (action === 'snooze') return 'snooze'
  return 'move'
}

// Each exit says what happened: archive slides away, delete shrinks in place, move drifts
// towards the sidebar, snooze floats up.
const EXIT: Record<Kind, Keyframe[]> = {
  archive: [{ transform: 'translateX(0)', opacity: 1 }, { transform: 'translateX(48px)', opacity: 0 }],
  delete: [{ transform: 'scale(1)', opacity: 1 }, { transform: 'scale(0.96)', opacity: 0 }],
  move: [{ transform: 'translate(0, 0)', opacity: 1 }, { transform: 'translate(-32px, -4px) scale(0.98)', opacity: 0 }],
  snooze: [{ transform: 'translateY(0)', opacity: 1 }, { transform: 'translateY(-10px)', opacity: 0 }],
}

/** How much a section's height changes when its rows give way to its empty state: the section is
 *  measured once with a hidden empty state in place of its body (nothing is painted in between). */
function emptyDelta(section: HTMLElement): number {
  const before = section.getBoundingClientRect().height
  const body = [...section.children].filter((c): c is HTMLElement => c instanceof HTMLElement && !c.classList.contains('section-head'))
  const shown = body.map((c) => c.style.display)
  body.forEach((c) => (c.style.display = 'none'))
  const probe = document.createElement('div')
  probe.className = 'section-empty'
  probe.setAttribute('aria-hidden', 'true')
  probe.style.visibility = 'hidden'
  const text = document.createElement('span')
  text.className = 'section-empty-text'
  text.textContent = '\u00a0'
  probe.append(text)
  section.append(probe)
  const after = section.getBoundingClientRect().height
  probe.remove()
  body.forEach((c, i) => (c.style.display = shown[i]))
  return after - before
}

/** The row grids of inbox sections whose every row leaves, with the change in the section's
 *  height when its empty state takes their place (0 when the empty state is one row tall, as
 *  list.css makes it for a usual row). */
function emptiedSections(rows: HTMLElement[], ids: number[]): Map<HTMLElement, number> {
  const leaving = new Set(ids.map(String))
  const out = new Map<HTMLElement, number>()
  for (const el of rows) {
    const grid = el.closest<HTMLElement>('[role="grid"]')
    const section = grid?.parentElement
    if (!grid || !section?.matches('section.section') || out.has(grid)) continue
    const all = [...grid.querySelectorAll<HTMLElement>('[data-id]')].every((r) => leaving.has(r.dataset.id ?? ''))
    if (all) out.set(grid, emptyDelta(section))
  }
  return out
}

function rowsFor(ids: number[]): HTMLElement[] {
  const out: HTMLElement[] = []
  for (const id of ids.slice(0, MAX_ANIMATED)) {
    document.querySelectorAll<HTMLElement>(`.list-scroll [data-id="${id}"]`).forEach((el) => out.push(el))
  }
  return out
}

/** Play the exit for these rows; resolves when the list may drop them. */
export function animateOut(ids: number[], action: ActionRequest['action']): Promise<void> {
  const rows = rowsFor(ids)
  if (!rows.length || typeof rows[0].animate !== 'function') return Promise.resolve()
  const reduced = reducedMotion()
  const frames = reduced ? [{ opacity: 1 }, { opacity: 0 }] : EXIT[kindOf(action)]
  // Let the virtualised rows below glide up once the list drops these (only for this moment,
  // so first layout and scrolling never animate).
  const scrollers = new Set(rows.map((el) => el.closest('.list-scroll')).filter((x): x is Element => !!x))
  scrollers.forEach((s) => s.classList.add('rows-moving'))
  setTimeout(() => scrollers.forEach((s) => s.classList.remove('rows-moving')), 300)
  // A section that loses all its rows goes straight from them to its empty state, at the same
  // height: its rows fade where they are (no gap closing, which made the section shrink to its
  // header and then grow again, moving everything below twice). Rows taller or shorter than the
  // empty state glide to its height instead.
  const emptied = reduced ? new Map<HTMLElement, number>() : emptiedSections(rows, ids)
  const grids = [...emptied].map(([grid, delta]) => {
    grid.animate([{ opacity: 1 }, { opacity: 0 }], { duration: EXIT_MS, easing: 'cubic-bezier(0.4, 0, 1, 1)', fill: 'forwards' })
    if (Math.abs(delta) < 0.5) return Promise.resolve()
    const h = grid.getBoundingClientRect().height
    grid.style.overflow = 'hidden'
    return grid.animate([{ height: `${h}px` }, { height: `${Math.max(0, h + delta)}px` }], {
      duration: COLLAPSE_MS, delay: EXIT_MS - OVERLAP_MS, easing: 'cubic-bezier(0.2, 0, 0, 1)', fill: 'forwards',
    }).finished.then(() => undefined)
  })
  const plays = rows.map((el, i) => {
    const delay = reduced ? 0 : Math.min(i, 10) * STAGGER_MS
    // A row swiped away has already left (features/pwa/useTouchList.ts): it stays out while its gap closes.
    const gone = el.dataset.swiped === 'true'
    const own = gone ? [{ opacity: 0, transform: el.style.transform || 'none' }, { opacity: 0, transform: el.style.transform || 'none' }] : frames
    // A leaving row takes no more clicks: a quick second click must not land on it.
    el.style.pointerEvents = 'none'
    const exit = el.animate(own, { duration: reduced ? 120 : EXIT_MS, delay, easing: 'cubic-bezier(0.4, 0, 1, 1)', fill: 'forwards' })
    // A row in normal flow (a section) closes its own gap; a virtualised row's wrapper is
    // absolutely positioned, and the rows below slide up when the list re-lays them out.
    const virtual = el.parentElement?.hasAttribute('data-index')
    const whole = [...emptied.keys()].some((g) => g.contains(el))
    if (reduced || virtual || whole) return exit.finished
    const h = el.offsetHeight
    const collapse = el.animate(
      [{ height: `${h}px`, marginTop: getComputedStyle(el).marginTop, marginBottom: getComputedStyle(el).marginBottom, paddingTop: getComputedStyle(el).paddingTop, paddingBottom: getComputedStyle(el).paddingBottom },
       { height: '0px', marginTop: '0px', marginBottom: '0px', paddingTop: '0px', paddingBottom: '0px' }],
      { duration: COLLAPSE_MS, delay: delay + EXIT_MS - OVERLAP_MS, easing: 'cubic-bezier(0.2, 0, 0, 1)', fill: 'forwards' },
    )
    el.style.overflow = 'hidden'
    return Promise.all([exit.finished, collapse.finished])
  })
  // Never hold the list hostage: resolve after the longest planned time even if a promise stalls.
  const longest = (reduced ? 120 : EXIT_MS + COLLAPSE_MS) + Math.min(rows.length, 10) * STAGGER_MS
  return Promise.race([Promise.all([...plays, ...grids]).then(() => undefined), new Promise<void>((r) => setTimeout(r, longest + 80))]).catch(() => undefined)
}

/** Rows coming back (undo) slide into place from where they left. */
export function animateIn(ids: number[], action: ActionRequest['action'] | null) {
  const reduced = reducedMotion()
  const exit = action ? EXIT[kindOf(action)] : EXIT.archive
  rowsFor(ids).forEach((el, i) => {
    if (typeof el.animate !== 'function') return
    el.animate(reduced ? [{ opacity: 0 }, { opacity: 1 }] : [exit[1], exit[0]], {
      duration: reduced ? 120 : UNDO_MS,
      delay: reduced ? 0 : Math.min(i, 10) * STAGGER_MS,
      easing: 'cubic-bezier(0, 0, 0.2, 1)',
      fill: 'backwards',
    })
  })
}

/** A small pop on a row's star or importance marker. */
export function pop(ids: number[], which: 'star' | 'important') {
  if (reducedMotion()) return
  const sel = which === 'star'
    ? 'button[aria-label="Star"], button[aria-label="Unstar"], button[aria-label="Starred"], button[aria-label="Not starred"], button[aria-label="Flag"], button[aria-label="Unflag"]'
    : '[class*="importance"], [class*="imp-"], [aria-label*="mportant"]'
  for (const id of ids.slice(0, MAX_ANIMATED)) {
    document.querySelectorAll<HTMLElement>(`[data-id="${id}"]`).forEach((row) => {
      const el = row.querySelector<HTMLElement>(sel)
      el?.animate?.([{ transform: 'scale(1)' }, { transform: 'scale(1.3)' }, { transform: 'scale(1)' }], { duration: 260, easing: 'cubic-bezier(0.3, 0, 0.3, 1.4)' })
    })
  }
}
