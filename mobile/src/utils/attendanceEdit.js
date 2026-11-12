// Mark Attendance (screens/admin/MarkAttendanceScreen.js): what the server
// already has for each employee on the chosen day, what the admin changed,
// and what to send back. Kept out of the screen so it can be unit-tested.

// Types with no working hours: sent (and stored) without check-in/out times.
export const NO_TIME_TYPES = ["Absent", "Leave", "Holiday", "Week Off"];

// "09:41", "09:41:00" or "09:41:00.722772" -> "09:41"; anything else -> "".
export const hhmm = (value) => {
  const m = /^(\d{1,2}):(\d{2})/.exec(String(value || "").trim());
  return m ? `${m[1].padStart(2, "0")}:${m[2]}` : "";
};

// What the server has for this employee on the day (GET
// /api/bulk_mark_attendance): an existing record's type and times, or for
// someone not marked yet an empty type with their shift times as a start.
export const initialEntry = (emp) => {
  const marked = !!(emp?.marked || emp?.attendance_type || emp?.login_time);
  if (marked) {
    return {
      type: emp.attendance_type || "",
      login: hhmm(emp.login_time),
      logout: hhmm(emp.logout_time),
    };
  }
  return { type: "", login: hhmm(emp?.shift_start), logout: hhmm(emp?.shift_end) };
};

export const isMarked = (emp) => !!(emp?.marked || emp?.attendance_type || emp?.login_time);

// Has the admin changed this row from what the server has? A row nobody has
// marked counts as changed once a type is picked.
export const isChanged = (entry, emp) => {
  if (!entry?.type) return false;
  if (!isMarked(emp)) return true;
  const saved = initialEntry(emp);
  const times = !NO_TIME_TYPES.includes(entry.type);
  return entry.type !== saved.type
    || (times && (entry.login || "") !== (saved.login || ""))
    || (times && (entry.logout || "") !== (saved.logout || ""));
};

// The record POST /api/bulk_mark_attendance takes for one employee.
export const buildRecord = (empId, entry) => {
  const times = !NO_TIME_TYPES.includes(entry.type);
  return {
    emp_id: empId,
    attendance_type: entry.type,
    login_time: times ? entry.login || null : null,
    logout_time: times ? entry.logout || null : null,
  };
};

// "In 09:41 · Out 18:12 · Late Login" for an employee's saved record.
export const savedSummary = (emp) => {
  if (!isMarked(emp)) return "";
  const parts = [];
  if (emp.login_time) parts.push(`In ${hhmm(emp.login_time)}`);
  if (emp.logout_time) parts.push(`Out ${hhmm(emp.logout_time)}`);
  const how = emp.status && emp.status !== emp.attendance_type ? emp.status : emp.attendance_type;
  if (how) parts.push(how);
  return parts.join(" · ");
};
