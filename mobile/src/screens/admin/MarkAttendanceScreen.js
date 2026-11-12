import React, { useState, useEffect } from "react";
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  TouchableOpacity,
  TextInput,
  FlatList,
  Alert,
  ActivityIndicator,
} from "react-native";
import SafeAreaView from "../../components/SafeAreaView";
import { DrawerActions } from "@react-navigation/native";
import { Ionicons } from "@expo/vector-icons";
import { LinearGradient } from "expo-linear-gradient";
// Expo SDK's own picker: rendering it opens the system date/time dialog on
// Android; onChange reports "set" (OK) or "dismissed" (Cancel). Replaced
// react-native-date-picker, whose build.gradle the SDK 58 Android tools
// (AGP 9) refuse, and which never worked in Expo Go.
import DateTimePicker from "@react-native-community/datetimepicker";
import { Picker } from "@react-native-picker/picker";

import AdminHeader from "../../components/admin/AdminHeader";
import { useTheme } from "../../store/ThemeContext";
import { fetchAttendanceEmployees, fetchEmployees, markAttendance } from "../../api/client";
import {
  NO_TIME_TYPES, buildRecord, initialEntry, isChanged, isMarked, savedSummary,
} from "../../utils/attendanceEdit";

// ── Constants ──────────────────────────────────────────────────────

const ATT_STATUS_OPTIONS = [
  { label: "— Skip —", value: "" },
  { label: "✅ Full Day", value: "Full Day" },
  { label: "🕐 Late – Full Day", value: "Late - Full Day" },
  { label: "🌓 Half Day", value: "Half Day" },
  { label: "❌ Absent", value: "Absent" },
  { label: "🌴 Leave", value: "Leave" },
  { label: "🎉 Holiday", value: "Holiday" },
  { label: "💤 Week Off", value: "Week Off" },
];

const QUICK_MARK_OPTIONS = [
  { label: "All Present", value: "Full Day" },
  { label: "All Absent", value: "Absent" },
  { label: "All Half Day", value: "Half Day" },
  { label: "All Holiday", value: "Holiday" },
  { label: "All On Leave", value: "Leave" },
];

const STATUS_COLORS = {
  "Full Day": { bg: "#DCFCE7", text: "#15803D", border: "#86EFAC" },
  "Late - Full Day": { bg: "#FEF9C3", text: "#92400E", border: "#FDE047" },
  "Half Day": { bg: "#FEE2E2", text: "#DC2626", border: "#FCA5A5" },
  "Absent": { bg: "#FEE2E2", text: "#B91C1C", border: "#FCA5A5" },
  "Leave": { bg: "#F5F3FF", text: "#6D28D9", border: "#C4B5FD" },
  "Holiday": { bg: "#EEF1F5", text: "#475467", border: "#D0D5DD" },
  "Week Off": { bg: "#EEF1F5", text: "#475467", border: "#D0D5DD" },
};

const WORK_MODE_COLORS = {
  office: { bg: "#DBEAFE", text: "#1D4ED8" },
  remote: { bg: "#DCFCE7", text: "#15803D" },
  hybrid: { bg: "#FEF9C3", text: "#854D0E" },
};

// ── Helpers ────────────────────────────────────────────────────────

const formatDate = (date) =>
  date.toLocaleDateString("en-US", {
    weekday: "long",
    day: "numeric",
    month: "long",
    year: "numeric",
  });

const formatDateISO = (date) => {
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, "0");
  const d = String(date.getDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
};

const getInitials = (name) =>
  (name || "")
    .split(" ")
    .map((n) => n[0])
    .join("")
    .toUpperCase()
    .slice(0, 2);

// ── Main Screen ────────────────────────────────────────────────────

