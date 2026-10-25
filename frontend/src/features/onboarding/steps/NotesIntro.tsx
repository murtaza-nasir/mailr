// The card on the last screen that introduces notes and lists: what they are, a shopping list or a
// note in one tap (the guide finishes and the new note opens), and on a phone the Notes app
// (Android) or mailr on the Home Screen (iPhone, where notes are in the web app). Left out when the
// admin's preset turns it off (offer.notes false).

import { useQueryClient } from '@tanstack/react-query'
import { Download, FileText, ListChecks, NotebookPen } from 'lucide-react'
import { useState } from 'react'
import { Spinner } from '../../../components/bits'
import { useMediaQuery } from '../../../hooks/useMediaQuery'
import { notePath } from '../../../lib/routes'
import { toastError } from '../../../stores/ui'
import { createNote, fromTemplate } from '../../notes/actions'
import { useStandalone } from '../../pwa/InstallGuide'
import { useGuide } from '../guideContext'
import { AppVersion } from '../InstallGuide'
import { appRelease, detectPhone, notesPhoneOffer } from '../steps'
import { t, tx } from '../../../i18n'

const RELEASES = 'https://github.com/murtaza-nasir/mailr/releases/latest'

export function NotesIntro() {
  const g = useGuide()
  const qc = useQueryClient()
  const [busy, setBusy] = useState<'list' | 'note' | null>(null)
  const narrow = useMediaQuery('(max-width: 640px)')
  const standalone = useStandalone()
  const here = typeof navigator !== 'undefined' ? detectPhone(navigator.userAgent, navigator.maxTouchPoints ?? 0) : null
  const { android, ios } = notesPhoneOffer(here, narrow, standalone)
  const notesApp = appRelease(g.state.phone.apps, 'notes', RELEASES)

  const start = async (what: 'list' | 'note') => {
    setBusy(what)
    try {
      let id: string | undefined
      if (what === 'list') {
        // ACT note.from_template @web.setup
        id = (await fromTemplate(qc, 'b-shopping')).note?.id
      } else {
        // ACT note.create @web.setup
        id = (await createNote(qc, { kind: 'text' }, { quiet: true })).id
      }
      g.finish(id ? notePath(id) : '/notes')
    } catch (e) {
      toastError(e, what === 'list' ? t('onboarding.steps.notesIntro.theListWasNotMade') : t('onboarding.steps.notesIntro.theNoteWasNotMade'))
      setBusy(null)
    }
  }

  return (
    <section className="ob-notes-intro" aria-labelledby="ob-notes-title" data-testid="notes-intro">
      <h2 id="ob-notes-title" className="ob-label">
        <NotebookPen aria-hidden />{' '}{t('onboarding.steps.notesIntro.notesAndLists')}
      </h2>
      <p className="ob-hint">
        {t('onboarding.steps.notesIntro.mailrAlsoKeepsNotesChecklists')}
      </p>
      <div className="ob-notes-actions">
        <button type="button" className="btn tonal" onClick={() => void start('list')} disabled={!!busy || g.busy}>
          {busy === 'list' ? <Spinner small /> : <ListChecks />}{' '}{t('onboarding.steps.notesIntro.startAShoppingList')}
        </button>
        <button type="button" className="btn outlined" onClick={() => void start('note')} disabled={!!busy || g.busy}>
          {busy === 'note' ? <Spinner small /> : <FileText />}{' '}{t('onboarding.steps.notesIntro.startANote')}
        </button>
      </div>
      {android && (
        <div className="ob-notes-phone" data-testid="notes-intro-android">
          <a className="btn text" href={notesApp.url} target="_blank" rel="noreferrer">
            <Download />{' '}{t('onboarding.steps.notesIntro.getTheNotesAppFor')}
          </a>
          <span className="ob-hint">{t('onboarding.steps.notesIntro.aSeparateAppForNotes')}</span>
          <AppVersion app={notesApp} />
        </div>
      )}
      {ios && (
        <p className="ob-hint" data-testid="notes-intro-ios">
          {tx('onboarding.steps.notesIntro.onAnIphoneNotesAre', undefined, { b: (c) => <b>{c}</b> })}
        </p>
      )}
    </section>
  )
}
