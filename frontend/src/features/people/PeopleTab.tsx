// Settings → Mail → People & groups (web.people): everyone you correspond with, strongest first, with
// a person card (details, where each came from, groups, recent Speakr recordings with them, details
// found in signatures to accept or dismiss); contact groups (create, rename, colour, members, delete); groups suggested from the
// mail you send (accept with a name, or dismiss); and the address book read from your calendar
// server (read-only).

import { BookUser, Check, FileUp, MoreVertical, Plus, RefreshCw, Trash2, UserPlus, Users, X } from 'lucide-react'
import { useQueryClient } from '@tanstack/react-query'
import { MemoryDialog, MemoryRow } from '../settings/assistant/memoryBits'
import { EntityDialog } from '../settings/assistant/EntityCard'
import type { Memory } from '../settings/assistant/types'
import { useAssistantName } from '../rail/assistant/useAssistantName'
import { useEffect, useState } from 'react'
import { useSearchAnswer } from './searchAnswer'
import { useSearchParams } from 'react-router'
import { Avatar, Skeleton, Spinner } from '../../components/bits'
import { ConfirmDialog, Dialog } from '../../components/Dialog'
import { displayName, formatRelative, isValidEmail } from '../../lib/format'
import { toast } from '../../stores/ui'
import { SWATCHES } from '../calendar/colour'
import { Section } from '../settings/common'
import { MenuItem, Popover } from '../../components/Popover'
import { ImportDialog } from '../imports/ImportDialog'
import { PersonRecordings } from '../speakr/PersonRecordings'
import {
  useAddressBooks, useDetailDecision, useGroupMutations, useGroups, usePeopleList, usePersonCard, useSyncAddressBooks,
  type DetailSuggestion, type Group, type Member, type PersonCard as PersonCardData, type PersonRow,
} from './api'
import './people.css'
import { mailtoHref } from '../../lib/mailto'
import { openComposeWith } from '../compose/mailtoLinks'
import { t } from '../../i18n'

const SOURCE_TEXT: Record<string, string> = { carddav: t('common.addressBook'), signature: 'signature', user: 'you', mail: 'mail', import: t('people.peopleTab.anImport') }
const FIELD_TEXT: Record<DetailSuggestion['field'], string> = { phone: t('people.peopleTab.phone'), title: t('common.title'), organization: t('people.peopleTab.organization'), address: t('common.address') }
const PAGE = 50

export function PeopleTab() {
  return (
    <>
      <PeopleSection />
      <GroupsSection />
      <SuggestedGroupsSection />
      <AddressBookSection />
    </>
  )
}

// ---------------- people ----------------

// ACT person.find @web.people
function PeopleSection() {
  const [q, setQ] = useState('')
  const [limit, setLimit] = useState(PAGE)
  // ?person=<address> opens that card (the recording sheet's People tab links here).
  const [params] = useSearchParams()
  const asked = params.get('person')
  const [sel, setSel] = useState<string | null>(asked)
  // A person Ambrose knows who is not a contact opens their entry instead.
  const [known, setKnown] = useState<number | null>(null)
  useEffect(() => { if (asked) setSel(asked) }, [asked])
  const list = usePeopleList(q.trim(), limit)
  const rows = list.data ?? []
  return (
    <Section title={t('common.people')} desc={t('people.peopleTab.everyoneYouWriteToOr')}
      actions={<PeopleMoreMenu />}>
      <div className="ppl-layout">
        <div>
          <input className="input ppl-search" value={q} placeholder={t('people.peopleTab.searchByNameAddressOr')} aria-label={t('people.peopleTab.searchPeople')}
            onChange={(e) => { setQ(e.target.value); setLimit(PAGE) }} />
          {list.isLoading ? <Skeleton height={200} /> : rows.length === 0 ? (
            <p className="ppl-muted">{q ? t('people.peopleTab.nobodyMatches') : t('people.peopleTab.nobodyYetPeopleAppearHere')}</p>
          ) : (
            <ul className="ppl-list" aria-label={t('common.people')}>
              {rows.map((p) => (
                <li key={p.email || `e${p.entity_id}`}>
                  <button type="button" className="ppl-row" aria-current={p.email ? sel === p.email : known === p.entity_id}
                    onClick={() => (p.email ? setSel(p.email) : setKnown(p.entity_id ?? null))}>
                    <Avatar who={{ ...p, email: p.email || (p.emails ?? [])[0] || '' }} size="sm" />
                    <span className="who">
                      <span dir="auto">{p.name || displayName(p)}</span>
                      <span>{[p.email || (p.emails ?? []).join(', '), p.title, p.organization].filter(Boolean).join(' · ')}</span>
                    </span>
                    {p.two_way && <span className="pill info" title={t('people.peopleTab.youWriteToEachOther')}>{t('people.peopleTab.twoWay')}</span>}
                    {p.no_mail && <span className="pill" data-testid="no-mail">{t('people.card.notInMail')}</span>}
                  </button>
                </li>
              ))}
            </ul>
          )}
          {rows.length >= limit && limit < 200 && (
            <button type="button" className="btn text sm" onClick={() => setLimit((l) => l + PAGE)}>{t('common.showMore')}</button>
          )}
        </div>
        <div>{sel ? <PersonCardView email={sel} onClose={() => setSel(null)} /> : <p className="ppl-muted">{t('people.peopleTab.chooseAPersonToSee')}</p>}</div>
      </div>
      {known !== null && <EntityDialog id={known} initialKind="person" onClose={() => setKnown(null)} />}
    </Section>
  )
}

