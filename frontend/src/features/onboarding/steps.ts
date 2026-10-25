// The setup guide's logic, kept free of React so it is tested on its own (steps.test.ts): which
// steps show, moving between them, progress, the email provider from an address, the history
// choice as connector settings, the phone from the browser, and the inbox styles.

import { t } from '../../i18n'
import { assistantNameNow } from '../rail/assistant/assistantName'

export type StepId = 'welcome' | 'accounts' | 'look' | 'inbox' | 'phone' | 'calendar' | 'notifications' | 'about' | 'done'
export type Mark = 'done' | 'skipped'

export const STEP_ORDER: StepId[] = ['welcome', 'accounts', 'look', 'inbox', 'phone', 'calendar', 'notifications', 'about', 'done']

export const STEP_TITLES: Record<StepId, string> = {
  welcome: t('onboarding.steps.welcome2'),
  accounts: t('onboarding.steps.connectYourEmail'),
  look: t('onboarding.steps.chooseYourLook'),
  inbox: t('onboarding.steps.yourInboxStyle'),
  phone: t('onboarding.steps.yourPhone'),
  calendar: t('onboarding.steps.yourCalendars'),
  notifications: t('common.notifications'),
  about: t('onboarding.steps.getToKnowYou'),
  done: t('onboarding.steps.allSet'),
}

export function isStepId(v: unknown): v is StepId {
  return typeof v === 'string' && (STEP_ORDER as string[]).includes(v)
}

/** The server's view of the guide (GET /onboarding). */
export interface OnboardingState {
  status: 'none' | 'pending' | 'active' | 'done' | 'dismissed'
  step: StepId
  steps: Partial<Record<StepId, Mark>>
  order: StepId[]
  optional: StepId[]
  started_at: string | null
  finished_at: string | null
  preset: string | null
  completed: number
  total: number
  has_accounts?: boolean
  offer: { assistant: boolean; notes?: boolean; calendar_url?: string }
  assistant: { allowed: boolean; available: boolean; name: string; message: string | null }
  phone: { server: string; apk_url: string; get_app: string; apps?: PhoneAppRelease[] }
}

/** One Android app this server hands out (GET /app/releases, backend core/phone_apps.py). The
 *  version, size and checksum are null when the server holds no APK of it (`url` is then the
 *  release page, or MAILR_ANDROID_APK_URL for mailr). */
export interface PhoneAppRelease {
  app: 'mailr' | 'notes'
  name: string
  package: string
  url: string
  on_server: boolean
  version_name: string | null
  version_code: number | null
  size: number | null
  sha256: string | null
  published_at: string | null
}

/** The steps this person sees. "About you" needs the assistant: the admin may leave it out
 *  of the guide, and the access policy may turn the assistant off. */
export function visibleSteps(s: Pick<OnboardingState, 'offer' | 'assistant'>): StepId[] {
  return STEP_ORDER.filter((id) => id !== 'about' || (s.offer.assistant !== false && s.assistant.allowed))
}

export function nextStep(steps: StepId[], current: StepId): StepId {
  const i = steps.indexOf(current)
  return steps[Math.min(steps.length - 1, i < 0 ? 0 : i + 1)]
}

export function prevStep(steps: StepId[], current: StepId): StepId | null {
  const i = steps.indexOf(current)
  return i > 0 ? steps[i - 1] : null
}

/** Where to open the guide: the step on record if it still shows, else the first step not done. */
export function resumeStep(steps: StepId[], s: Pick<OnboardingState, 'step' | 'steps'>): StepId {
  if (steps.includes(s.step)) return s.step
  return steps.find((id) => !s.steps[id]) ?? steps[steps.length - 1]
}

/** "Step 3 of 8": the final "All set" screen is not counted as a step. */
export function progress(steps: StepId[], current: StepId): { index: number; count: number; fraction: number } {
  const counted = steps.filter((id) => id !== 'done')
  const i = current === 'done' ? counted.length : Math.max(0, counted.indexOf(current))
  return { index: Math.min(i + 1, counted.length), count: counted.length, fraction: counted.length ? i / counted.length : 1 }
}

// ---- email providers ----

export type Provider = 'gmail' | 'icloud' | 'yahoo' | 'fastmail' | 'outlook' | 'other'

const DOMAINS: [Provider, string[]][] = [
  ['gmail', ['gmail.com', 'googlemail.com']],
  ['icloud', ['icloud.com', 'me.com', 'mac.com']],
  ['yahoo', ['yahoo.com', 'ymail.com', 'rocketmail.com', 'yahoo.co.uk', 'yahoo.fr', 'yahoo.de']],
  ['fastmail', ['fastmail.com', 'fastmail.fm']],
  ['outlook', ['outlook.com', 'hotmail.com', 'live.com', 'msn.com', 'hotmail.co.uk']],
]

/** The provider from the address alone (Google Workspace and others are found by the server's
 *  lookup instead, see providerFromPreset). */
export function providerFor(email: string): Provider {
  const domain = email.trim().toLowerCase().split('@')[1] ?? ''
  for (const [p, ds] of DOMAINS) if (ds.includes(domain)) return p
  return 'other'
}