export default function MarkAttendanceScreen({ navigation, route }) {
  const { colors } = useTheme();
  const styles = React.useMemo(() => makeStyles(colors), [colors]);
  const [selectedDate, setSelectedDate] = useState(new Date());
  const [datePickerOpen, setDatePickerOpen] = useState(false);
  const [employees, setEmployees] = useState([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [saving, setSaving] = useState(false);
  const [savingRow, setSavingRow] = useState(null);

  // Time picker state
  const [timePickerOpen, setTimePickerOpen] = useState(false);
  const [timePickerField, setTimePickerField] = useState(null); // { empId, field }
  const [tempTime, setTempTime] = useState(new Date());

  // Attendance state: { [empId]: { type, login, logout } }
  const [attendance, setAttendance] = useState({});

  // ── Effects ──────────────────────────────────────────────────────

  useEffect(() => {
    loadEmployees();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedDate]);

  // Opened from Daily Attendance's "Edit" on one employee: show that
  // person on that day.
  const focusId = route?.params?.focusEmployeeId;
  const focusDate = route?.params?.date;
  useEffect(() => {
    if (focusId) setSearch(focusId);
    if (focusDate) {
      const [y, m, d] = String(focusDate).split("-").map(Number);
      if (y && m && d) setSelectedDate(new Date(y, m - 1, d));
    }
  }, [focusId, focusDate]);

  // ── Handlers ─────────────────────────────────────────────────────

  const loadEmployees = async () => {
    setLoading(true);
    try {
      const res = await fetchAttendanceEmployees(formatDateISO(selectedDate));
      if (res.data?.ok) {
        const emps = res.data.employees || [];
        setEmployees(emps);
        initAttendance(emps);
      } else {
        throw new Error("API error");
      }
    } catch {
      // Fallback to fetchEmployees
      try {
        const res2 = await fetchEmployees();
        if (res2.data?.ok) {
          const emps = res2.data.employees || res2.data.data || [];
          setEmployees(emps);
          initAttendance(emps);
        } else {
          throw new Error("API error");
        }
      } catch {
        setEmployees([]);
        initAttendance([]);
      }
    }
    setLoading(false);
  };

  // Start from what the server already has for the day, so a marked
  // employee shows their record and can be updated.
  const initAttendance = (emps) => {
    const initial = {};
    emps.forEach((e) => {
      initial[e.employee_id] = initialEntry(e);
    });
    setAttendance(initial);
  };

  const handleQuickMark = (value) => {
    const newAttendance = { ...attendance };
    filteredEmployees.forEach((e) => {
      newAttendance[e.employee_id] = {
        ...newAttendance[e.employee_id],
        type: value,
      };
    });
    setAttendance(newAttendance);
  };

  const handleStatusChange = (empId, value) => {
    setAttendance((prev) => ({
      ...prev,
      [empId]: {
        ...prev[empId],
        type: value,
      },
    }));
  };

  const openTimePicker = (empId, field) => {
    const current = attendance[empId]?.[field] || "";
    if (current) {
      const [h, m] = current.split(":").map(Number);
      setTempTime(new Date(2000, 0, 1, h || 0, m || 0));
    } else {
      setTempTime(new Date());
    }
    setTimePickerField({ empId, field });
    setTimePickerOpen(true);
  };

  const handleTimeConfirm = (selected) => {
    const timeStr = selected.toLocaleTimeString("en-US", {
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    });
    if (timePickerField) {
      const { empId, field } = timePickerField;
      setAttendance((prev) => ({
        ...prev,
        [empId]: {
          ...prev[empId],
          [field]: timeStr,
        },
      }));
    }
    setTimePickerOpen(false);
    setTimePickerField(null);
  };

  const empById = React.useMemo(() => {
    const map = {};
    employees.forEach((e) => { map[e.employee_id] = e; });
    return map;
  }, [employees]);

  // Only rows the admin changed: new marks and updates to saved records.
  const changedIds = Object.keys(attendance).filter((id) => isChanged(attendance[id], empById[id]));
  const updateCount = changedIds.filter((id) => isMarked(empById[id])).length;

  const saveRecords = async (ids, rowId = null) => {
    if (ids.length === 0) {
      Alert.alert("No Changes", "Mark or change attendance for at least one employee first.");
      return;
    }
    const records = ids.map((id) => buildRecord(id, attendance[id]));
    if (rowId) setSavingRow(rowId); else setSaving(true);
    try {
      const res = await markAttendance(formatDateISO(selectedDate), records);
      if (res.data?.ok) {
        const updated = ids.filter((id) => isMarked(empById[id])).length;
        const added = ids.length - updated;
        const what = [added && `${added} marked`, updated && `${updated} updated`].filter(Boolean).join(", ");
        Alert.alert("Saved", `Attendance on ${formatDate(selectedDate)}: ${what}.`);
        await loadEmployees();   // the rows now show what was saved
      } else {
        Alert.alert("Save Failed", res.data?.msg || "Could not save attendance. Please try again.");
      }
    } catch (e) {
      const msg = e.response?.data?.msg || e.message || "Could not save attendance. Please check your connection and try again.";
      Alert.alert("Save Failed", msg);
    }
    if (rowId) setSavingRow(null); else setSaving(false);
  };

  const handleSave = () => saveRecords(changedIds);

  // ── Computed ─────────────────────────────────────────────────────

  const filteredEmployees = employees.filter((e) => {
    const q = search.toLowerCase().trim();
    if (!q) return true;
    return (
      (e.name || "").toLowerCase().includes(q) ||
      (e.employee_id || "").toLowerCase().includes(q) ||
      (e.department || "").toLowerCase().includes(q)
    );
  });

  const markedCount = employees.filter((e) => isMarked(e)).length;

  // ── Render ───────────────────────────────────────────────────────

  const renderEmployee = ({ item }) => {
    const att = attendance[item.employee_id] || { type: "", login: "", logout: "" };
    const marked = isMarked(item);
    const changed = isChanged(att, item);
    const noTimes = NO_TIME_TYPES.includes(att.type);
    // A saved type the list doesn't offer (an older value) still shows.
    const typeOptions = att.type && !ATT_STATUS_OPTIONS.some((o) => o.value === att.type)
      ? [...ATT_STATUS_OPTIONS, { label: att.type, value: att.type }]
      : ATT_STATUS_OPTIONS;
    const statusColor = att.type ? STATUS_COLORS[att.type] : null;
    const workMode = (item.work_mode || "office").toLowerCase();
    const workModeColor = WORK_MODE_COLORS[workMode] || WORK_MODE_COLORS.office;
    const ms = item.month_summary || { present: 0, half: 0, absent: 0, leave: 0 };

    return (
      <View style={styles.employeeCard}>
        {/* Header: Avatar + Name + ID */}
        <View style={styles.empHeader}>
          <View style={[styles.avatar, { backgroundColor: colors.employee }]}>
            <Text style={styles.avatarText}>{getInitials(item.name)}</Text>
          </View>
          <View style={styles.empInfo}>
            <Text style={styles.empName}>{item.name}</Text>
            <Text style={styles.empId}>{item.employee_id}</Text>
            {item.role ? <Text style={styles.empRole}>{item.role}</Text> : null}
          </View>
        </View>

        {/* Department / Designation */}
        <View style={styles.empMetaRow}>
          {item.department ? (
            <Text style={styles.empDept}>{item.department}</Text>
          ) : null}
          {item.designation ? (
            <Text style={styles.empDesig}>{item.designation}</Text>
          ) : null}
          {!item.department && !item.designation ? (
            <Text style={styles.empNA}>—</Text>
          ) : null}
        </View>

        {/* Shift & Mode */}
        <View style={styles.shiftRow}>
          {item.shift ? (
            <View style={styles.shiftBadge}>
              <Text style={styles.shiftText}>{item.shift}</Text>
              {item.shift_start && item.shift_end ? (
                <Text style={styles.shiftTime}>
                  {" "}
                  {item.shift_start}–{item.shift_end}
                </Text>
              ) : null}
            </View>
          ) : (
            <Text style={styles.noShift}>No shift</Text>
          )}
          <View
            style={[styles.modeBadge, { backgroundColor: workModeColor.bg }]}
          >
            <Text style={[styles.modeText, { color: workModeColor.text }]}>
              {workMode.toUpperCase()}
            </Text>
          </View>
        </View>

        {/* This Month stats */}
        {ms && (ms.present || ms.half || ms.absent || ms.leave) ? (
          <View style={styles.monthStats}>
            <View style={[styles.statPill, { backgroundColor: "#DCFCE7" }]}>
              <Text style={[styles.statPillText, { color: "#15803D" }]}>
                ✅ {ms.present}P
              </Text>
            </View>
            <View style={[styles.statPill, { backgroundColor: "#FEF9C3" }]}>
              <Text style={[styles.statPillText, { color: "#854D0E" }]}>
                🌓 {ms.half}H
              </Text>
            </View>
            <View style={[styles.statPill, { backgroundColor: "#FEE2E2" }]}>
              <Text style={[styles.statPillText, { color: "#B91C1C" }]}>
                ❌ {ms.absent}A
              </Text>
            </View>
            <View style={[styles.statPill, { backgroundColor: colors.purpleBg }]}>
              <Text style={[styles.statPillText, { color: "#6D28D9" }]}>
                🌴 {ms.leave}L
              </Text>
            </View>
          </View>
        ) : (
          <Text style={styles.noRecords}>No records</Text>
        )}

        {/* What's saved for the day */}
        <View style={[styles.savedRow, marked ? styles.savedRowOn : null]}>
          <Ionicons
            name={marked ? "checkmark-circle" : "ellipse-outline"}
            size={16}
            color={marked ? "#15803D" : "#98A2B3"}
          />
          <Text style={[styles.savedText, marked ? styles.savedTextOn : null]} testID={`saved-${item.employee_id}`}>
            {marked ? `Saved: ${savedSummary(item)}` : "Not marked yet"}
          </Text>
        </View>

        {/* Attendance Status Picker */}
        <View style={styles.pickerContainer}>
          <Picker
            selectedValue={att.type}
            onValueChange={(val) => handleStatusChange(item.employee_id, val)}
            style={styles.picker}
            dropdownIconColor={colors.textLight}
            itemStyle={{ fontSize: 14, color: colors.text }}
          >
            {typeOptions.map((opt) => (
              <Picker.Item
                key={opt.value}
                label={opt.label}
                value={opt.value}
                color={colors.text}
              />
            ))}
          </Picker>
        </View>

        {/* Login / Logout Time (none for Absent, Leave, Holiday, Week Off) */}
        <View style={styles.timeRow}>
          <TouchableOpacity
            style={[styles.timeInput, noTimes && styles.timeInputOff]}
            onPress={() => openTimePicker(item.employee_id, "login")}
            disabled={noTimes}
            accessibilityRole="button"
            accessibilityLabel={`Check-in time for ${item.name}: ${noTimes ? "none" : att.login || "not set"}`}
          >
            <Ionicons name="log-in-outline" size={16} color="#475467" />
            <Text style={styles.timeText}>{noTimes ? "—" : att.login || "In —"}</Text>
          </TouchableOpacity>
          <TouchableOpacity
            style={[styles.timeInput, noTimes && styles.timeInputOff]}
            onPress={() => openTimePicker(item.employee_id, "logout")}
            disabled={noTimes}
            accessibilityRole="button"
            accessibilityLabel={`Check-out time for ${item.name}: ${noTimes ? "none" : att.logout || "not set"}`}
          >
            <Ionicons name="log-out-outline" size={16} color="#475467" />
            <Text style={styles.timeText}>{noTimes ? "—" : att.logout || "Out —"}</Text>
          </TouchableOpacity>
        </View>

        {/* Save or update just this employee */}
        {changed ? (
          <TouchableOpacity
            style={[styles.rowSaveBtn, marked ? styles.rowUpdateBtn : null]}
            onPress={() => saveRecords([item.employee_id], item.employee_id)}
            disabled={savingRow === item.employee_id || saving}
            accessibilityRole="button"
            accessibilityLabel={`${marked ? "Update" : "Save"} attendance for ${item.name}`}
            testID={`rowSave-${item.employee_id}`}
          >
            {savingRow === item.employee_id ? (
              <ActivityIndicator size="small" color="#FFFFFF" />
            ) : (
              <Ionicons name={marked ? "refresh" : "save"} size={16} color="#FFFFFF" />
            )}
            <Text style={styles.rowSaveText}>{marked ? "Update" : "Save"}</Text>
          </TouchableOpacity>
        ) : null}
      </View>
    );
  };

  return (
    <LinearGradient
      colors={colors.screenGradient}
      style={{ flex: 1 }}
    >
      <SafeAreaView style={{ flex: 1 }}>
        <AdminHeader
          title="Mark Attendance"
          onMenu={() => navigation.dispatch(DrawerActions.openDrawer())}
        />

        <ScrollView
          showsVerticalScrollIndicator={false}
          contentContainerStyle={styles.scrollContent}
        >
          {/* Date Bar */}
          <View style={styles.dateBar}>
            <Text style={styles.dateLabel}>Select Date</Text>
            <TouchableOpacity accessibilityRole="button"
              style={styles.dateButton}
              onPress={() => setDatePickerOpen(true)}
            >
              <Ionicons name="calendar" size={18} color={colors.employee} />
              <Text style={styles.dateText}>{formatDate(selectedDate)}</Text>
              <Ionicons name="chevron-down" size={16} color={colors.textLight} />
            </TouchableOpacity>
            <Text style={styles.empCount}>
              Showing <Text style={styles.empCountBold}>{employees.length}</Text>{" "}
              employee(s)
            </Text>
          </View>

          {/* Quick Mark */}
          <View style={styles.quickBar}>
            <Text style={styles.quickLabel}>Quick Mark All:</Text>
            {QUICK_MARK_OPTIONS.map((opt) => (
              <TouchableOpacity accessibilityRole="button"
                key={opt.value}
                style={styles.qbtn}
                onPress={() => handleQuickMark(opt.value)}
              >
                <Text style={styles.qbtnText}>{opt.label}</Text>
              </TouchableOpacity>
            ))}
          </View>

          {/* Search */}
          <View style={styles.searchWrap}>
            <Ionicons name="search" size={18} color={colors.employee} />
            <TextInput
              placeholder="Search employee by name or ID…"
              placeholderTextColor={colors.textLight}
              value={search}
              onChangeText={setSearch}
              style={styles.searchInput}
            />
          </View>

          {/* Employee List */}
          {loading ? (
            <View style={styles.loadingContainer}>
              <ActivityIndicator size="large" color={colors.employee} />
              <Text style={styles.loadingText}>Loading employees…</Text>
            </View>
          ) : filteredEmployees.length === 0 ? (
            <View style={styles.emptyContainer}>
              <Ionicons name="people" size={48} color="#D0D5DD" />
              <Text style={styles.emptyText}>No employees found.</Text>
              <Text style={styles.emptySubtext}>
                Try adjusting your search.
              </Text>
            </View>
          ) : (
            <FlatList
              data={filteredEmployees}
              keyExtractor={(item) => item.employee_id}
              renderItem={renderEmployee}
              scrollEnabled={false}
              showsVerticalScrollIndicator={false}
            />
          )}

          {/* Spacer */}
          <View style={{ height: 100 }} />
        </ScrollView>

        {/* Footer */}
        <View style={styles.footer}>
          <Text style={styles.footerCount}>
            {markedCount} of {employees.length} marked
            {changedIds.length ? ` · ${changedIds.length} change(s)` : ""}
          </Text>
          <TouchableOpacity
            style={[styles.saveButton, (saving || changedIds.length === 0) && styles.saveButtonDisabled]}
            onPress={handleSave}
            disabled={saving || changedIds.length === 0}
            accessibilityRole="button"
            testID="saveAllAttendance"
          >
            {saving ? (
              <ActivityIndicator size="small" color="#FFFFFF" />
            ) : (
              <Ionicons name="save" size={18} color="#FFFFFF" />
            )}
            <Text style={styles.saveText}>
              {saving ? "Saving…" : updateCount ? `Save & Update (${changedIds.length})` : `Save Attendance${changedIds.length ? ` (${changedIds.length})` : ""}`}
            </Text>
          </TouchableOpacity>
        </View>

        {/* Date picker dialog */}
        {datePickerOpen && (
          <DateTimePicker
            testID="attendanceDatePicker"
            value={selectedDate}
            mode="date"
            maximumDate={new Date()}
            onChange={(event, d) => {
              setDatePickerOpen(false);
              if (event?.type === "set" && d) setSelectedDate(d);
            }}
          />
        )}

        {/* Time picker dialog */}
        {timePickerOpen && (
          <DateTimePicker
            testID="attendanceTimePicker"
            value={tempTime}
            mode="time"
            is24Hour
            onChange={(event, t) => {
              if (event?.type === "set" && t) {
                handleTimeConfirm(t);
              } else {
                setTimePickerOpen(false);
                setTimePickerField(null);
              }
            }}
          />
        )}
      </SafeAreaView>
    </LinearGradient>
  );
}

// ── Styles ─────────────────────────────────────────────────────────

const makeStyles = (colors) => StyleSheet.create({
  savedRow: {
    flexDirection: "row", alignItems: "center", gap: 6, marginTop: 10,
    paddingVertical: 6, paddingHorizontal: 10, borderRadius: 8, backgroundColor: "#F2F4F7",
  },
  savedRowOn: { backgroundColor: "#ECFDF3" },
  savedText: { fontSize: 12.5, color: "#667085", flexShrink: 1 },
  savedTextOn: { color: "#067647", fontWeight: "600" },
  timeInputOff: { opacity: 0.45 },
  rowSaveBtn: {
    marginTop: 10, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6,
    backgroundColor: "#2563EB", borderRadius: 10, paddingVertical: 10,
  },
  rowUpdateBtn: { backgroundColor: "#D97706" },
  rowSaveText: { color: "#FFFFFF", fontSize: 14, fontWeight: "700" },
  scrollContent: {
    paddingHorizontal: 20,
    paddingBottom: 20,
  },

  // Date Bar
  dateBar: {
    backgroundColor: colors.card,
    borderRadius: 16,
    padding: 18,
    marginBottom: 18,
    borderWidth: 1.5,
    borderColor: "#DBEAFE",
    shadowColor: "#000",
    shadowOpacity: 0.05,
    shadowRadius: 10,
    shadowOffset: { width: 0, height: 5 },
    elevation: 4,
  },
  dateLabel: {
    fontSize: 12,
    fontWeight: "700",
    color: "#475467",
    marginBottom: 8,
    textTransform: "uppercase",
    letterSpacing: 0.5,
  },
  dateButton: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
    paddingVertical: 10,
    paddingHorizontal: 12,
    backgroundColor: colors.background,
    borderRadius: 10,
    borderWidth: 1.5,
    borderColor: "#DBEAFE",
  },
  dateText: {
    flex: 1,
    fontSize: 14,
    fontWeight: "600",
    color: "#1E293B",
  },
  empCount: {
    marginTop: 10,
    fontSize: 13,
    color: "#475467",
  },
  empCountBold: {
    fontWeight: "700",
    color: "#1E293B",
  },

  // Quick Mark
  quickBar: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    marginBottom: 16,
    flexWrap: "wrap",
  },
  quickLabel: {
    fontSize: 11,
    fontWeight: "700",
    color: colors.textLight,
    textTransform: "uppercase",
    letterSpacing: 0.5,
  },
  qbtn: {
    paddingHorizontal: 12,
    paddingVertical: 7,
    borderRadius: 8,
    backgroundColor: colors.background,
    borderWidth: 1.5,
    borderColor: colors.border,
  },
  qbtnText: {
    fontSize: 12,
    fontWeight: "700",
    color: colors.textSecondary,
  },

  // Search
  searchWrap: {
    position: "relative",
    marginBottom: 18,
    flexDirection: "row",
    alignItems: "center",
  },
  searchInput: {
    flex: 1,
    marginLeft: 10,
    fontSize: 14,
    fontWeight: "500",
    color: colors.text,
    paddingVertical: 12,
    paddingHorizontal: 14,
    backgroundColor: colors.card,
    borderRadius: 12,
    borderWidth: 1.5,
    borderColor: "#DBEAFE",
  },

  // Employee Card
  employeeCard: {
    backgroundColor: colors.card,
    borderRadius: 18,
    padding: 18,
    marginBottom: 14,
    borderWidth: 1,
    borderColor: "#E5E7EB",
    shadowColor: "#000",
    shadowOpacity: 0.04,
    shadowRadius: 8,
    shadowOffset: { width: 0, height: 3 },
    elevation: 2,
  },
  empHeader: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    marginBottom: 12,
  },
  avatar: {
    width: 40,
    height: 40,
    borderRadius: 20,
    justifyContent: "center",
    alignItems: "center",
  },
  avatarText: {
    fontSize: 15,
    fontWeight: "700",
    color: "#FFFFFF",
  },
  empInfo: {
    flex: 1,
  },
  empName: {
    fontSize: 16,
    fontWeight: "700",
    color: colors.text,
  },
  empId: {
    fontSize: 12,
    color: colors.textLight,
    marginTop: 2,
  },
  empRole: {
    fontSize: 11,
    color: "#D0D5DD",
    marginTop: 1,
    textTransform: "capitalize",
  },

  // Meta
  empMetaRow: {
    marginBottom: 10,
  },
  empDept: {
    fontSize: 13,
    fontWeight: "600",
    color: "#1E293B",
  },
  empDesig: {
    fontSize: 11,
    color: colors.textLight,
    marginTop: 2,
  },
  empNA: {
    fontSize: 12,
    color: "#D0D5DD",
  },

  // Shift & Mode
  shiftRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
    marginBottom: 12,
  },
  shiftBadge: {
    backgroundColor: colors.blueBg,
    borderRadius: 8,
    paddingHorizontal: 10,
    paddingVertical: 5,
  },
  shiftText: {
    fontSize: 12,
    fontWeight: "600",
    color: "#1D4ED8",
  },
  shiftTime: {
    fontSize: 11,
    color: "#475467",
    fontWeight: "500",
  },
  noShift: {
    fontSize: 12,
    color: "#D0D5DD",
  },
  modeBadge: {
    borderRadius: 6,
    paddingHorizontal: 8,
    paddingVertical: 3,
  },
  modeText: {
    fontSize: 10,
    fontWeight: "700",
  },

  // Month Stats
  monthStats: {
    flexDirection: "row",
    gap: 6,
    marginBottom: 14,
    flexWrap: "wrap",
  },
  statPill: {
    borderRadius: 6,
    paddingHorizontal: 8,
    paddingVertical: 3,
  },
  statPillText: {
    fontSize: 10,
    fontWeight: "700",
  },
  noRecords: {
    fontSize: 11,
    color: "#D0D5DD",
    marginBottom: 14,
  },

  // Picker
  pickerContainer: {
    backgroundColor: colors.background,
    borderRadius: 10,
    borderWidth: 1.5,
    borderColor: colors.border,
    marginBottom: 14,
    overflow: "hidden",
  },
  picker: {
    height: 44,
  },

  // Time Row
  timeRow: {
    flexDirection: "row",
    gap: 12,
  },
  timeInput: {
    flex: 1,
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    paddingVertical: 10,
    paddingHorizontal: 12,
    backgroundColor: colors.background,
    borderRadius: 10,
    borderWidth: 1.5,
    borderColor: colors.border,
  },
  timeText: {
    fontSize: 13,
    fontWeight: "600",
    color: "#1E293B",
  },

  // Loading / Empty
  loadingContainer: {
    alignItems: "center",
    paddingVertical: 40,
  },
  loadingText: {
    marginTop: 12,
    fontSize: 14,
    color: "#475467",
  },
  emptyContainer: {
    alignItems: "center",
    paddingVertical: 48,
  },
  emptyText: {
    marginTop: 12,
    fontSize: 15,
    fontWeight: "700",
    color: colors.textLight,
  },
  emptySubtext: {
    marginTop: 6,
    fontSize: 13,
    color: "#D0D5DD",
  },

  // Footer
  footer: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingHorizontal: 20,
    paddingVertical: 16,
    backgroundColor: colors.card,
    borderTopWidth: 1,
    borderTopColor: "#E5E7EB",
  },
  footerCount: {
    fontSize: 13,
    color: "#475467",
    fontWeight: "600",
  },
  saveButton: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    paddingHorizontal: 24,
    paddingVertical: 12,
    backgroundColor: colors.employee,
    borderRadius: 12,
    shadowColor: "#3B82F6",
    shadowOpacity: 0.3,
    shadowRadius: 10,
    shadowOffset: { width: 0, height: 4 },
    elevation: 4,
  },
  saveButtonDisabled: {
    opacity: 0.7,
  },
  saveText: {
    fontSize: 14,
    fontWeight: "700",
    color: "#FFFFFF",
  },
});
