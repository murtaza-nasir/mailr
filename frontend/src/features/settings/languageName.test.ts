// The Language menu names each language in its own words (Settings → You → Region).
import { describe, expect, it } from 'vitest'
import { languageName } from './language'

describe('languageName', () => {
  it('names the shipped languages as the major apps do', () => {
    expect(['de', 'es', 'fr', 'pt-BR', 'ru', 'zh-Hans'].map(languageName)).toEqual(
      ['Deutsch', 'Español', 'Français', 'Português (Brasil)', 'Русский', '中文（简体）'])
  })
})
