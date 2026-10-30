import React, { useState, useEffect } from "react";
import {
  ScrollView,
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
  RefreshControl,
  ActivityIndicator,
  Modal,
  TextInput,
  Alert,
} from "react-native";
import SafeAreaView from "../../components/SafeAreaView";
import { DrawerActions } from "@react-navigation/native";
import { LinearGradient } from "expo-linear-gradient";
import { Ionicons } from "@expo/vector-icons";

import AdminHeader from "../../components/admin/AdminHeader";
import {
  fetchLeaveRequests,
  leaveAction,
  fetchCompOff,
  fetchHolidays,
  addHoliday,
  deleteHoliday,
} from "../../api/client";
import { useTheme } from "../../store/ThemeContext";

export default function LeavesHolidaysScreen({ navigation }) {
  const { colors } = useTheme();
  const styles = React.useMemo(() => makeStyles(colors), [colors]);
  const [activeTab, setActiveTab] = useState("leaves"); // 'leaves' | 'compoff' | 'holidays'
  const [refreshing, setRefreshing] = useState(false);
  const [loading, setLoading] = useState(true);

  // Leaves state
  const [leaves, setLeaves] = useState([]);
  const [selectedLeave, setSelectedLeave] = useState(null);

  // Comp-off state
  const [compoffs, setCompoffs] = useState([]);

  // Holidays state
  const [holidays, setHolidays] = useState([]);
  const [addHolidayModalVisible, setAddHolidayModalVisible] = useState(false);
  const [holidayName, setHolidayName] = useState("");
  const [holidayDate, setHolidayDate] = useState("");
  const [holidayType, setHolidayType] = useState("Public");

  const loadData = async () => {
    try {
      if (activeTab === "leaves") {
        const res = await fetchLeaveRequests();
        if (res?.data?.requests) setLeaves(res.data.requests);
        else if (Array.isArray(res?.data)) setLeaves(res.data);
      } else if (activeTab === "compoff") {
        // blueprints/leave.py's /api/compoff returns {"ok": true, "balances": [...]}
        // -- comp-off is credited automatically from approved overtime, not a
        // manual approve/reject workflow, so this is a read-only balance list.
        const res = await fetchCompOff();
        if (Array.isArray(res?.data?.balances)) setCompoffs(res.data.balances);
      } else if (activeTab === "holidays") {
        const res = await fetchHolidays();
        if (res?.data?.holidays) setHolidays(res.data.holidays);
        else if (Array.isArray(res?.data)) setHolidays(res.data);
      }
    } catch (e) {
      // Keep existing data or load default fallback
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  };

  useEffect(() => {
    setLoading(true);
    loadData();
  }, [activeTab]);

  const onRefresh = () => {
    setRefreshing(true);
    loadData();
  };

  const handleLeaveAction = async (lid, action) => {
    const apiAction = action === "approve" || action === "Approved" ? "Approved" : "Declined";
    try {
      const res = await leaveAction(lid, apiAction);
      if (res?.data?.ok) {
        Alert.alert("Success", `Leave request ${apiAction.toLowerCase()}d successfully.`);
        loadData();
      } else {
        Alert.alert("Action Failed", res?.data?.msg || "Could not update status.");
      }
    } catch (e) {
      Alert.alert("Error", e?.response?.data?.msg || "Failed to update leave request.");
    }
  };

  const [savingHoliday, setSavingHoliday] = useState(false);

  const handleAddHoliday = async () => {
    if (!holidayName.trim() || !holidayDate.trim()) {
      Alert.alert("Missing Details", "Holiday name and date are both required.");
      return;
    }
    setSavingHoliday(true);
    try {
      const res = await addHoliday(holidayDate.trim(), holidayName.trim());
      if (res?.data?.ok) {
        setAddHolidayModalVisible(false);
        setHolidayName("");
        setHolidayDate("");
        loadData();
      } else {
        Alert.alert("Could Not Add", res?.data?.msg || "Please check the date and try again.");
      }
    } catch (e) {
      Alert.alert("Could Not Add", e?.response?.data?.msg || "Please check the date and try again.");
    }
    setSavingHoliday(false);
  };

  const handleDeleteHoliday = (holiday) => {
    Alert.alert("Remove Holiday", `Remove "${holiday.name}" from the company calendar?`, [
      { text: "Cancel", style: "cancel" },
      {
        text: "Remove",
        style: "destructive",
        onPress: async () => {
          try {
            const res = await deleteHoliday(holiday.id);
            if (res?.data?.ok) {
              setHolidays((prev) => prev.filter((h) => h.id !== holiday.id));
            } else {
              Alert.alert("Could Not Remove", res?.data?.msg || "Please try again.");
            }
          } catch (e) {
            Alert.alert("Could Not Remove", e?.response?.data?.msg || "Please try again.");
          }
        },
      },
    ]);
  };

  return (
    <LinearGradient colors={colors.screenGradient} style={styles.container}>
      <SafeAreaView style={{ flex: 1 }}>
        <AdminHeader
          title="Leaves & Holidays"
          onMenu={() => navigation.dispatch(DrawerActions.openDrawer())}
        />

        {/* Tab Switcher */}
        <View style={styles.tabsContainer}>
          <TouchableOpacity
            style={[styles.tab, activeTab === "leaves" && styles.activeTab]}
            onPress={() => setActiveTab("leaves")}
          >
            <Ionicons name="document-text-outline" size={16} color={activeTab === "leaves" ? "#FFFFFF" : "#5A6678"} />
            <Text style={[styles.tabText, activeTab === "leaves" && styles.activeTabText]}>Leave Requests</Text>
          </TouchableOpacity>

          <TouchableOpacity
            style={[styles.tab, activeTab === "compoff" && styles.activeTab]}
            onPress={() => setActiveTab("compoff")}
          >
            <Ionicons name="time-outline" size={16} color={activeTab === "compoff" ? "#FFFFFF" : "#5A6678"} />
            <Text style={[styles.tabText, activeTab === "compoff" && styles.activeTabText]}>Comp-Off</Text>
          </TouchableOpacity>

          <TouchableOpacity
            style={[styles.tab, activeTab === "holidays" && styles.activeTab]}
            onPress={() => setActiveTab("holidays")}
          >
            <Ionicons name="calendar-outline" size={16} color={activeTab === "holidays" ? "#FFFFFF" : "#5A6678"} />
            <Text style={[styles.tabText, activeTab === "holidays" && styles.activeTabText]}>Holidays</Text>
          </TouchableOpacity>
        </View>

        <ScrollView
          showsVerticalScrollIndicator={false}
          contentContainerStyle={styles.content}
          refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} colors={[colors.primary]} />}
        >
          {loading ? (
            <ActivityIndicator size="large" color="#0B5CAD" style={{ marginTop: 40 }} />
          ) : activeTab === "leaves" ? (
            <View>
              <Text style={styles.sectionHeader}>Pending Leave Approvals ({leaves.length})</Text>
              {leaves.length === 0 ? (
                <View style={styles.emptyCard}>
                  <Ionicons name="checkmark-circle-outline" size={44} color="#10B981" />
                  <Text style={styles.emptyTitle}>All Caught Up!</Text>
                  <Text style={styles.emptySub}>No pending leave requests requiring approval.</Text>
                </View>
              ) : (
                leaves.map((req, idx) => (
                  <View key={req.id || idx} style={styles.card}>
                    <View style={styles.cardHeader}>
                      <View>
                        <Text style={styles.empName}>{req.employee_name || req.name || `Employee #${req.employee_id}`}</Text>
                        <Text style={styles.leaveType}>{req.leave_type || "Casual Leave"} • {req.leave_date || req.date}</Text>
                      </View>
                      <View style={[styles.statusBadge, { backgroundColor: req.status === "Approved" ? "#DCFCE7" : req.status === "Rejected" ? "#FEE2E2" : "#FEF3C7" }]}>
                        <Text style={[styles.statusText, { color: req.status === "Approved" ? "#15803D" : req.status === "Rejected" ? "#B91C1C" : "#B45309" }]}>
                          {req.status || "Pending"}
                        </Text>
                      </View>
                    </View>

                    <Text style={styles.reasonText}>"{req.reason || "Personal reasons"}"</Text>

                    {req.status === "Pending" || !req.status ? (
                      <View style={styles.actionRow}>
                        <TouchableOpacity style={[styles.btn, styles.btnReject]} onPress={() => handleLeaveAction(req.id, "reject")}>
                          <Ionicons name="close" size={16} color="#DC2626" />
                          <Text style={styles.rejectText}>Reject</Text>
                        </TouchableOpacity>

                        <TouchableOpacity style={[styles.btn, styles.btnApprove]} onPress={() => handleLeaveAction(req.id, "approve")}>
                          <Ionicons name="checkmark" size={16} color="#FFFFFF" />
                          <Text style={styles.approveText}>Approve</Text>
                        </TouchableOpacity>
                      </View>
                    ) : null}
                  </View>
                ))
              )}
            </View>
          ) : activeTab === "compoff" ? (
            <View>
              <Text style={styles.sectionHeader}>Comp-Off Balances ({compoffs.length})</Text>
              {compoffs.length === 0 ? (
                <View style={styles.emptyCard}>
                  <Ionicons name="time-outline" size={44} color="#5A6678" />
                  <Text style={styles.emptyTitle}>No Balances</Text>
                  <Text style={styles.emptySub}>Comp-off is credited automatically from approved overtime -- there's no manual approval step.</Text>
                </View>
              ) : (
                compoffs.map((co, idx) => (
                  <View key={co.id || idx} style={styles.card}>
                    <View style={styles.cardHeader}>
                      <Text style={styles.empName}>{co.name || `Employee #${co.employee_id}`}</Text>
                      <Text style={styles.leaveType}>{co.department || "General"}</Text>
                    </View>
                    <View style={{ flexDirection: "row", gap: 18, marginTop: 6 }}>
                      <Text style={styles.reasonText}>Earned: {co.earned_days ?? 0}d</Text>
                      <Text style={styles.reasonText}>Used: {co.used_days ?? 0}d</Text>
                      <Text style={[styles.reasonText, { fontWeight: "800", color: "#0B5CAD" }]}>Balance: {co.balance_days ?? 0}d</Text>
                    </View>
                  </View>
                ))
              )}
            </View>
          ) : (
            <View>
              <View style={styles.holidayHeaderRow}>
                <Text style={styles.sectionHeader}>Company Holidays ({holidays.length})</Text>
                <TouchableOpacity style={styles.addBtn} onPress={() => setAddHolidayModalVisible(true)}>
                  <Ionicons name="add" size={18} color="#FFFFFF" />
                  <Text style={styles.addBtnText}>Add Holiday</Text>
                </TouchableOpacity>
              </View>

              {holidays.length === 0 ? (
                <View style={styles.emptyCard}>
                  <Ionicons name="calendar-outline" size={44} color={colors.employee} />
                  <Text style={styles.emptyTitle}>No Holidays Added</Text>
                  <Text style={styles.emptySub}>Add public holidays to the company calendar.</Text>
                </View>
              ) : (
                holidays.map((h, idx) => (
                  <View key={h.id || idx} style={styles.card}>
                    <View style={styles.cardHeader}>
                      <View style={{ flexDirection: "row", alignItems: "center" }}>
                        <View style={styles.dateCircle}>
                          <Ionicons name="gift-outline" size={20} color="#0B5CAD" />
                        </View>
                        <View style={{ marginLeft: 12 }}>
                          <Text style={styles.empName}>{h.name || h.title}</Text>
                          <Text style={styles.leaveType}>{h.date} • {h.type || "Public Holiday"}</Text>
                        </View>
                      </View>

                      <TouchableOpacity onPress={() => handleDeleteHoliday(h)}>
                        <Ionicons name="trash-outline" size={20} color={colors.danger} />
                      </TouchableOpacity>
                    </View>
                  </View>
                ))
              )}
            </View>
          )}
        </ScrollView>

        {/* Add Holiday Modal */}
        <Modal visible={addHolidayModalVisible} transparent animationType="slide">
          <View style={styles.modalBg}>
            <View style={styles.modalCard}>
              <Text style={styles.modalTitle}>Add Company Holiday</Text>

              <Text style={styles.inputLabel}>HOLIDAY NAME</Text>
              <TextInput
                style={styles.textInput}
                placeholder="e.g. Independence Day"
                value={holidayName}
                onChangeText={setHolidayName}
              />

              <Text style={styles.inputLabel}>DATE (YYYY-MM-DD)</Text>
              <TextInput
                style={styles.textInput}
                placeholder="2026-08-15"
                value={holidayDate}
                onChangeText={setHolidayDate}
              />

              <View style={styles.modalBtnRow}>
                <TouchableOpacity style={styles.modalCancelBtn} onPress={() => setAddHolidayModalVisible(false)}>
                  <Text style={styles.modalCancelText}>Cancel</Text>
                </TouchableOpacity>

                <TouchableOpacity style={styles.modalSubmitBtn} onPress={handleAddHoliday} disabled={savingHoliday}>
                  {savingHoliday ? (
                    <ActivityIndicator color="#FFFFFF" size="small" />
                  ) : (
                    <Text style={styles.modalSubmitText}>Add Holiday</Text>
                  )}
                </TouchableOpacity>
              </View>
            </View>
          </View>
        </Modal>
      </SafeAreaView>
    </LinearGradient>
  );
}

