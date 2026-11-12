import { localISODate, parseISODate, addDaysISO } from '../dates';

describe('dates', () => {
  it("uses the phone's own calendar day, not UTC's", () => {
    // 00:30 local on 12 Nov: in India toISOString() still said 11 Nov.
    const justAfterMidnight = new Date(2026, 10, 12, 0, 30);
    expect(localISODate(justAfterMidnight)).toBe('2026-11-12');
  });

  it('pads months and days', () => {
    expect(localISODate(new Date(2026, 0, 5))).toBe('2026-01-05');
  });

  it('parses YYYY-MM-DD as local midnight', () => {
    const d = parseISODate('2026-11-15');
    expect([d.getFullYear(), d.getMonth(), d.getDate(), d.getHours()]).toEqual([2026, 10, 15, 0]);
    expect(Number.isNaN(parseISODate('nope').getTime())).toBe(true);
  });

  it('adds days across month ends', () => {
    expect(addDaysISO(1, new Date(2026, 10, 30, 23, 50))).toBe('2026-12-01');
    expect(addDaysISO(30, new Date(2026, 0, 31, 0, 10))).toBe('2026-03-02');
  });
});
