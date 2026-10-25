// Store mode needs the server's daily mail export (backend MAILR_MAIL_EXPORT): while it is off,
// GET /me `features.store_mode` is false and the mode pickers (the add-account wizard, an
// administrator's managed account form) show store mode as unavailable with this reason. The
// server refuses it too (accounts.store_needs_mail_export). An older server without the field
// offers it. With MAILR_STORE_BACKUP_CHECK=false (`features.store_backup_check` false) store mode
// needs no export, and deletion at the provider is not checked against a backup.

import { t } from '../../i18n'

/** Why store mode cannot be chosen, or null when it can. */
export function storeModeUnavailable(features: { store_mode?: boolean } | null | undefined): string | null {
  return features?.store_mode === false ? t('settings.storeMode.unavailable') : null
}

/** Deletion at the provider is not checked against a backup (MAILR_STORE_BACKUP_CHECK=false). An
 *  older server without the field checks it. */
export function backupUnchecked(features: { store_backup_check?: boolean } | null | undefined): boolean {
  return features?.store_backup_check === false
}
