// One Idempotency-Key per send attempt (POST /send): kept while the attempt's outcome is unknown, so
// after a lost answer (network failure, timeout) the next Send gets the first one's result from the
// server (core/idempotency.py) instead of queueing a second copy. A definite answer, sent or refused
// with a reason, ends the attempt; the next Send is a new one.

import { ApiError, newIdempotencyKey } from '../../api/client'

export interface SendAttempt {
  /** The current attempt's key, made on first use. */
  key: () => string
  /** The request answered: `error` is what it threw, if anything. */
  settle: (error?: unknown) => void
}

export function sendAttempt(): SendAttempt {
  let current: string | null = null
  return {
    key: () => (current ??= newIdempotencyKey()),
    settle: (error?: unknown) => {
      if (!(error instanceof ApiError && error.status === 0)) current = null
    },
  }
}
