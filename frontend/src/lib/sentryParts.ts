// The parts of the Sentry SDK the error reports use (lib/errorReports.ts), re-exported by name so
// the lazy chunk holds only these and not the whole SDK.
export {
  dedupeIntegration,
  eventFiltersIntegration,
  functionToStringIntegration,
  globalHandlersIntegration,
  init,
  linkedErrorsIntegration,
} from '@sentry/browser'
