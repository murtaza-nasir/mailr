/** On a phone a week of seven columns is unreadable ("Studi", "Lu"): Week shows three days there,
 *  as the 3-day view does. A link or the remembered place that says week counts too. */
export function phoneView<V extends string>(view: V, narrow: boolean): V | 'ndays' {
  return narrow && view === 'week' ? 'ndays' : view
}
