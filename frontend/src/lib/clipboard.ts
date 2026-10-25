// Copying text to the clipboard with a short confirmation (the code chip, the reader's address
// buttons and menus). Without clipboard access the toast shows the text, so it can be copied by hand.
import type { Address } from '../api/types'
import { toast } from '../stores/ui'
import { safeAddress, safeText } from './safeText'

/** Put `text` on the clipboard and say `done` in a toast. False when the browser refused. */
export async function copyText(text: string, done: string, failed?: string): Promise<boolean> {
  try {
    // No clipboard (an insecure page, an old browser): the TypeError lands in catch.
    await navigator.clipboard.writeText(text)
    toast({ message: done })
    return true
  } catch {
    toast(failed ? { message: failed, error: true } : { message: text })
    return false
  }
}

/** The sender's or recipient's name as the message gives it, cleaned of control characters. */
export function addressName(a: Address): string {
  return safeText(a.name ?? '').trim()
}

/** "Name <address>", ready to paste into a To field. A name with a comma, quote or bracket in
 *  it is quoted ("Nguyen, Bich" <bich@example.com>); no name gives the bare address. */
export function nameAndAddress(a: Address): string {
  const name = addressName(a)
  const email = safeAddress(a.email)
  if (!name) return email
  const quoted = /[,;:<>()[\]"@\\]/.test(name) ? `"${name.replace(/(["\\])/g, '\\$1')}"` : name
  return `${quoted} <${email}>`
}