/** The provider from the server's preset id (POST /connectors/discover). */
export function providerFromPreset(preset: string | null | undefined, email: string): Provider {
  switch (preset) {
    case 'gmail':
      return 'gmail'
    case 'icloud':
      return 'icloud'
    case 'yahoo':
      return 'yahoo'
    case 'fastmail':
      return 'fastmail'
    case 'office365':
      return 'outlook'
    default:
      return providerFor(email)
  }
}

export interface PasswordGuide {
  /** What the provider is called in plain words. */
  name: string
  /** Why mailr needs a special password, in one or two sentences. */
  why: string
  /** What must be on first (2-Step Verification), or null. */
  before: { title: string; steps: string[]; link: string; linkText: string } | null
  steps: string[]
  link: string
  linkText: string
  /** What the password looks like, to reassure. */
  looks: string
}

export const PASSWORD_GUIDES: Partial<Record<Provider, PasswordGuide>> = {
  gmail: {
    name: t('onboarding.steps.google'),
    why: t('onboarding.steps.googleDoesNotLetOther'),
    before: {
      title: t('onboarding.steps.first2StepVerificationMust'),
      steps: [
        t('onboarding.steps.openYourGoogleAccountsSecurity'),
        t('onboarding.steps.find2StepVerificationIf'),
        t('onboarding.steps.ifItIsOffChoose'),
      ],
      link: 'https://myaccount.google.com/signinoptions/twosv',
      linkText: t('onboarding.steps.open2StepVerification'),
    },
    steps: [
      t('onboarding.steps.openTheAppPasswordsPage'),
      t('onboarding.steps.underAppNameTypeMailr'),
      t('onboarding.steps.googleShowsAPasswordOf'),
      t('onboarding.steps.comeBackHereAndPaste'),
    ],
    link: 'https://myaccount.google.com/apppasswords',
    linkText: t('onboarding.steps.openAppPasswords'),
    looks: t('onboarding.steps.itLooksLikeThisAbcd'),
  },
  icloud: {
    name: t('onboarding.steps.apple'),
    why: t('onboarding.steps.appleDoesNotLetOther'),
    before: null,
    steps: [
      t('onboarding.steps.openTheAppleAccountPage'),
      t('onboarding.steps.chooseSignInAndSecurity'),
      t('onboarding.steps.chooseTheButtonTypeMailr'),
      t('onboarding.steps.copyThePasswordAppleShows'),
    ],
    link: 'https://account.apple.com/account/manage',
    linkText: t('onboarding.steps.openYourAppleAccount'),
    looks: t('onboarding.steps.itLooksLikeThisAbcd2'),
  },
  yahoo: {
    name: t('onboarding.steps.yahoo'),
    why: t('onboarding.steps.yahooDoesNotLetOther'),
    before: null,
    steps: [
      t('onboarding.steps.openYahoosAccountSecurityPage'),
      t('onboarding.steps.chooseGenerateAppPasswordOr'),
      t('onboarding.steps.typeMailrAsTheApp'),
      t('onboarding.steps.copyThePasswordAndPaste'),
    ],
    link: 'https://login.yahoo.com/account/security',
    linkText: t('onboarding.steps.openAccountSecurity'),
    looks: t('onboarding.steps.itIs16LettersLong'),
  },
  fastmail: {
    name: t('onboarding.steps.fastmail'),
    why: t('onboarding.steps.fastmailAsksForASeparate'),
    before: null,
    steps: [
      t('onboarding.steps.openFastmailsPrivacySecuritySettings'),
      t('onboarding.steps.chooseManageAppPasswordsAnd'),
      t('onboarding.steps.giveItTheNameMailr'),
      t('onboarding.steps.copyThePasswordAndPaste'),
    ],
    link: 'https://app.fastmail.com/settings/security/devicekeys',
    linkText: t('onboarding.steps.openFastmailSettings'),
    looks: t('onboarding.steps.itIs16LettersAnd'),
  },
}

// ---- how much mail to bring in ----

export type HistoryChoice = 'recent_then_all' | 'year' | 'everything'

export const HISTORY_CHOICES: { id: HistoryChoice; title: string; desc: string }[] = [
  { id: 'recent_then_all', title: t('onboarding.steps.startWithTheLast3'), desc: t('onboarding.steps.recommendedYourRecentMailIs') },
  { id: 'year', title: t('onboarding.steps.onlyTheLastYear'), desc: t('onboarding.steps.olderMailStaysWhereIt') },
  { id: 'everything', title: t('onboarding.steps.everythingAtOnce'), desc: t('onboarding.steps.allYourMailOldestFirst') },
]

function isoDay(d: Date): string {
  return d.toISOString().slice(0, 10)
}

/** The connector settings for a history choice (backend connectors/imap.py since/backfill). */
export function historyConfig(choice: HistoryChoice, today: Date = new Date()): { since?: string; backfill?: boolean } {
  if (choice === 'everything') return {}
  const d = new Date(Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), today.getUTCDate()))
  if (choice === 'year') {
    d.setUTCFullYear(d.getUTCFullYear() - 1)
    return { since: isoDay(d) }
  }
  d.setUTCMonth(d.getUTCMonth() - 3)
  return { since: isoDay(d), backfill: true }
}

