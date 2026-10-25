// The signed-in user's Settings visibility (visibility.ts), from GET /me.
import { useMemo } from 'react'
import { useMe } from '../../api/hooks'
import { settingsVisibility, type SettingsVisibility } from './visibility'

export function useVisibility(): SettingsVisibility {
  const me = useMe().data
  return useMemo(() => settingsVisibility(me), [me])
}
