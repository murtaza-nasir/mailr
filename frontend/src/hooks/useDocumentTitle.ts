import { useEffect } from 'react'

/** The page title (WCAG 2.4.2): the most specific part first, then the app, then
 * "Mailr": "Groceries - Notes - Mailr", "Calendar - Mailr". Mail and Settings set theirs the same way. */
export function documentTitle(...parts: (string | null | undefined | false)[]): string {
  return [...parts.filter((p): p is string => !!p && !!p.trim()), 'Mailr'].join(' - ')
}

export function useDocumentTitle(...parts: (string | null | undefined | false)[]): void {
  const title = documentTitle(...parts)
  useEffect(() => {
    document.title = title
  }, [title])
}
