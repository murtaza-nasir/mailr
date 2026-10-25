// Before the app starts: make sure this browser's copy of the
// chosen language's table belongs to this build. The build (vite.config.ts i18nKeys) writes this
// function's source into index.html, with the build's locale files as `files` (code → hashed URL),
// and starts the app from it. So it has no imports and refers to nothing outside itself.
//
// A kept table whose URL is this build's: the app starts at once. Another build's (or none): the
// file is fetched first, kept, and then the app starts, so the first paint has the new words. If
// the fetch fails the app starts anyway (src/i18n then uses the old table, or English).

export interface BootStore {
  getItem(key: string): string | null
  setItem(key: string, value: string): void
}

export interface BootEnv {
  local: BootStore | null
  session: BootStore | null
  /** location.search */
  search: string
  /** navigator.languages */
  languages: readonly string[]
  fetchText: (url: string) => Promise<string>
  /** Start the app (add its module script). */
  start: () => void
}

/** Fetch the chosen language's table when the kept one is not this build's, then start the app. */
export function bootLocale(env: BootEnv, files: Record<string, string>): void {
  let code: string | null = null
  try {
    // The same choice as pickLanguage in src/i18n/index.ts (written out here: this source runs on its own).
    const list = ['en', 'en-XA', 'ar-XB', ...Object.keys(files)]
    const match = (want: string | null | undefined): string | null => {
      if (!want) return null
      const w = want.toLowerCase()
      const exact = list.find((c) => c.toLowerCase() === w)
      if (exact) return exact
      // Traditional Chinese is not given Simplified Chinese (TRADITIONAL in src/i18n/index.ts).
      if (/^zh-(hant|tw|hk|mo)\b/.test(w)) return null
      const base = w.split('-')[0]
      return list.find((c) => c.toLowerCase() === base) ?? list.find((c) => c.split('-')[0].toLowerCase() === base && c !== 'en-XA' && c !== 'ar-XB') ?? null
    }
    let fromUrl: string | null = null
    try {
      fromUrl = new URLSearchParams(env.search).get('lang')
    } catch {
      fromUrl = null
    }
    let found = false
    for (const c of [fromUrl, env.session?.getItem('mailr.language.tab'), env.local?.getItem('mailr.language')]) {
      const m = match(c)
      if (m) {
        code = files[m] ? m : null
        found = true
        break
      }
    }
    if (!found) {
      for (const l of env.languages) {
        const m = match(l)
        if (m && m !== 'en-XA' && m !== 'ar-XB') {
          code = files[m] ? m : null
          break
        }
      }
    }
  } catch {
    code = null
  }
  const url = code ? files[code] : undefined
  const local = env.local
  if (!url || !local) return env.start()
  const key = `mailr.i18n.table.${code}`
  if (local.getItem(`${key}.v`) === url && local.getItem(key)) return env.start()
  env.fetchText(url)
    .then((text) => {
      JSON.parse(text)
      local.setItem(key, text)
      local.setItem(`${key}.v`, url)
    })
    .catch(() => undefined)
    .then(() => env.start())
}
