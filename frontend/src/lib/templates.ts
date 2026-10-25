// Templates in compose: `;shortcut` then Space or Tab.

/** The `;shortcut` that ends the text before the cursor (at the start or after a space), or null.
 *  Typed as "a;b" or ";;x" it is not a trigger, so semicolons in ordinary text stay text. */
export function shortcutBefore(textBefore: string): string | null {
  const m = /(?:^|[\s(]);([a-z0-9][a-z0-9_-]{0,47})$/i.exec(textBefore)
  return m ? m[1].toLowerCase() : null
}