/** People ⋮: Import contacts from a .vcf file (ImportDialog). */
function PeopleMoreMenu() {
  const [anchor, setAnchor] = useState<HTMLElement | null>(null)
  const [importing, setImporting] = useState(false)
  return (
    <>
      <button type="button" className="icon-btn" aria-label={t('people.peopleTab.morePeopleActions')} data-tip={t('common.more')} aria-haspopup="menu" data-testid="people-more"
        onClick={(e) => setAnchor(e.currentTarget)}><MoreVertical size={18} /></button>
      <Popover anchor={anchor} open={!!anchor} onClose={() => setAnchor(null)} placement="bottom-end" label={t('people.peopleTab.morePeopleActions')}>
        <MenuItem icon={<FileUp size={18} />} onSelect={() => { setAnchor(null); setImporting(true) }}>{t('people.peopleTab.importContactsVcf')}</MenuItem>
      </Popover>
      {importing && (
        <ImportDialog kind="vcf" title={t('people.peopleTab.importContacts')} onClose={() => setImporting(false)} refresh={[['people']]}
          intro={t('people.peopleTab.contactsFromAVcfFile')} />
      )}
    </>
  )
}

function Source({ s }: { s?: string }) {
  return s ? <span className="ppl-source">{t('people.peopleTab.from', { sourceText: SOURCE_TEXT[s] ?? s })}</span> : null
}

