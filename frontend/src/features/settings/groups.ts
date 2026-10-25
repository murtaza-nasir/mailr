// The one map of Settings: group → pages → sections.
// The settings shell (SettingsPage.tsx) draws the groups, the pages and their sections from it,
// the palette's "Settings: …" entries come from it, and the old tab addresses (/settings/general,
// /settings/inbox, /settings/agents#approvals …) redirect through LEGACY_TABS below.
//
// To go back to the old layout, change this map, or check out the tag
// pre-regroup-2026-10-06.
//
// A section's id is also its anchor (/settings/mail/inbox#vip) and the key of its component in
// sections.tsx. `keys` are the settings it holds (tools/registry/settings), so a search for any
// setting finds its page; `words` are more words a search should match.

import { t } from '../../i18n'
import { assistantNameNow, DEFAULT_ASSISTANT_NAME } from '../rail/assistant/assistantName'

export type GroupId = 'you' | 'look' | 'mail' | 'calendar' | 'notes' | 'notifications' | 'ambrose' | 'agents' | 'admin'

export interface SettingsSection {
  id: string
  title: string
  keys?: string[]
  words?: string[]
}

export interface SettingsPageDef {
  id: string
  title: string
  sections: SettingsSection[]
}

export interface SettingsGroup {
  id: GroupId
  title: string
  /** One line under the group's name. */
  desc?: string
  pages: SettingsPageDef[]
}

