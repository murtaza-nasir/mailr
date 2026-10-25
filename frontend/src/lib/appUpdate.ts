// New builds and failed chunk loads in the browser: the pure rules are in buildCheck.ts.
import { useEffect } from 'react'
import { useAppStatus } from '../stores/appStatus'
import { buildSignature, claimChunkReload, countsForBuild, isNewBuild, moduleScripts } from './buildCheck'
import { flushAllDrafts } from './draftStore'

/** The build this tab runs: the scripts its page loaded, as moduleScripts reads index.html. The entry
 *  that the locale boot adds (data-mailr-boot) is not in index.html, so it is left out here too. */
function runningBuild(): string {
  return buildSignature([...document.querySelectorAll<HTMLScriptElement>('script[src]:not([data-mailr-boot])')]
    .map((s) => ({ module: s.type === 'module', src: s.getAttribute('src') ?? '' }))
    .filter(countsForBuild)
    .map((s) => s.src))
}

let lastCheck = 0
let checking = false

/** Fetch index.html and compare its entry scripts with this tab's. Throttled unless forced. */
export async function checkForNewBuild(force = false): Promise<void> {
  if (checking || useAppStatus.getState().newVersion) return
  if (!force && Date.now() - lastCheck < 60_000) return
  lastCheck = Date.now()
  checking = true
  try {
    const res = await fetch('/', { cache: 'no-store', credentials: 'same-origin', headers: { Accept: 'text/html' } })
    if (!res.ok || !(res.headers.get('content-type') ?? '').includes('text/html')) return
    if (isNewBuild(runningBuild(), buildSignature(moduleScripts(await res.text())))) {
      useAppStatus.getState().setNewVersion(true)
      void import('./serviceWorker').then((m) => m.prefetchUpdate())
    }
  } catch {
    /* offline: try again later */
  } finally {
    checking = false
  }
}

/** Reload the page, after writing open compose text to the local copy. */
export function reloadApp(): void {
  flushAllDrafts(true)
  window.location.reload()
}

function session(): Pick<Storage, 'getItem' | 'setItem'> | null {
  try {
    return window.sessionStorage
  } catch {
    return null
  }
}

/** A lazy chunk failed to load: reload once (the new build has the chunk); a second failure
 * soon after shows the "New version" prompt instead of looping. */
export function reloadForChunkError(): void {
  if (claimChunkReload(Date.now(), session())) {
    reloadApp()
    return
  }
  useAppStatus.getState().setNewVersion(true)
}

/** While the shell is mounted: check for a new build when the tab comes back and every 10 minutes. */
export function useBuildWatch(): void {
  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState === 'visible') void checkForNewBuild()
    }
    const t = setInterval(() => void checkForNewBuild(true), 10 * 60_000)
    document.addEventListener('visibilitychange', onVisible)
    window.addEventListener('focus', onVisible)
    void checkForNewBuild(true)
    return () => {
      clearInterval(t)
      document.removeEventListener('visibilitychange', onVisible)
      window.removeEventListener('focus', onVisible)
    }
  }, [])
}
