// A numbered citation chip: hover or focus shows who, what, when, and the quoted words; click
// opens the email at that spot with the quote highlighted. An event or task chip shows its title,
// when it is and its colour, and opens the record (lib/routes eventPath / taskPath). A recording
// chip ("▶ Weekly sync · 12:34") opens the recording sheet at that moment. A web chip shows the page's
// site and title and opens the page in a new tab, outside mailr. A note chip shows the note's title and
// the cited item, and opens the note with that item focused.

import { AlertTriangle, BadgeCheck, CalendarDays, CheckSquare, ExternalLink, Globe, Play, StickyNote, Table2 } from 'lucide-react'
import { useId, useLayoutEffect, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { placeFloating } from '../../../lib/floating'
import { fmtT } from '../../speakr/format'
import { useRecordingSheet } from '../../speakr/store'
import { useDatePrefs } from '../../tasks/api'
import { useAmbroseNav } from './ambroseNav'
import { citationRoute, citeWhen, openWebCitation, shortDate, type Citation } from './citations'
import { t } from '../../../i18n'

/** A chip's card: drawn in a layer over the whole page (not inside the chat, which would clip it),
 *  above the chip when there is room and below it otherwise, and always inside the window
 *  (lib/floating). It follows the chip while the chat scrolls. */
function CiteCard({ id, children }: { id: string; children: ReactNode }) {
  const marker = useRef<HTMLSpanElement>(null)
  const card = useRef<HTMLSpanElement>(null)
  const [pos, setPos] = useState<{ top: number; left: number; side: 'top' | 'bottom' } | null>(null)
  useLayoutEffect(() => {
    const place = () => {
      const anchor = marker.current?.parentElement
      const el = card.current
      if (!anchor || !el) return
      const r = anchor.getBoundingClientRect()
      setPos(placeFloating(r, { width: el.offsetWidth, height: el.offsetHeight }, { width: window.innerWidth, height: window.innerHeight }))
    }
    place()
    window.addEventListener('scroll', place, true)
    window.addEventListener('resize', place)
    return () => {
      window.removeEventListener('scroll', place, true)
      window.removeEventListener('resize', place)
    }
  }, [])
  return (
    <>
      <span ref={marker} hidden />
      {createPortal(
        <span ref={card} className="cite-card" role="tooltip" id={id} data-side={pos?.side}
          style={pos ? { top: pos.top, left: pos.left } : { top: 0, left: 0, visibility: 'hidden' }}>
          {children}
        </span>,
        document.body,
      )}
    </>
  )
}

export function CitationChip({ c, onOpen }: { c: Citation; onOpen?: (c: Citation) => void }) {
  if (c.kind === 'event' || c.kind === 'task') return <RecordChip c={c} />
  if (c.kind === 'recording') return <RecordingChip c={c} />
  if (c.kind === 'web') return <WebChip c={c} />
  if (c.kind === 'table') return <TableChip c={c} />
  if (c.kind === 'note') return <NoteChip c={c} />
  return <MailChip c={c} onOpen={onOpen} />
}

/** A table or one of its rows ([[table:<id>#<row>]]): the row's primary value and the table's
 * name; a click opens the table with that row's form. */
function TableChip({ c }: { c: Citation }) {
  const [open, setOpen] = useState(false)
  const tipId = useId()
  const nav = useAmbroseNav()
  const title = c.title || c.subject || t('common.table')
  return (
    <span className="cite-wrap" onMouseEnter={() => setOpen(true)} onMouseLeave={() => setOpen(false)}>
      <button type="button" className="cite-chip" aria-describedby={open ? tipId : undefined}
        aria-label={t('rail.assistant.citationChip.text', { value: c.row ? t('rail.assistant.citationChip.row') : t('common.table'), n: c.n, title, value2: c.row && c.from ? t('rail.assistant.citationChip.in', { from: c.from }) : '' })}
        onFocus={() => setOpen(true)} onBlur={() => setOpen(false)}
        onClick={(e) => {
          e.preventDefault()
          const route = citationRoute(c)
          if (route) nav(route)
        }}>
        {c.n}
      </button>
      {open && (
        <CiteCard id={tipId}>
          <span className="cite-card-head"><span className="cite-from"><Table2 size={13} aria-hidden /> {c.from || t('common.table')}</span></span>
          <span className="cite-subject" dir="auto">{title}</span>
          <span className="cite-status ok"><Table2 size={13} />{c.row ? t('rail.assistant.citationChip.opensTheRow') : t('rail.assistant.citationChip.opensTheTable')}</span>
        </CiteCard>
      )}
    </span>
  )
}

function RecordChip({ c }: { c: Citation }) {
  const [open, setOpen] = useState(false)
  const tipId = useId()
  const nav = useAmbroseNav()
  const title = c.title || c.subject || t('common.noTitle')
  const when = citeWhen(c, useDatePrefs().zone)
  const Icon = c.kind === 'event' ? CalendarDays : CheckSquare
  return (
    <span className="cite-wrap" onMouseEnter={() => setOpen(true)} onMouseLeave={() => setOpen(false)}>
      <button
        type="button"
        className="cite-chip"
        style={c.color ? { boxShadow: `inset 0 -2px 0 ${c.color}` } : undefined}
        aria-describedby={open ? tipId : undefined}
        aria-label={t('rail.assistant.citationChip.text', { value: c.kind === 'event' ? t('common.event') : t('common.task'), n: c.n, title, value2: when ? `, ${when}` : '' })}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onClick={(e) => {
          e.preventDefault()
          const route = citationRoute(c)
          if (route) nav(route)
        }}
      >
        {c.n}
      </button>
      {open && (
        <CiteCard id={tipId}>
          <span className="cite-card-head">
            <span className="cite-from">
              <span aria-hidden style={{ display: 'inline-block', width: 8, height: 8, borderRadius: 4, marginInlineEnd: 6,
                background: c.color || 'currentColor', opacity: c.color ? 1 : 0.4 }} />
              {c.from || (c.kind === 'event' ? t('common.calendar') : t('common.tasks'))}
            </span>
            {when && <span className="cite-date">{when}</span>}
          </span>
          <span className="cite-subject" dir="auto">{title}</span>
          {c.location && <span className="cite-quote" dir="auto">{c.location}</span>}
          <span className="cite-status ok">
            <Icon size={13} />
            {c.kind === 'event' ? t('rail.assistant.citationChip.opensTheEvent') : c.completed ? t('rail.assistant.citationChip.opensTheTaskDone') : t('rail.assistant.citationChip.opensTheTask')}
          </span>
        </CiteCard>
      )}
    </span>
  )
}

function MailChip({ c, onOpen }: { c: Citation; onOpen?: (c: Citation) => void }) {
  const [open, setOpen] = useState(false)
  const tipId = useId()
  const title = c.subject || t('common.noSubject')
  return (
    <span className="cite-wrap" onMouseEnter={() => setOpen(true)} onMouseLeave={() => setOpen(false)}>
      <button
        type="button"
        className={`cite-chip${c.verified ? '' : ' unverified'}`}
        aria-describedby={open ? tipId : undefined}
        aria-label={t('rail.assistant.citationChip.source', { n: c.n, title, value: c.from ? t('rail.assistant.citationChip.from', { from: c.from }) : '' })}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onClick={(e) => {
          e.preventDefault()
          onOpen?.(c)
        }}
      >
        {c.n}
      </button>
      {open && (
        <CiteCard id={tipId}>
          <span className="cite-card-head">
            <span className="cite-from">{c.from || t('common.conversation')}</span>
            {c.date && <span className="cite-date">{shortDate(c.date)}</span>}
          </span>
          <span className="cite-subject" dir="auto">{title}</span>
          {c.quote && <span className="cite-quote">“<bdi>{c.quote}</bdi>”</span>}
          <span className={`cite-status${c.verified ? ' ok' : ''}`}>
            {c.verified ? <BadgeCheck size={13} /> : <AlertTriangle size={13} />}
            {c.verified ? (c.quote ? t('rail.assistant.citationChip.quoteFoundInTheEmail') : t('rail.assistant.citationChip.opensTheConversation')) : t('rail.assistant.citationChip.quoteNotFoundInThe')}
          </span>
        </CiteCard>
      )}
    </span>
  )
}

// ACT recording.view @web.recording (from a citation)
function RecordingChip({ c }: { c: Citation }) {
  const [open, setOpen] = useState(false)
  const tipId = useId()
  const title = c.title || c.subject || t('common.recording')
  const at = fmtT(c.t ?? c.start)
  return (
    <span className="cite-wrap" onMouseEnter={() => setOpen(true)} onMouseLeave={() => setOpen(false)}>
      <button
        type="button"
        className={`cite-chip rec${c.verified ? '' : ' unverified'}`}
        aria-describedby={open ? tipId : undefined}
        aria-label={t('rail.assistant.citationChip.recording', { n: c.n, title, value: at ? t('rail.assistant.citationChip.at', { at }) : '' })}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onClick={(e) => {
          e.preventDefault()
          if (c.connector_id && c.recording_id) useRecordingSheet.getState().show(c.connector_id, c.recording_id, c.t ?? null)
        }}
      >
        <Play size={9} aria-hidden /> {c.n}
      </button>
      {open && (
        <CiteCard id={tipId}>
          <span className="cite-card-head">
            <span className="cite-from">{c.speaker || t('common.recording')}</span>
            {c.date && <span className="cite-date">{shortDate(c.date)}</span>}
          </span>
          <span className="cite-subject">▶ <bdi>{title}</bdi>{at ? ` · ${at}` : ''}</span>
          {c.quote && <span className="cite-quote">“<bdi>{c.quote}</bdi>”</span>}
          <span className={`cite-status${c.verified ? ' ok' : ''}`}>
            {c.verified ? <BadgeCheck size={13} /> : <AlertTriangle size={13} />}
            {c.verified ? (c.quote ? t('rail.assistant.citationChip.saidWithin30SecondsOf') : t('rail.assistant.citationChip.opensTheRecording')) : t('rail.assistant.citationChip.quoteNotFoundInThe2')}
          </span>
        </CiteCard>
      )}
    </span>
  )
}

// ACT agent.web_search @web.panel (a web source opens outside mailr)
function WebChip({ c }: { c: Citation }) {
  const [open, setOpen] = useState(false)
  const tipId = useId()
  const site = c.site || c.from || t('rail.assistant.citationChip.webPage')
  const title = c.title || c.subject || site
  return (
    <span className="cite-wrap" onMouseEnter={() => setOpen(true)} onMouseLeave={() => setOpen(false)}>
      <button
        type="button"
        className="cite-chip web"
        aria-describedby={open ? tipId : undefined}
        aria-label={t('rail.assistant.citationChip.webSourceOpensInA', { n: c.n, title, site })}
        data-testid="cite-web"
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onClick={(e) => {
          e.preventDefault()
          openWebCitation(c)
        }}
      >
        <Globe size={9} aria-hidden /> {c.n}
      </button>
      {open && (
        <CiteCard id={tipId}>
          <span className="cite-card-head">
            <span className="cite-from">{site}</span>
            {c.date && <span className="cite-date">{shortDate(c.date)}</span>}
          </span>
          <span className="cite-subject" dir="auto">{title}</span>
          <span className="cite-status ok">
            <ExternalLink size={13} />
            {t('rail.assistant.citationChip.opensThePageInA')}
          </span>
        </CiteCard>
      )}
    </span>
  )
}

// A note or one item of a list ([[note:id#item]]): opens the note with the item focused.
function NoteChip({ c }: { c: Citation }) {
  const [open, setOpen] = useState(false)
  const tipId = useId()
  const nav = useAmbroseNav()
  const title = c.title || c.subject || t('common.note')
  const what = c.note_kind === 'checklist' ? t('common.list') : t('common.note')
  return (
    <span className="cite-wrap" onMouseEnter={() => setOpen(true)} onMouseLeave={() => setOpen(false)}>
      <button
        type="button"
        className="cite-chip note"
        aria-describedby={open ? tipId : undefined}
        aria-label={t('rail.assistant.citationChip.text2', { what, n: c.n, title, value: c.item_id && c.snippet ? t('rail.assistant.citationChip.item', { snippet: c.snippet }) : '' })}
        data-testid="cite-note"
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onClick={(e) => {
          e.preventDefault()
          const route = citationRoute(c)
          if (route) nav(route)
        }}
      >
        <StickyNote size={9} aria-hidden /> {c.n}
      </button>
      {open && (
        <CiteCard id={tipId}>
          <span className="cite-card-head">
            <span className="cite-from">{what}</span>
            {c.date && <span className="cite-date">{shortDate(c.date)}</span>}
          </span>
          <span className="cite-subject" dir="auto">{title}</span>
          {c.snippet && <span className="cite-quote">{c.item_id ? (c.checked ? '☑ ' : '☐ ') : ''}<bdi>{c.snippet}</bdi></span>}
          <span className="cite-status ok">
            <StickyNote size={13} />
            {c.item_id ? t('rail.assistant.citationChip.opensTheListAtThis') : t('rail.assistant.citationChip.opensThe', { what: what.toLowerCase() })}
          </span>
        </CiteCard>
      )}
    </span>
  )
}
