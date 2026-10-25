// Settings → Mail → Labels → Learn from my choices: on or off (setting auto_label), how sure
// before a label is applied automatically (three levels, learningLevels.ts), and in advanced mode
// "Favour recent labels" with its half-life (label_recency, backend intel/classify.py). A change is
// saved at once, and the server queues a retrain of the label models. ACT mail.label @web.settings

import { useSettings } from '../../../api/hooks'
import type { HalfLifeMonths, Settings } from '../../../api/types'
import { Check, Radio, Section } from '../common'
import { useSaveSettings } from '../save'
import { useVisibility } from '../useVisibility'
import { HALF_LIVES, labelRecency } from './labelRecency'
import { LEARNING_LEVELS, learningLevel, learningOn, levelPatch, type LearningLevelId } from './learningLevels'
import { t } from '../../../i18n'

type LevelChoice = LearningLevelId | 'custom'

export function LabelLearningSection() {
  const settings = useSettings()
  const saveSettings = useSaveSettings()
  const detail = useVisibility().learningDetail
  const r = labelRecency(settings)
  const on = learningOn(settings)
  const level = learningLevel(settings)

  const save = (patch: Partial<Settings>) => void saveSettings(patch, { message: t('settings.assistant.labelLearningSection.savedYourLabelModelsAre') })
  const saveRecency = (enabled: boolean, half_life_months: HalfLifeMonths) => save({ label_recency: { enabled, half_life_months } })

  return (
    <Section
      title={t('settings.assistant.labelLearningSection.learnFromMyChoices')}
      desc={<>{t('settings.assistant.labelLearningSection.theLabelsYouGiveAre')}</>}
    >
      <Check checked={on} onChange={(v) => save({ auto_label: v })} label={t('settings.assistant.labelLearningSection.learnFromMyChoices')}
        desc={t('settings.assistant.labelLearningSection.suggestLabelsForNewMail')} />
      <fieldset className="ll-levels" disabled={!on} style={on ? undefined : { opacity: 0.5 }}>
        <legend>{t('settings.assistant.labelLearningSection.howSureBeforeALabel')}</legend>
        {LEARNING_LEVELS.map((l) => (
          <Radio<LevelChoice> key={l.id} name="ll-level" value={l.id} current={level}
            onChange={(id) => save(levelPatch(id as LearningLevelId))} label={l.label} desc={l.desc} />
        ))}
        {level === 'custom' && (
          <Radio<LevelChoice> name="ll-level" value="custom" current={level} onChange={() => undefined} label={t('settings.assistant.labelLearningSection.custom')}
            desc={t('settings.assistant.labelLearningSection.appliedAtConfidenceAfterExamples', { round: Math.round(settings.auto_label_precision * 100), autoLabelMinExamples: settings.auto_label_min_examples })} />
        )}
      </fieldset>
      {detail && (
        <>
          <Check
            checked={r.enabled}
            onChange={(v) => saveRecency(v, r.half_life_months)}
            label={t('settings.assistant.labelLearningSection.favourRecentLabels')}
            desc={<>{t('settings.assistant.labelLearningSection.labelsYouGaveRecentlyCount')}</>}
          />
          <label className="choice ll-half-life" style={{ paddingInlineStart: 28, ...(r.enabled ? {} : { opacity: 0.5 }) }}>
            <span>
              {t('settings.assistant.labelLearningSection.aLabelCountsHalfAfter')}
              <select
                className="input"
                aria-label={t('settings.assistant.labelLearningSection.halfLifeOfALabel')}
                value={r.half_life_months}
                disabled={!r.enabled}
                onChange={(e) => saveRecency(true, Number(e.target.value) as HalfLifeMonths)}
                style={{ marginInlineStart: 8, width: 'auto', display: 'inline-block' }}
              >
                {HALF_LIVES.map((m) => (
                  <option key={m} value={m}>
                    {t('settings.assistant.labelLearningSection.months', { count: m })}
                  </option>
                ))}
              </select>
              <div className="desc">{t('settings.assistant.labelLearningSection.andAQuarterAfterTwice')}</div>
            </span>
          </label>
          <p className="muted" style={{ fontSize: 12.5, marginTop: 8 }}>
            {t('settings.assistant.labelLearningSection.aLabelYouAddOr')}
          </p>
        </>
      )}
    </Section>
  )
}
