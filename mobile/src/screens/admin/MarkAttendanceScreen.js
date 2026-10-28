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
import DateTimePicker from "react-native-date-picker";
import { Picker } from "@react-native-picker/picker";

import AdminHeader from "../../components/admin/AdminHeader";
import { useTheme } from "../../store/ThemeContext";
import { fetchAttendanceEmployees, fetchEmployees, markAttendance } from "../../api/client";

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
  "Holiday": { bg: "#F1F5F9", text: "#475569", border: "#CBD5E1" },
  "Week Off": { bg: "#F1F5F9", text: "#475569", border: "#CBD5E1" },
};

const WORK_MODE_COLORS = {
  office: { bg: "#DBEAFE", text: "#1E40AF" },
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

export default function MarkAttendanceScreen({ navigation }) {
  const { colors } = useTheme();
  const styles = React.useMemo(() => makeStyles(colors), [colors]);
  const [selectedDate, setSelectedDate] = useState(new Date());
  const [datePickerOpen, setDatePickerOpen] = useState(false);
  const [employees, setEmployees] = useState([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [saving, setSaving] = useState(false);

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

  const initAttendance = (emps) => {
    const initial = {};
    emps.forEach((e) => {
      initial[e.employee_id] = {
        type: "",
        login: e.shift_start || "",
        logout: e.shift_end || "",
      };
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

  const handleSave = async () => {
    const records = Object.entries(attendance)
      .filter(([_, v]) => v.type)
      .map(([empId, v]) => ({
        emp_id: empId,
        attendance_type: v.type,
        login_time: v.login || null,
        logout_time: v.logout || null,
      }));

    if (records.length === 0) {
      Alert.alert("No Changes", "Please mark attendance for at least one employee.");
      return;
    }

    setSaving(true);
    try {
      const res = await markAttendance(formatDateISO(selectedDate), records);
      if (res.data?.ok) {
        Alert.alert(
          "Success",
          `Attendance saved for ${records.length} employee(s) on ${formatDate(selectedDate)}.`
        );
      } else {
        Alert.alert("Save Failed", res.data?.msg || "Could not save attendance. Please try again.");
      }
    } catch (e) {
      const msg = e.response?.data?.msg || e.message || "Could not save attendance. Please check your connection and try again.";
      Alert.alert("Save Failed", msg);
    }
    setSaving(false);
  };

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

  const markedCount = Object.values(attendance).filter((v) => v.type).length;

  // ── Render ───────────────────────────────────────────────────────

  const renderEmployee = ({ item }) => {
    const att = attendance[item.employee_id] || { type: "", login: "", logout: "" };
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

        {/* Attendance Status Picker */}
        <View style={styles.pickerContainer}>
          <Picker
            selectedValue={att.type}
            onValueChange={(val) => handleStatusChange(item.employee_id, val)}
            style={styles.picker}
            dropdownIconColor={colors.textLight}
            itemStyle={{ fontSize: 14, color: colors.text }}
          >
            {ATT_STATUS_OPTIONS.map((opt) => (
              <Picker.Item
                key={opt.value}
                label={opt.label}
                value={opt.value}
                color={colors.text}
              />
            ))}
          </Picker>
        </View>

        {/* Login / Logout Time */}
        <View style={styles.timeRow}>
          <TouchableOpacity
            style={styles.timeInput}
            onPress={() => openTimePicker(item.employee_id, "login")}
          >
            <Ionicons name="time-outline" size={16} color="#64748B" />
            <Text style={styles.timeText}>{att.login || "—"}</Text>
          </TouchableOpacity>
          <TouchableOpacity
            style={styles.timeInput}
            onPress={() => openTimePicker(item.employee_id, "logout")}
          >
            <Ionicons name="time-outline" size={16} color="#64748B" />
            <Text style={styles.timeText}>{att.logout || "—"}</Text>
          </TouchableOpacity>
        </View>
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
            <TouchableOpacity
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
              <TouchableOpacity
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
              <Ionicons name="people" size={48} color="#CBD5E1" />
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
            {markedCount} of {filteredEmployees.length} marked
          </Text>
          <TouchableOpacity
            style={[styles.saveButton, saving && styles.saveButtonDisabled]}
            onPress={handleSave}
            disabled={saving}
          >
            {saving ? (
              <ActivityIndicator size="small" color="#FFFFFF" />
            ) : (
              <Ionicons name="save" size={18} color="#FFFFFF" />
            )}
            <Text style={styles.saveText}>
              {saving ? "Saving…" : "Save Attendance"}
            </Text>
          </TouchableOpacity>
        </View>

        {/* Date Picker Modal */}
        <DateTimePicker
          modal
          open={datePickerOpen}
          date={selectedDate}
          mode="date"
          maximumDate={new Date()}
          onConfirm={(d) => {
            setSelectedDate(d);
            setDatePickerOpen(false);
          }}
          onCancel={() => setDatePickerOpen(false)}
        />

        {/* Time Picker Modal */}
        <DateTimePicker
          modal
          open={timePickerOpen}
          date={tempTime}
          mode="time"
          onConfirm={handleTimeConfirm}
          onCancel={() => setTimePickerOpen(false)}
        />
      </SafeAreaView>
    </LinearGradient>
  );
}

// ── Styles ─────────────────────────────────────────────────────────

const makeStyles = (colors) => StyleSheet.create({
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
    color: "#64748B",
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
    color: "#64748B",
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
    color: "#CBD5E1",
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
    color: "#CBD5E1",
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
    color: "#1E40AF",
  },
  shiftTime: {
    fontSize: 11,
    color: "#64748B",
    fontWeight: "500",
  },
  noShift: {
    fontSize: 12,
    color: "#CBD5E1",
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
    color: "#CBD5E1",
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
    color: "#64748B",
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
    color: "#CBD5E1",
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
    color: "#64748B",
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
