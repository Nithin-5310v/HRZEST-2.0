import React, { useState } from "react";
import { View, Text, StyleSheet, TouchableOpacity, ActivityIndicator } from "react-native";
import { Ionicons } from "@expo/vector-icons";

import { fetchAttendanceEmployees } from "../../api/client";
import useLiveRefresh, { formatUpdated } from "../../utils/useLiveRefresh";
import { localISODate } from "../../utils/dates";
import { hhmm, NO_TIME_TYPES } from "../../utils/attendanceEdit";
import { useTheme } from "../../store/ThemeContext";

// Daily Attendance -> Today: every employee's check-in and check-out time
// for today, refreshed every 30 s, with "Edit" opening Mark Attendance on
// that person to correct or add their record.

// One employee's day as a label and colours.
export const todayState = (emp) => {
  const type = emp?.attendance_type || "";
  if (!emp?.login_time && NO_TIME_TYPES.includes(type)) {
    return { label: type, tone: type === "Absent" ? "bad" : "neutral" };
  }
  if (emp?.login_time && emp?.logout_time) return { label: "Checked out", tone: "done" };
  if (emp?.login_time) {
    const late = /late/i.test(`${emp.status || ""} ${type}`);
    return { label: late ? "In · Late" : "Checked in", tone: late ? "warn" : "good" };
  }
  return { label: "Not checked in", tone: "none" };
};

// Checked in first (earliest first), then marked-off days, then nobody yet.
export const sortForToday = (list) => [...list].sort((a, b) => {
  const rank = (e) => (e.login_time ? 0 : e.attendance_type ? 1 : 2);
  return rank(a) - rank(b)
    || hhmm(a.login_time).localeCompare(hhmm(b.login_time))
    || (a.name || "").localeCompare(b.name || "");
});

const TONES = {
  good: { bg: "#DCFCE7", text: "#15803D" },
  warn: { bg: "#FEF3C7", text: "#B45309" },
  done: { bg: "#E0E7FF", text: "#3730A3" },
  bad: { bg: "#FEE2E2", text: "#B91C1C" },
  neutral: { bg: "#F2F4F7", text: "#475467" },
  none: { bg: "#F2F4F7", text: "#98A2B3" },
};

export default function TodayAttendance({ navigation, search = "" }) {
  const { colors } = useTheme();
  const styles = React.useMemo(() => makeStyles(colors), [colors]);
  const [rows, setRows] = useState(null);
  const today = localISODate();

  const load = async () => {
    const res = await fetchAttendanceEmployees(today);
    if (!res?.data?.ok) return false;
    setRows(res.data.employees || []);
    return true;
  };
  const { lastUpdated } = useLiveRefresh(load, [today]);

  const q = search.trim().toLowerCase();
  const list = sortForToday((rows || []).filter((e) => !q
    || (e.name || "").toLowerCase().includes(q)
    || (e.employee_id || "").toLowerCase().includes(q)));
  const inCount = (rows || []).filter((e) => e.login_time && !e.logout_time).length;
  const outCount = (rows || []).filter((e) => e.login_time && e.logout_time).length;
  const notYet = (rows || []).filter((e) => !e.login_time && !e.attendance_type).length;

  const edit = (emp) => navigation.navigate("AdminTabs", {
    screen: "MarkAttendance",
    params: { focusEmployeeId: emp.employee_id, date: today },
  });

  return (
    <View style={styles.card} testID="todayAttendance">
      <View style={styles.head}>
        <View>
          <Text style={styles.title}>Today</Text>
          <Text style={styles.sub}>{lastUpdated ? formatUpdated(lastUpdated) : "Loading…"}</Text>
        </View>
        <View style={styles.counts}>
          <Text style={[styles.count, { color: "#15803D" }]}>{inCount} in</Text>
          <Text style={[styles.count, { color: "#3730A3" }]}>{outCount} out</Text>
          <Text style={[styles.count, { color: "#98A2B3" }]}>{notYet} not yet</Text>
        </View>
      </View>

      {rows === null ? (
        <ActivityIndicator color={colors.primary} style={{ marginVertical: 16 }} />
      ) : list.length === 0 ? (
        <Text style={styles.empty}>No employees found.</Text>
      ) : (
        list.map((emp) => {
          const st = todayState(emp);
          const tone = TONES[st.tone];
          return (
            <View key={emp.employee_id} style={styles.row} testID={`today-${emp.employee_id}`}>
              <View style={{ flex: 1 }}>
                <Text style={styles.name} numberOfLines={1}>{emp.name}</Text>
                <Text style={styles.times}>
                  {emp.employee_id} · In {hhmm(emp.login_time) || "--:--"} · Out {hhmm(emp.logout_time) || "--:--"}
                </Text>
              </View>
              <View style={[styles.badge, { backgroundColor: tone.bg }]}>
                <Text style={[styles.badgeText, { color: tone.text }]}>{st.label}</Text>
              </View>
              <TouchableOpacity
                onPress={() => edit(emp)}
                style={styles.editBtn}
                accessibilityRole="button"
                accessibilityLabel={`Edit today's attendance for ${emp.name}`}
                testID={`todayEdit-${emp.employee_id}`}
              >
                <Ionicons name="create-outline" size={18} color={colors.primary} />
              </TouchableOpacity>
            </View>
          );
        })
      )}
    </View>
  );
}

const makeStyles = (colors) => StyleSheet.create({
  card: {
    backgroundColor: colors.card, borderRadius: 16, borderWidth: 1, borderColor: colors.border,
    padding: 14, marginBottom: 16,
  },
  head: { flexDirection: "row", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 8 },
  title: { fontSize: 17, fontWeight: "800", color: colors.text },
  sub: { fontSize: 12, color: colors.textLight, marginTop: 2 },
  counts: { alignItems: "flex-end", gap: 2 },
  count: { fontSize: 12.5, fontWeight: "700" },
  row: {
    flexDirection: "row", alignItems: "center", gap: 8, paddingVertical: 10,
    borderTopWidth: 1, borderTopColor: colors.border,
  },
  name: { fontSize: 14.5, fontWeight: "700", color: colors.text },
  times: { fontSize: 12.5, color: colors.textLight, marginTop: 2 },
  badge: { borderRadius: 999, paddingHorizontal: 9, paddingVertical: 4 },
  badgeText: { fontSize: 11.5, fontWeight: "700" },
  editBtn: { padding: 6 },
  empty: { color: colors.textLight, paddingVertical: 12 },
});