// ---- the phone ----

export type Phone = 'android' | 'ios'

/** The kind of phone this browser runs on, when it says (iPadOS calls itself a Mac with touch). */
export function detectPhone(ua: string, maxTouchPoints = 0): Phone | null {
  if (/android/i.test(ua)) return 'android'
  if (/iphone|ipad|ipod/i.test(ua)) return 'ios'
  if (/macintosh/i.test(ua) && maxTouchPoints > 1) return 'ios'
  return null
}

/** The link the Android app opens with the server filled in (Android parseSetupLink). */
export function androidSetupLink(server: string): string {
  return `mailr://setup?server=${encodeURIComponent(server)}`
}

/** The link the Notes app opens with the server filled in (its mailr-notes://setup link). */
export function notesSetupLink(server: string): string {
  return `mailr-notes://setup?server=${encodeURIComponent(server)}`
}

/** The release of one app, or a stand-in that links to `fallbackUrl` with no version. */
export function appRelease(apps: PhoneAppRelease[] | undefined, app: 'mailr' | 'notes', fallbackUrl: string): PhoneAppRelease {
  const hit = apps?.find((a) => a.app === app)
  if (hit) return hit
  return { app, name: app === 'mailr' ? 'Mailr' : t('onboarding.steps.mailrNotes'), package: '', url: fallbackUrl, on_server: false, version_name: null, version_code: null, size: null, sha256: null, published_at: null }
}

/** What the notes card on the last screen offers for the phone: the Notes app on Android, mailr on
 *  the Home Screen on an iPhone (notes are in the web app). A phone-sized screen that does not say
 *  which phone it is gets both; a computer gets neither; mailr already on the Home Screen needs no
 *  iPhone hint. */
export function notesPhoneOffer(here: Phone | null, narrow: boolean, standalone: boolean): { android: boolean; ios: boolean } {
  return { android: here === 'android' || (!here && narrow), ios: (here === 'ios' || (!here && narrow)) && !standalone }
}

/** The page a phone opens from the QR code: download the app, then open it with the server set. */
export function getAppLink(server: string): string {
  return `${server.replace(/\/+$/, '')}/get-app`
}

// ---- inbox styles ----

export type InboxStyle = 'simple' | 'important_first' | 'default'

export const INBOX_STYLES: { id: InboxStyle; title: string; desc: string }[] = [
  { id: 'simple', title: t('common.simple'), desc: t('onboarding.steps.allYourMailInOne') },
  { id: 'important_first', title: t('common.importantFirst'), desc: t('onboarding.steps.whatMattersMostAtThe') },
  { id: 'default', title: t('common.categories'), desc: t('onboarding.steps.tabsForPrimaryPromotionsSocial') },
]

/** The style an inbox_type setting corresponds to, or null for anything else (custom layouts). */
export function inboxStyleOf(inboxType: string | undefined): InboxStyle | null {
  return inboxType === 'simple' || inboxType === 'important_first' || inboxType === 'default' ? inboxType : null
}

// ---- notifications ----

export type NotifyChoice = 'important' | 'vip' | 'all' | 'off'

export const NOTIFY_CHOICES: { id: NotifyChoice; title: string; desc: string }[] = [
  { id: 'important', title: t('common.importantMail'), desc: t('onboarding.steps.mailFromRealPeopleThat') },
  { id: 'vip', title: t('onboarding.steps.onlyMyVips'), desc: t('onboarding.steps.onlyPeopleYouMarkAs') },
  { id: 'all', title: t('onboarding.steps.everyNewEmail'), desc: t('onboarding.steps.everyMessageAsItArrives') },
  { id: 'off', title: t('common.none'), desc: t('onboarding.steps.noMailNotificationsRemindersStill') },
]

/** The Web Push events a browser takes (backend core/push.py EVENTS). What notifies is the
 *  account's choice (Settings → Notifications, backend core/notifyprefs.py); a browser only
 *  narrows it: `mail` false is "Mail on this browser: Off". Reminders are opt-in per device. */
export function pushEventsFor(mail = true): string[] {
  const base = ['reminder', 'approval', 'send_failed', 'assistant', 'account']
  return mail ? [...base, 'new_mail'] : base
}

// ---- the tour ----

export const TIPS: { id: string; title: string; body: string }[] = [
  { id: 'search', title: t('onboarding.steps.findAnything'), body: t('onboarding.steps.typeInTheSearchBox') },
  { id: 'archive', title: t('onboarding.steps.archiveAndSwipe'), body: t('onboarding.steps.doneWithAnEmailArchive') },
  { id: 'snooze', title: t('onboarding.steps.snoozeForLater'), body: t('onboarding.steps.notNowSnoozeAnEmail') },
  { id: 'labels', title: t('common.labels'), body: t('onboarding.steps.giveEmailsLabelsLikeFamily') },
  { id: 'help', get title() { return t('onboarding.steps.helpAnd', { name: assistantNameNow() }) }, get body() { return t('onboarding.steps.theQuestionMarkInThe', { name: assistantNameNow() }) } },
]
