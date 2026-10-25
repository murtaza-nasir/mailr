// "Insert times" in the compose toolbar (event.insert_times): three free slots from find-a-time
// (POST /calendar/find-time over my busy calendars and working hours), inserted at the cursor as
// text in my date format ("Would any of these work? …"). The draft saves it like any typing.
// ACT event.insert_times @web.editor, ACT event.find_time @web.editor

import { useQuery } from '@tanstack/react-query'
import type { Editor } from '@tiptap/react'
import { CalendarSearch } from 'lucide-react'
import { useState } from 'react'
import { api } from '../../api/client'
import { useSettings } from '../../api/hooks'
import { Spinner } from '../../components/bits'
import { Popover, useAnchor } from '../../components/Popover'
import { resolveDatePrefs, utcIsoformat } from '../../lib/dates'
import { slotText, spreadSlots, timesHtml } from './meeting'
import { t } from '../../i18n'

type Range = 'next5' | 'nextweek'

function rangeOf(r: Range, now: Date): { from: Date; to: Date } {
  if (r === 'next5') return { from: now, to: new Date(now.getTime() + 7 * 86_400_000) }
  const d = new Date(now)
  d.setHours(0, 0, 0, 0)
  d.setDate(d.getDate() + (((8 - d.getDay()) % 7) || 7))
  return { from: d, to: new Date(d.getTime() + 7 * 86_400_000) }
}

export function InsertTimesButton({ editor }: { editor: Editor | null }) {
  const menu = useAnchor()
  return (
    <>
      <button type="button" className={`icon-btn${menu.open ? ' active' : ''}`} aria-label={t('conversation.insertTimes.insertTimes')} data-tip={t('conversation.insertTimes.insertTimesYouAreFree')}
        aria-haspopup="dialog" onMouseDown={(e) => e.preventDefault()} onClick={(e) => menu.toggle(e.currentTarget)}>
        <CalendarSearch />
      </button>
      <Popover anchor={menu.anchor} open={menu.open} onClose={menu.close} placement="top-start" role="dialog" label={t('conversation.insertTimes.insertTimes')} minWidth={300} className="it-pop">
        {menu.open && <Picker onInsert={(html) => {
          menu.close()
          editor?.chain().focus().insertContent(html).run()
        }} />}
      </Popover>
    </>
  )
}

function Picker({ onInsert }: { onInsert: (html: string) => void }) {
  const s = useSettings() as unknown as { timezone?: string; week_start?: number; time_format?: string }
  const prefs = resolveDatePrefs(s)
  const [duration, setDuration] = useState(30)
  const [range, setRange] = useState<Range>('next5')
  const [off, setOff] = useState<Set<number>>(new Set())
  const q = useQuery({
    queryKey: ['calendar', 'find-time', 'insert', duration, range],
    queryFn: async () => {
      const { from, to } = rangeOf(range, new Date())
      const r = await api.post<{ slots: { start: string; end: string }[] }>('/calendar/find-time', {
        duration, from: utcIsoformat(from), to: utcIsoformat(to), tz: prefs.zone, now: utcIsoformat(new Date()), granularity: 30, limit: 12,
      })
      return spreadSlots(r.slots.map((x) => ({ start: new Date(x.start), end: new Date(x.end) })), 3, prefs.zone)
    },
    staleTime: 30_000,
    retry: false,
  })
  const slots = q.data ?? []
  const picked = slots.filter((_, i) => !off.has(i))
  return (
    <div className="it">
      <b className="it-head">{t('conversation.insertTimes.insertTimesYouAreFree')}</b>
      <div className="it-opts">
        <select className="select" aria-label={t('conversation.insertTimes.length')} value={duration} onChange={(e) => { setDuration(Number(e.target.value)); setOff(new Set()) }}>
          {[15, 30, 45, 60, 90].map((m) => <option key={m} value={m}>{m < 60 ? t('conversation.insertTimes.min', { m }) : m === 60 ? t('common.n1Hour') : t('conversation.insertTimes.n1Hours')}</option>)}
        </select>
        <select className="select" aria-label={t('common.when')} value={range} onChange={(e) => { setRange(e.target.value as Range); setOff(new Set()) }}>
          <option value="next5">{t('conversation.insertTimes.next5WorkingDays')}</option>
          <option value="nextweek">{t('common.nextWeek')}</option>
        </select>
      </div>
      {q.isLoading ? (
        <div className="it-wait"><Spinner small /></div>
      ) : q.isError ? (
        <p className="muted it-note">{t('conversation.insertTimes.freeTimesNeedAConnected')}</p>
      ) : !slots.length ? (
        <p className="muted it-note">{t('conversation.insertTimes.noFreeTimeInYour')}</p>
      ) : (
        <div className="it-slots" role="group" aria-label={t('conversation.insertTimes.timesToInsert')}>
          {slots.map((x, i) => (
            <label key={x.start.toISOString()} className="it-slot">
              <input type="checkbox" className="checkbox" checked={!off.has(i)}
                onChange={(e) => setOff((o) => { const n = new Set(o); if (e.target.checked) n.delete(i); else n.add(i); return n })} />
              <span>{slotText(x.start, x.end, prefs)}</span>
            </label>
          ))}
        </div>
      )}
      <div className="it-actions">
        <button type="button" className="btn filled sm" disabled={!picked.length} onClick={() => onInsert(timesHtml(picked, prefs))}>{t('conversation.insertTimes.insert')}</button>
      </div>
    </div>
  )
}
