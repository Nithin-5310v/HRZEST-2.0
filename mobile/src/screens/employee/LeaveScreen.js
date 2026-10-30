import React, { useState, useCallback } from "react";
import {
  View,
  Text,
  ScrollView,
  StyleSheet,
  TouchableOpacity,
  Alert,
  ActivityIndicator,
  TextInput,
  RefreshControl,
} from "react-native";
import { LinearGradient } from "expo-linear-gradient";
import { Ionicons } from "@expo/vector-icons";
import ProfileHeader from "../../components/profile/ProfileHeader";
import { useFocusEffect } from "@react-navigation/native";
import { useAuth } from "../../store/AuthContext";
import { useTheme } from "../../store/ThemeContext";
import { submitLeaveRequest, fetchEmployeeLeaves, cancelLeaveRequest } from "../../api/client";

const LEAVE_TYPES = [
  { label: "Casual Leave (CL)", value: "Casual Leave", icon: "briefcase-outline" },
  { label: "Sick Leave (SL)", value: "Sick Leave", icon: "medical-outline" },
  { label: "Earned Leave (EL)", value: "Earned Leave", icon: "star-outline" },
  { label: "Comp-Off", value: "Comp-Off", icon: "time-outline" },
];

const LEAVE_SLOTS = [
  { label: "Full Day", value: "Full Day", icon: "sunny-outline" },
  { label: "First Half (Morning)", value: "First Half", icon: "partly-sunny-outline" },
  { label: "Second Half (Afternoon)", value: "Second Half", icon: "moon-outline" },
];

const REASONS = [
  { label: "Health / Medical", value: "Health / Medical", icon: "medical-outline" },
  { label: "Personal Work", value: "Personal Work", icon: "person-outline" },
  { label: "Family Function", value: "Family Function", icon: "people-outline" },
  { label: "Travel / Vacation", value: "Travel / Vacation", icon: "airplane-outline" },
  { label: "Other Reason", value: null, icon: "ellipsis-horizontal-outline" },
];

const STATUS_STYLE = {
  Approved: { bg: "#DCFCE7", text: "#166534" },
  Pending: { bg: "#FEF9C3", text: "#854D0E" },
  Rejected: { bg: "#FEE2E2", text: "#991B1B" },
};

function addDays(n) {
  const d = new Date();
  d.setDate(d.getDate() + n);
  return d;
}

function toISO(d) {
  return new Date(d).toISOString().split("T")[0];
}

function fmtDate(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return d.toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" });
}

