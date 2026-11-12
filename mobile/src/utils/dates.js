// Calendar dates as the person holding the phone sees them.
//
// `date.toISOString().split("T")[0]` is the date in UTC: in India (UTC+5:30)
// it's still yesterday until 05:30, so "today", a leave date or a joining
// date picked after midnight came out one day early. Use these instead.

const pad = (n) => String(n).padStart(2, "0");

// "YYYY-MM-DD" of `date` in the phone's own time zone.
export const localISODate = (date = new Date()) => {
  const d = date instanceof Date ? date : new Date(date);
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
};

// "YYYY-MM-DD" -> a Date at local midnight (new Date("YYYY-MM-DD") is UTC
// midnight, which is the day before in time zones west of UTC).
export const parseISODate = (iso) => {
  const [y, m, d] = String(iso || "").split("-").map(Number);
  return y && m && d ? new Date(y, m - 1, d) : new Date(NaN);
};

// Today + n days, as "YYYY-MM-DD" in the phone's time zone.
export const addDaysISO = (n, from = new Date()) => {
  const d = new Date(from.getFullYear(), from.getMonth(), from.getDate());
  d.setDate(d.getDate() + n);
  return localISODate(d);
};
