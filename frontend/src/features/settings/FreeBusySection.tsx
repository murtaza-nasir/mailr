// Settings → Calendar & tasks → Free/busy sharing (backend
// calendar/household.py): "Share my free/busy with my groups" (setting calendar_share_free_busy,
// off by default). People in a mailr group with you then see when you are busy in Find a time,
// from your mailr calendars: busy times only, no titles, places or guests. It shows when you have
// the mailr calendar, and saves at once with Undo (save.ts).
// ACT event.find_time_household @web.settings

import { useApiList, useSettings } from '../../api/hooks'
import type { Connector, Settings } from '../../api/types'
import { Check, Section, SetRow } from './common'
import { useSaveSettings } from './save'
import { t } from '../../i18n'

export function FreeBusySection() {
  const s = useSettings() as unknown as Settings & { calendar_share_free_busy?: boolean }
  const save = useSaveSettings()
  const conns = useApiList<Connector>(['connectors'], '/connectors')
  if (!(conns.data ?? []).some((c) => c.kind === 'caldav' && (c as Connector & { builtin?: boolean }).builtin)) return null
  return (
    <Section title={t('settings.freeBusySection.findATimeWithYour')}>
      <SetRow k="Free/busy:">
        <Check
          checked={s.calendar_share_free_busy === true}
          onChange={(v) => void save({ calendar_share_free_busy: v } as Partial<Settings>, {
            message: v ? t('settings.freeBusySection.yourGroupsSeeWhenYou') : t('settings.freeBusySection.yourFreeBusyIsNo'),
          })}
          label={t('settings.freeBusySection.shareMyFreeBusyWith')}
          desc={t('settings.freeBusySection.peopleInYourGroupsSee')}
        />
      </SetRow>
    </Section>
  )
}
