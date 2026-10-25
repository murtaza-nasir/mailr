import { useState } from 'react'
import { useLabelList } from '../../api/hooks'
import { buildQuery, DATE_WITHIN_OPTIONS, parseQuery, type AdvancedSearch } from '../../lib/search'
import { SYSTEM_VIEWS } from '../../lib/views'
import { t } from '../../i18n'

interface Props {
  initialQuery: string
  onSearch?: (q: string) => void
  onCreateFilter?: (q: string) => void
  /** Filter dialog mode: hide "Search in" and show only the criteria. */
  filterMode?: boolean
  onChange?: (q: string) => void
}

export function AdvancedSearchForm({ initialQuery, onSearch, onCreateFilter, filterMode, onChange }: Props) {
  const [f, setF] = useState<AdvancedSearch>(() => parseQuery(initialQuery))
  const labels = (useLabelList()).filter((l) => !l.role)
  const set = <K extends keyof AdvancedSearch>(k: K, v: AdvancedSearch[K]) => {
    const next = { ...f, [k]: v }
    setF(next)
    onChange?.(buildQuery(next))
  }
  const q = buildQuery(f)
  const empty = !q.trim()

  return (
    <form
      className="adv-search"
      onSubmit={(e) => {
        e.preventDefault()
        if (!empty) onSearch?.(q)
      }}
    >
      <label htmlFor="as-from">{t('common.from')}</label>
      <input id="as-from" className="input underline" value={f.from} onChange={(e) => set('from', e.target.value)} autoFocus={!filterMode} />
      <label htmlFor="as-to">{t('common.to')}</label>
      <input id="as-to" className="input underline" value={f.to} onChange={(e) => set('to', e.target.value)} />
      <label htmlFor="as-subject">{t('common.subject')}</label>
      <input id="as-subject" className="input underline" value={f.subject} onChange={(e) => set('subject', e.target.value)} />
      <label htmlFor="as-has">{t('search.advancedSearchForm.hasTheWords')}</label>
      <input id="as-has" className="input underline" value={f.hasWords} onChange={(e) => set('hasWords', e.target.value)} />
      <label htmlFor="as-not">{t('search.advancedSearchForm.doesntHave')}</label>
      <input id="as-not" className="input underline" value={f.doesntHave} onChange={(e) => set('doesntHave', e.target.value)} />
      <label htmlFor="as-size">{t('search.advancedSearchForm.size')}</label>
      <div className="inline">
        <select className="select" style={{ width: 150 }} value={f.sizeOp} onChange={(e) => set('sizeOp', e.target.value as AdvancedSearch['sizeOp'])} aria-label={t('search.advancedSearchForm.sizeComparison')}>
          <option value="larger">{t('search.advancedSearchForm.greaterThan')}</option>
          <option value="smaller">{t('search.advancedSearchForm.lessThan')}</option>
        </select>
        <input id="as-size" className="input underline" inputMode="numeric" style={{ width: 90 }} value={f.size} onChange={(e) => set('size', e.target.value.replace(/[^\d]/g, ''))} />
        <select className="select" style={{ width: 90 }} value={f.sizeUnit} onChange={(e) => set('sizeUnit', e.target.value as AdvancedSearch['sizeUnit'])} aria-label={t('search.advancedSearchForm.sizeUnit')}>
          <option>{t('search.advancedSearchForm.mb')}</option>
          <option>{t('search.advancedSearchForm.kb')}</option>
          <option value="bytes">{t('search.advancedSearchForm.bytes')}</option>
        </select>
      </div>
      <label htmlFor="as-date">{t('search.advancedSearchForm.dateWithin')}</label>
      <div className="inline">
        <select id="as-date" className="select" style={{ width: 150 }} value={f.dateWithin} onChange={(e) => set('dateWithin', e.target.value as AdvancedSearch['dateWithin'])}>
          <option value="">{t('search.advancedSearchForm.anyTime')}</option>
          {DATE_WITHIN_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
        <input type="date" className="input underline" style={{ width: 170 }} value={f.dateAnchor} disabled={!f.dateWithin} onChange={(e) => set('dateAnchor', e.target.value)} aria-label={t('search.advancedSearchForm.aroundDate')} />
      </div>
      {!filterMode && (
        <>
          <label htmlFor="as-in">{t('common.search')}</label>
          <select id="as-in" className="select" value={f.searchIn} onChange={(e) => set('searchIn', e.target.value)}>
            <option value="all">{t('common.allMail2')}</option>
            <option value="anywhere">{t('search.advancedSearchForm.mailSpamTrash')}</option>
            {SYSTEM_VIEWS.filter((v) => v.id !== 'all').map((v) => (
              <option key={v.id} value={v.id}>
                {v.title}
              </option>
            ))}
            {labels.map((l) => (
              <option key={l.id} value={`label:${l.name}`}>
                {l.name}
              </option>
            ))}
          </select>
        </>
      )}
      <span />
      <label className="choice" style={{ padding: 0 }}>
        <input type="checkbox" className="checkbox" checked={f.hasAttachment} onChange={(e) => set('hasAttachment', e.target.checked)} />
        <span>{t('common.hasAttachment')}</span>
      </label>
      {!filterMode && (
        <div className="actions">
          <button type="button" className="btn text" disabled={empty} onClick={() => onCreateFilter?.(q)}>
            {t('common.createFilter')}
          </button>
          <button type="submit" className="btn filled" disabled={empty}>
            {t('common.search')}
          </button>
        </div>
      )}
    </form>
  )
}
