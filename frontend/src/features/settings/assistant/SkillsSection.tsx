import { assistantNameNow, withAssistantName } from '../../rail/assistant/useAssistantName'
// Settings → Ambrose → Skills: named prompts invoked as //name.

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Copy, Pencil, Play, Plus, RotateCcw, Trash2, Wand2 } from 'lucide-react'
import { useState } from 'react'
import { api, ApiError } from '../../../api/client'
import { Skeleton, Spinner, Switch } from '../../../components/bits'
import { ConfirmDialog, Dialog } from '../../../components/Dialog'
import { toast, toastError } from '../../../stores/ui'
import { Check, Section } from '../common'
import { CONTEXT_LABELS, qkA, type Skill, type SkillContext, type SkillList } from './types'
import { t, tx } from '../../../i18n'

const NAME_RE = /^[a-z0-9][a-z0-9_-]{0,47}$/
const ALL_CONTEXTS: SkillContext[] = ['compose', 'reply', 'chat', 'summarize']

export function useSkills() {
  return useQuery<SkillList>({ queryKey: qkA.skills, queryFn: () => api.get<SkillList>('/assistant/skills') })
}

export function SkillsSection() {
  const qc = useQueryClient()
  const q = useSkills()
  const [editing, setEditing] = useState<Partial<Skill> | null>(null)
  const [deleting, setDeleting] = useState<Skill | null>(null)
  const done = () => void qc.invalidateQueries({ queryKey: qkA.skills })

  const toggle = useMutation({
    mutationFn: (s: Skill) => api.patch(`/assistant/skills/${s.id}`, { enabled: !s.enabled }),
    onSuccess: done,
    onError: (e) => toastError(e),
  })
  const act = (path: string, message: string) =>
    api
      .post(path)
      .then(() => {
        done()
        toast({ message })
      })
      .catch((e) => toastError(e))

  const items = q.data?.items ?? []
  const missingBuiltins = (q.data?.builtins ?? []).filter((n) => !items.some((s) => s.name === n))

  return (
    <>
      <Section
        title={t('common.skills')}
        desc={
          <>
            {tx('settings.assistant.skillsSection.aSkillIsASet', { name: assistantNameNow() }, { code: (c) => <code className="as-code">{c}</code> })}
          </>
        }
        actions={
          <div className="row">
            {missingBuiltins.length > 0 && (
              <button type="button" className="btn outlined" onClick={() => void act('/assistant/skills/restore-builtins', t('settings.assistant.skillsSection.builtInSkillsRestored'))}>
                <RotateCcw />{' '}{t('settings.assistant.skillsSection.restoreBuiltInSkills')}
              </button>
            )}
            <button type="button" className="btn filled" onClick={() => setEditing({ applies_to: ['compose', 'reply', 'chat'], enabled: true })}>
              <Plus />{' '}{t('settings.assistant.skillsSection.newSkill')}
            </button>
          </div>
        }
      >
        {q.isLoading ? (
          <Skeleton height={120} />
        ) : items.length === 0 ? (
          <p className="muted">{t('settings.assistant.skillsSection.youHaveNoSkillsCreate', { name: assistantNameNow() })}</p>
        ) : (
          <ul className="as-list">
            {items.map((s) => (
              <li key={s.id} className={`as-item${s.enabled ? '' : ' off'}`}>
                <Switch checked={s.enabled} onChange={() => toggle.mutate(s)} label={s.enabled ? t('settings.assistant.skillsSection.turnOff', { itemName: s.name }) : t('settings.assistant.skillsSection.turnOn', { itemName: s.name })} />
                <button type="button" className="as-item-main" onClick={() => setEditing(s)}>
                  <span className="as-item-title">
                    <code className="as-skill-name">/{s.name}</code>
                    <span className="as-skill-title">{s.title}</span>
                    {s.builtin && <span className="pill">{t('settings.assistant.skillsSection.builtIn')}</span>}
                    {s.modified && <span className="pill warn">{t('settings.assistant.skillsSection.modified')}</span>}
                    {s.auto && <span className="pill info">{t('settings.assistant.skillsSection.auto')}</span>}
                    {s.source === 'agent' && <span className="pill">{t('settings.assistant.skillsSection.byAssistant')}</span>}
                    {!s.enabled && <span className="pill err">{t('common.off')}</span>}
                  </span>
                  <span className="as-item-sub">{s.description || t('settings.assistant.skillsSection.noDescription')}</span>
                  {s.applies_to.length > 0 && <span className="as-item-meta">{s.applies_to.map((c) => withAssistantName(CONTEXT_LABELS[c])).join(' · ')}</span>}
                </button>
                <div className="as-item-actions">
                  <button type="button" className="icon-btn sm" aria-label={t('settings.assistant.skillsSection.edit', { itemName: s.name })} data-tip={t('common.edit2')} onClick={() => setEditing(s)}>
                    <Pencil size={18} />
                  </button>
                  <button type="button" className="icon-btn sm" aria-label={t('settings.assistant.skillsSection.duplicate', { itemName: s.name })} data-tip={t('common.duplicate')} onClick={() => void act(`/assistant/skills/${s.id}/duplicate`, t('settings.assistant.skillsSection.copied', { itemName: s.name }))}>
                    <Copy size={18} />
                  </button>
                  {s.builtin && s.modified && (
                    <button type="button" className="icon-btn sm" aria-label={t('settings.assistant.skillsSection.reset', { itemName: s.name })} data-tip={t('settings.assistant.skillsSection.resetToTheBuiltIn')} onClick={() => void act(`/assistant/skills/${s.id}/reset`, `/${s.name} reset.`)}>
                      <RotateCcw size={18} />
                    </button>
                  )}
                  <button type="button" className="icon-btn sm" aria-label={t('settings.assistant.skillsSection.delete', { itemName: s.name })} data-tip={t('common.delete2')} onClick={() => setDeleting(s)}>
                    <Trash2 size={18} />
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </Section>
      {editing && <SkillDialog skill={editing} taken={items.filter((s) => s.id !== editing.id).map((s) => s.name)} onClose={() => setEditing(null)} />}
      <ConfirmDialog
        open={!!deleting}
        title={t('settings.assistant.skillsSection.deleteSkill')}
        body={
          deleting?.builtin
            ? t('settings.assistant.skillsSection.deleteTheBuiltInSkill', { deletingName: deleting?.name })
            : t('settings.assistant.skillsSection.deleteThisCannotBeUndone', { deletingName: deleting?.name })
        }
        confirmLabel={t('common.delete2')}
        danger
        onClose={() => setDeleting(null)}
        onConfirm={() => {
          const s = deleting!
          setDeleting(null)
          api
            .del(`/assistant/skills/${s.id}`)
            .then(() => {
              done()
              toast({ message: `/${s.name} deleted.` })
            })
            .catch((e) => toastError(e))
        }}
      />
    </>
  )
}

function SkillDialog({ skill, taken, onClose }: { skill: Partial<Skill>; taken: string[]; onClose: () => void }) {
  const qc = useQueryClient()
  const [name, setName] = useState(skill.name ?? '')
  const [title, setTitle] = useState(skill.title ?? '')
  const [description, setDescription] = useState(skill.description ?? '')
  const [instructions, setInstructions] = useState(skill.instructions ?? '')
  const [appliesTo, setAppliesTo] = useState<SkillContext[]>(skill.applies_to ?? [])
  const [auto, setAuto] = useState(skill.auto ?? false)
  const [sample, setSample] = useState('')
  const [result, setResult] = useState<{ text?: string; error?: string } | null>(null)

  const nameErr = !name ? t('settings.assistant.skillsSection.required') : !NAME_RE.test(name) ? t('settings.assistant.skillsSection.lowercaseLettersDigitsAndOnly') : taken.includes(name) ? t('settings.assistant.skillsSection.anotherSkillHasThisName') : ''
  const valid = !nameErr && instructions.trim() !== ''

  const save = useMutation({
    mutationFn: () => {
      const body = { name, title: title.trim(), description: description.trim(), instructions, applies_to: appliesTo, auto }
      return skill.id ? api.patch<Skill>(`/assistant/skills/${skill.id}`, body) : api.post<Skill>('/assistant/skills', body)
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qkA.skills })
      toast({ message: skill.id ? `/${name} saved.` : `/${name} created. Type /${name} to use it.` })
      onClose()
    },
    onError: (e) => toastError(e, t('settings.assistant.skillsSection.skillNotSaved')),
  })

  const test = useMutation({
    mutationFn: () => api.post<{ text: string; model?: string }>('/assistant/skills/test', { sample, skill_id: skill.id ?? null, instructions, title: title || name }),
    onSuccess: (r) => setResult({ text: r.text }),
    onError: (e) => setResult({ error: e instanceof ApiError ? e.message : t('settings.assistant.skillsSection.theTestFailed') }),
  })

  return (
    <Dialog
      open
      wide
      modal
      onClose={onClose}
      title={skill.id ? t('settings.assistant.skillsSection.edit2', { skillName: skill.name }) : t('settings.assistant.skillsSection.newSkill')}
      footer={
        <>
          <button type="button" className="btn text" onClick={onClose}>
            {t('common.cancel')}
          </button>
          <button type="button" className="btn filled" disabled={!valid || save.isPending} onClick={() => save.mutate()}>
            {skill.id ? t('common.save') : t('settings.assistant.skillsSection.createSkill')}
          </button>
        </>
      }
    >
      <div className="stack as-dialog">
        <div className="grid-2">
          <div className="field">
            <label htmlFor="sk-name">{t('common.name')}</label>
            <div className="as-slug">
              <span aria-hidden>//</span>
              <input id="sk-name" className="input" value={name} autoComplete="off" spellCheck={false} placeholder="polite-decline" onChange={(e) => setName(e.target.value.toLowerCase().replace(/\s+/g, '-'))} />
            </div>
            {nameErr && name ? <span className="error">{nameErr}</span> : <span className="hint">{t('settings.assistant.skillsSection.whatYouTypeAfter')}</span>}
          </div>
          <div className="field">
            <label htmlFor="sk-title">{t('common.title')}</label>
            <input id="sk-title" className="input" value={title} placeholder={t('settings.assistant.skillsSection.politeDecline')} onChange={(e) => setTitle(e.target.value)} />
          </div>
        </div>
        <div className="field">
          <label htmlFor="sk-desc">{t('settings.assistant.skillsSection.whenShouldUseThis', { name: assistantNameNow() })}</label>
          <input id="sk-desc" className="input" value={description} placeholder={t('settings.assistant.skillsSection.sayNoToARequest')} onChange={(e) => setDescription(e.target.value)} />
          <span className="hint">{t('settings.assistant.skillsSection.oneSentenceReadsItTo', { name: assistantNameNow() })}</span>
        </div>
        <div className="field">
          <label htmlFor="sk-ins">{t('settings.assistant.skillsSection.instructions')}</label>
          <textarea
            id="sk-ins"
            className="textarea as-mono"
            rows={9}
            value={instructions}
            placeholder={t('settings.assistant.skillsSection.thankThemThenDeclineIn')}
            onChange={(e) => setInstructions(e.target.value)}
          />
          <span className="hint">{t('settings.assistant.skillsSection.markdownShortConcreteBulletPoints')}</span>
        </div>
        <div className="field">
          <span className="field-label">{t('settings.assistant.skillsSection.offerItIn')}</span>
          <div className="as-checks">
            {ALL_CONTEXTS.map((c) => (
              <Check key={c} checked={appliesTo.includes(c)} onChange={(on) => setAppliesTo(on ? ALL_CONTEXTS.filter((x) => x === c || appliesTo.includes(x)) : appliesTo.filter((x) => x !== c))} label={withAssistantName(CONTEXT_LABELS[c])} />
            ))}
          </div>
          <span className="hint">{t('settings.assistant.skillsSection.typingAlwaysWorksThisOnly', { itemName: name || 'name', name: assistantNameNow() })}</span>
        </div>
        <Check checked={auto} onChange={setAuto} label={t('settings.assistant.skillsSection.applyAutomatically')} desc={t('settings.assistant.skillsSection.useTheseInstructionsEveryTime')} />
        <div className="as-try">
          <div className="row">
            <Wand2 size={18} />
            <span className="field-label" style={{ flex: 1 }}>
              {t('settings.assistant.skillsSection.tryIt')}
            </span>
            <button type="button" className="btn tonal sm" disabled={!sample.trim() || !instructions.trim() || test.isPending} onClick={() => test.mutate()}>
              {test.isPending ? <Spinner small /> : <Play size={16} />}{' '}{t('settings.assistant.skillsSection.run')}
            </button>
          </div>
          <textarea className="textarea" rows={3} value={sample} placeholder={t('settings.assistant.skillsSection.pasteASampleEmailOr')} onChange={(e) => setSample(e.target.value)} aria-label={t('settings.assistant.skillsSection.sampleText')} />
          {result?.error && <div className="as-result error">{result.error}</div>}
          {result?.text && <div className="as-result">{result.text}</div>}
        </div>
      </div>
    </Dialog>
  )
}
