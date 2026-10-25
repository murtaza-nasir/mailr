# Organisations and small businesses

Mailr can be run for a household as well as for a small business, a club or an office. Each person still has their own mail store, accounts, labels and Ambrose, and no person can see another person's mail. The settings below apply to a server run for people at work.

## Sign-in and groups

- Use your identity provider (Authentik, Keycloak, Pocket ID and others) for sign-in, with groups such as `staff` or `office`. See [Sign-in and OIDC](sign-in.md).
- Give each group an [access policy](access.md): which models, Ambrose, web search and API tokens are available to its members.
- With [Prepare a new user](users.md#prepare-a-new-user), choose the look, inbox and notifications before someone's first sign-in, for one address or a whole group.

## Mailboxes assigned by the organisation

When the organisation assigns mailboxes to people, an admin can connect them:

1. Turn on **Administrators may connect mail accounts for members** in **Settings → Admin → Users**.
2. For someone who has signed in: **mail accounts** in their row. For someone who has not: **Connect a mail account for them** in [Prepare a new user](users.md#prepare-a-new-user), connected at their first sign-in.

See [Connect a member's mail account](users.md#connect-a-members-mail-account) for the details. The member sees who connected it and can disconnect it. With the switch off, the default, each person connects their own.

Choose **Keep in sync** for mailboxes on the organisation's own mail server, so that server stays the source of truth.

## Rules for work mail

- [Local-only domains](users.md#instance-policies): mail at the work domain is never sent to a hosted model.
- [Hosted models for everyone](models.md): local models only, for every person.
- [Calendars](calendars.md): built-in calendars, group calendars (for example `Front desk`), and who may share with whom.

## Phones

- Publish the [phone apps](phone-apps.md) on the server, so people install them from it.
- Push: with a self-hosted ntfy server, each person pastes its address once. See [Member setup](push.md#member-setup).
- Invite people with a link or QR code: [Invite someone](users.md#invite-someone).
