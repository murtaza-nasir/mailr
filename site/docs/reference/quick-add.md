# Quick-add grammar

With quick add, an event or a task is created from one typed line. The line is read as you type, and
what was understood is shown as chips under the field; the rest of the line becomes the title. Remove a chip
to keep that text in the title instead. Parsing is done on the device; no model is involved.

For a guided tour, see [Quick add](../user-guide/quick-add.md).

| Part | Events | Tasks | Effect | Forms you can type |
|---|---|---|---|---|
| Date | yes | yes | The day. Relative words, weekdays, dates in several orders, and offsets. | `today`, `tonight`, `tomorrow`, `tmrw`, `tmr`, `tom`, `day after tomorrow`, `fri`, `friday`, `on fri`, `this fri`, `next fri`, `oct 12`, `12 oct`, `October 12th`, `12th of October`, `oct 12 2027`, `2026-10-12`, `10/12`, `10/12/27`, `the 12th`, `on the 1st`, `in 3 days`, `in 2 weeks`, `in 1 month`, `in 2h`, `in 30 min`, `in an hour`, `+3d`, `+2w`, `next week`, `this weekend`, `next weekend`, `next month`, `end of month`, `end of the month`, `eom`, `this morning`, `this afternoon`, `this evening` |
| Time | yes | yes | The time of day. | `3pm`, `3 pm`, `3p`, `3:30p`, `3:30 pm`, `15:30`, `noon`, `midnight`, `at 9`, `at 15`, `@ 9:30`, `by 5pm`, `in the morning` |
| Time range | yes | — | A start and an end time (events). | `3-4pm`, `9:30–11`, `2 to 4pm`, `3pm - 4:30pm`, `from 2 to 4`, `11-1pm`, `10pm-1am` |
| Duration | yes | — | How long an event lasts, or all day. | `for 45 min`, `for 2h`, `for 1h30`, `for 1.5 hours`, `for 1 hour 30 min`, `for an hour`, `for half an hour`, `all day`, `all-day`, or an amount right after the time: `thu 3pm 45m`, `3pm 1h30` |
| Span | yes | — | Several days (an all-day event across a range). | `12–14 oct`, `oct 28-30`, `oct 30 - nov 2`, `dec 30 to jan 2`, `fri to sun`, `from 12 oct to 14 oct` |
| Repeat | yes | yes | A repeating event or task. | `every day`, `every other week`, `every 3 days`, `every 2 weeks [on mon and thu]`, `every month [on the 1st / on day 20 / on the last fri]`, `every year`, `every weekday`, `every weekend`, `every mon`, `every mon, wed and fri`, `every first mon`, `every last fri`, `every 15th`, `daily`, `weekly`, `monthly`, `yearly`, `annually`, `weekdays` |
| Reminder | yes | yes | A reminder before the start, or at a set time. | `remind [me] 10m before`, `… 1 hour before`, `… 1 day before`, `remind [me] at 9am`, `remind 9am`, `remind tom 9am`, `remind fri at 5pm` |
| Start | — | yes | A task's start date (it stays out of Today until then). | `start mon`, `starts 12 oct`, `starting on fri`, `from oct 10` |
| Location | yes | — | Where an event happens. | `at Café Luna`, `@ Café Luna`, `@Hopleaf`, `in Room 204`, `at home` |
| Guest | yes (suggestion) | — | People to invite (matching contacts are suggested). | `with Dana`, `with Dana Whitfield`, `with Dana and Lee`, `with Dana, Lee & Sam`, `with dana@lumencoffee.example` |
| Video | yes | — | A video call link. | `+meet`, `+video`, `video` |
| Priority | — | yes | A task's priority: high, medium or low. | `!!!`, `p1`, `!!`, `p2`, `!`, `p3` |
| Tag | — | yes | A task tag. | `@phone`, `@errand` |
| Collection | `#Work` (a calendar) | `#Groceries` (a list) | The calendar or task list to save to. | `#Work`, `#Groceries`, `#Clinic_board` |

Notes:

- In a calendar or list name after `#`, type `_`, `-` or `/` for a space: `#Clinic_board`.
- `[me]` and `[on …]` in the table are optional words.
- Text in quotes is never parsed: with `"Lunch at noon" fri`, "Lunch at noon" stays in the title, quotes included.
- `10/12` is read in your locale's date order (month first or day first).
- Month and weekday names are English, with the usual short forms.
- A place after `at` or `@` starts with a capital letter and runs to the end of the line, or to a comma, a date, a repeat word or `with`: `Supplier call fri 14:00 at Back office` is on Friday at 14:00, location Back office. After `in`, only words with a capital letter and numbers belong to the place (`in Room 204`). A known place, such as `at home`, needs no capital.

## Examples

| Input | Result |
|---|---|
| `Lunch with Dana fri 12:30 at Café Luna` | An event on Friday at 12:30, location Café Luna, Dana suggested as a guest. |
| `Supplier call at Back office fri 2pm` | An event on Friday at 2 pm, location Back office. |
| `Studio standup every weekday 9:30 for 15 min +meet` | A repeating event with a video link. |
| `Offsite oct 28-30 #Work` | A three-day all-day event in the Work calendar. |
| `Renew studio insurance fri !! @phone` | A task due Friday, medium priority, tagged phone. |
| `Timesheet every fri 4pm remind 30m before #Studio` | A repeating task in the Studio list with a reminder. |
| `Book the dentist start mon` | A task that starts on Monday. |
