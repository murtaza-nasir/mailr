// The component of each section in groups.ts, by section id. The settings shell draws a page's
// sections in the order groups.ts gives; a section id without a component here fails the test in
// groups.test.ts.

import type { ComponentType } from 'react'
import { ActivityPage } from '../activity/ActivityPage'
import { PeopleTab } from '../people/PeopleTab'
import { AccessTab } from './access/AccessTab'
import { DevicesSection, IdentitiesSection, ImapSection, MailAccountsSection, PasswordSection } from './AccountsTab'
import { CalendarsSection, DefaultsSection, ImageProxySection, LocalPolicySection, ModelsSection as AdminModelsSection, PoliciesSection, UsersSection } from './AdminTab'
import { ApprovalsSection, AuditPart, OutboxSection, PermissionsPart, TokensPart } from './AgentsTab'
import { BriefsSection } from './AmbroseSections'
import { ImportanceReview } from './assistant/ImportanceReview'
import { LabelLearningSection } from './assistant/LabelLearningSection'
import { LabSection } from './assistant/LabSection'
import { LocalModelsSection, ModelUseSection } from './assistant/LocalModelsSection'
import { AboutYouMemories, MemorySection } from './assistant/MemorySection'
import { ModelsSection } from './assistant/ModelsSection'
import { OrdersSection } from './assistant/OrdersSection'
import { ProfileSection } from './assistant/ProfileSection'
import { SkillsSection } from './assistant/SkillsSection'
import { SweepSection } from './assistant/SweepSection'
import { HandledElsewhereSection, TrustedAddressesSection } from './assistant/TrustedAddressesSection'
import { CalendarAccountsSection } from './CalendarTab'
import { FreeBusySection } from './FreeBusySection'
import { InvitesAndTasksSection, RegionSection, RemindersSection, WorkingHoursSection } from './CalendarPrefs'
import { DiagnosticsSection } from './DiagnosticsAdmin'
import { EmailLinksSection } from './EmailLinks'
import { FiltersTab } from './FiltersTab'
import { InboxTab } from './InboxTab'
import { LabelColoursSetting } from './LabelColours'
import { LabelsTab } from './LabelsTab'
import { ColoursSection, KeyboardSection, LightDarkSection, StyleSection, TextSpacingSection, ThisLayoutSection } from './LookSections'
import { ArchiveSection, AwaySection, ReadingSection, VipSection, WritingSection } from './MailSections'
import { NotificationsTab } from './NotificationsTab'
import { PhoneAppsSection as AdminPhoneAppsSection } from './PhoneAppsAdmin'
import { PictureSection } from './PictureSection'
import { ScreenerTab } from './ScreenerTab'
import { SpamTab } from './SpamTab'
import { SpeakrSection } from './SpeakrSection'
import { TemplatesTab } from './TemplatesTab'
import { WebSearchSection } from './WebSearchAdmin'
import { PageReadersSection } from './PageReadersAdmin'
import { InstallSection, NameSection, NotesExportSection, SetupGuideSection } from './YouSections'

function Labels() {
  return (
    <>
      <LabelsTab />
      <LabelColoursSetting />
    </>
  )
}

export const SECTION_COMPONENTS: Record<string, ComponentType> = {
  // You
  picture: PictureSection,
  name: NameSection,
  'setup-guide': SetupGuideSection,
  region: RegionSection,
  password: PasswordSection,
  devices: DevicesSection,
  install: InstallSection,
  // Look
  style: StyleSection,
  colours: ColoursSection,
  'light-dark': LightDarkSection,
  'text-spacing': TextSpacingSection,
  'this-layout': ThisLayoutSection,
  keyboard: KeyboardSection,
  // Mail
  reading: ReadingSection,
  archive: ArchiveSection,
  writing: WritingSection,
  identities: IdentitiesSection,
  templates: TemplatesTab,
  away: AwaySection,
  'email-links': EmailLinksSection,
  inbox: InboxTab,
  vip: VipSection,
  labels: Labels,
  'label-learning': LabelLearningSection,
  filters: FiltersTab,
  spam: SpamTab,
  screener: ScreenerTab,
  people: PeopleTab,
  accounts: MailAccountsSection,
  imap: ImapSection,
  speakr: SpeakrSection,
  // Calendar & tasks
  'calendar-accounts': CalendarAccountsSection,
  'working-hours': WorkingHoursSection,
  'reminder-defaults': RemindersSection,
  invitations: InvitesAndTasksSection,
  'free-busy': FreeBusySection,
  // Notes & tables
  'notes-export': NotesExportSection,
  // Notifications
  notifications: NotificationsTab,
  // Ambrose
  about: ProfileSection,
  'about-memories': AboutYouMemories,
  orders: OrdersSection,
  'trusted-addresses': TrustedAddressesSection,
  'handled-elsewhere': HandledElsewhereSection,
  skills: SkillsSection,
  sweep: SweepSection,
  memory: MemorySection,
  'local-models': LocalModelsSection,
  models: ModelsSection,
  'model-use': ModelUseSection,
  briefs: BriefsSection,
  lab: LabSection,
  importance: ImportanceReview,
  // Agents & API
  approvals: ApprovalsSection,
  permissions: PermissionsPart,
  tokens: TokensPart,
  failed: OutboxSection,
  audit: AuditPart,
  activity: ActivityPage,
  // Admin
  users: UsersSection,
  access: AccessTab,
  'admin-model-defaults': DefaultsSection,
  'admin-local-policy': LocalPolicySection,
  'admin-models': AdminModelsSection,
  'web-search': WebSearchSection,
  'page-readers': PageReadersSection,
  'admin-calendars': CalendarsSection,
  'admin-phones': AdminPhoneAppsSection,
  diagnostics: DiagnosticsSection,
  'data-policies': PoliciesSection,
  'image-proxy': ImageProxySection,
}
