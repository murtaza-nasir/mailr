// Slash commands in the chat (backend/mailr/agent/commands.py, GET /agent/commands).
//
// One list from the server, in the user's language. A command opens the message (`/today`,
// `/task call the bank fri 3pm`). App commands run here (AssistantPanel.tsx `runCommand`); ask
// commands go to the server, which turns them into a request to the assistant; `/model` is answered
// by the server. A skill is `/name` too, but a command comes first.

import { useQuery, type QueryClient } from '@tanstack/react-query'
import { api } from '../../../api/client'

export interface ChatCommand {
  name: string
  aliases: string[]
  kind: 'app' | 'info' | 'ask'
  group: 'chat' | 'add' | 'ask'
  arg: 'none' | 'optional' | 'required'
  summary: string
  example: string | null
}

const COMMANDS_QUERY = {
  queryKey: ['assistant', 'commands'],
  queryFn: () => api.get<{ items: ChatCommand[] }>('/agent/commands'),
  staleTime: 10 * 60_000,
}

export function useCommands() {
  return useQuery(COMMANDS_QUERY)
}

const LEADING = /^\/([A-Za-z][A-Za-z0-9_-]*)(?:\s+([\s\S]*))?$/

/** The command a message starts with and the text after it (as the server reads it), or null. */
export function parseCommand(text: string, list: ChatCommand[] | undefined): { cmd: ChatCommand; text: string } | null {
  const m = LEADING.exec(text.trim())
  if (!m || !list) return null
  const name = m[1].toLowerCase()
  const cmd = list.find((c) => c.name === name || c.aliases.includes(name))
  return cmd ? { cmd, text: (m[2] ?? '').trim() } : null
}

/** The list to read `text` with. A message that opens with `/word` while the list is still loading
 *  waits for it: a `/help` sent the moment the panel opens runs here, not as a question to the
 *  server. Without the list (it failed) the message goes as typed, and the server reads it. */
export async function commandListFor(qc: QueryClient, text: string, list: ChatCommand[] | undefined): Promise<ChatCommand[] | undefined> {
  if (list || !LEADING.test(text.trim())) return list
  try {
    return (await qc.ensureQueryData(COMMANDS_QUERY)).items
  } catch {
    return undefined
  }
}

/** The commands whose name or alias starts with `prefix`, in list order. */
export function matchCommands(list: ChatCommand[] | undefined, prefix: string): ChatCommand[] {
  if (!list) return []
  return list.filter((c) => c.name.startsWith(prefix) || c.aliases.some((a) => a.startsWith(prefix)))
}
