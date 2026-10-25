// Thin fetch wrapper: JSON, CSRF header on unsafe methods, 401 → sign-in, typed errors.

import { senderTextReviver } from '../lib/safeText'
import { CLIENT_ID } from '../lib/live/clientId'
import { browserZone, deviceClock } from '../lib/dates'
import { t as tr } from '../i18n'
import { codedText, loadServerMessages, serverText } from './serverMessages'

export const API = '/api/v1'

export class ApiError extends Error {
  status: number
  detail: unknown
  /** The whole JSON answer of a failed request (a 409 live-editing conflict carries base, theirs and mine). */
  body?: unknown
  /** Status 0 only: the network failed, or the request timed out (lib/netErrors.ts). */
  kind?: 'network' | 'timeout'
  constructor(status: number, message: string, detail?: unknown, kind?: 'network' | 'timeout') {
    super(message)
    this.status = status
    this.detail = detail
    this.kind = kind
  }
}

let csrfToken = ''
let onUnauthorized: ((confirmed: boolean) => void) | null = null

export function setCsrf(token: string) {
  csrfToken = token
}

export function getCsrf(): string {
  return csrfToken
}

export function setUnauthorizedHandler(fn: (confirmed: boolean) => void) {
  onUnauthorized = fn
}

/** Ask the app to sign out. `confirmed`: the session check itself said signed out; otherwise
 * the handler checks the session before it signs out (a stray 401 is not proof). */
export function notifyUnauthorized(confirmed = false) {
  onUnauthorized?.(confirmed)
}

type Json = Record<string, unknown> | unknown[] | null

export interface RequestOptions {
  method?: string
  body?: Json | FormData | undefined
  query?: Record<string, string | number | boolean | null | undefined>
  idempotencyKey?: string
  signal?: AbortSignal
  /** Do not trigger the global 401 handler (used by /me probe and login). */
  quiet401?: boolean
  /** Give up after this long with a timeout ApiError (status 0, kind 'timeout'). */
  timeoutMs?: number
}

export function buildUrl(path: string, query?: RequestOptions['query']): string {
  const url = path.startsWith('http') || path.startsWith('/api') ? path : `${API}${path}`
  if (!query) return url
  const params = new URLSearchParams()
  for (const [k, v] of Object.entries(query)) {
    if (v === undefined || v === null || v === '') continue
    params.set(k, String(v))
  }
  const qs = params.toString()
  return qs ? `${url}?${qs}` : url
}

/** The message for an error answer without a JSON body: 502–504 while Mailr restarts or a proxy
 *  cannot reach it, else the status alone. */
export function plainHttpError(status: number): string {
  if (status === 502 || status === 503 || status === 504) return tr('api.client.serverUnavailable')
  return tr('api.client.serverError', { status })
}

function detailMessage(detail: unknown, fallback: string): string {
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    // FastAPI validation errors
    const first = detail[0] as { msg?: string; loc?: unknown[] } | undefined
    if (first?.msg) return `${first.msg}${first.loc ? ` (${first.loc.slice(1).join('.')})` : ''}`
  }
  return fallback
}