export default function LeaveScreen() {
  const { signOut } = useAuth();
  const { colors } = useTheme();
  const styles = React.useMemo(() => makeStyles(colors), [colors]);
  const [tab, setTab] = useState("request");
  const [leaveType, setLeaveType] = useState("Casual Leave");
  const [leaveSlot, setLeaveSlot] = useState("Full Day");
  const [leaveDate, setDate] = useState(toISO(addDays(1)));
  const [reason, setReason] = useState("Health / Medical");
  const [custom, setCustom] = useState("");
  const [loading, setLoading] = useState(false);
  const [success, setSuccess] = useState(false);

  const [history, setHistory] = useState(null);
  const [histLoading, setHistLoading] = useState(false);
  const [cancelling, setCancelling] = useState(null);
  const [statusFilter, setStatusFilter] = useState("All");

  const loadHistory = async () => {
    setHistLoading(true);
    try {
      const res = await fetchEmployeeLeaves();
      if (res?.data?.ok) setHistory(res.data);
    } catch (_) {}
    setHistLoading(false);
  };

  useFocusEffect(
    useCallback(() => {
      loadHistory();
    }, [])
  );

  const handleCancel = (lid, leaveDate) => {
    Alert.alert(
      "Cancel Leave Request",
      `Are you sure you want to cancel your leave request for ${fmtDate(leaveDate)}?`,
      [
        { text: "No", style: "cancel" },
        {
          text: "Yes, Cancel",
          style: "destructive",
          onPress: async () => {
            setCancelling(lid);
            try {
              const res = await cancelLeaveRequest(lid);
              if (res?.data?.ok) {
                loadHistory();
              } else {
                Alert.alert("Error", res?.data?.msg || "Could not cancel.");
              }
            } catch (e) {
              Alert.alert("Error", e.response?.data?.msg || "Failed to connect.");
            }
            setCancelling(null);
          },
        },
      ]
    );
  };

  const handleSubmit = async () => {
    const reasonText = reason === null ? custom.trim() : reason;
    if (!reasonText) {
      Alert.alert("Reason Required", "Please select or enter a reason for leave.");
      return;
    }

    const finalReason = `[${leaveType} • ${leaveSlot}] ${reasonText}`;

    Alert.alert(
      "Confirm Leave Request",
      `Type: ${leaveType}\nSlot: ${leaveSlot}\nDate: ${fmtDate(leaveDate)}\nReason: ${reasonText}\n\nSubmit this leave application?`,
      [
        { text: "Cancel", style: "cancel" },
        {
          text: "Submit",
          onPress: async () => {
            setLoading(true);
            try {
              const res = await submitLeaveRequest(leaveDate, finalReason);
              if (res?.data?.ok) {
                setSuccess(true);
                setCustom("");
                setTimeout(() => setSuccess(false), 3500);
                loadHistory();
              } else {
                Alert.alert("Submission Notice", res?.data?.msg || "Could not submit leave request.");
              }
            } catch (e) {
              const serverMsg = e?.response?.data?.msg || e?.response?.data?.message;
              if (e?.response?.status === 401 || (serverMsg && serverMsg.toLowerCase().includes("token"))) {
                Alert.alert(
                  "Session Expired / Re-login Needed 🔒",
                  "Your active login token has expired. Tap 'Sign Out' to sign in with your Employee ID.",
                  [
                    { text: "Cancel", style: "cancel" },
                    { text: "Sign Out & Re-Login", style: "destructive", onPress: () => signOut() },
                  ]
                );
              } else {
                Alert.alert("Submission Notice", serverMsg || "Failed to connect to server.");
              }
            }
            setLoading(false);
          },
        },
      ]
    );
  };

  const dateOptions = Array.from({ length: 30 }, (_, i) => {
    const d = addDays(i + 1);
    return {
      iso: toISO(d),
      num: d.getDate(),
      mon: d.toLocaleString("default", { month: "short" }),
      day: d.toLocaleString("default", { weekday: "short" }),
    };
  });

  const filteredLeaves = (history?.leaves || []).filter((l) => {
    if (statusFilter === "All") return true;
    return l.status === statusFilter;
  });

  return (
    <LinearGradient colors={colors.screenGradient} style={styles.bg}>
      <ProfileHeader title="Leave Portal" showBack={false} />

      {/* Tab Switcher */}
      <View style={styles.tabBar}>
        <TouchableOpacity
          style={[styles.tabBtn, tab === "request" && styles.tabBtnActive]}
          onPress={() => setTab("request")}
        >
          <Text style={[styles.tabTxt, tab === "request" && styles.tabTxtActive]}>Apply Leave</Text>
        </TouchableOpacity>

        <TouchableOpacity
          style={[styles.tabBtn, tab === "history" && styles.tabBtnActive]}
          onPress={() => setTab("history")}
        >
          <Text style={[styles.tabTxt, tab === "history" && styles.tabTxtActive]}>Leave History</Text>
          {history?.summary?.pending > 0 && (
            <View style={styles.badge}>
              <Text style={styles.badgeTxt}>{history.summary.pending}</Text>
            </View>
          )}
        </TouchableOpacity>
      </View>

      {tab === "request" ? (
        <ScrollView
          contentContainerStyle={styles.scroll}
          showsVerticalScrollIndicator={false}
          keyboardShouldPersistTaps="handled"
          refreshControl={
            <RefreshControl refreshing={histLoading} onRefresh={loadHistory} />
          }
        >
          {success && (
            <View style={styles.successBanner}>
              <Ionicons name="checkmark-circle" size={20} color="#166534" />
              <Text style={styles.successTxt}>Leave request submitted successfully for approval!</Text>
            </View>
          )}

          {/* Leave summary for this year -- real counts from the server.
              Per-type quota balances (e.g. "8 of 12 Casual Leave left")
              aren't exposed by any employee-facing API yet, so we show
              request-status counts instead of inventing numbers. */}
          <Text style={styles.sectionLabel}>THIS YEAR</Text>
          <View style={styles.balanceRow}>
            <View style={[styles.balCard, { backgroundColor: "#DCFCE7", borderColor: "#BBF7D0" }]}>
              <Ionicons name="checkmark-circle" size={18} color="#15803D" />
              <Text style={[styles.balNum, { color: "#166534" }]}>{history?.summary?.approved ?? "—"}</Text>
              <Text style={[styles.balLabel, { color: "#15803D" }]}>Approved</Text>
            </View>

            <View style={[styles.balCard, { backgroundColor: "#FEF9C3", borderColor: "#FDE047" }]}>
              <Ionicons name="time" size={18} color="#A16207" />
              <Text style={[styles.balNum, { color: "#854D0E" }]}>{history?.summary?.pending ?? "—"}</Text>
              <Text style={[styles.balLabel, { color: "#A16207" }]}>Pending</Text>
            </View>

            <View style={[styles.balCard, { backgroundColor: "#FEE2E2", borderColor: "#FECACA" }]}>
              <Ionicons name="close-circle" size={18} color="#B91C1C" />
              <Text style={[styles.balNum, { color: "#991B1B" }]}>{history?.summary?.rejected ?? "—"}</Text>
              <Text style={[styles.balLabel, { color: "#B91C1C" }]}>Rejected</Text>
            </View>
          </View>

          {/* Select Leave Type */}
          <View style={styles.card}>
            <Text style={styles.cardTitle}>LEAVE TYPE</Text>
            <View style={styles.typeGrid}>
              {LEAVE_TYPES.map((t) => (
                <TouchableOpacity
                  key={t.value}
                  style={[styles.typeChip, leaveType === t.value && styles.typeChipActive]}
                  onPress={() => setLeaveType(t.value)}
                >
                  <Ionicons name={t.icon} size={16} color={leaveType === t.value ? "#FFFFFF" : colors.primary} />
                  <Text style={[styles.typeChipText, leaveType === t.value && styles.typeChipTextActive]}>
                    {t.label}
                  </Text>
                </TouchableOpacity>
              ))}
            </View>
          </View>

          {/* Select Duration / Slot */}
          <View style={styles.card}>
            <Text style={styles.cardTitle}>DURATION / SLOT</Text>
            <View style={styles.typeGrid}>
              {LEAVE_SLOTS.map((s) => (
                <TouchableOpacity
                  key={s.value}
                  style={[styles.slotChip, leaveSlot === s.value && styles.slotChipActive]}
                  onPress={() => setLeaveSlot(s.value)}
                >
                  <Ionicons name={s.icon} size={15} color={leaveSlot === s.value ? "#FFFFFF" : colors.textSecondary} />
                  <Text style={[styles.slotChipText, leaveSlot === s.value && styles.slotChipTextActive]}>
                    {s.label}
                  </Text>
                </TouchableOpacity>
              ))}
            </View>
          </View>

          {/* Select Date */}
          <View style={styles.card}>
            <Text style={styles.cardTitle}>SELECT LEAVE DATE</Text>
            <ScrollView horizontal showsHorizontalScrollIndicator={false}>
              {dateOptions.map((opt) => (
                <TouchableOpacity
                  key={opt.iso}
                  style={[styles.dateChip, leaveDate === opt.iso && styles.dateChipActive]}
                  onPress={() => setDate(opt.iso)}
                >
                  <Text style={[styles.dateDay, leaveDate === opt.iso && styles.dateDayActive]}>{opt.day}</Text>
                  <Text style={[styles.dateNum, leaveDate === opt.iso && styles.dateNumActive]}>{opt.num}</Text>
                  <Text style={[styles.dateMon, leaveDate === opt.iso && styles.dateMonActive]}>{opt.mon}</Text>
                </TouchableOpacity>
              ))}
            </ScrollView>

            <View style={styles.selectedDateBadge}>
              <Ionicons name="calendar-sharp" size={14} color={colors.primary} />
              <Text style={styles.selectedDateText}>Selected: {fmtDate(leaveDate)}</Text>
            </View>
          </View>

          {/* Reason Selection */}
          <View style={styles.card}>
            <Text style={styles.cardTitle}>REASON FOR LEAVE</Text>
            <View style={styles.chips}>
              {REASONS.map((r) => (
                <TouchableOpacity
                  key={r.label}
                  style={[styles.chip, reason === r.value && styles.chipActive]}
                  onPress={() => {
                    setReason(r.value);
                    setCustom("");
                  }}
                >
                  <Ionicons name={r.icon} size={14} color={reason === r.value ? "#FFFFFF" : colors.textSecondary} />
                  <Text style={[styles.chipTxt, reason === r.value && styles.chipTxtActive]}>{r.label}</Text>
                </TouchableOpacity>
              ))}
            </View>

            {reason === null && (
              <TextInput
                style={styles.textarea}
                placeholder="Describe your leave reason..."
                placeholderTextColor={colors.textLight}
                multiline
                numberOfLines={3}
                value={custom}
                onChangeText={setCustom}
              />
            )}
          </View>

          <TouchableOpacity
            style={[styles.submitBtn, loading && { opacity: 0.6 }]}
            onPress={handleSubmit}
            disabled={loading}
          >
            {loading ? (
              <ActivityIndicator color="#FFFFFF" />
            ) : (
              <>
                <Ionicons name="paper-plane-sharp" size={18} color="#FFFFFF" />
                <Text style={styles.submitTxt}>Submit Leave Application</Text>
              </>
            )}
          </TouchableOpacity>

          <View style={{ height: 40 }} />
        </ScrollView>
      ) : (
        <ScrollView
          contentContainerStyle={styles.scroll}
          showsVerticalScrollIndicator={false}
          refreshControl={
            <RefreshControl refreshing={histLoading} onRefresh={loadHistory} />
          }
        >
          {/* Status Filter Chips */}
          <View style={styles.filterRow}>
            {["All", "Pending", "Approved", "Rejected"].map((st) => (
              <TouchableOpacity
                key={st}
                style={[styles.filterChip, statusFilter === st && styles.filterChipActive]}
                onPress={() => setStatusFilter(st)}
              >
                <Text style={[styles.filterChipText, statusFilter === st && styles.filterChipTextActive]}>
                  {st}
                </Text>
              </TouchableOpacity>
            ))}
          </View>

          {histLoading ? (
            <View style={styles.center}>
              <ActivityIndicator size="large" color={colors.primary} />
            </View>
          ) : filteredLeaves.length === 0 ? (
            <View style={styles.empty}>
              <Ionicons name="document-text-outline" size={48} color={colors.border} />
              <Text style={styles.emptyTxt}>No leave requests found ({statusFilter})</Text>
            </View>
          ) : (
            filteredLeaves.map((l) => {
              const st = STATUS_STYLE[l.status] || STATUS_STYLE.Pending;
              const isFuture = new Date(l.leave_date) > new Date();
              return (
                <View key={l.id} style={styles.leaveRow}>
                  <View style={styles.leaveDateBox}>
                    <Text style={styles.leaveDateNum}>{new Date(l.leave_date).getDate()}</Text>
                    <Text style={styles.leaveDateMon}>
                      {new Date(l.leave_date).toLocaleString("default", { month: "short" })}
                    </Text>
                  </View>

                  <View style={styles.leaveInfo}>
                    <Text style={styles.leaveReason}>{l.reason}</Text>
                    <Text style={styles.leaveSubmitted}>Applied on {fmtDate(l.created_at)}</Text>
                  </View>

                  <View style={{ alignItems: "flex-end", gap: 6 }}>
                    <View style={[styles.statusBadge, { backgroundColor: st.bg }]}>
                      <Text style={[styles.statusTxt, { color: st.text }]}>{l.status}</Text>
                    </View>

                    {l.status === "Pending" && isFuture && (
                      <TouchableOpacity
                        onPress={() => handleCancel(l.id, l.leave_date)}
                        disabled={cancelling === l.id}
                        style={styles.cancelBtn}
                      >
                        <Text style={styles.cancelBtnTxt}>
                          {cancelling === l.id ? "..." : "Cancel"}
                        </Text>
                      </TouchableOpacity>
                    )}
                  </View>
                </View>
              );
            })
          )}

          <View style={{ height: 120 }} />
        </ScrollView>
      )}
    </LinearGradient>
  );
}