export const SETTINGS_GROUPS: SettingsGroup[] = [
  {
    id: 'you',
    title: t('common.you'),
    pages: [
      {
        id: 'profile',
        title: t('settings.groups.profile'),
        sections: [
          { id: 'picture', title: t('common.yourPicture'), words: ['photo', 'avatar', 'gravatar'] },
          { id: 'name', title: t('common.yourName'), words: ['name'] },
          { id: 'setup-guide', title: t('settings.groups.setupGuide'), words: ['onboarding', t('settings.groups.startAgain')] },
        ],
      },
      {
        id: 'region',
        title: t('settings.groups.region'),
        sections: [
          {
            id: 'region',
            title: t('settings.groups.region'),
            keys: ['language', 'timezone', 'time_format', 'week_start', 'date_order', 'currency'],
            words: [t('settings.groups.timeZone'), 'clock', '12-hour', '24-hour', t('settings.groups.weekStarts'), t('settings.groups.dateOrder'), t('settings.groups.datesLike1210'), 'currency', 'language'],
          },
        ],
      },
      // What you, Ambrose and your agents changed, with Undo (features/activity). It sits
      // under You so every member has it; Agents & API stays an advanced group.
      {
        id: 'activity',
        title: t('common.activity'),
        sections: [{ id: 'activity', title: t('common.activity'), words: [t('settings.groups.recentChanges'), 'undo', 'history', t('settings.groups.whatChanged'), 'administrator'] }],
      },
      {
        id: 'security',
        title: t('settings.groups.passwordAndDevices'),
        sections: [
          { id: 'password', title: t('settings.groups.changePassword'), words: ['password', t('settings.groups.signIn')] },
          { id: 'devices', title: t('settings.groups.signedInDevices'), words: ['sessions', t('settings.groups.signOut'), 'phone'] },
          { id: 'install', title: t('settings.groups.installTheApp'), words: ['install', t('settings.groups.homeScreen'), 'pwa'] },
        ],
      },
    ],
  },
  {
    id: 'look',
    title: t('settings.groups.look'),
    pages: [
      {
        id: 'look',
        title: t('settings.groups.look'),
        sections: [
          { id: 'style', title: t('common.style'), keys: ['ui_theme'], words: ['theme', 'layout', 'classic'] },
          { id: 'colours', title: t('colour.picker.title'), keys: ['ui_accent'], words: ['accent', 'colour', 'color', 'palette', 'harmony', t('colour.picker.custom')] },
          { id: 'light-dark', title: t('common.lightOrDark'), keys: ['theme'], words: [t('settings.groups.darkMode'), t('settings.groups.colourMode'), t('settings.groups.colorMode')] },
          { id: 'text-spacing', title: t('common.textAndSpacing'), keys: ['density'], words: ['density', 'compact', 'roomy'] },
          { id: 'this-layout', title: t('settings.groups.thisLayout'), keys: ['reading_pane', 'reading_pane_by_theme', 'card_bundles'], words: [t('settings.groups.readingPane'), 'split', 'bundles'] },
          { id: 'keyboard', title: t('common.keyboardShortcuts'), keys: ['keyboard_shortcuts'], words: ['keys', 'hotkeys'] },
        ],
      },
    ],
  },
  {
    id: 'mail',
    title: t('common.mail2'),
    pages: [
      {
        id: 'reading',
        title: t('common.reading'),
        sections: [
          {
            id: 'reading',
            title: t('common.reading'),
            keys: ['conversation_view', 'page_size', 'auto_advance', 'images', 'sender_pictures'],
            words: [t('settings.groups.conversationView'), 'threads', t('settings.groups.pageSize'), 'auto-advance', 'images', t('settings.groups.senderPictures'), 'swipe'],
          },
          { id: 'archive', title: t('common.archive'), keys: ['archive_removes_current_label', 'archive_also_removes'], words: ['archiving'] },
        ],
      },
      {
        id: 'writing',
        title: t('settings.groups.writing'),
        sections: [
          {
            id: 'writing',
            title: t('settings.groups.writing'),
            keys: ['default_reply', 'undo_send_seconds', 'send_and_archive', 'signature_placement'],
            words: [t('settings.groups.replyAll'), t('settings.groups.undoSend'), t('settings.groups.sendAndArchive'), 'signature'],
          },
          { id: 'identities', title: t('settings.groups.sendMailAs'), words: ['identities', 'signature', t('settings.groups.fromAddress'), 'reply-to'] },
          { id: 'templates', title: t('common.templates'), words: [t('settings.groups.cannedResponses'), 'snippets'] },
          { id: 'away', title: t('settings.groups.awayMessage'), keys: ['vacation'], words: ['vacation', t('settings.groups.outOfOffice'), t('settings.groups.autoReply')] },
          // Make mailr the browser's app for email links (EmailLinks.tsx).
          { id: 'email-links', title: t('settings.emailLinks.title'), words: ['mailto', t('settings.groups.defaultEmailApp'), t('settings.groups.emailLinks')] },
        ],
      },
      {
        id: 'inbox',
        title: t('common.inbox'),
        sections: [
          {
            id: 'inbox',
            title: t('common.inbox'),
            keys: ['inbox_type', 'inbox_count', 'priority_sections', 'multiple_inboxes', 'categories', 'importance_markers', 'inbox_layouts', 'account_folders', 'account_folders_show_unread'],
            words: [t('settings.groups.inboxType'), 'layouts', 'tabs', 'sections', 'categories', t('settings.groups.importanceMarkers'), t('settings.groups.priorityInbox'), t('settings.groups.multipleInboxes'), t('settings.groups.eachAccountSeparately')],
          },
          { id: 'vip', title: t('settings.groups.vipSenders'), keys: ['vip'], words: [t('settings.groups.importantSenders')] },
        ],
      },
      {
        id: 'labels',
        title: t('common.labels'),
        sections: [
          { id: 'labels', title: t('common.labels'), keys: ['label_scheme'], words: [t('settings.groups.labelColours'), t('settings.groups.labelColors'), 'folders'] },
          { id: 'label-learning', title: t('settings.groups.learnFromMyChoices'), keys: ['auto_label', 'auto_label_precision', 'auto_label_min_examples', 'label_recency'], words: [t('settings.groups.autoLabel'), t('settings.groups.suggestedLabels')] },
        ],
      },
      {
        id: 'filters',
        title: t('settings.groups.filtersAndBlocked'),
        sections: [{ id: 'filters', title: t('settings.groups.filtersAndBlockedAddresses'), words: ['rules', 'block', t('settings.groups.blockedSenders')] }],
      },
      {
        id: 'spam',
        title: t('settings.groups.spamAndScreener'),
        sections: [
          { id: 'spam', title: t('common.spam'), keys: ['spam_classifier', 'spam_junk_score', 'spam_upstream_unknown_threshold', 'spam_suspect_score'], words: ['junk', 'phishing'] },
          { id: 'screener', title: t('common.screener'), keys: ['screener', 'screener_accounts'], words: ['first-time senders', t('settings.groups.newSenders')] },
        ],
      },
      {
        id: 'people',
        title: t('settings.groups.peopleAndGroups'),
        sections: [{ id: 'people', title: t('settings.groups.peopleAndGroups'), words: ['contacts', t('common.addressBook'), t('settings.groups.contactGroups')] }],
      },
      {
        id: 'accounts',
        title: t('common.mailAccounts'),
        sections: [
          { id: 'accounts', title: t('common.mailAccounts'), words: ['connect', 'imap', 'gmail', 'outlook', t('settings.groups.addAccount')] },
          { id: 'imap', title: t('settings.groups.phoneAndDesktopApps'), words: ['imap', t('settings.groups.appPassword'), 'thunderbird'] },
          { id: 'speakr', title: t('settings.groups.speakr'), words: ['recordings', 'transcripts'] },
        ],
      },
    ],
  },
  {
    id: 'calendar',
    title: t('settings.groups.calendarTasks'),
    pages: [
      {
        id: 'calendar',
        title: t('settings.groups.calendarTasks'),
        sections: [
          { id: 'calendar-accounts', title: t('settings.groups.calendarAndTaskAccounts'), words: ['caldav', 'calendars', t('settings.groups.taskLists')] },
          { id: 'working-hours', title: t('settings.groups.workingHoursAndNewEvents'), keys: ['calendar_working_hours', 'calendar_default_duration'], words: [t('settings.groups.eventLength'), t('settings.groups.workingDays')] },
          {
            id: 'reminder-defaults',
            title: t('settings.groups.reminderDefaults'),
            keys: ['morning_hour', 'reminders.default_timed_event', 'reminders.default_all_day_event', 'reminders.default_task', 'reminders.morning_hour'],
            words: [t('settings.groups.defaultReminder'), t('settings.groups.morningHour')],
          },
          { id: 'invitations', title: t('settings.groups.invitationsAndRepeatingTasks'), keys: ['calendar_add_new_invites'], words: ['invites', t('settings.groups.repeatingTasks')] },
          { id: 'free-busy', title: t('settings.groups.findATimeWithYour'), keys: ['calendar_share_free_busy'], words: ['free/busy', t('settings.groups.freeBusy'), 'household', 'family', t('settings.groups.shareMyCalendar'), t('settings.groups.findATime')] },
        ],
      },
    ],
  },
  {
    id: 'notes',
    title: t('settings.groups.notesTables'),
    pages: [{ id: 'notes', title: t('settings.groups.notesTables'), sections: [{ id: 'notes-export', title: t('settings.groups.exportYourNotes'), words: ['notes', 'lists', 'backup', 'markdown', 'tables'] }] }],
  },
  {
    id: 'notifications',
    title: t('common.notifications'),
    pages: [
      {
        id: 'notifications',
        title: t('common.notifications'),
        sections: [
          {
            id: 'notifications',
            title: t('common.notifications'),
            keys: ['mail_notifications', 'notify_approvals', 'notify_assistant', 'notify_accounts', 'reminders.events', 'reminders.tasks', 'reminders.web', 'reminders.missed', 'reminders.sound', 'reminders.repeat'],
            words: ['push', t('settings.groups.desktopNotifications'), 'reminders', 'sound', t('settings.groups.remindAgain')],
          },
        ],
      },
    ],
  },
  {
    id: 'ambrose',
    get title() { return t('settings.groups.text', { name: assistantNameNow() }) },
    get desc() { return t('settings.groups.whatKnowsAboutYouHow', { name: assistantNameNow() }) },
    pages: [
      {
        id: 'about',
        title: t('settings.groups.aboutYou'),
        sections: [
          { id: 'about', title: t('settings.groups.aboutYou'), words: [t('settings.groups.preferredName'), 'sign-off', t('settings.groups.writingPreferences'), 'role', 'languages', t('settings.groups.quietHours'), t('settings.groups.assistantName')] },
          { id: 'about-memories', title: t('memory.page.aboutYouMemories'), words: ['memories', 'remember'] },
        ],
      },
      { id: 'orders', title: t('settings.groups.standingOrdersRoutines'), sections: [{ id: 'orders', title: t('settings.groups.standingOrdersRoutines'), keys: ['meeting_prep_digest'], words: ['routines', 'schedule', 'digest', 'forward', 'note', t('settings.assistant.ordersSection.builtinForwardsName')] }] },
      {
        // Trusted addresses: the identity page only. What Ambrose does with a forwarded note is a standing order.
        id: 'forwards',
        title: t('settings.assistant.trustedSection.title'),
        sections: [
          { id: 'trusted-addresses', title: t('settings.assistant.trustedSection.title'), words: ['forward', 'trusted', t('settings.assistant.trustedSection.wordsWork'), t('settings.assistant.trustedSection.wordsCode')] },
          { id: 'handled-elsewhere', title: t('settings.assistant.trustedSection.elsewhereTitle'), keys: ['handled_elsewhere'], words: ['alias', 'memory'] },
        ],
      },
      { id: 'skills', title: t('common.skills'), sections: [{ id: 'skills', title: t('common.skills'), words: [t('settings.groups.slashCommands')] }] },
      // Sweep: the standing rules of every bulk review and the moves remembered from review cards.
      { id: 'sweep', title: t('settings.assistant.sweepSection.title'), sections: [{ id: 'sweep', title: t('settings.assistant.sweepSection.title'), keys: ['sweep_rules'], words: ['review', 'cleanup', 'rules', t('settings.assistant.sweepSection.pastChoices')] }] },
      // One Memory page: People & memories joined it; /ambrose/people redirects.
      { id: 'memory', title: t('settings.groups.memory'), sections: [{ id: 'memory', title: t('settings.groups.memory'), words: ['memories', 'remember', 'people', 'topics', 'suggestions', 'learn'] }] },
      {
        id: 'models',
        title: t('common.models'),
        sections: [
          { id: 'local-models', title: t('settings.groups.localModelsOnlyForMe'), keys: ['local_models_only'], words: [t('common.localOnly'), t('settings.groups.hostedModels'), 'privacy'] },
          { id: 'models', title: t('common.models'), keys: ['models'], words: [t('settings.groups.modelChoice')] },
          { id: 'model-use', title: t('settings.groups.modelUse'), words: ['usage', t('settings.groups.tokensUsed')] },
        ],
      },
      {
        id: 'briefs',
        title: t('settings.groups.briefsAndReplies'),
        sections: [{ id: 'briefs', title: t('settings.groups.briefsAndReplySuggestions'), keys: ['brief_position', 'reply_suggestions'], words: [t('settings.groups.suggestedReplies'), 'brief'] }],
      },
      { id: 'lab', title: t('settings.groups.decisionsLab'), sections: [{ id: 'lab', title: t('settings.groups.decisionsLab'), words: ['experiments'] }] },
      { id: 'importance', title: t('common.reviewImportance'), sections: [{ id: 'importance', title: t('common.reviewImportance'), words: ['important', 'importance'] }] },
    ],
  },
  {
    id: 'agents',
    title: t('settings.groups.agentsApi'),
    pages: [
      {
        id: 'agents',
        title: t('settings.groups.agentsApi'),
        sections: [
          { id: 'approvals', title: t('common.approvals'), words: ['approve', 'history'] },
          { id: 'permissions', title: t('settings.groups.permissions'), words: [t('settings.groups.whatAgentsMayDo')] },
          { id: 'tokens', title: t('settings.groups.apiTokens'), words: ['token', 'mcp', t('settings.groups.claudeCode')] },
          { id: 'failed', title: t('settings.groups.failedMessages'), words: ['outbox', t('settings.groups.notSent')] },
          { id: 'audit', title: t('settings.groups.auditLog'), words: ['log'] },
        ],
      },
    ],
  },
  {
    id: 'admin',
    title: t('settings.groups.admin'),
    pages: [
      { id: 'users', title: t('common.users'), sections: [{ id: 'users', title: t('common.users'), keys: ['accounts.admin_connect'], words: ['people', 'members', 'prepare', t('settings.groups.addUser'), t('settings.groups.resetPassword'), t('settings.groups.connectMailAccountsForMembers'), 'organisation'] }] },
      { id: 'access', title: t('settings.groups.accessPolicies'), sections: [{ id: 'access', title: t('settings.groups.groupPolicies'), words: ['groups', 'access', 'policies'] }] },
      {
        id: 'models',
        title: t('common.models'),
        sections: [
          { id: 'admin-model-defaults', title: t('settings.groups.whichModelDoesWhat'), words: [t('settings.groups.modelDefaults')] },
          { id: 'admin-local-policy', title: t('settings.groups.hostedModelsForEveryone'), keys: ['models.local_policy'], words: [t('settings.groups.localOnlyForEveryone')] },
          { id: 'admin-models', title: t('settings.groups.modelEndpoints'), words: ['vllm', 'ollama', 'openai', 'endpoint'] },
        ],
      },
      {
        id: 'web-search',
        title: t('settings.groups.webSearch'),
        sections: [
          { id: 'web-search', title: t('settings.groups.webSearch'), words: [t('settings.groups.searchProviders'), 'searxng', 'jina', 'firecrawl'] },
          { id: 'page-readers', title: t('settings.groups.pageReaders'), words: ['browserless', 'firecrawl', 'jina', 'reddit'] },
        ],
      },
      {
        id: 'calendars',
        title: t('common.calendars'),
        sections: [{ id: 'admin-calendars', title: t('common.calendars'), keys: ['calendars.builtin', 'calendars.builtin_who', 'calendars.sharing'], words: ['built-in calendars', 'sharing'] }],
      },
      {
        id: 'phones',
        title: t('settings.groups.phoneApps'),
        sections: [{ id: 'admin-phones', title: t('settings.groups.phoneApps'), keys: ['calendars.phone_access', 'calendars.phone_url', 'phone_apps.notes_min_version'], words: ['android', 'apk', 'davx5', t('settings.groups.oldestVersion'), 'update'] }],
      },
      { id: 'diagnostics', title: t('settings.groups.diagnostics'), sections: [{ id: 'diagnostics', title: t('settings.groups.diagnostics'), words: ['health', 'status', 'problems'] }] },
      {
        id: 'data',
        title: t('settings.groups.dataAndPrivacy'),
        sections: [
          { id: 'data-policies', title: t('settings.groups.dataPolicies'), words: ['local-only domains', 'edu'] },
          { id: 'image-proxy', title: t('settings.groups.imageProxy'), words: ['images', 'egress'] },
        ],
      },
    ],
  },
]

