import { describe, expect, it } from 'vitest'
import { backupUnchecked, storeModeUnavailable } from './storeMode'

describe('storeModeUnavailable', () => {
  it('gives the reason while the mail export is off', () => {
    expect(storeModeUnavailable({ store_mode: false })).toMatch(/daily mail export/)
  })

  it('offers store mode when the server allows it or does not say', () => {
    expect(storeModeUnavailable({ store_mode: true })).toBeNull()
    expect(storeModeUnavailable({})).toBeNull()
    expect(storeModeUnavailable(undefined)).toBeNull()
  })
})

describe('backupUnchecked', () => {
  it('is true only when the server says the backup check is off', () => {
    expect(backupUnchecked({ store_backup_check: false })).toBe(true)
    expect(backupUnchecked({ store_backup_check: true })).toBe(false)
    expect(backupUnchecked({})).toBe(false)
    expect(backupUnchecked(undefined)).toBe(false)
  })
})
