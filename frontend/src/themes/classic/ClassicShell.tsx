import { useLocation } from 'react-router'
import { useUI } from '../../stores/ui'
import { ComposeDock } from '../../features/compose/ComposeDock'
import { ComposeFab } from '../../features/compose/ComposeFab'
import { Sidebar } from '../../features/layout/Sidebar'
import { TopBar } from '../../features/layout/TopBar'
import { SidePanel, SideRail } from '../../features/rail/SideRail'
import { GlobalOverlays } from '../shared/frame'
import { CommandPalette } from '../shared/PaletteHost'
import type { ShellProps } from '../types'

/** Roomy: top bar with search, label sidebar, rounded main surface, app rail on the right. */
export function ClassicShell({ children }: ShellProps) {
  const panel = useUI((s) => s.panel)
  const location = useLocation()
  return (
    <div className="app">
      <TopBar />
      <Sidebar />
      <main className="main" id="main">
        {children}
      </main>
      {panel && <div className="panel-scrim" aria-hidden onClick={() => useUI.getState().setPanel(null)} />}
      {/* Renders nothing while no panel is open (and keeps a stepped-aside Ambrose chat, hidden). */}
      <SidePanel />
      <SideRail />
      {/* Compose is the mail app's button; Calendar and Tasks bring their own Create. */}
      {location.pathname.startsWith('/mail') && (
        <ComposeFab />
      )}
      <ComposeDock />
      <CommandPalette />
      <GlobalOverlays />
    </div>
  )
}
