// Date helpers. Due dates are stored as date-only strings ("YYYY-MM-DD"). The trap: `new
// Date("2026-07-14")` parses as UTC midnight, which in a negative-offset (US) timezone is the
// *previous* evening — so naive comparisons mark cards overdue early and `toLocaleDateString`
// renders the day before. Everything below parses date-only values as LOCAL calendar days and
// does day math on whole days, so results are correct regardless of timezone.

const DATE_ONLY = /^(\d{4})-(\d{2})-(\d{2})$/;

// Parse a due-date value as a local Date. A bare "YYYY-MM-DD" becomes local midnight; anything
// carrying a time is parsed as-is. Returns null for empty/invalid input.
export const parseLocalDate = (dateString: string): Date | null => {
  if (!dateString) return null;
  const m = DATE_ONLY.exec(dateString);
  if (m) return new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
  const d = new Date(dateString);
  return isNaN(d.getTime()) ? null : d;
};

// Local calendar-day "today" at midnight.
export const startOfToday = (): Date => {
  const d = new Date();
  d.setHours(0, 0, 0, 0);
  return d;
};

// Format a Date as a local "YYYY-MM-DD" key (for day-bucketing). Uses local components, not
// toISOString() — which would shift the day by the UTC offset.
export const toLocalYMD = (date: Date): string => {
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, '0');
  const d = String(date.getDate()).padStart(2, '0');
  return `${y}-${m}-${d}`;
};

// Whole-day difference from today to a due date, in local calendar days.
// Negative = overdue, 0 = today, 1 = tomorrow. null for no/invalid date.
export const daysUntilDue = (dateString: string): number | null => {
  const due = parseLocalDate(dateString);
  if (!due) return null;
  due.setHours(0, 0, 0, 0);
  // Round (not floor/ceil) so DST-shifted 23h/25h days still land on whole days.
  return Math.round((due.getTime() - startOfToday().getTime()) / 86400000);
};

export const formatDueDate = (dateString: string): string => {
  const diffDays = daysUntilDue(dateString);
  if (diffDays === null) return '';
  if (diffDays < 0) return 'Overdue';
  if (diffDays === 0) return 'Today';
  if (diffDays === 1) return 'Tomorrow';
  return `${diffDays} days`;
};

export const getDateColor = (dateString: string): string => {
  const diffDays = daysUntilDue(dateString);
  if (diffDays === null) return 'text-fg-muted';
  if (diffDays < 0) return 'text-red-600 dark:text-red-400';
  if (diffDays === 0) return 'text-orange-600 dark:text-orange-400';
  if (diffDays <= 2) return 'text-yellow-600 dark:text-yellow-400';
  return 'text-fg-subtle';
};
