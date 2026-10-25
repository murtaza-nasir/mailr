import { describe, expect, it } from 'vitest'
import {
  appRelease,
  notesPhoneOffer,
  notesSetupLink,
  androidSetupLink, detectPhone, pushEventsFor, getAppLink, historyConfig, inboxStyleOf, nextStep, prevStep, progress, providerFor, providerFromPreset,
  resumeStep, STEP_ORDER, visibleSteps, PASSWORD_GUIDES, type OnboardingState,
} from './steps'

const base = { offer: { assistant: true }, assistant: { allowed: true, available: true, name: 'Ambrose', message: null } } as Pick<OnboardingState, 'offer' | 'assistant'>

describe('which steps show', () => {
  it('shows every step when the assistant is offered and allowed', () => {
    expect(visibleSteps(base)).toEqual(STEP_ORDER)
  })
  it('leaves out About you when the admin leaves it out or the policy turns the assistant off', () => {
    expect(visibleSteps({ ...base, offer: { assistant: false } })).not.toContain('about')
    expect(visibleSteps({ ...base, assistant: { ...base.assistant, allowed: false } })).not.toContain('about')
    // Allowed but no model right now: the step stays, with the fields.
    expect(visibleSteps({ ...base, assistant: { ...base.assistant, available: false } })).toContain('about')
  })
})

describe('moving between steps', () => {
  const steps = visibleSteps(base)
  it('goes forward and back, and stops at the ends', () => {
    expect(nextStep(steps, 'welcome')).toBe('accounts')
    expect(nextStep(steps, 'done')).toBe('done')
    expect(prevStep(steps, 'accounts')).toBe('welcome')
    expect(prevStep(steps, 'welcome')).toBeNull()
  })
  it('skips steps that do not show', () => {
    const noAbout = visibleSteps({ ...base, offer: { assistant: false } })
    expect(nextStep(noAbout, 'notifications')).toBe('done')
  })
  it('resumes at the step on record, else the first step not done', () => {
    expect(resumeStep(steps, { step: 'look', steps: { welcome: 'done' } })).toBe('look')
    const noAbout = visibleSteps({ ...base, offer: { assistant: false } })
    expect(resumeStep(noAbout, { step: 'about', steps: { welcome: 'done', accounts: 'skipped' } })).toBe('look')
  })
  it('counts progress without the final screen', () => {
    expect(progress(steps, 'welcome')).toEqual({ index: 1, count: 8, fraction: 0 })
    expect(progress(steps, 'look').index).toBe(3)
    expect(progress(steps, 'done')).toEqual({ index: 8, count: 8, fraction: 1 })
  })
})

describe('email providers', () => {
  it('finds the provider from the address', () => {
    expect(providerFor('Rosa.Lind@Gmail.com ')).toBe('gmail')
    expect(providerFor('a@googlemail.com')).toBe('gmail')
    expect(providerFor('a@me.com')).toBe('icloud')
    expect(providerFor('a@hotmail.com')).toBe('outlook')
    expect(providerFor('a@example.org')).toBe('other')
  })
  it('trusts the server lookup for custom domains (Google Workspace)', () => {
    expect(providerFromPreset('gmail', 'rosa@lind.family')).toBe('gmail')
    expect(providerFromPreset('office365', 'rosa@lind.family')).toBe('outlook')
    expect(providerFromPreset(null, 'rosa@yahoo.com')).toBe('yahoo')
  })
  it('gives Gmail the 2-Step Verification prerequisite and the app passwords page', () => {
    const g = PASSWORD_GUIDES.gmail!
    expect(g.before?.title).toMatch(/2-Step Verification/)
    expect(g.link).toBe('https://myaccount.google.com/apppasswords')
    expect(g.steps.length).toBeGreaterThanOrEqual(3)
    expect(PASSWORD_GUIDES.other).toBeUndefined()
  })
})

describe('history', () => {
  const today = new Date('2026-10-04T12:00:00Z')
  it('starts with three months and brings the rest in afterwards', () => {
    expect(historyConfig('recent_then_all', today)).toEqual({ since: '2026-07-04', backfill: true })
  })
  it('can keep only the last year, or take everything at once', () => {
    expect(historyConfig('year', today)).toEqual({ since: '2025-10-04' })
    expect(historyConfig('everything', today)).toEqual({})
  })
})

describe('the phone', () => {
  it('detects Android and iPhone, and iPadOS that calls itself a Mac', () => {
    expect(detectPhone('Mozilla/5.0 (Linux; Android 14; Pixel 8)')).toBe('android')
    expect(detectPhone('Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X)')).toBe('ios')
    expect(detectPhone('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)', 5)).toBe('ios')
    expect(detectPhone('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)', 0)).toBeNull()
    expect(detectPhone('Mozilla/5.0 (X11; Linux x86_64)')).toBeNull()
  })
  it('builds the links the app and the QR code use', () => {
    expect(androidSetupLink('https://mail.example.com')).toBe('mailr://setup?server=https%3A%2F%2Fmail.example.com')
    expect(getAppLink('https://mail.example.com/')).toBe('https://mail.example.com/get-app')
  })
})

it('maps the inbox setting to a style', () => {
  expect(inboxStyleOf('simple')).toBe('simple')
  expect(inboxStyleOf('default')).toBe('default')
  expect(inboxStyleOf('layout:mine')).toBeNull()
})

it('turns the notification choice into Web Push events', () => {
  // One notification model: the browser follows the account's choice, and can only drop mail.
  expect(pushEventsFor()).toEqual(['reminder', 'approval', 'send_failed', 'assistant', 'account', 'new_mail'])
  expect(pushEventsFor(false)).toEqual(['reminder', 'approval', 'send_failed', 'assistant', 'account'])
})

describe('notes and the Notes app', () => {
  it('offers the Notes app on Android and the Home Screen on an iPhone', () => {
    expect(notesPhoneOffer('android', true, false)).toEqual({ android: true, ios: false })
    expect(notesPhoneOffer('ios', true, false)).toEqual({ android: false, ios: true })
    // Already on the Home Screen: nothing to add.
    expect(notesPhoneOffer('ios', true, true)).toEqual({ android: false, ios: false })
    // A phone-sized window that does not say which phone: both; a computer: neither.
    expect(notesPhoneOffer(null, true, false)).toEqual({ android: true, ios: true })
    expect(notesPhoneOffer(null, false, false)).toEqual({ android: false, ios: false })
  })
  it('links the Notes app with the server filled in, and falls back to the release page', () => {
    expect(notesSetupLink('https://mail.example.com')).toBe('mailr-notes://setup?server=https%3A%2F%2Fmail.example.com')
    const fallback = appRelease(undefined, 'notes', 'https://example.org/releases')
    expect(fallback).toMatchObject({ app: 'notes', url: 'https://example.org/releases', version_name: null, on_server: false })
    const served = { ...fallback, url: '/api/v1/app/notes.apk', on_server: true, version_name: '1.0.2' }
    expect(appRelease([served], 'notes', 'x')).toBe(served)
  })
})
