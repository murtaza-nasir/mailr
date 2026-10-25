import { Ellipsis } from 'lucide-react'
import { useRef, type ReactNode } from 'react'
import { MenuItem, Popover, useAnchor } from './Popover'
import { t } from '../i18n'

/** A command a toolbar had no room for (hooks/useToolbarFit.ts). */
export interface OverflowCommand {
  id: string
  label: string
  icon: ReactNode
  enabled: boolean
  shortcut?: string
  /** Menu commands (Move, Label, Snooze) open their menu at the More button. */
  run: (anchor?: HTMLElement | null) => void
}

/**
 * The More button at the end of a toolbar that folds to fit: it lists the commands that left the
 * bar, in the bar's order. Nothing has left: no button.
 */
export function OverflowMore({ commands, className }: { commands: OverflowCommand[]; className: string }) {
  const btn = useRef<HTMLButtonElement>(null)
  const menu = useAnchor()
  if (!commands.length) return null
  return (
    <>
      <button ref={btn} type="button" className={className} aria-label={t('common.more')} data-tip={t('common.more')} aria-haspopup="menu"
        aria-expanded={menu.open} data-testid="toolbar-more" onClick={(e) => menu.toggle(e.currentTarget)}>
        <Ellipsis />
      </button>
      <Popover anchor={menu.anchor} open={menu.open} onClose={menu.close} label={t('common.more')} minWidth={200}>
        {commands.map((c) => (
          <MenuItem key={c.id} icon={c.icon} disabled={!c.enabled} shortcut={c.shortcut} onSelect={() => { menu.close(); c.run(btn.current) }}>
            {c.label}
          </MenuItem>
        ))}
      </Popover>
    </>
  )
}
