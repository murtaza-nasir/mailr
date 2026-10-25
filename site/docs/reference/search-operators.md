# Search operators

The same syntax, close to Gmail's, is used in the search box, filters and standing orders. A filter
matches exactly the results of the same search. Words without an operator are searched for in the
subject, the people, attachment names, the body (quoted history left out) and the text of attached emails. Spam and Trash are excluded unless
`in:spam`, `in:trash` or `in:anywhere` is added.

For a guided tour with examples, see [Search](../user-guide/search.md).

## Operators

| Operator | Example | Matches |
|---|---|---|
| `bcc:` | `bcc:me` | A Bcc recipient (on mail you sent). |
| `cc:` | `cc:sam` | A Cc recipient. |
| `deliveredto:` | `deliveredto:alex@northwind.example` | The address the message was delivered to. |
| `from:` | `from:dana` `from:me` | The sender's address or name. `me` matches mail you sent. |
| `list:` | `list:forgehub.example` | A mailing list id. |
| `to:` | `to:alex@northwind.example` `to:me` | A recipient's address or name. `me` matches any of your addresses. |
| `subject:` | `subject:"q3 plan"` | Words in the subject. |
| `label:` | `label:clients` `label:clients/lumen` | A label, including its nested labels. A hyphen or a space both work in names. |
| `in:` | `in:sent` | A system location (see the values below). |
| `is:` | `is:unread` | A state (see the values below). |
| `has:` | `has:attachment` | Something the message has (see the values below). |
| `recording:` | `recording:kickoff` | Mail linked to a meeting recording whose title or participants match. |
| `filename:` | `filename:pdf` `filename:invoice` | An attachment's name or type. |
| `after:` | `after:2026/09/01` | Sent on or after a date. `newer:` is the same. |
| `before:` | `before:2026/10/01` | Sent before a date. `older:` is the same. |
| `newer_than:` | `newer_than:7d` | Newer than a number of days (`d`), months (`m`) or years (`y`). |
| `older_than:` | `older_than:1y` | Older than a number of days, months or years. |
| `larger:` | `larger:5M` | Bigger than a size in `K` or `M`. `size:` is the same. |
| `smaller:` | `smaller:100K` | Smaller than a size. |
| `category:` | `category:promotions` | An inbox category (see the values below). |
| `rfc822msgid:` | `rfc822msgid:<abc@example.com>` | One exact Message-ID. |
| `account:` | `account:work@example.com` | Mail of one of your mail accounts, by its address: mail that arrived through it, and mail you sent from its addresses. |

## Locations: `in:`

| Value | Matches |
|---|---|
| `in:all` | All mail except Spam and Trash. |
| `in:anywhere` | All mail, including Spam and Trash. |
| `in:archive` | Archived mail (not in the inbox). |
| `in:drafts` | Your drafts. |
| `in:important` | Mail marked important. |
| `in:inbox` | The inbox. |
| `in:junk` | Spam (the same as `in:spam`). |
| `in:scheduled` | Mail scheduled to send later. |
| `in:sent` | Mail you sent. |
| `in:snoozed` | Snoozed conversations. |
| `in:spam` | Spam. |
| `in:starred` | Starred mail. |
| `in:trash` | Trash. |

## States: `is:`

| Value | Matches |
|---|---|
| `is:unread` | Unread. |
| `is:read` | Read. |
| `is:starred` | Starred. |
| `is:important` | Marked important. |
| `is:snoozed` | Snoozed. |
| `is:muted` | In a muted conversation. |
| `is:vip` | From one of your VIP senders. |
| `is:nudged` | Came back with no reply (a follow-up nudge fired). Also `is:no_reply`, `is:noreply`. |
| `is:follow_up` | A follow-up reminder is set. Also `is:followup`, `is:follow-up`. |
| `is:draft` | A draft. Also `is:drafts`. |
| `is:answered` | You replied to it. Also `is:replied`. |
| `is:needs_reply` | Waiting for your reply. Also `is:needsreply`, `is:needs-reply`. |
| `is:waiting` | You are waiting on someone else's reply. Also `is:waiting_on`, `is:waiting-on`. |

## Contents: `has:`

| Value | Matches |
|---|---|
| `has:attachment` | Has an attachment. Also `has:attachments`. |
| `has:userlabels` | Has at least one of your own labels. `has:nouserlabels` matches mail with none. |
| `has:drive` | Mentions a shared document. Also `has:document`, `has:spreadsheet`, `has:presentation`, `has:youtube`. |
| `has:deadline` | Carries a detected deadline. |
| `has:recording` | Linked to a meeting recording. Also `has:recordings`. |
| `has:star` | Starred (the same as `is:starred`). |

## Categories: `category:`

| Value | Matches |
|---|---|
| `category:forums` | Forums: groups and mailing lists. |
| `category:primary` | Primary: personal mail. |
| `category:promotions` | Promotions: offers and marketing. |
| `category:purchases` | Purchases: receipts, orders and shipping. |
| `category:reservations` | Reservations: travel, bookings and tickets. |
| `category:social` | Social: social networks and community sites. |
| `category:updates` | Updates: bills, statements, notifications. |

## Combining terms

Terms side by side must all match: `from:dana has:attachment`.

| Syntax | Example | Meaning |
|---|---|---|
| `OR` | `from:dana OR from:theo` | Either term. `\|` is the same. |
| `{ }` | `{from:dana from:theo}` | An OR group: any of the terms inside. |
| `( )` | `(from:dana budget) OR invoice` | Groups terms. An operator applies to a whole group: `from:(dana OR theo)`. |
| `-` | `-label:newsletters` | Excludes a term or a group. |
| `"exact phrase"` | `"landing page"` | Matches the words in this order. |

Address operators (`from:`, `to:`, `cc:`, `bcc:`, `deliveredto:`, `list:`) take a comma list:
`from:dana@lumencoffee.example,marco.silva@bluefin.example` matches either sender. `from:me to:me` matches
notes sent to yourself.

!!! tip "Search by meaning"

    When you remember what a message said but not its words, ask Ambrose, or use
    `GET /api/v1/search/semantic?q=…`. An embedding model is required for meaning-based search
    (see [Models](../admin/models.md)).
