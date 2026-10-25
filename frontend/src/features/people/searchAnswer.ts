// Suggestions under an address field (group members, compose recipients, event guests) come from
// searches that keep the last search's results while the next one runs (keepPreviousData). Enter
// and Tab must act on the suggestions for what is typed now, never on those: this says whether the
// searches answer `q` yet, and runs an Enter pressed too early once they do (typing on cancels it).
import { useEffect, useRef, useState } from 'react'

interface SearchState {
  isPlaceholderData: boolean
  isPending: boolean
  isError: boolean
}

export function useSearchAnswer(q: string, searches: SearchState[], onEnter: () => void) {
  const answered = q.length > 0 && searches.every((s) => !s.isPlaceholderData && !s.isPending)
  const ready = answered || (q.length > 0 && searches.some((s) => s.isError))
  const [enterFor, setEnterFor] = useState<string | null>(null)
  const onEnterRef = useRef(onEnter)
  useEffect(() => {
    onEnterRef.current = onEnter
  })
  useEffect(() => {
    if (enterFor === null) return
    if (enterFor !== q) setEnterFor(null)
    else if (ready) {
      setEnterFor(null)
      onEnterRef.current()
    }
  }, [enterFor, q, ready])
  return {
    /** The suggestions shown belong to `q`. */
    answered,
    /** `q`'s searches are back (or failed): Enter can act now. */
    ready,
    /** Enter before `ready`: act when the answer comes. */
    deferEnter: () => setEnterFor(q),
  }
}
