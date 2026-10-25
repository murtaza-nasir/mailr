// ACT-SURFACES: web.recording
import { describe, expect, it } from 'vitest'
import { toCalendarQuery } from '../rail/SearchGlance'
import { excerptHtml, extractedEventSeed, fmtDuration, fmtT, isMedia, markdownHtml, recordingPath, recordingQuery, speakrAt } from './format'
import { recordingMode } from './palette'

describe('recording times', () => {
  it('formats seconds and durations', () => {
    expect(fmtT(754)).toBe('12:34')
    expect(fmtT(3723)).toBe('1:02:03')
    expect(fmtT(null)).toBe('')
    expect(fmtDuration(2700)).toBe('45 min')
    expect(fmtDuration(3900)).toBe('1 h 05 min')
  })
  it('builds the sheet route and Speakr deep links', () => {
    expect(recordingPath(3, 12)).toBe('/recordings/3/12')
    expect(recordingPath(3, 12, 754.6)).toBe('/recordings/3/12?t=754')
    expect(speakrAt('https://s.example/recordings/12?t=5', 754)).toBe('https://s.example/recordings/12?t=754')
    expect(speakrAt('https://s.example/recordings/12')).toBe('https://s.example/recordings/12')
  })
})

describe('markdownHtml', () => {
  it('converts headings, lists and emphasis, escaping everything else', () => {
    expect(markdownHtml('## Decisions\n- **Ship** on Friday\n- <script>x</script>\n\nThanks *all*')).toBe(
      '<p><b>Decisions</b></p><ul><li><b>Ship</b> on Friday</li><li>&lt;script&gt;x&lt;/script&gt;</li></ul><p>Thanks <i>all</i></p>',
    )
    expect(markdownHtml('1. one\n2. two')).toBe('<ol><li>one</li><li>two</li></ol>')
  })
  it('quotes transcript turns', () => {
    expect(excerptHtml('Sync <1>', [{ t: 754, speaker: 'Dana', text: 'We ship & go' }])).toBe(
      '<blockquote><div><i>From the recording “Sync &lt;1&gt;”</i></div><div><b>Dana</b> (12:34): We ship &amp; go</div></blockquote><p></p>',
    )
  })
})

// ACT recording.search, ACT mail.to_speakr, ACT recording.event_to_calendar
describe('recordings in mail', () => {
  it('reads the recording part of a mail search', () => {
    expect(recordingQuery('has:recording')).toEqual({ text: '', on: true })
    expect(recordingQuery('recording:"budget review" from:dana')).toEqual({ text: 'budget review', on: true })
    expect(recordingQuery('weekly sync -recording:old is:unread')).toEqual({ text: 'weekly sync', on: false })
    expect(recordingQuery('-has:recording')).toEqual({ text: '', on: false })
    // The calendar part of the same search drops the operator.
    expect(toCalendarQuery('recording:budget lunch')).toBe('lunch')
  })
  it('knows audio and video attachments', () => {
    expect(isMedia({ type: 'audio/mpeg', filename: 'a.bin' })).toBe(true)
    expect(isMedia({ type: 'application/octet-stream', filename: 'Voice memo.M4A' })).toBe(true)
    expect(isMedia({ type: 'video/mp4', filename: 'x' })).toBe(true)
    expect(isMedia({ type: 'application/pdf', filename: 'notes.pdf' })).toBe(false)
  })
  it('opens the palette\'s recording mode', () => {
    expect(recordingMode('Recording: bud')).toBe('bud')
    expect(recordingMode('recording:')).toBe('')
    expect(recordingMode('record')).toBeNull()
  })
  it('turns an extracted event into the editor\'s seed', () => {
    const seed = extractedEventSeed({ title: 'Budget review', start: '2026-10-08T15:00:00Z', end: null, location: 'Room 4', description: 'Follow-up' },
      { title: 'Weekly sync', url: 'https://s.example/recordings/12' })!
    expect(seed.summary).toBe('Budget review')
    expect(seed.end.getTime() - seed.start.getTime()).toBe(3_600_000)
    expect(seed.description).toBe('Follow-up\n\nFrom the recording “Weekly sync”: https://s.example/recordings/12')
    expect(extractedEventSeed({ title: 'x', start: null, end: null, location: '', description: '' }, { title: 'y', url: 'u' })).toBeNull()
  })
  it('prefills guests, the reminder and floating times from a newer Speakr', () => {
    const seed = extractedEventSeed({
      title: 'Budget review', start: '2026-10-08T15:00:00Z', end: '2026-10-08T16:00:00', location: '', description: '',
      attendees: [{ name: 'Priya Shah', email: 'priya@example.org' }, { name: 'Dana Lee', email: null }],
      reminder_minutes: 15, floating: true,
    }, { title: 'Weekly sync', url: 'u' })!
    // Floating: 15:00 on the wall clock in the user's own zone, whatever zone the value carried.
    expect(seed.start.getHours()).toBe(15)
    expect(seed.end.getTime() - seed.start.getTime()).toBe(3_600_000)
    expect(seed.guests).toEqual([{ email: 'priya@example.org', name: 'Priya Shah' }])
    expect(seed.reminders).toEqual([15])
    expect(seed.description).toBe('Also mentioned: Dana Lee\n\nFrom the recording “Weekly sync”: u')
  })
})