/** [onCompose]: the card's address opened a draft (a card in a dialog closes, so the draft shows). */
export function PersonCardView({ email, onClose, onCompose }: { email: string; onClose?: () => void; onCompose?: () => void }) {
  const card = usePersonCard(email)
  const decide = useDetailDecision(email)
  if (card.isLoading) return <div className="ppl-card"><Skeleton height={160} /></div>
  if (!card.data) return <div className="ppl-card"><p className="ppl-muted">{t('people.peopleTab.thisPersonCouldNotBe')}</p></div>
  const p = card.data
  const src = p.sources ?? {}
  const sugg = p.suggestions ?? []
  return (
    <article className="ppl-card" aria-label={t('people.peopleTab.cardOf', { displayName: displayName(p) })} data-testid="person-card">
      <div className="row" style={{ alignItems: 'center', gap: 12 }}>
        <Avatar who={p} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <h3><bdi>{displayName(p)}</bdi><Source s={src.name} /></h3>
          {/* The address writes to them in mailr's composer. ACT compose.deeplink @web.people */}
          <a className="ppl-muted ppl-address" href={mailtoHref({ to: [{ email: p.email }] })} data-mailto="self" data-testid="person-email"
            aria-label={t('people.peopleTab.writeTo', { email: p.email })} data-tip={t('people.peopleTab.writeAnEmail')}
            onClick={(e) => {
              if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return
              e.preventDefault()
              openComposeWith({ to: [p.name ? { email: p.email, name: displayName(p) } : { email: p.email }], cc: [], bcc: [], subject: '', body: '' })
              onCompose?.()
            }}>{p.email}</a>
        </div>
        {onClose && <button type="button" className="icon-btn" aria-label={t('people.peopleTab.closeTheCard')} onClick={onClose}><X size={18} /></button>}
      </div>
      <dl>
        {p.title && <><dt>{t('common.title')}</dt><dd>{p.title}<Source s={src.title} /></dd></>}
        {p.organization && <><dt>{t('people.peopleTab.organization')}</dt><dd>{p.organization}<Source s={src.organization} /></dd></>}
        {(p.phones ?? []).map((ph) => <PhoneRow key={ph.value} value={ph.value} type={ph.type} source={ph.source} />)}
        {(p.addresses ?? []).map((a) => <AddressRow key={a.text} text={a.text} source={a.source} />)}
        {p.birthday && <><dt>{t('people.peopleTab.birthday')}</dt><dd>{p.birthday.startsWith('--') ? p.birthday.slice(2) : p.birthday}<Source s={src.birthday} /></dd></>}
        <dt>{t('common.mail2')}</dt>
        <dd>{t('people.peopleTab.youWroteTheyWrote', { sentCount: p.sent_count ?? 0, receivedCount: p.received_count ?? 0, value: p.last_seen ? t('people.peopleTab.last', { relative: formatRelative(p.last_seen) }) : '' })}</dd>
        {(p.groups ?? []).length > 0 && <><dt>{t('common.groups')}</dt><dd>{p.groups!.map((g) => g.name).join(', ')}</dd></>}
      </dl>
      <WhatAmbroseKnows email={p.email} name={displayName(p)} knowledge={p.knowledge} />
      {/* ACT recording.view @web.people: recent recordings with this person (only with a Speakr account) */}
      <PersonRecordings email={p.email} />
      {sugg.length > 0 && (
        <>
          <h4 style={{ margin: '8px 0 0' }}>{t('people.peopleTab.foundInTheirSignature')}</h4>
          <ul className="ppl-sugg" aria-label={t('people.peopleTab.detailsFoundInTheirSignature')}>
            {sugg.map((s) => (
              <li key={s.id} data-testid="detail-suggestion">
                <span className="ppl-muted">{FIELD_TEXT[s.field]}</span>
                <span className="v">{s.value}</span>
                {/* ACT person.accept_detail @web.people */}
                <button type="button" className="btn tonal sm" aria-label={t('people.peopleTab.accept', { fieldText: FIELD_TEXT[s.field].toLowerCase(), value: s.value })} disabled={decide.isPending}
                  onClick={() => decide.mutate({ id: s.id, accept: true })}><Check size={14} />{t('common.accept')}</button>
                {/* ACT person.dismiss_detail @web.people */}
                <button type="button" className="btn text sm" aria-label={t('people.peopleTab.dismiss', { fieldText: FIELD_TEXT[s.field].toLowerCase(), value: s.value })} disabled={decide.isPending}
                  onClick={() => decide.mutate({ id: s.id, accept: false })}>{t('common.dismiss')}</button>
              </li>
            ))}
          </ul>
        </>
      )}
    </article>
  )
}

/** "What Ambrose knows": the person's notes, editable, with Add memory. ACT person.memories @web.people */
function WhatAmbroseKnows({ email, name, knowledge }: { email: string; name: string; knowledge: PersonCardData['knowledge'] }) {
  const assistant = useAssistantName()
  const qc = useQueryClient()
  const [editing, setEditing] = useState<Partial<Memory> | null>(null)
  const ent = knowledge?.entity ?? null
  const mems = knowledge?.memories ?? []
  const close = () => {
    setEditing(null)
    void qc.invalidateQueries({ queryKey: ['people'] })
  }
  return (
    <section className="ppl-knows" aria-label={t('people.card.knows', { name: assistant })} data-testid="person-knows">
      <div className="row" style={{ alignItems: 'center' }}>
        <h4 style={{ margin: 0, flex: 1 }}>{t('people.card.knows', { name: assistant })}</h4>
        <button type="button" className="btn text sm" onClick={() => setEditing({})}><Plus size={16} />{t('common.addMemory')}</button>
      </div>
      {mems.length ? (
        <ul className="as-mems">
          {mems.map((m) => <MemoryRow key={m.id} m={m} hideEntity onEdit={setEditing} />)}
        </ul>
      ) : <p className="ppl-muted">{t('people.card.knowsNothing')}</p>}
      {editing && <MemoryDialog memory={editing} fixedEntity={ent ?? undefined} aboutPerson={ent ? undefined : { name, email }} onClose={close} />}
    </section>
  )
}