/** Settings that live outside Settings, with where (the search test reads this). */
export const OUTSIDE_SETTINGS: Record<string, string> = {
  advanced_settings: t('settings.groups.theAdvancedSettingsSwitchAt'),
  nav_favorites: t('settings.groups.theNavigationAddToFavorites'),
  account_folder_pins: t('settings.groups.theNavigationPinToAccount'),
  account_folders_more_open: t('settings.groups.theNavigationMoreFolders'),
  account_folders_hint_dismissed: t('settings.groups.theNavigationAccountsHint'),
  side_panel: t('settings.groups.theSidePanelButtons'),
  calendar_snap: t('settings.groups.theCalendarsOwnViewMenu'),
  calendar_dim_past: t('settings.groups.theCalendarsOwnViewMenu'),
  calendar_week_numbers: t('settings.groups.theCalendarsOwnViewMenu'),
  calendar_conflicts: t('settings.groups.theCalendarsOwnViewMenu'),
  calendar_video_template: t('settings.groups.theCalendarsOwnViewMenu'),
  calendar_view: t('settings.groups.theCalendarsOwnViewMenu2'),
  get llm_triage() { return t('settings.groups.andTheApiOnly', { name: assistantNameNow() }) },
}

/** Where each old tab went (before 7 Oct 2026 Settings had 15 tabs). */
export const LEGACY_TABS: Record<string, string> = {
  general: 'mail/reading',
  labels: 'mail/labels',
  inbox: 'mail/inbox',
  accounts: 'mail/accounts',
  templates: 'mail/writing#templates',
  people: 'mail/people',
  filters: 'mail/filters',
  spam: 'mail/spam',
  screener: 'mail/spam#screener',
  assistant: 'ambrose',
  access: 'admin/access',
  activity: 'you/activity',
  'agents/activity': 'you/activity',
  'ambrose/people': 'ambrose/memory#memory-people',
}

