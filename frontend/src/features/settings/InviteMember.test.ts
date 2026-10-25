// ACT-SURFACES: web.settings web.setup
// Household invites: the status words (household.invite, household.invite_revoke) and what the
// invite page offers (household.join).
import { describe, expect, it } from 'vitest'
import { joinChoices } from '../auth/JoinPage'
import { inviteStatus, type Invite } from './InviteMember'

const base: Invite = { id: 1, email: 'cleo@example.com', name: 'Cleo', defaults: {}, state: 'ok', created_at: null,
  expires_at: '2026-10-13T12:00:00Z', used_at: null, revoked_at: null, created_by: 'Admin' }

describe('invites', () => {
  it('says how long a waiting invite lasts', () => {
    expect(inviteStatus(base, Date.parse('2026-10-06T12:00:00Z'))).toBe('Waiting · ends in 7 days')
    expect(inviteStatus({ ...base, state: 'revoked' })).toBe('Withdrawn')
    expect(inviteStatus({ ...base, state: 'expired' })).toBe('Expired')
  })
  it('offers single sign-on, a password, or the sign-in page', () => {
    const si = (oidc: boolean) => ({ oidc, oidc_name: 'Pocket', local_login: true })
    expect(joinChoices({ sign_in: si(true), set_password: false, has_account: false })).toEqual({ sso: true, password: false, signIn: false })
    expect(joinChoices({ sign_in: si(false), set_password: true, has_account: false })).toEqual({ sso: false, password: true, signIn: false })
    expect(joinChoices({ sign_in: si(false), set_password: false, has_account: true })).toEqual({ sso: false, password: false, signIn: true })
  })
})
