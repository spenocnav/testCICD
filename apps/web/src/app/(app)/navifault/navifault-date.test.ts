import { describe, expect, it } from 'vitest';
import {
  addUtcDays,
  createNavifaultDateState,
  getBogotaDateString,
  getLast24HoursDateRange,
  getLast7DaysDateRange,
  getSingleDayDateRange,
  getTimelineMaxLookbackDate,
  rolloverNavifaultDateState,
} from './navifault-date';

describe('navifault-date', () => {
  it('formats dates in America/Bogota regardless of local system timezone', () => {
    // 2026-08-22 04:30:00 UTC is 2026-08-21 23:30:00 in America/Bogota (UTC-5)
    const lateNightUtc = new Date('2026-08-22T04:30:00Z');
    expect(getBogotaDateString(lateNightUtc)).toBe('2026-08-21');

    // 2026-08-22 05:00:00 UTC is 2026-08-22 00:00:00 in America/Bogota (midnight)
    const midnightUtc = new Date('2026-08-22T05:00:00Z');
    expect(getBogotaDateString(midnightUtc)).toBe('2026-08-22');
  });

  it('calculates 24-hour range spanning yesterday to today in Bogota', () => {
    const sample = new Date('2026-08-22T14:00:00Z'); // 09:00 Bogota
    const range = getLast24HoursDateRange(sample);

    expect(range.today).toBe('2026-08-22');
    expect(range.dateTo).toBe('2026-08-22');
    expect(range.dateFrom).toBe('2026-08-21');
  });

  it('calculates 7-day range spanning last week to today in Bogota', () => {
    const sample = new Date('2026-08-22T14:00:00Z');
    const range = getLast7DaysDateRange(sample);

    expect(range.today).toBe('2026-08-22');
    expect(range.dateTo).toBe('2026-08-22');
    expect(range.dateFrom).toBe('2026-08-15');
  });

  it('handles month boundary correctly with addUtcDays', () => {
    expect(addUtcDays('2026-03-01', -1)).toBe('2026-02-28');
    expect(addUtcDays('2026-01-01', -1)).toBe('2025-12-31');
    expect(addUtcDays('2026-08-22', -7)).toBe('2026-08-15');
  });

  it('calculates single day range and maximum 7-day lookback date', () => {
    const sample = new Date('2026-08-22T14:00:00Z');
    const single = getSingleDayDateRange(sample);
    expect(single.today).toBe('2026-08-22');
    expect(single.dateFrom).toBe('2026-08-22');
    expect(single.dateTo).toBe('2026-08-22');

    const maxLookback = getTimelineMaxLookbackDate(sample);
    expect(maxLookback).toBe('2026-08-15');
  });

  it('rolls over date state when midnight crosses', () => {
    const initial = createNavifaultDateState(new Date('2026-08-21T20:00:00Z'));
    expect(initial.today).toBe('2026-08-21');
    expect(initial.dateFrom).toBe('2026-08-20');
    expect(initial.dateTo).toBe('2026-08-21');

    // Same day -> no rollover
    const same = rolloverNavifaultDateState(initial, '2026-08-21');
    expect(same).toBe(initial);

    // Day changed to 2026-08-22
    const next = rolloverNavifaultDateState(initial, '2026-08-22');
    expect(next.today).toBe('2026-08-22');
    expect(next.dateFrom).toBe('2026-08-21');
    expect(next.dateTo).toBe('2026-08-22');
  });
});
