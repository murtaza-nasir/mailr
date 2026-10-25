import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from 'typescript-eslint'

export const ZONE_RULE = 'Pass a zone through src/lib/dates (dateTimeFormat, formatInstant, safeZone): a zone the browser refuses throws.'

export default tseslint.config(
  { ignores: ['dist', 'public/sw.js', 'mock'] },
  {
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    files: ['**/*.{ts,tsx}'],
    languageOptions: { ecmaVersion: 2023, globals: globals.browser },
    plugins: { 'react-hooks': reactHooks, 'react-refresh': reactRefresh },
    rules: {
      'react-hooks/rules-of-hooks': 'error',
      'react-hooks/exhaustive-deps': 'warn',
      'react-refresh/only-export-components': 'off',
      '@typescript-eslint/no-unused-vars': ['error', { argsIgnorePattern: '^_', varsIgnorePattern: '^_' }],
    },
  },
  // Zones (9 Oct 2026): a `timeZone:` option is written only in src/lib/dates.ts, whose helpers
  // (safeZone, dateTimeFormat, formatInstant) check the name first. Outlook's "Central Standard
  // Time" made a bare Intl formatter throw and the whole screen fail.
  {
    files: ['src/**/*.{ts,tsx}'],
    ignores: ['src/lib/dates.ts', 'src/**/*.test.{ts,tsx}'],
    rules: {
      'no-restricted-syntax': ['error',
        { selector: "Property[key.name='timeZone']", message: ZONE_RULE },
        { selector: "Property[key.value='timeZone']", message: ZONE_RULE },
      ],
    },
  },
)