export async function request<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const method = (opts.method ?? (opts.body !== undefined ? 'POST' : 'GET')).toUpperCase()
  // This tab's id (lib/live/clientId.ts): live events say which client made a change.
  // The browser's zone: the server's zone for this user while Region is Automatic (Ambrose's
  // times, reminders; backend calendar/tz.py note_device_zone). Its clock likewise, for Time
  // format on Automatic (note_device_clock).
  const headers: Record<string, string> = {
    Accept: 'application/json', 'X-Mailr-Client': CLIENT_ID, 'X-Mailr-Zone': browserZone(), 'X-Mailr-Clock': deviceClock(),
  }
  let body: BodyInit | undefined
  if (opts.body instanceof FormData) {
    body = opts.body
  } else if (opts.body !== undefined) {
    headers['Content-Type'] = 'application/json'
    body = JSON.stringify(opts.body)
  }
  if (!['GET', 'HEAD', 'OPTIONS'].includes(method) && csrfToken) headers['X-CSRF-Token'] = csrfToken
  if (opts.idempotencyKey) headers['Idempotency-Key'] = opts.idempotencyKey

  let res: Response
  let timedOut = false
  let timer: ReturnType<typeof setTimeout> | undefined
  let signal = opts.signal
  if (opts.timeoutMs) {
    const ctl = new AbortController()
    timer = setTimeout(() => {
      timedOut = true
      ctl.abort()
    }, opts.timeoutMs)
    opts.signal?.addEventListener('abort', () => ctl.abort(), { once: true })
    signal = ctl.signal
  }
  try {
    res = await fetch(buildUrl(path, opts.query), {
      method,
      headers,
      body,
      credentials: 'same-origin',
      signal,
    })
  } catch (e) {
    if (timedOut) throw new ApiError(0, tr('api.client.mailrDidNotAnswerIn'), undefined, 'timeout')
    if ((e as Error).name === 'AbortError') throw e
    throw new ApiError(0, tr('api.client.networkErrorCheckYourConnection'), undefined, 'network')
  } finally {
    if (timer) clearTimeout(timer)
  }

  if (res.status === 401 && !opts.quiet401) onUnauthorized?.(false)
  if (res.status === 204) return undefined as T
  const ctype = res.headers.get('content-type') ?? ''
  const data: unknown = ctype.includes('application/json')
    ? await res.text().then((t) => JSON.parse(t, senderTextReviver)).catch(() => null)
    : await res.text()
  if (!res.ok) {
    // An answer that is not JSON comes from something in front of Mailr (a proxy's 502 page while
    // the app restarts): its HTML is never shown as the message.
    if (typeof data === 'string') {
      const err = new ApiError(res.status, plainHttpError(res.status))
      err.body = null
      throw err
    }
    const detail = (data as { detail?: unknown } | null)?.detail ?? data
    // A coded error ({detail, code, params}) in this app's language when the server has the text.
    const coded = data as { code?: unknown; params?: unknown } | null
    if (coded?.code) await loadServerMessages()
    const text = serverText(coded?.code, coded?.params, detail) ?? detailMessage(detail, `${res.status} ${res.statusText}`)
    const err = new ApiError(res.status, text, detail)
    err.body = data
    throw err
  }
  return data as T
}

export const api = {
  get: <T>(path: string, query?: RequestOptions['query'], signal?: AbortSignal) =>
    request<T>(path, { method: 'GET', query, signal }),
  post: <T>(path: string, body?: Json | FormData, opts: Omit<RequestOptions, 'body' | 'method'> = {}) =>
    request<T>(path, { ...opts, method: 'POST', body: body ?? {} }),
  put: <T>(path: string, body?: Json | FormData) => request<T>(path, { method: 'PUT', body: body ?? {} }),
  patch: <T>(path: string, body?: Json) => request<T>(path, { method: 'PATCH', body: body ?? {} }),
  del: <T>(path: string) => request<T>(path, { method: 'DELETE' }),
}

/** Accept a bare array, {items: [...]}, or {events: [...]} (the calendar agenda). */
export function asList<T>(data: unknown): T[] {
  if (Array.isArray(data)) return data as T[]
  const d = data as { items?: unknown; events?: unknown } | null
  const items = d?.items ?? d?.events
  return Array.isArray(items) ? (items as T[]) : []
}

export function newIdempotencyKey(): string {
  return crypto.randomUUID()
}

