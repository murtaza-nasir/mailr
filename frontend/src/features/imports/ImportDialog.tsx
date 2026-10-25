// Imports (backend core/imports.py), each from its app's ⋮ menu: Google Keep into
// Notes, an .ics file into a calendar, a .vcf file into People. One dialog for all three: choose the
// files, see what would come in (POST /imports/preview writes nothing), then import. The toast
// that follows has Undo, which takes the whole import back (POST /imports/{id}/undo).
// ACT note.import_keep @web.notes, ACT event.import_ics @web.calendar, ACT contacts.import_vcf @web.people, ACT import.undo @web.notes @web.calendar @web.people

import { useQueryClient } from '@tanstack/react-query'
import { FileUp } from 'lucide-react'
import { useRef, useState } from 'react'
import { api, uploadFile } from '../../api/client'
import { Spinner } from '../../components/bits'
import { Dialog } from '../../components/Dialog'
import { toast, toastError } from '../../stores/ui'
import './imports.css'
import { t } from '../../i18n'

export type ImportKind = 'keep' | 'ics' | 'vcf'

export interface ImportPreview {
  kind: ImportKind
  source: string
  counts: Record<string, number>
  sample: string[]
  calendar: { href: string; id: number; name: string } | null
}

export interface ImportBatch {
  id: number
  kind: ImportKind
  counts: Record<string, number>
}

/** "3 notes": a count and its noun, each noun a plural key (written out, so the build finds the keys). */
const NOUNS = {
  notes: 'imports.importDialog.notes',
  checklists: 'imports.importDialog.checklists',
  labels: 'imports.importDialog.labels',
  attachments: 'imports.importDialog.attachments',
  events: 'imports.importDialog.events',
  newPeople: 'imports.importDialog.newPeople',
  people: 'imports.importDialog.people',
  cards: 'imports.importDialog.cards',
  groupCards: 'imports.importDialog.groupCards',
  files: 'imports.importDialog.files',
  otherFiles: 'imports.importDialog.otherFiles',
} as const
const n = (count: number, noun: keyof typeof NOUNS) => t(NOUNS[noun], { count })

/** The preview's counts as plain lines: what comes in first, then what stays out. */
export function previewLines(p: Pick<ImportPreview, 'kind' | 'counts' | 'calendar'>): { in: string[]; out: string[] } {
  const c = p.counts
  const ins: string[] = []
  const outs: string[] = []
  if (p.kind === 'keep') {
    if (c.notes) ins.push(n(c.notes, 'notes'))
    if (c.checklists) ins.push(n(c.checklists, 'checklists'))
    if (c.labels) ins.push(`${n(c.labels, 'labels')}${c.new_labels && c.new_labels < c.labels ? t('imports.importDialog.new', { newLabels: c.new_labels }) : ''}`)
    if (c.archived) ins.push(t('imports.importDialog.stayArchived', { count: c.archived, n: n(c.archived, 'notes') }))
    if (c.trashed) outs.push(t('imports.importDialog.fromKeepsTrash', { n: n(c.trashed, 'notes') }))
    if (c.attachments) outs.push(n(c.attachments, 'attachments'))
  } else if (p.kind === 'ics') {
    ins.push(`${n(c.events ?? 0, 'events')}${p.calendar ? t('imports.importDialog.into', { calendarName: p.calendar.name }) : ''}`)
    if (c.existing) outs.push(t('imports.importDialog.alreadyInYourCalendars', { n: n(c.existing, 'events') }))
  } else {
    ins.push(n(c.people ?? 0, 'newPeople'))
    if (c.existing) ins.push(t('imports.importDialog.youKnowAlreadyOnlyEmpty', { n: n(c.existing, 'people') }))
    if (c.no_email) outs.push(t('imports.importDialog.withoutAnEmailAddress', { n: n(c.no_email, 'cards') }))
    if (c.groups) outs.push(n(c.groups, 'groupCards'))
  }
  if (c.unreadable) outs.push(t('imports.importDialog.thatCouldNotBeRead', { n: n(c.unreadable, 'files') }))
  if (c.other_files) outs.push(n(c.other_files, 'otherFiles'))
  return { in: ins, out: outs }
}

/** How many items an import would make or fill in (0: the Import button stays off). */
export function importable(p: Pick<ImportPreview, 'kind' | 'counts'>): number {
  const c = p.counts
  if (p.kind === 'keep') return (c.notes ?? 0) + (c.checklists ?? 0)
  if (p.kind === 'ics') return c.events ?? 0
  return (c.people ?? 0) + (c.existing ?? 0)
}

/** The toast after an import. */
export function doneText(b: Pick<ImportBatch, 'kind' | 'counts'>): string {
  const c = b.counts
  if (b.kind === 'keep') return t('imports.importDialog.importedFromGoogleKeep', { n: n((c.notes ?? 0) + (c.checklists ?? 0), 'notes') })
  if (b.kind === 'ics') return t('imports.importDialog.imported', { n: n(c.events ?? 0, 'events') })
  return t('imports.importDialog.imported2', { n: n(c.people ?? 0, 'people'), value: c.updated ? t('imports.importDialog.updated', { updated: c.updated }) : '' })
}

const ACCEPT: Record<ImportKind, string> = {
  keep: '.zip,.json,.html,application/zip,application/json,text/html',
  ics: '.ics,.ical,text/calendar',
  vcf: '.vcf,.vcard,text/vcard,text/x-vcard',
}

