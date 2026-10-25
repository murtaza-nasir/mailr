import { Plus } from 'lucide-react'
import { useState } from 'react'
import { useLabelMutations, useLabels } from '../../api/hooks'
import type { Label } from '../../api/types'
import { LabelChip, Skeleton } from '../../components/bits'
import { ConfirmDialog, Dialog } from '../../components/Dialog'
import { Popover, useAnchor } from '../../components/Popover'
import { labelTree } from '../../lib/views'
import { ColorPicker, CreateLabelDialog } from '../mail/menus'
import { Section } from './common'
import { t, appLocale } from '../../i18n'

function deleteText(l: Label | null, labels: Label[]): string {
  if (!l) return ''
  const children = labels.filter((x) => x.name.startsWith(`${l.name}/`)).map((x) => x.name)
  const base = t('settings.labelsTab.deleteTheLabel', { itemName: l.name })
  const kids = children.length
    ? t('settings.labelsTab.itsWillBeDeletedToo', { value: t('settings.labelsTab.nestedLabels', { count: children.length }), children: children.slice(0, 6).join(', '), value2: children.length > 6 ? ', …' : '' })
    : ''
  return t('settings.labelsTab.conversationsKeepTheirOtherLabels', { base, kids })
}

const ROLE_NAMES: Record<string, string> = { inbox: t('common.inbox'), sent: t('common.sent'), drafts: t('common.drafts'), junk: t('common.spam'), trash: t('common.trash'), archive: t('common.archive') }

export function LabelsTab() {
  const q = useLabels()
  const labels = q.data ?? []
  const { update, remove } = useLabelMutations()
  const [creating, setCreating] = useState(false)
  const [editing, setEditing] = useState<Label | null>(null)
  const [name, setName] = useState('')
  const [deleting, setDeleting] = useState<Label | null>(null)
  const colorMenu = useAnchor()
  const [colorFor, setColorFor] = useState<Label | null>(null)

  const system = labels.filter((l) => l.role)
  const tree = labelTree(labels)

  if (q.isLoading) return <Skeleton height={200} />

  return (
    <>
      <Section title={t('settings.labelsTab.systemLabels')} desc={t('settings.labelsTab.mailboxesEveryAccountHasTheir')}>
        <div className="table-wrap"><table className="table">
          <thead>
            <tr>
              <th>{t('common.label')}</th>
              <th>{t('settings.labelsTab.conversations')}</th>
              <th>{t('common.unread')}</th>
            </tr>
          </thead>
          <tbody>
            {system.map((l) => (
              <tr key={l.id}>
                <td>{ROLE_NAMES[l.role!] ?? l.name}</td>
                <td>{l.total.toLocaleString(appLocale())}</td>
                <td>{l.unread.toLocaleString(appLocale())}</td>
              </tr>
            ))}
          </tbody>
        </table></div>
      </Section>
      <Section
        title={t('common.labels')}
        desc={t('settings.labelsTab.nestLabelsWithASlash')}
        actions={
          <button type="button" className="btn tonal" onClick={() => setCreating(true)}>
            <Plus />{' '}{t('common.createNewLabel')}
          </button>
        }
      >
        {tree.length === 0 ? (
          <p className="muted">{t('settings.labelsTab.youHaveNoLabelsYet')}</p>
        ) : (
          <div className="table-wrap"><table className="table">
            <thead>
              <tr>
                <th>{t('common.label')}</th>
                <th>{t('settings.labelsTab.conversations')}</th>
                <th>{t('settings.labelsTab.showInLabelList')}</th>
                <th>{t('settings.labelsTab.showInMessageList')}</th>
                <th>{t('common.actions')}</th>
              </tr>
            </thead>
            <tbody>
              {tree.map(({ label: l, depth }) => (
                <tr key={l.id}>
                  <td style={{ paddingInlineStart: 12 + depth * 20 }}>
                    <button
                      type="button"
                      onClick={(e) => {
                        setColorFor(l)
                        colorMenu.show(e.currentTarget)
                      }}
                      aria-label={t('settings.labelsTab.changeColourOf', { itemName: l.name })}
                      data-tip={t('settings.labelsTab.changeColour')}
                    >
                      <LabelChip label={l} large />
                    </button>
                  </td>
                  <td>{l.total.toLocaleString(appLocale())}</td>
                  <td>
                    <div className="row" style={{ gap: 12 }}>
                      {(['show', 'hide', 'unread'] as const).map((v) => (
                        <button key={v} type="button" className="btn text sm" style={{ fontWeight: l.show_in_list === v ? 700 : 400, color: l.show_in_list === v ? 'var(--text)' : undefined }} onClick={() => update.mutate({ id: l.id, show_in_list: v })}>
                          {v === 'show' ? 'show' : v === 'hide' ? 'hide' : t('settings.labelsTab.showIfUnread')}
                        </button>
                      ))}
                    </div>
                  </td>
                  <td>
                    <div className="row" style={{ gap: 12 }}>
                      <button type="button" className="btn text sm" style={{ fontWeight: l.show_in_message_list ? 700 : 400 }} onClick={() => update.mutate({ id: l.id, show_in_message_list: true })}>
                        {t('settings.labelsTab.show')}
                      </button>
                      <button type="button" className="btn text sm" style={{ fontWeight: !l.show_in_message_list ? 700 : 400 }} onClick={() => update.mutate({ id: l.id, show_in_message_list: false })}>
                        {t('settings.labelsTab.hide')}
                      </button>
                    </div>
                  </td>
                  <td>
                    <div className="row">
                      <button type="button" className="btn text sm" onClick={() => { setEditing(l); setName(l.name) }}>
                        {t('common.edit')}
                      </button>
                      <button type="button" className="btn text sm" onClick={() => setDeleting(l)}>
                        {t('settings.labelsTab.remove')}
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table></div>
        )}
      </Section>
      <Popover anchor={colorMenu.anchor} open={colorMenu.open} onClose={colorMenu.close} role="dialog" label={t('settings.labelsTab.labelColour')}>
        <ColorPicker
          value={colorFor?.color ?? null}
          name={colorFor?.name ?? ''}
          onPick={(c) => {
            colorMenu.close()
            if (colorFor) update.mutate({ id: colorFor.id, color: c })
          }}
        />
      </Popover>
      <CreateLabelDialog open={creating} onClose={() => setCreating(false)} />
      <Dialog
        open={!!editing}
        onClose={() => setEditing(null)}
        title={t('settings.labelsTab.editLabel')}
        footer={
          <>
            <button type="button" className="btn text" onClick={() => setEditing(null)}>
              {t('common.cancel')}
            </button>
            <button
              type="button"
              className="btn filled"
              disabled={!name.trim() || name.trim() === editing?.name}
              onClick={() => {
                if (editing) update.mutate({ id: editing.id, name: name.trim() })
                setEditing(null)
              }}
            >
              {t('common.save')}
            </button>
          </>
        }
      >
        <div className="field">
          <label htmlFor="label-rename">{t('settings.labelsTab.labelName')}</label>
          <input id="label-rename" className="input" value={name} onChange={(e) => setName(e.target.value)} autoFocus />
        </div>
      </Dialog>
      <ConfirmDialog
        open={!!deleting}
        title={t('common.removeLabel')}
        body={deleteText(deleting, labels)}
        confirmLabel={t('common.delete2')}
        danger
        onClose={() => setDeleting(null)}
        onConfirm={() => {
          if (deleting) remove.mutate(deleting.id)
          setDeleting(null)
        }}
      />
    </>
  )
}