/** Old anchors of the General tab, Accounts and the others, to the section that has them now. */
const LEGACY_ANCHORS: Record<string, string> = { speakr: 'speakr', what: 'notifications', 'push-mail': 'notifications' }

export function groupById(id: string | undefined): SettingsGroup | undefined {
  return SETTINGS_GROUPS.find((g) => g.id === id)
}

/** The page and group that hold a section. */
export function findSection(id: string): { group: SettingsGroup; page: SettingsPageDef; section: SettingsSection } | null {
  for (const group of SETTINGS_GROUPS)
    for (const page of group.pages)
      for (const section of page.sections) if (section.id === id) return { group, page, section }
  return null
}

/**
 * The new address for an old one, or null when the address is already new. `tab` is the first
 * part after /settings/, `sub` the second; `search` and `hash` come along, so a link such as
 * /settings/filters?new=… or /settings/agents#approvals keeps working.
 */
export function legacyTarget(tab: string | undefined, sub: string | undefined, search: string, hash: string): string | null {
  if (!tab) return null
  const params = new URLSearchParams(search)
  if (tab === 'assistant') {
    // /settings/assistant?section=models (and ?part=lab) → /settings/ambrose/models
    const part = params.get('section') ?? params.get('part')
    params.delete('section')
    params.delete('part')
    const q = params.toString()
    const ambrose = groupById('ambrose')!
    const page = ambrose.pages.find((p) => p.id === part)?.id ?? ambrose.pages[0].id
    return `/settings/ambrose/${page}${q ? `?${q}` : ''}${hash}`
  }
  // A page that moved to another group (/settings/agents/activity → /settings/you/activity).
  const moved = sub ? LEGACY_TABS[`${tab}/${sub}`] : undefined
  if (moved) {
    const [path, anchor] = moved.split('#')
    return `/settings/${path}${search}${hash || (anchor ? `#${anchor}` : '')}`
  }
  const g = groupById(tab)
  if (g) {
    // A page id that is really a section of the group (/settings/agents/approvals), or an old anchor.
    if (sub && !g.pages.some((p) => p.id === sub)) {
      const f = findSection(sub)
      if (f && f.group.id === g.id) return `/settings/${g.id}/${f.page.id}${search}#${f.section.id}`
      return `/settings/${g.id}${search}${hash}`
    }
    return null
  }
  const to = LEGACY_TABS[tab]
  if (!to) return `/settings${search}`
  const [path, anchor] = to.split('#')
  const old = hash.replace(/^#/, '')
  // An anchor of the old tab wins: it names a section, wherever that is now.
  const named = old ? (findSection(LEGACY_ANCHORS[old] ?? old) ?? null) : null
  if (named) return `/settings/${named.group.id}/${named.page.id}${search}#${named.section.id}`
  return `/settings/${path}${search}${anchor ? `#${anchor}` : hash}`
}

/**
 * Former names of renamed sections (UI-text sweep, October 2026),
 * kept as search words so older docs, notes and habits still find them. English only, as they were.
 */
export const FORMER_NAMES: Record<string, string[]> = {
  'admin-model-defaults': ['which model does what'], // i18n-ignore
  'trusted-addresses': ['addresses that speak for you'], // i18n-ignore
  'handled-elsewhere': ['addresses another assistant handles'], // i18n-ignore
  memory: ['what ambrose knows'], // i18n-ignore
  about: ['get to know you'], // i18n-ignore
  'free-busy': ['find a time with your groups'], // i18n-ignore
  filters: ['filters and blocked', 'filters and blocking', 'filters and blocked addresses'], // i18n-ignore
  password: ['change password', 'password and devices'], // i18n-ignore
  devices: ['password and devices'], // i18n-ignore
  install: ['install the app', 'password and devices'], // i18n-ignore
  // Titles that said "and" before they took "&".
  'text-spacing': ['text and spacing'], // i18n-ignore
  spam: ['spam and screener'], // i18n-ignore
  screener: ['spam and screener'], // i18n-ignore
  people: ['people and groups'], // i18n-ignore
  imap: ['phone and desktop apps'], // i18n-ignore
  'calendar-accounts': ['calendar and task accounts'], // i18n-ignore
  'working-hours': ['working hours and new events'], // i18n-ignore
  invitations: ['invitations and repeating tasks'], // i18n-ignore
  briefs: ['briefs and replies', 'briefs and reply suggestions'], // i18n-ignore
  'data-policies': ['data and privacy'], // i18n-ignore
  'image-proxy': ['data and privacy'], // i18n-ignore
  'notes-export': ['export your notes'], // i18n-ignore
  'local-models': ['local models only for me'], // i18n-ignore
  notifications: ['what notifies you', 'which reminders'], // i18n-ignore
}

/** Every section with its group and page, for search (the palette's "Settings: …" entries). */
export function allSections(): { group: SettingsGroup; page: SettingsPageDef; section: SettingsSection }[] {
  return SETTINGS_GROUPS.flatMap((group) => group.pages.flatMap((page) => page.sections.map((section) => ({ group, page, section }))))
}

/** The words a search matches for a section: its title, its page and group, its words and the
 *  plain names of its settings, and its former names. */
export function sectionSearchText(s: { group: SettingsGroup; page: SettingsPageDef; section: SettingsSection }): string {
  const keys = (s.section.keys ?? []).map((k) => k.replace(/^reminders\./, '').replace(/[._]/g, ' '))
  // The assistant's group keeps its default name as a search word: "Ambrose" still finds it after a rename.
  const former = s.group.id === 'ambrose' ? [DEFAULT_ASSISTANT_NAME] : []
  return [s.section.title, s.page.title, s.group.title, ...(s.section.words ?? []), ...(FORMER_NAMES[s.section.id] ?? []), ...former, ...keys].join(' ').toLowerCase()
}
