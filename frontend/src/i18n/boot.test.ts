// The kept language table and the build: src/i18n/boot.ts runs
// from index.html before the app; src/i18n/index.ts then reads the kept table at its first load.
import { afterEach, describe, expect, it, vi } from 'vitest'
import { bootLocale, type BootEnv } from './boot'

class Store {
  data = new Map<string, string>()
  getItem(k: string) {
    return this.data.get(k) ?? null
  }
  setItem(k: string, v: string) {
    this.data.set(k, v)
  }
  removeItem(k: string) {
    this.data.delete(k)
  }
}

const NEW = '/assets/locale-de-NEW.json'
const OLD = '/assets/locale-de-OLD.json'

function env(over: Partial<Omit<BootEnv, 'local' | 'session'>> = {}): BootEnv & { local: Store; session: Store; started: number; fetched: string[] } {
  const e = {
    local: new Store(),
    session: new Store(),
    search: '',
    languages: ['en-US'],
    started: 0,
    fetched: [] as string[],
    fetchText: async (u: string) => {
      e.fetched.push(u)
      return JSON.stringify({ greeting: { hello: 'Hallo (neu)' } })
    },
    start: () => {
      e.started++
    },
    ...over,
  }
  return e
}

const flush = () => new Promise((r) => setTimeout(r, 0))

describe('bootLocale', () => {
  it('starts at once for English: nothing to fetch', () => {
    const e = env()
    bootLocale(e, { de: NEW })
    expect(e.started).toBe(1)
    expect(e.fetched).toEqual([])
  })

  it('starts at once when the kept table is this build\'s', () => {
    const e = env()
    e.local.setItem('mailr.language', 'de')
    e.local.setItem('mailr.i18n.table.de', '{}')
    e.local.setItem('mailr.i18n.table.de.v', NEW)
    bootLocale(e, { de: NEW })
    expect(e.started).toBe(1)
    expect(e.fetched).toEqual([])
  })

  it('fetches this build\'s table first when the kept one is an older build\'s, then starts', async () => {
    const e = env()
    e.local.setItem('mailr.language', 'de')
    e.local.setItem('mailr.i18n.table.de', JSON.stringify({ greeting: { hello: 'Hallo (alt)' } }))
    e.local.setItem('mailr.i18n.table.de.v', OLD)
    bootLocale(e, { de: NEW })
    expect(e.started).toBe(0)
    await flush()
    expect(e.fetched).toEqual([NEW])
    expect(e.local.getItem('mailr.i18n.table.de.v')).toBe(NEW)
    expect(e.started).toBe(1)
  })

  it('follows the browser on Automatic', async () => {
    const e = env({ languages: ['de-AT', 'en'] })
    bootLocale(e, { de: NEW })
    await flush()
    expect(e.fetched).toEqual([NEW])
    expect(e.started).toBe(1)
  })

  it('gives a Chinese browser Simplified Chinese, but not a Taiwanese one', async () => {
    const zh = '/assets/locale-zh-Hans-1.json'
    const cn = env({ languages: ['zh-CN', 'en'] })
    bootLocale(cn, { de: NEW, 'zh-Hans': zh })
    await flush()
    expect(cn.fetched).toEqual([zh])
    const tw = env({ languages: ['zh-TW', 'en'] })
    bootLocale(tw, { de: NEW, 'zh-Hans': zh })
    await flush()
    expect(tw.fetched).toEqual([])
    expect(tw.started).toBe(1)
  })

  it('starts anyway when the fetch fails, keeping the old table', async () => {
    const e = env({ fetchText: () => Promise.reject(new Error('offline')) })
    e.local.setItem('mailr.language', 'de')
    e.local.setItem('mailr.i18n.table.de', '{"a":"alt"}')
    e.local.setItem('mailr.i18n.table.de.v', OLD)
    bootLocale(e, { de: NEW })
    await flush()
    expect(e.started).toBe(1)
    expect(e.local.getItem('mailr.i18n.table.de')).toBe('{"a":"alt"}')
  })

  it('runs from its own source, as index.html runs it', async () => {
    const e = env()
    e.local.setItem('mailr.language', 'de')
    const run = new Function('env', 'files', `return (${bootLocale.toString()})(env, files)`) as typeof bootLocale
    run(e, { de: NEW })
    await flush()
    expect(e.fetched).toEqual([NEW])
    expect(e.started).toBe(1)
  })
})

describe('the first paint after a release', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.resetModules()
  })

  it('a kept table from the previous build: the new words are there when the app first reads them', async () => {
    const local = new Store()
    local.setItem('mailr.language', 'de')
    local.setItem('mailr.i18n.table.de', JSON.stringify({ greeting: { hello: 'Hallo (alt)' } }))
    local.setItem('mailr.i18n.table.de.v', OLD)
    const session = new Store()
    // index.html: the boot script, then the app.
    let started = false
    const e = env({ start: () => { started = true } })
    e.local = local
    e.session = session
    bootLocale(e, { de: NEW })
    await flush()
    expect(started).toBe(true)
    vi.stubGlobal('__MAILR_LOCALES__', { de: NEW })
    vi.stubGlobal('window', { localStorage: local, sessionStorage: session })
    vi.resetModules()
    const i18n = await import('.')
    expect(i18n.language()).toBe('de')
    expect(i18n.t('greeting.hello')).toBe('Hallo (neu)')
  })
})