function PhoneRow({ value, type, source }: { value: string; type?: string; source?: string }) {
  return <><dt>{type === 'mobile' || type === 'cell' ? t('people.peopleTab.mobile') : t('people.peopleTab.phone')}</dt><dd><a href={`tel:${value.replace(/[^\d+]/g, '')}`}>{value}</a><Source s={source} /></dd></>
}

function AddressRow({ text, source }: { text: string; source?: string }) {
  return <><dt>{t('common.address')}</dt><dd>{text}<Source s={source} /></dd></>
}

// ---------------- groups ----------------

// ACT group.list @web.people
function GroupsSection() {
  const groups = useGroups()
  const [editing, setEditing] = useState<Group | 'new' | null>(null)
  const list = groups.data ?? []
  return (
    <Section title={t('common.groups')} desc={t('people.peopleTab.typeAGroupsNameIn')}
      actions={<button type="button" className="btn tonal" onClick={() => setEditing('new')}><Plus size={16} />{t('people.peopleTab.newGroup')}</button>}>
      {groups.isLoading ? <Skeleton height={80} /> : list.length === 0 ? <p className="ppl-muted">{t('people.peopleTab.noGroupsYet')}</p> : (
        <ul className="grp-list" aria-label={t('common.groups')}>
          {list.map((g) => (
            <li key={g.id} className="grp-row" data-testid="group-row">
              <span className="grp-dot" style={g.colour ? { background: g.colour, borderColor: g.colour } : undefined} aria-hidden />
              <span className="nm">{g.name}</span>
              <span className="ppl-muted">{t('people.peopleTab.people', { count: g.count })}</span>
              {g.source === 'carddav' && <span className="pill">{t('people.peopleTab.addressBook')}</span>}
              {g.source === 'suggested' && <span className="pill">{t('people.peopleTab.fromYourMail')}</span>}
              <button type="button" className="btn text sm" onClick={() => setEditing(g)} aria-label={`${g.read_only ? t('common.open') : t('common.edit2')} ${g.name}`}>{g.read_only ? t('common.open') : t('common.edit2')}</button>
            </li>
          ))}
        </ul>
      )}
      {editing && <GroupDialog group={editing === 'new' ? null : editing} onClose={() => setEditing(null)} />}
    </Section>
  )
}

