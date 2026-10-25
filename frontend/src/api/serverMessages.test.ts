/// <reference types="node" />
// The server's coded errors in the app's language.
import { readFileSync } from 'node:fs'
import path from 'node:path'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { interpolate, pseudo, type Params } from '../i18n'
import { api } from './client'
import { codedText, serverText, setServerMessages } from './serverMessages'
import { taskRequest } from '../features/tasks/api'

describe('server messages', () => {
  it('fills a coded error from the catalogue, and nothing without one', () => {
    setServerMessages({ 'labels.limit': 'Sie haben {{max}} Labels' })
    expect(serverText('labels.limit', { max: 200 })).toBe('Sie haben 200 Labels')
    expect(serverText('labels.unknown', {})).toBeNull()
    expect(serverText(undefined, undefined)).toBeNull()
  })
})

// The server's real catalogue (backend/mailr/i18n/en.json), its errors turned into a pseudo-language
// as a translation would be: the codes the routes raise show the catalogue's text, not the detail.
type Tree = { [k: string]: string | Tree }
function flatten(o: Tree, prefix = '', out: Record<string, string> = {}): Record<string, string> {
  for (const [k, v] of Object.entries(o)) {
    if (typeof v === 'string') out[prefix + k] = v
    else flatten(v, `${prefix}${k}.`, out)
  }
  return out
}
const english = flatten((JSON.parse(readFileSync(path.resolve(__dirname, '../../../backend/mailr/i18n/en.json'), 'utf8')) as { errors: Tree }).errors)
const translated = Object.fromEntries(Object.entries(english).map(([k, v]) => [k, pseudo(v)]))

/** What the server answers for a code: the English detail, the code and its params. */
function answer(status: number, code: string, params: Params = {}): Response {
  const body = { detail: interpolate(english[code], params), code, params }
  return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })
}

describe('converted route errors in another language', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('shows the catalogue text of the code, filled with its params, on every client path', async () => {
    setServerMessages(translated, 'xx')
    const cases: [number, string, Params][] = [
      [409, 'templates.shortcut_taken', { shortcut: 'sig' }],
      [404, 'dictation.not_set_up', {}],
      [404, 'calendar.no_invitation', {}],
      [400, 'contacts.group_max_members', { max: 500 }],
    ]
    for (const [status, code, params] of cases) {
      expect(english[code], code).toBeTruthy()
      vi.stubGlobal('fetch', vi.fn(async () => answer(status, code, params)))
      const err = await api.get('/anything').catch((e: Error) => e)
      expect((err as Error).message).toBe(interpolate(pseudo(english[code]), params))
      expect((err as Error).message).not.toBe(interpolate(english[code], params))
    }
    // The tasks client has a fetch of its own.
    vi.stubGlobal('fetch', vi.fn(async () => answer(400, 'tasks.nothing_to_move')))
    const err = await taskRequest('POST', '/tasks/actions', {}).catch((e: Error) => e)
    expect((err as Error).message).toBe(pseudo('nothing to move'))
  })

  it('keeps the English detail of an error without a code, or a code the catalogue lacks', async () => {
    setServerMessages(translated, 'xx')
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ detail: 'spec must be JSON' }), { status: 400, headers: { 'content-type': 'application/json' } })))
    expect(((await api.get('/x').catch((e: Error) => e)) as Error).message).toBe('spec must be JSON')
    expect(await codedText({ detail: 'odd', code: 'no.such.code' })).toBeNull()
  })
})
