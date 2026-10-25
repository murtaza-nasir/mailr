// Whether this tab's live stream (api/events.ts) is open. While it is, the server follows every
// change with a `counts` event that carries every sidebar number and each label's unread and total
// (backend core/events.py, features/layout/sidebarCounts.ts applyServerCounts), so the label list,
// GET /counts and GET /inbox/count need no refetch after a change. While it is down, they refetch
// as before.

let open = false

export const liveCounts = {
  get open(): boolean {
    return open
  },
  set(v: boolean) {
    open = v
  },
}