const makeStyles = (colors) => StyleSheet.create({
  bg: { flex: 1 },
  tabBar: {
    flexDirection: "row",
    backgroundColor: colors.card,
    paddingHorizontal: 20,
    borderBottomWidth: 1,
    borderColor: colors.border,
  },
  tabBtn: {
    flex: 1,
    paddingVertical: 14,
    alignItems: "center",
    flexDirection: "row",
    justifyContent: "center",
  },
  tabBtnActive: { borderBottomWidth: 2, borderColor: colors.primary },
  tabTxt: { fontSize: 13, fontWeight: "600", color: colors.textLight },
  tabTxtActive: { color: colors.primary },
  badge: {
    backgroundColor: colors.danger,
    borderRadius: 10,
    minWidth: 18,
    height: 18,
    justifyContent: "center",
    alignItems: "center",
    marginLeft: 6,
    paddingHorizontal: 4,
  },
  badgeTxt: { color: "#FFF", fontSize: 10, fontWeight: "700" },
  scroll: { padding: 18, paddingBottom: 130 },
  center: { alignItems: "center", paddingVertical: 60 },
  successBanner: {
    flexDirection: "row",
    alignItems: "center",
    backgroundColor: "#DCFCE7",
    borderRadius: 14,
    padding: 14,
    marginBottom: 16,
    borderWidth: 1,
    borderColor: "#BBF7D0",
  },
  successTxt: { color: "#166534", fontWeight: "700", marginLeft: 8, fontSize: 13 },
  sectionLabel: { fontSize: 11, fontWeight: "800", color: colors.textSecondary, marginBottom: 8, letterSpacing: 0.5 },
  balanceRow: { flexDirection: "row", justifyContent: "space-between", marginBottom: 16 },
  balCard: {
    flex: 1,
    marginHorizontal: 3,
    borderRadius: 14,
    padding: 12,
    alignItems: "center",
    borderWidth: 1,
  },
  balNum: { fontSize: 15, fontWeight: "800", marginTop: 4 },
  balLabel: { fontSize: 10, fontWeight: "700", marginTop: 2 },
  card: {
    backgroundColor: colors.card,
    borderRadius: 18,
    padding: 16,
    marginBottom: 14,
    borderWidth: 1,
    borderColor: colors.border,
    shadowColor: "#132026",
    shadowOpacity: 0.03,
    shadowRadius: 8,
    shadowOffset: { width: 0, height: 2 },
    elevation: 2,
  },
  cardTitle: { fontSize: 11, fontWeight: "800", color: colors.textSecondary, marginBottom: 12, letterSpacing: 0.5 },
  typeGrid: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  typeChip: {
    flexDirection: "row",
    alignItems: "center",
    paddingHorizontal: 12,
    paddingVertical: 8,
    borderRadius: 12,
    backgroundColor: colors.primaryLight,
    borderWidth: 1,
    borderColor: "#CDEBF2",
    gap: 6,
  },
  typeChipActive: { backgroundColor: colors.primary, borderColor: colors.primary },
  typeChipText: { fontSize: 12, fontWeight: "700", color: colors.primary },
  typeChipTextActive: { color: "#FFFFFF" },
  slotChip: {
    flexDirection: "row",
    alignItems: "center",
    paddingHorizontal: 12,
    paddingVertical: 8,
    borderRadius: 12,
    backgroundColor: colors.background,
    borderWidth: 1,
    borderColor: colors.border,
    gap: 6,
  },
  slotChipActive: { backgroundColor: colors.primary, borderColor: colors.primary },
  slotChipText: { fontSize: 12, fontWeight: "600", color: colors.textSecondary },
  slotChipTextActive: { color: "#FFFFFF" },
  dateChip: {
    width: 58,
    alignItems: "center",
    padding: 10,
    borderRadius: 14,
    marginRight: 8,
    backgroundColor: colors.background,
    borderWidth: 1.5,
    borderColor: colors.border,
  },
  dateChipActive: { backgroundColor: colors.primary, borderColor: colors.primary },
  dateDay: { fontSize: 10, color: colors.textSecondary, fontWeight: "600", marginBottom: 2 },
  dateDayActive: { color: "rgba(255,255,255,0.8)" },
  dateNum: { fontSize: 15, fontWeight: "800", color: colors.text },
  dateNumActive: { color: "#FFFFFF" },
  dateMon: { fontSize: 10, color: colors.textSecondary, marginTop: 2 },
  dateMonActive: { color: "rgba(255,255,255,0.8)" },
  selectedDateBadge: {
    flexDirection: "row",
    alignItems: "center",
    backgroundColor: colors.primaryLight,
    borderRadius: 10,
    paddingHorizontal: 10,
    paddingVertical: 6,
    marginTop: 10,
    alignSelf: "flex-start",
  },
  selectedDateText: { fontSize: 12, fontWeight: "700", color: colors.primary, marginLeft: 6 },
  chips: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  chip: {
    flexDirection: "row",
    alignItems: "center",
    paddingHorizontal: 12,
    paddingVertical: 8,
    borderRadius: 20,
    backgroundColor: colors.background,
    borderWidth: 1.5,
    borderColor: colors.border,
    gap: 6,
  },
  chipActive: { backgroundColor: colors.primary, borderColor: colors.primary },
  chipTxt: { color: colors.textSecondary, fontSize: 12, fontWeight: "600" },
  chipTxtActive: { color: "#FFFFFF" },
  textarea: {
    marginTop: 12,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: 12,
    padding: 12,
    fontSize: 13,
    color: colors.text,
    minHeight: 80,
    textAlignVertical: "top",
    backgroundColor: colors.background,
  },
  submitBtn: {
    backgroundColor: colors.primary,
    paddingVertical: 14,
    borderRadius: 16,
    alignItems: "center",
    flexDirection: "row",
    justifyContent: "center",
    gap: 8,
    marginTop: 4,
  },
  submitTxt: { color: "#FFFFFF", fontWeight: "800", fontSize: 14 },
  filterRow: { flexDirection: "row", marginBottom: 14, gap: 8 },
  filterChip: {
    paddingHorizontal: 14,
    paddingVertical: 6,
    borderRadius: 20,
    backgroundColor: colors.card,
    borderWidth: 1,
    borderColor: colors.border,
  },
  filterChipActive: { backgroundColor: colors.primary, borderColor: colors.primary },
  filterChipText: { fontSize: 12, fontWeight: "700", color: colors.textSecondary },
  filterChipTextActive: { color: "#FFFFFF" },
  empty: { alignItems: "center", paddingVertical: 50 },
  emptyTxt: { color: colors.textLight, fontSize: 13, marginTop: 12 },
  leaveRow: {
    flexDirection: "row",
    alignItems: "center",
    backgroundColor: colors.card,
    borderRadius: 16,
    padding: 14,
    marginBottom: 10,
    borderWidth: 1,
    borderColor: colors.border,
    elevation: 1,
  },
  leaveDateBox: {
    width: 48,
    height: 52,
    borderRadius: 14,
    backgroundColor: colors.primaryLight,
    alignItems: "center",
    justifyContent: "center",
    marginRight: 14,
  },
  leaveDateNum: { fontSize: 15, fontWeight: "800", color: colors.primary },
  leaveDateMon: { fontSize: 10, fontWeight: "600", color: colors.primary },
  leaveInfo: { flex: 1 },
  leaveReason: { fontSize: 13, fontWeight: "700", color: colors.text },
  leaveSubmitted: { fontSize: 12, color: colors.textLight, marginTop: 2 },
  statusBadge: { paddingHorizontal: 10, paddingVertical: 5, borderRadius: 20 },
  statusTxt: { fontSize: 11, fontWeight: "700" },
  cancelBtn: {
    paddingHorizontal: 10,
    paddingVertical: 4,
    borderRadius: 20,
    backgroundColor: colors.redBg,
    borderWidth: 1,
    borderColor: "#FECACA",
  },
  cancelBtnTxt: { fontSize: 11, fontWeight: "700", color: colors.danger },
});