export interface ImportDialogProps {
  kind: ImportKind
  title: string
  intro: React.ReactNode
  /** ics: the calendars it may go into (id, name); the first is chosen. */
  calendars?: { id: number; name: string }[]
  onClose: () => void
  /** Query keys to refresh after the import and after Undo. */
  refresh: readonly (readonly unknown[])[]
}

export function ImportDialog({ kind, title, intro, calendars, onClose, refresh }: ImportDialogProps) {
  const qc = useQueryClient()
  const input = useRef<HTMLInputElement>(null)
  const [blobs, setBlobs] = useState<string[]>([])
  const [names, setNames] = useState<string[]>([])
  // '' is the default calendar (the server's choice, shown by name in the preview).
  const [calendar, setCalendar] = useState<string>('')
  const [preview, setPreview] = useState<ImportPreview | null>(null)
  const [busy, setBusy] = useState<'upload' | 'preview' | 'import' | null>(null)
  const [error, setError] = useState<string | null>(null)
  const refreshAll = () => refresh.forEach((k) => void qc.invalidateQueries({ queryKey: k }))

  const look = async (bs: string[], cal: string) => {
    setBusy('preview')
    setError(null)
    try {
      setPreview(await api.post<ImportPreview>('/imports/preview', { kind, blobs: bs, calendar: cal || null }))
    } catch (e) {
      setPreview(null)
      setError((e as Error).message)
    } finally {
      setBusy(null)
    }
  }
  const pick = async (files: FileList | null) => {
    if (!files?.length) return
    setBusy('upload')
    setError(null)
    setPreview(null)
    try {
      const up = await Promise.all([...files].map((f) => uploadFile(f).promise))
      setBlobs(up.map((u) => u.blob))
      setNames(up.map((u) => u.filename))
      await look(up.map((u) => u.blob), calendar)
    } catch (e) {
      setError((e as Error).message)
      setBusy(null)
    }
  }
  const run = async () => {
    setBusy('import')
    try {
      const b = await api.post<ImportBatch>('/imports', { kind, blobs, calendar: calendar || null })
      refreshAll()
      onClose()
      toast({
        message: doneText(b),
        actionLabel: t('common.undo'),
        onAction: () =>
          void api
            .post(`/imports/${b.id}/undo`)
            .then(() => {
              refreshAll()
              toast({ message: t('imports.importDialog.importUndone') })
            })
            .catch((e) => toastError(e, t('imports.importDialog.notUndone'))),
        duration: 12_000,
      })
    } catch (e) {
      toastError(e, t('imports.importDialog.notImported'))
      setBusy(null)
    }
  }
  const lines = preview ? previewLines(preview) : null
  const nothing = !!preview && importable(preview) === 0
  return (
    <Dialog
      open
      onClose={onClose}
      title={title}
      modal
      footer={
        <>
          <button type="button" className="btn text" onClick={onClose}>{t('common.cancel')}</button>
          <button type="button" className="btn filled" disabled={!preview || nothing || busy !== null} onClick={() => void run()} data-testid="import-run">
            {busy === 'import' ? <Spinner small /> : null}{' '}{t('imports.importDialog.import')}
          </button>
        </>
      }
    >
      <div className="stack import-dialog" data-testid={`import-${kind}`}>
        <div className="muted">{intro}</div>
        <input ref={input} type="file" hidden multiple={kind === 'keep'} accept={ACCEPT[kind]} data-testid="import-file"
          onChange={(e) => void pick(e.target.files)} />
        <button type="button" className="btn tonal import-pick" onClick={() => input.current?.click()} disabled={busy !== null}>
          <FileUp /> {kind === 'keep' ? (names.length ? t('imports.importDialog.chooseOtherFiles') : t('imports.importDialog.chooseTheTakeoutFile')) : names.length ? t('imports.importDialog.chooseAnotherFile') : t('imports.importDialog.chooseAFile')}
        </button>
        {names.length > 0 && <div className="import-files">{names.join(', ')}</div>}
        {kind === 'ics' && calendars && calendars.length > 1 && (
          <div className="field">
            <label htmlFor="import-cal">{t('common.calendar')}</label>
            <select id="import-cal" className="select" value={calendar} onChange={(e) => {
              setCalendar(e.target.value)
              if (blobs.length) void look(blobs, e.target.value)
            }}>
              <option value="">{t('imports.importDialog.yourDefaultCalendar')}</option>
              {calendars.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
          </div>
        )}
        {/* The preview area keeps its height while it loads (no jump). */}
        <div className="import-preview" aria-live="polite">
          {busy === 'upload' || busy === 'preview' ? (
            <div className="import-wait"><Spinner small /> {busy === 'upload' ? t('imports.importDialog.uploading') : t('imports.importDialog.readingTheFile')}</div>
          ) : error ? (
            <div className="banner error" role="alert">{error}</div>
          ) : preview && lines ? (
            <div data-testid="import-preview">
              <b>{t('imports.importDialog.whatComesIn')}</b>
              <ul>{lines.in.map((l) => <li key={l}>{l}</li>)}</ul>
              {lines.out.length > 0 && (
                <>
                  <b>{t('imports.importDialog.leftOut')}</b>
                  <ul className="muted">{lines.out.map((l) => <li key={l}>{l}</li>)}</ul>
                </>
              )}
              {preview.sample.length > 0 && (
                <div className="muted import-sample">{t('imports.importDialog.forExample', { filter: preview.sample.filter(Boolean).slice(0, 5).join(' · ') })}</div>
              )}
              <div className="muted import-undo-hint">{t('imports.importDialog.youCanUndoTheWhole')}</div>
            </div>
          ) : null}
        </div>
      </div>
    </Dialog>
  )
}
