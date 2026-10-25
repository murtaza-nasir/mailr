// The id of this browser tab, sent with every API request as X-Mailr-Client (backend core/live.py).
// The server returns it as `origin` in the live events of the changes this tab made, so the tab
// can tell its own saves from the assistant's or another tab's.

function make(): string {
  try {
    const a = new Uint8Array(8)
    crypto.getRandomValues(a)
    return `w${Array.from(a, (b) => b.toString(16).padStart(2, '0')).join('')}`
  } catch {
    return `w${Math.random().toString(36).slice(2, 14)}`
  }
}

export const CLIENT_ID = make()