function GroupDialog({ group, onClose }: { group: Group | null; onClose: () => void }) {
  const m = useGroupMutations()
  const [name, setName] = useState(group?.name ?? '')
  const [colour, setColour] = useState<string | null>(group?.colour ?? null)
  const [membersNow, setMembers] = useState<Member[]>(group?.members ?? [])
  const members = membersNow
  const [adding, setAdding] = useState('')
  const [confirm, setConfirm] = useState(false)
  const ro = !!group?.read_only
  const query = adding.trim()
  const people = usePeopleList(query, 6)
  // Only suggestions for what is typed now; Enter before they arrive acts when they do.
  const search = useSearchAnswer(query, [people], () => takeFirst())
  const shown = search.answered ? (people.data ?? []).filter((p) => !members.some((x) => x.email === p.email)).slice(0, 5) : []
  const addMember = (p: { email: string; name?: string }) => {
    const email = p.email.trim().toLowerCase()
    if (!email.includes('@') || members.some((x) => x.email === email)) return
    setMembers([...members, { email, name: p.name ?? '' }])
    setAdding('')
  }
  /** Enter: the first suggestion for what is typed, else the typed address. */
  const takeFirst = () => {
    if (shown[0]) addMember(shown[0])
    else addMember({ email: adding })
  }
  const [bad, setBad] = useState<string | null>(null)
  const save = async () => {
    if (!name.trim()) return
    // What is still typed in "Add member" is never dropped: an address joins, other text stops Save.
    let members = membersNow
    if (query) {
      if (!isValidEmail(query)) {
        setBad(t('people.peopleTab.isNotAnEmailAddress', { query }))
        return
      }
      const email = query.toLowerCase()
      if (!members.some((x) => x.email === email)) members = [...members, { email, name: '' }]
      setMembers(members)
      setAdding('')
    }
    if (!group) {
      await m.create.mutateAsync({ name: name.trim(), colour, members })
      toast({ message: t('people.peopleTab.groupCreated', { itemName: name.trim() }) })
    } else {
      const before = new Set(group.members.map((x) => x.email))
      const after = new Set(members.map((x) => x.email))
      await m.update.mutateAsync({
        id: group.id, name: name.trim(), colour,
        add: members.filter((x) => !before.has(x.email)), remove: [...before].filter((e) => !after.has(e)),
      })
    }
    onClose()
  }
  return (
    <Dialog open onClose={onClose} title={group ? (ro ? group.name : t('people.peopleTab.edit', { groupName: group.name })) : t('people.peopleTab.newGroup')}
      footer={ro ? <button type="button" className="btn filled" onClick={onClose}>{t('common.close')}</button> : (
        <>
          {group && <button type="button" className="btn text danger" style={{ marginInlineEnd: 'auto' }} onClick={() => setConfirm(true)}><Trash2 size={16} />{t('common.delete2')}</button>}
          <button type="button" className="btn text" onClick={onClose}>{t('common.cancel')}</button>
          <button type="button" className="btn filled" disabled={!name.trim() || m.create.isPending || m.update.isPending} onClick={() => void save()}>{t('common.save')}</button>
        </>
      )}>
      {ro && <p className="ppl-muted">{t('people.peopleTab.thisGroupComesFromYour')}</p>}
      {!ro && (
        <>
          <label className="field-label" htmlFor="grp-name">{t('common.name')}</label>
          <input id="grp-name" className="input" value={name} onChange={(e) => setName(e.target.value)} aria-label={t('people.peopleTab.groupName')} autoFocus />
          <div className="grp-swatches" role="radiogroup" aria-label={t('people.peopleTab.groupColour')} style={{ margin: '10px 0' }}>
            <button type="button" role="radio" aria-checked={!colour} aria-label={t('common.noColour')} className="grp-swatch" style={{ background: 'var(--chip-bg)' }} onClick={() => setColour(null)} />
            {SWATCHES.map((s) => (
              <button key={s.id} type="button" role="radio" aria-checked={colour === s.light} aria-label={s.name} className="grp-swatch" style={{ background: s.light }} onClick={() => setColour(s.light)} />
            ))}
          </div>
        </>
      )}
      <ul className="grp-members" aria-label={t('people.peopleTab.members')}>
        {members.map((x) => (
          <li key={x.email}>
            <Avatar who={x} size="xs" />
            <span className="who">{x.name ? `${x.name} · ${x.email}` : x.email}</span>
            {!ro && (
              // ACT group.remove_members @web.people
              <button type="button" className="icon-btn xs" aria-label={t('people.peopleTab.removeFromTheGroup', { email: x.email })} onClick={() => setMembers(members.filter((y) => y.email !== x.email))}><X size={14} /></button>
            )}
          </li>
        ))}
        {members.length === 0 && <li className="ppl-muted">{t('people.peopleTab.nobodyYet')}</li>}
      </ul>
      {!ro && (
        <div style={{ position: 'relative' }}>
          {/* ACT group.add_members @web.people */}
          <input className="input" value={adding} placeholder={t('people.peopleTab.addAPersonOrAn')} aria-label={t('people.peopleTab.addAMember')}
            aria-invalid={bad ? true : undefined} aria-describedby={bad ? 'grp-add-bad' : undefined}
            onChange={(e) => {
              setAdding(e.target.value)
              setBad(null)
            }}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault()
                if (!query) return
                if (search.ready) takeFirst()
                else search.deferEnter()
              }
            }} />
          {bad && <p className="ppl-error" id="grp-add-bad" role="alert" data-testid="grp-add-bad">{bad}</p>}
          {shown.length > 0 && (
            <div className="suggest" role="listbox">
              {shown.map((p: PersonRow) => (
                <button key={p.email} type="button" role="option" aria-selected={false} className="suggest-item" onMouseDown={(e) => e.preventDefault()} onClick={() => addMember(p)}>
                  <Avatar who={p} size="sm" />
                  <span className="who"><span dir="auto">{displayName(p)}</span><span>{p.email}</span></span>
                </button>
              ))}
            </div>
          )}
        </div>
      )}
      {group && (
        <ConfirmDialog open={confirm} title={t('people.peopleTab.delete', { groupName: group.name })} danger confirmLabel={t('common.delete2')}
          body={t('people.peopleTab.thePeopleStayInPeople')}
          onClose={() => setConfirm(false)}
          onConfirm={() => { void m.remove.mutateAsync(group.id).then(() => { setConfirm(false); onClose(); toast({ message: t('people.peopleTab.groupDeleted', { groupName: group.name }) }) }) }} />
      )}
    </Dialog>
  )
}