const makeStyles = (colors) => StyleSheet.create({
  container: { flex: 1 },
  tabsContainer: {
    flexDirection: "row",
    backgroundColor: colors.card,
    marginHorizontal: 16,
    marginTop: 12,
    borderRadius: 14,
    padding: 4,
    elevation: 3,
  },
  tab: {
    flex: 1,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    paddingVertical: 10,
    borderRadius: 10,
  },
  activeTab: { backgroundColor: "#0B5CAD" },
  tabText: { fontSize: 12, fontWeight: "600", color: "#5A6678", marginLeft: 6 },
  activeTabText: { color: "#FFFFFF" },
  content: { padding: 16 },
  sectionHeader: { fontSize: 14, fontWeight: "700", color: colors.text, marginBottom: 12 },
  card: {
    backgroundColor: colors.card,
    borderRadius: 16,
    padding: 16,
    marginBottom: 12,
    elevation: 2,
  },
  cardHeader: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  empName: { fontSize: 13, fontWeight: "700", color: colors.text },
  leaveType: { fontSize: 12, color: "#5A6678", marginTop: 2 },
  statusBadge: { paddingHorizontal: 10, paddingVertical: 4, borderRadius: 12 },
  statusText: { fontSize: 11, fontWeight: "700" },
  reasonText: { fontSize: 12, color: "#2E3A4A", fontStyle: "italic", marginTop: 10 },
  actionRow: { flexDirection: "row", justifyContent: "flex-end", marginTop: 14, gap: 10 },
  btn: { paddingHorizontal: 16, paddingVertical: 8, borderRadius: 10, flexDirection: "row", alignItems: "center" },
  btnReject: { backgroundColor: "#FEE2E2" },
  btnApprove: { backgroundColor: "#10B981" },
  rejectText: { color: "#DC2626", fontWeight: "700", fontSize: 12 },
  approveText: { color: "#FFFFFF", fontWeight: "700", fontSize: 12 },
  emptyCard: { backgroundColor: colors.card, padding: 32, borderRadius: 20, alignItems: "center", marginTop: 20 },
  emptyTitle: { fontSize: 14, fontWeight: "700", color: colors.text, marginTop: 12 },
  emptySub: { fontSize: 12, color: "#5A6678", textAlign: "center", marginTop: 4 },
  holidayHeaderRow: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  addBtn: { backgroundColor: "#0B5CAD", flexDirection: "row", alignItems: "center", paddingHorizontal: 12, paddingVertical: 6, borderRadius: 10 },
  addBtnText: { color: "#FFFFFF", fontSize: 12, fontWeight: "700", marginLeft: 4 },
  dateCircle: { width: 40, height: 40, borderRadius: 20, backgroundColor: colors.blueBg, justifyContent: "center", alignItems: "center" },
  modalBg: { flex: 1, backgroundColor: "rgba(15,23,42,0.75)", justifyContent: "center", padding: 20 },
  modalCard: { backgroundColor: colors.card, borderRadius: 20, padding: 20 },
  modalTitle: { fontSize: 15, fontWeight: "700", color: colors.text, marginBottom: 16 },
  inputLabel: { fontSize: 11, fontWeight: "700", color: "#5A6678", marginTop: 10, marginBottom: 4 },
  textInput: { backgroundColor: colors.background, borderWidth: 1, borderColor: colors.border, borderRadius: 10, padding: 12, fontSize: 13 },
  modalBtnRow: { flexDirection: "row", justifyContent: "flex-end", marginTop: 20, gap: 10 },
  modalCancelBtn: { paddingHorizontal: 16, paddingVertical: 10, borderRadius: 10, backgroundColor: "#EDF2F8" },
  modalCancelText: { color: "#5A6678", fontWeight: "600" },
  modalSubmitBtn: { paddingHorizontal: 16, paddingVertical: 10, borderRadius: 10, backgroundColor: "#0B5CAD" },
  modalSubmitText: { color: "#FFFFFF", fontWeight: "700" },
});