/** POST that streams Server-Sent Events back (used by /agent/chat). */
export async function* postStream(
  path: string,
  body: Json,
  signal?: AbortSignal,
): AsyncGenerator<{ event: string; data: string }> {
  const res = await fetch(buildUrl(path), {
    method: 'POST',
    credentials: 'same-origin',
    headers: {
      'Content-Type': 'application/json',
      Accept: 'text/event-stream',
      'X-Mailr-Zone': browserZone(),
      'X-Mailr-Clock': deviceClock(),
      ...(csrfToken ? { 'X-CSRF-Token': csrfToken } : {}),
    },
    body: JSON.stringify(body),
    signal,
  })
  if (res.status === 401) onUnauthorized?.(false)
  if (!res.ok || !res.body) {
    const text = await res.text().catch(() => '')
    let msg = plainHttpError(res.status)
    try {
      const data = JSON.parse(text)
      msg = (await codedText(data)) ?? detailMessage(data.detail, msg)
    } catch {
      /* not json */
    }
    throw new ApiError(res.status, msg)
  }
  yield* parseSSE(res.body, signal)
}

/** GET an event stream (re-attaching to a running assistant turn). Throws ApiError on 4xx/5xx. */
export async function* getStream(
  path: string,
  query?: RequestOptions['query'],
  signal?: AbortSignal,
): AsyncGenerator<{ event: string; data: string }> {
  const res = await fetch(buildUrl(path, query), {
    method: 'GET',
    credentials: 'same-origin',
    headers: { Accept: 'text/event-stream' },
    signal,
  })
  if (res.status === 401) onUnauthorized?.(false)
  if (!res.ok || !res.body) throw new ApiError(res.status, `${res.status} ${res.statusText}`)
  yield* parseSSE(res.body, signal)
}

export async function* parseSSE(
  stream: ReadableStream<Uint8Array>,
  signal?: AbortSignal,
): AsyncGenerator<{ event: string; data: string }> {
  const reader = stream.getReader()
  const decoder = new TextDecoder()
  let buf = ''
  try {
    while (!signal?.aborted) {
      const { value, done } = await reader.read()
      if (done) break
      buf += decoder.decode(value, { stream: true })
      let idx: number
      while ((idx = buf.search(/\r?\n\r?\n/)) >= 0) {
        const chunk = buf.slice(0, idx)
        buf = buf.slice(idx).replace(/^\r?\n\r?\n/, '')
        let event = 'message'
        const data: string[] = []
        for (const line of chunk.split(/\r?\n/)) {
          if (line.startsWith('event:')) event = line.slice(6).trim()
          else if (line.startsWith('data:')) data.push(line.slice(5).replace(/^ /, ''))
        }
        if (data.length) yield { event, data: data.join('\n') }
      }
    }
  } finally {
    reader.releaseLock()
  }
}

export interface UploadResult {
  blob: string
  filename: string
  type: string
  size: number
}

/** Multipart upload with progress (XHR, since fetch has no upload progress). */
export function uploadFile(file: File, onProgress?: (fraction: number) => void): { promise: Promise<UploadResult>; abort: () => void } {
  const xhr = new XMLHttpRequest()
  const promise = new Promise<UploadResult>((resolve, reject) => {
    xhr.open('POST', `${API}/uploads`)
    xhr.withCredentials = true
    if (csrfToken) xhr.setRequestHeader('X-CSRF-Token', csrfToken)
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onProgress?.(e.loaded / e.total)
    }
    xhr.onload = () => {
      let data: unknown = null
      try {
        data = JSON.parse(xhr.responseText)
      } catch {
        /* not json */
      }
      if (xhr.status >= 200 && xhr.status < 300) resolve(data as UploadResult)
      else {
        if (xhr.status === 401) onUnauthorized?.(false)
        const fallback = detailMessage((data as { detail?: unknown } | null)?.detail, tr('api.client.uploadFailed', { status: xhr.status }))
        void codedText(data).then((text) => reject(new ApiError(xhr.status, text ?? fallback)))
      }
    }
    xhr.onerror = () => reject(new ApiError(0, tr('api.client.uploadFailedNetworkError'), undefined, 'network'))
    xhr.onabort = () => reject(new ApiError(0, tr('api.client.uploadCancelled')))
    const fd = new FormData()
    fd.append('file', file, file.name)
    xhr.send(fd)
  })
  return { promise, abort: () => xhr.abort() }
}