// ---------------- suggested groups ----------------

function SuggestedGroupsSection() {
  const sugg = useGroups('', 'suggested')
  const m = useGroupMutations()
  const list = sugg.data ?? []
  return (
    <Section title={t('people.peopleTab.suggestedGroups')} desc={t('people.peopleTab.peopleYouOftenWriteTo')}
      actions={<button type="button" className="btn text" disabled={m.refresh.isPending} onClick={() => m.refresh.mutate()}>
        {m.refresh.isPending ? <Spinner small /> : <RefreshCw size={16} />}{t('people.peopleTab.lookAgain')}</button>}>
      {sugg.isLoading ? <Skeleton height={80} /> : list.length === 0 ? <p className="ppl-muted">{t('people.peopleTab.noSuggestionsRightNow')}</p> : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {list.map((g) => <SuggestionCard key={g.id} g={g} />)}
        </div>
      )}
    </Section>
  )
}

function SuggestionCard({ g }: { g: Group }) {
  const m = useGroupMutations()
  const [name, setName] = useState(g.name_hint ?? '')
  const ev = g.evidence ?? {}
  const shown = g.members.slice(0, 8)
  return (
    <div className="grp-sugg" data-testid="group-suggestion">
      <div className="row">
        <Users size={16} aria-hidden />
        <strong>{t('people.peopleTab.people2', { count: g.count })}</strong>
        <span className="ppl-muted">
          {ev.messages ? t('people.peopleTab.emails', { count: ev.messages }) : ''}{ev.last ? t('people.peopleTab.last', { relative: formatRelative(ev.last) }) : ''}
          {ev.subjects?.length ? ` · “${ev.subjects[0]}”` : ''}
        </span>
      </div>
      <div className="ppl-muted">{shown.map((x) => x.name || x.email).join(', ')}{g.count > shown.length ? t('people.peopleTab.andMore', { value: g.count - shown.length }) : ''}</div>
      <div className="row">
        <input className="input" value={name} placeholder={t('people.peopleTab.nameTheGroup')} aria-label={t('people.peopleTab.nameForTheSuggestedGroup')} onChange={(e) => setName(e.target.value)} />
        {/* ACT group.accept_suggestion @web.people */}
        <button type="button" className="btn tonal sm" disabled={!name.trim() || m.accept.isPending}
          onClick={() => void m.accept.mutateAsync({ id: g.id, name: name.trim() }).then(() => toast({ message: t('people.peopleTab.groupCreated', { itemName: name.trim() }) }))}>
          <UserPlus size={14} />{t('common.accept')}</button>
        {/* ACT group.dismiss_suggestion @web.people */}
        <button type="button" className="btn text sm" disabled={m.dismiss.isPending} onClick={() => m.dismiss.mutate(g.id)}>{t('common.dismiss')}</button>
      </div>
    </div>
  )
}

// ---------------- address book ----------------

function AddressBookSection() {
  const books = useAddressBooks()
  const sync = useSyncAddressBooks()
  const list = books.data ?? []
  return (
    <Section title={t('people.peopleTab.addressBook')} desc={t('people.peopleTab.contactsAndGroupsReadFrom')}
      actions={list.length > 0 ? (
        // ACT contacts.sync @web.people
        <button type="button" className="btn text" disabled={sync.isPending} onClick={() => sync.mutate()}>
          {sync.isPending ? <Spinner small /> : <RefreshCw size={16} />}{t('people.peopleTab.readNow')}</button>
      ) : undefined}>
      {books.isLoading ? <Skeleton height={40} /> : list.length === 0 ? (
        <p className="ppl-muted">{t('people.peopleTab.connectACalendarAccountIn')}</p>
      ) : list.map((b) => (
        <div key={b.connector_id} className="grp-row" data-testid="address-book">
          <BookUser size={18} aria-hidden />
          <span className="nm">{b.name}</span>
          <span className="ppl-muted">
            {b.available === false ? (b.error || t('people.peopleTab.noAddressBookOnThis')) : b.available == null ? t('people.peopleTab.notReadYet')
              : t('people.peopleTab.contactsGroups', { cards: b.cards, groups: b.groups, value: b.checked_at ? t('people.peopleTab.read', { relative: formatRelative(b.checked_at) }) : '' })}
          </span>
        </div>
      ))}
    </Section>
  )
}

export default PeopleTab
