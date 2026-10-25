import { cardsTheme } from './cards'
import { classicTheme } from './classic'
import { deskTheme } from './desk'
import { focusTheme } from './focus'
import { officeTheme } from './office'
import type { ThemeDef, ThemeId } from './types'

export const THEME_IDS: ThemeId[] = ['classic', 'office', 'focus', 'desk', 'cards']

export function isThemeId(v: unknown): v is ThemeId {
  return typeof v === 'string' && (THEME_IDS as string[]).includes(v)
}

export const THEMES: Record<ThemeId, ThemeDef> = {
  classic: classicTheme,
  office: officeTheme,
  focus: focusTheme,
  desk: deskTheme,
  cards: cardsTheme,
}

export const THEME_LIST: ThemeDef[] = THEME_IDS.map((id) => THEMES[id])
