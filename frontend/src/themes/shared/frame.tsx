import { Toasts } from '../../components/Toasts'
import { TooltipLayer } from '../../components/TooltipLayer'
import { AskHost } from '../../features/ask/AskHost'
import { DropHint } from '../../features/mail/dragDrop'
import { HelpOverlay } from '../../features/shortcuts/HelpOverlay'

/** Overlays every shell draws last: keyboard help, the ask capture, toasts, tooltips and the drop verb of a drag. */
export function GlobalOverlays() {
  return (
    <>
      <HelpOverlay />
      <AskHost />
      <Toasts />
      <TooltipLayer />
      <DropHint />
    </>
  )
}
