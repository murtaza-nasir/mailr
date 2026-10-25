// An account's colour on its card (Settings → Mail → Mail accounts): the dot it shows on list rows
// and in the navigation (features/layout/accounts.tsx). mailr picks one per account; this changes it.
// ACT mail.account_colour @web.settings

import { useQueryClient } from '@tanstack/react-query'
import { api } from '../../api/client'
import type { MailAccount } from '../../api/types'
import { MenuItem, MenuSep, Popover, useAnchor } from '../../components/Popover'
import { toast, toastError } from '../../stores/ui'
import { ACCOUNT_HUES, AccountDot, hueName } from '../layout/accounts'
import { sidebarCountsKey } from '../layout/sidebarCounts'
import { t } from '../../i18n'

export function AccountColourButton({ account }: { account: MailAccount }) {
  const menu = useAnchor()
  const qc = useQueryClient()
  const label = t('settings.accountColour.colourFor', { address: account.address, colour: hueName(account.color) })
  const pick = (hue: string) => {
    menu.close()
    api
      .patch(`/connectors/${account.id}`, { color: hue })
      .then(() => {
        void qc.invalidateQueries({ queryKey: sidebarCountsKey })
        void qc.invalidateQueries({ queryKey: ['connectors'] })
        toast({ message: hue === 'auto' ? t('settings.accountColour.colourReset') : t('settings.accountColour.colourChanged', { colour: hueName(hue) }) })
      })
      .catch((e) => toastError(e))
  }
  return (
    <>
      <button type="button" className="icon-btn sm acct-colour-btn" aria-label={label} data-tip={label} data-testid={`account-colour-${account.id}`}
        aria-haspopup="menu" aria-expanded={menu.open} onClick={(e) => menu.toggle(e.currentTarget)}>
        <AccountDot account={account} label={false} />
      </button>
      <Popover anchor={menu.anchor} open={menu.open} onClose={menu.close} placement="bottom-start" minWidth={200} label={t('settings.accountColour.accountColour')}>
        {ACCOUNT_HUES.map((h) => (
          <MenuItem key={h} selected={account.color === h} icon={<AccountDot account={{ color: h, address: account.address }} label={false} />} onSelect={() => pick(h)}>
            {hueName(h)}
          </MenuItem>
        ))}
        <MenuSep />
        <MenuItem onSelect={() => pick('auto')}>{t('settings.accountColour.automatic')}</MenuItem>
      </Popover>
    </>
  )
}
