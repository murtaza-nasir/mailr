// The command shape shared by the Calendar and Tasks command hooks (useCalendarCommands,
// useTaskCommands), the same contract as useMailCommands: actions as data that command bars
// (Office's ribbon, Desk's toolbar) and the palette draw.

import type { ReactNode } from 'react'

export interface AppCommand {
  id: string
  label: string
  /** Short label for tight bars. */
  short?: string
  icon: ReactNode
  /** Bars draw one group per value, in the order they first appear. */
  group: string
  /** The key, as lib/shortcuts writes it ("c", "g t"). */
  shortcut?: string
  enabled: boolean
  /** Menu commands open a menu at the element they were run from. */
  menu?: boolean
  /** A toggle that is on (a view that is showing, "Show completed"). */
  on?: boolean
  run: (anchor?: HTMLElement | null) => void
}

export interface AppCommands {
  commands: AppCommand[]
  byId: (id: string) => AppCommand | undefined
  /** Menus the commands open, to render once next to the bar. */
  menus: ReactNode
}

export function commandSet(commands: AppCommand[], menus: ReactNode = null): AppCommands {
  return { commands, byId: (id) => commands.find((c) => c.id === id), menus }
}

/** Commands grouped for a bar, in first-seen group order. */
export function groupCommands(commands: AppCommand[]): { group: string; commands: AppCommand[] }[] {
  const out: { group: string; commands: AppCommand[] }[] = []
  for (const c of commands) {
    const g = out.find((x) => x.group === c.group)
    if (g) g.commands.push(c)
    else out.push({ group: c.group, commands: [c] })
  }
  return out
}
