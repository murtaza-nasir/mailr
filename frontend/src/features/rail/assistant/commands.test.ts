import { QueryClient } from '@tanstack/react-query'
import { describe, expect, it, vi } from 'vitest'
import { api } from '../../../api/client'
import { commandListFor, matchCommands, parseCommand, type ChatCommand } from './commands'

const c = (name: string, extra: Partial<ChatCommand> = {}): ChatCommand => ({
  name, aliases: [], kind: 'app', group: 'chat', arg: 'none', summary: name, example: null, ...extra,
})
const LIST = [c('new', { aliases: ['clear'] }), c('chats', { arg: 'optional' }), c('today', { kind: 'ask', group: 'ask' })]

describe('slash commands', () => {
  it('reads a leading command, as the server does', () => {
    expect(parseCommand('/clear', LIST)?.cmd.name).toBe('new')
    expect(parseCommand('  /chats fitness log ', LIST)).toEqual({ cmd: LIST[1], text: 'fitness log' })
    expect(parseCommand('what about /today', LIST)).toBeNull()
    expect(parseCommand('//today', LIST)).toBeNull()
    expect(parseCommand('/formal reply', LIST)).toBeNull()
    expect(parseCommand('/new', undefined)).toBeNull()
  })
  it('matches names and aliases by prefix, in list order', () => {
    expect(matchCommands(LIST, 'c').map((x) => x.name)).toEqual(['new', 'chats'])
    expect(matchCommands(LIST, '').length).toBe(3)
  })
  it('a command sent before the list arrived waits for it; other text never does', async () => {
    const get = vi.spyOn(api, 'get').mockResolvedValue({ items: LIST })
    const qc = new QueryClient()
    expect(await commandListFor(qc, 'what about today', undefined)).toBeUndefined()
    expect(get).not.toHaveBeenCalled()
    expect(await commandListFor(qc, '/new', LIST)).toBe(LIST)
    expect(get).not.toHaveBeenCalled()
    const list = await commandListFor(qc, ' /new', undefined)
    expect(parseCommand('/new', list)?.cmd.kind).toBe('app')
    expect(get).toHaveBeenCalledWith('/agent/commands')
    // A list that cannot be had sends the message as typed.
    get.mockRejectedValue(new Error('offline'))
    expect(await commandListFor(new QueryClient({ defaultOptions: { queries: { retry: false } } }), '/today', undefined)).toBeUndefined()
    get.mockRestore()
  })
})
