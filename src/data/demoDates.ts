// Demo due dates are stored as OFFSETS, not dates, and resolved when the demo workspace is built.
//
// Why: a literal date rots. The authored due dates are meant to show a realistic board -- a few
// overdue, several due this week, most within the month, some further out. As literal dates they
// slide into the past within weeks, and the demo starts reading as a project in crisis. That is
// not only cosmetic: Timeline plots a 7-day window, Matrix derives urgency from due dates, the
// Feed has "This Week" and "Urgent" filters, and the intelligence narrative leads with overdue
// high-priority cards. All four degrade as the dates drift.
//
// A demo date never means a fixed calendar day. It means "due next week". Storing the offset says
// that, and reproduces the intended spread on every load.

/** Resolve a day offset to a `YYYY-MM-DD` due date, anchored on today.
 *
 * LOCAL time on purpose. The bug to avoid: date-only values parsed as UTC produce
 * off-by-one days and false "overdue" flags for anyone west of Greenwich. `new Date(y, m, d)`
 * builds a local midnight, and the day arithmetic is done by the constructor, so month and year
 * rollover (and DST) are handled without manual carrying.
 */
export function resolveDueDate(dueInDays: number, today: Date = new Date()): string {
  const d = new Date(today.getFullYear(), today.getMonth(), today.getDate() + dueInDays);
  const yyyy = d.getFullYear();
  const mm = String(d.getMonth() + 1).padStart(2, '0');
  const dd = String(d.getDate()).padStart(2, '0');
  return `${yyyy}-${mm}-${dd}`;
}

/** A demo card definition may carry `dueInDays`; anything else means "no due date". */
export function resolveCardDueDate(
  cardDef: { dueInDays?: number | null },
  today: Date = new Date()
): string | null {
  return typeof cardDef.dueInDays === 'number' ? resolveDueDate(cardDef.dueInDays, today) : null;
}
