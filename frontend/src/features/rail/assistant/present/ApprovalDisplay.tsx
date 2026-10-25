// The body of an approval card, drawn from the server's display model (types.ts): a summary
// sentence, then one row per field with a typed value (label chips in their colours, people,
// search conditions, dates, lists, quotes, before/after). The same renderer serves the assistant
// panel, the top-bar approvals drawer and Settings → Agents & API; vitest runs it on the golden
// fixtures in spec/fixtures/agent-blocks-fixtures.json. It only draws; ApprovalCard.tsx decides (approval.approve).

import { useState } from 'react'
import { chipStyle } from '../../../../lib/labels'
import type { ChoiceItem, Condition, Display, DisplayField, DisplayValue, LabelRef, Person } from './types'
import { t } from '../../../../i18n'
import { msgText, msgsText } from './serverText'

function Chips({ items }: { items: LabelRef[] }) {
  return (
    <span className="pv-chips">
      {items.map((l, i) => (
        <span key={`${l.name}-${i}`} className="label-chip" style={chipStyle(l.color)} title={l.name}>
          {l.name}
        </span>
      ))}
    </span>
  )
}

function People({ items, more }: { items: Person[]; more: number }) {
  return (
    <span className="pv-people">
      {items.map((p, i) => (
        <span key={`${p.email}-${i}`} className="pv-person">
          {p.name ? (
            <>
              <span className="pv-person-name">{p.name}</span> <span className="pv-person-email">{p.email}</span>
            </>
          ) : (
            <span className="pv-person-name">{p.email}</span>
          )}
        </span>
      ))}
      {more > 0 && <span className="pv-more">{t('rail.assistant.present.approvalDisplay.andMore', { more })}</span>}
    </span>
  )
}

function ConditionRow({ c }: { c: Condition }) {
  const [all, setAll] = useState(false)
  const long = c.values.length > 3 && c.field !== 'any' && c.field !== 'all' && c.field !== 'label'
  return (
    <li className={c.neg ? 'neg' : undefined}>
      <span>{c.text}</span>
      {c.labels && c.labels.length > 0 && c.field === 'label' && <Chips items={c.labels} />}
      {long && (
        <>
          {' '}
          <button type="button" className="pv-link" aria-expanded={all} onClick={() => setAll(!all)}>
            {all ? t('rail.assistant.present.approvalDisplay.showFewer') : t('rail.assistant.present.approvalDisplay.showAll', { valuesLength: c.values.length })}
          </button>
          {all && (
            <span className="pv-values">
              {c.values.map((v, i) => (
                <code key={`${v}-${i}`}>{v}</code>
              ))}
            </span>
          )}
        </>
      )}
    </li>
  )
}

function Query({ v }: { v: Extract<DisplayValue, { t: 'query' }> }) {
  if (!v.conditions.length) return <code className="pv-raw">{v.raw || v.text}</code>
  return (
    <div className="pv-query">
      <ul className="pv-conds">
        {v.conditions.map((c, i) => (
          <ConditionRow key={i} c={c} />
        ))}
      </ul>
      {v.raw && (
        <details className="pv-syntax">
          <summary>{t('rail.assistant.present.approvalDisplay.searchSyntax')}</summary>
          <code>{v.raw}</code>
        </details>
      )}
    </div>
  )
}

function Quote({ text }: { text: string }) {
  const [open, setOpen] = useState(false)
  const long = text.length > 280 || text.split('\n').length > 5
  return (
    <div className={`pv-quote${long && !open ? ' clamped' : ''}`}>
      <div className="pv-quote-text bidi" dir="auto">{text}</div>
      {long && (
        <button type="button" className="pv-link" aria-expanded={open} onClick={() => setOpen(!open)}>
          {open ? t('rail.assistant.present.approvalDisplay.showLess') : t('common.showMore')}
        </button>
      )}
    </div>
  )
}

function Choices({ items }: { items: ChoiceItem[] }) {
  return (
    <ul className="pv-choices">
      {items.map((c, i) => (
        <li key={`${c.name}-${i}`}>
          <span className="pv-choice-name" dir="auto">{c.name}</span>
          {c.description && <span className="pv-choice-desc" dir="auto">{c.description}</span>}
          {c.then.length > 0 && (
            <span className="pv-choice-then">
              {c.then.map((f, j) => (
                <span key={j}>
                  <span className="pv-choice-label">{msgText(f.label_msg, f.label)}</span> <Value v={f.value} />
                </span>
              ))}
            </span>
          )}
        </li>
      ))}
    </ul>
  )
}

/** One value by its type; an unknown type shows the server's text. */
export function Value({ v }: { v: DisplayValue }) {
  switch (v.t) {
    case 'labels':
      return <Chips items={(v as Extract<DisplayValue, { t: 'labels' }>).items} />
    case 'people': {
      const p = v as Extract<DisplayValue, { t: 'people' }>
      return <People items={p.items} more={p.more} />
    }
    case 'query':
      return <Query v={v as Extract<DisplayValue, { t: 'query' }>} />
    case 'list': {
      const l = v as Extract<DisplayValue, { t: 'list' }>
      return (
        <ul className="pv-list">
          {l.items.map((x, i) => (
            <li key={i} dir="auto">{msgText(l.msgs?.[i], x)}</li>
          ))}
          {l.more > 0 && <li className="pv-more">{t('rail.assistant.present.approvalDisplay.andMore', { more: l.more })}</li>}
        </ul>
      )
    }
    case 'quote':
      return <Quote text={v.text} />
    case 'diff': {
      const d = v as Extract<DisplayValue, { t: 'diff' }>
      return (
        <span className="pv-diff">
          {d.before && <del dir="auto">{d.before}</del>}
          {d.before && <span aria-hidden> → </span>}
          <ins dir="auto">{msgText(d.after_msg, d.after)}</ins>
        </span>
      )
    }
    case 'choices':
      return <Choices items={(v as Extract<DisplayValue, { t: 'choices' }>).items} />
    default: {
      const x = v as Extract<DisplayValue, { t: 'text' }>
      return <span className="pv-text" dir="auto">{x.msgs ? msgsText(x.msgs, v.text) : msgText(x.msg, v.text)}</span>
    }
  }
}

function Field({ f }: { f: DisplayField }) {
  if (!f.label) {
    return (
      <div className="pv-row wide">
        <Value v={f.value} />
      </div>
    )
  }
  return (
    <div className="pv-row">
      <span className="pv-label">{msgText(f.label_msg, f.label)}</span>
      <span className="pv-value">
        <Value v={f.value} />
      </span>
    </div>
  )
}

export function ApprovalDisplay({ d }: { d: Display }) {
  return (
    <div className="pv">
      {d.summary && <p className="pv-summary">{msgText(d.summary_msg, d.summary)}</p>}
      {d.reason && <p className="pv-reason">{msgText(d.reason_msg, d.reason)}</p>}
      {d.fields.length > 0 && (
        <div className="pv-fields">
          {d.fields.map((f, i) => (
            <Field key={i} f={f} />
          ))}
        </div>
      )}
    </div>
  )
}
