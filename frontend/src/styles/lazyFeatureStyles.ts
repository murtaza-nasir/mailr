// Stylesheets of features whose code loads on first use (dev/speed, 14 Oct 2026), loaded with the
// first page anyway, before styles/*.css (main.tsx imports App, and so this file, first).
//
// Rules of the same weight win by order. These files used to arrive with the first page, before
// the shared styles, so `.icon-btn`, `.chip`, `.btn` and `.dialog` won over a feature rule on the
// same element. Loaded later with their code, the feature rules won instead: the calendar header's
// More button (`.cal-more`, also the grid's absolutely placed "+N more" chip) covered the
// calendar-list toggle at phone width, and the header chips and buttons changed size. Keeping them
// here keeps the order (and the look) the app was built and tested with. About 13 kB gzip.
import '../features/calendar/calendar.css'
import '../features/calendar/household.css'
import '../features/conversation/calendar-bits.css'
import '../features/imports/imports.css'
import '../features/live/live.css'
import '../features/mail/screener.css'
import '../features/people/people.css'
import '../features/speakr/speaker-people.css'
