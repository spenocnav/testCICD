export const BOGOTA_TIME_ZONE = 'America/Bogota';

function padTwo(value: number): string {
  return String(value).padStart(2, '0');
}

export function toIsoDateString(d: Date): string {
  return `${d.getUTCFullYear()}-${padTwo(d.getUTCMonth() + 1)}-${padTwo(d.getUTCDate())}`;
}

export function getBogotaDateString(now: Date = new Date()): string {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: BOGOTA_TIME_ZONE,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(now);

  const values = Object.fromEntries(parts.map(({ type, value }) => [type, value]));
  return `${values.year}-${values.month}-${values.day}`;
}

export function addUtcDays(dateStr: string, days: number): string {
  const d = new Date(`${dateStr}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return toIsoDateString(d);
}

export function getLast24HoursDateRange(now: Date = new Date()): {
  today: string;
  dateFrom: string;
  dateTo: string;
} {
  const today = getBogotaDateString(now);
  const dateFrom = addUtcDays(today, -1);
  return {
    today,
    dateFrom,
    dateTo: today,
  };
}

export function getLast7DaysDateRange(now: Date = new Date()): {
  today: string;
  dateFrom: string;
  dateTo: string;
} {
  const today = getBogotaDateString(now);
  const dateFrom = addUtcDays(today, -7);
  return {
    today,
    dateFrom,
    dateTo: today,
  };
}

export interface NavifaultDateState {
  today: string;
  dateFrom: string;
  dateTo: string;
}

export function createNavifaultDateState(now: Date = new Date()): NavifaultDateState {
  return getLast24HoursDateRange(now);
}

export function getSingleDayDateRange(now: Date = new Date()): {
  today: string;
  dateFrom: string;
  dateTo: string;
} {
  const today = getBogotaDateString(now);
  return {
    today,
    dateFrom: today,
    dateTo: today,
  };
}

export function getTimelineMaxLookbackDate(now: Date = new Date()): string {
  const today = getBogotaDateString(now);
  return addUtcDays(today, -7);
}

export function rolloverNavifaultDateState(
  current: NavifaultDateState,
  nextToday: string,
): NavifaultDateState {
  if (current.today === nextToday) {
    return current;
  }
  return {
    today: nextToday,
    dateFrom: addUtcDays(nextToday, -1),
    dateTo: nextToday,
  };
}
