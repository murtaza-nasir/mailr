/// <reference types="node" />
// Server texts kept as a key: the web's copy of the server's `approvals` section
// matches the server catalogue, and a msg renders through t() with its English as the fallback.
import { readFileSync } from 'node:fs'
import path from 'node:path'
import { describe, expect, it } from 'vitest'
import en from '../../../../locales/en.json'
import { SERVER_KEYS } from './serverKeys'
import { asQuestion, msgsText, msgText } from './serverText'

const server = JSON.parse(readFileSync(path.resolve(__dirname, '../../../../../../backend/mailr/i18n/en.json'), 'utf8'))

describe('server texts', () => {
  it('the web copy is the server catalogue (tools/i18n_server_texts.py)', () => {
    expect((en as Record<string, unknown>).serverText).toEqual({ approvals: server.approvals })
    expect(Object.keys(SERVER_KEYS).length).toBeGreaterThan(300)
  })

  it('renders nested msgs and plural counts', () => {
    const m = { key: 'approvals.mail.trash.ask', params: { what: { key: 'approvals.what.messages', params: { count: 6 } } } }
    expect(msgText(m, 'x')).toBe('Move 6 messages to Trash')
    const one = { key: 'approvals.what.messages', params: { count: 1 } }
    expect(msgText(one, 'x')).toBe('1 message')
    expect(msgText({ key: 'approvals.send.title', params: { who: 'Marco Silva' } }, 'x')).toBe('Send to Marco Silva')
  })

  it('falls back to the English for a key it lacks, or no msg', () => {
    expect(msgText({ key: 'approvals.no.such.key', params: {} }, 'Stored')).toBe('Stored')
    expect(msgText({ key: 'approvals.send.title', params: { who: { key: 'approvals.nope', params: {} } } }, 'Stored')).toBe('Stored')
    expect(msgText(undefined, 'Stored')).toBe('Stored')
    expect(msgsText([{ key: 'approvals.order.deliver.brief', params: {} }, { key: 'approvals.order.deliver.notify', params: {} }], 'x'))
      .toBe('a card on the email, a notification')
  })

  it('asks a question of a waiting card', () => {
    expect(asQuestion('Send to Marco Silva')).toBe('Send to Marco Silva?')
    expect(asQuestion('Done.')).toBe('Done.')
  })
})
