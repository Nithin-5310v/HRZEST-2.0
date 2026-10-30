import React, { useState, useEffect } from "react";
import {
  ScrollView,
  StyleSheet,
  View,
  Text,
  TouchableOpacity,
  RefreshControl,
  ActivityIndicator,
  Alert,
} from "react-native";
import SafeAreaView from "../../components/SafeAreaView";
import { DrawerActions } from "@react-navigation/native";
import { LinearGradient } from "expo-linear-gradient";
import { Ionicons } from "@expo/vector-icons";

import AdminHeader from "../../components/admin/AdminHeader";
import AdminSearchBar from "../../components/admin/AdminSearchBar";
import SaasFilterSheet from "../../components/common/SaasFilterSheet";
import { fetchLeaveRequests, leaveAction } from "../../api/client";
import { useTheme } from "../../store/ThemeContext";

export default function LeaveRequestsScreen({ navigation }) {
  const { colors } = useTheme();
  const styles = React.useMemo(() => makeStyles(colors), [colors]);
  const [search, setSearch] = useState("");
  const [activeTab, setActiveTab] = useState("Pending");
  const [selectedLeaveType, setSelectedLeaveType] = useState("All");
  const [selectedSort, setSelectedSort] = useState("Newest First");
  const [filterModalVisible, setFilterModalVisible] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [loading, setLoading] = useState(true);
  const [requests, setRequests] = useState([]);

  const loadData = async () => {
    try {
      const res = await fetchLeaveRequests();
      const rawList = res?.data?.leaves || res?.data?.requests || [];
      if (Array.isArray(rawList)) {
        const formatted = rawList.map((item) => {
          let type = "Leave Application";
          let cleanReason = item.reason || "";
          if (cleanReason && cleanReason.startsWith("[")) {
            const match = cleanReason.match(/^\[(.*?)\]\s*(.*)$/);
            if (match) {
              type = match[1];
              cleanReason = match[2];
            }
          }
          const formattedStatus =
            item.status === "Declined" ? "Rejected" : item.status || "Pending";

          return {
            id: item.id,
            employee_id: item.employee_id,
            employee_name: item.name || item.employee_name || `Employee #${item.employee_id}`,
            leave_type: type,
            leave_date: item.leave_date || item.start_date || "Date Not Specified",
            start_date: item.leave_date || item.start_date || "N/A",
            end_date: item.leave_date || item.end_date || "N/A",
            reason: cleanReason || "Leave request submitted",
            status: formattedStatus,
            requested_at: item.requested_at,
          };
        });
        setRequests(formatted);
      } else {
        setRequests([]);
      }
    } catch (e) {
      setRequests([]);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  };

  useEffect(() => {
    loadData();
  }, []);

  const onRefresh = () => {
    setRefreshing(true);
    loadData();
  };

  const handleAction = async (id, actionType) => {
    const apiAction = actionType === "Approved" ? "Approved" : "Declined";
    try {
      const res = await leaveAction(id, apiAction);
      if (res?.data?.ok) {
        setRequests((prev) =>
          prev.map((r) => (r.id === id ? { ...r, status: actionType } : r))
        );
        Alert.alert("Success 🎉", `Leave request ${actionType.toLowerCase()} successfully.`);
      } else {
        Alert.alert("Failed", res?.data?.msg || `Could not mark leave request as ${actionType}.`);
      }
    } catch (e) {
      Alert.alert("Failed", e?.response?.data?.msg || `Could not mark leave request as ${actionType}. Check your connection.`);
    }
  };

  const hasActiveFilter = activeTab !== "Pending" || selectedLeaveType !== "All" || selectedSort !== "Newest First";

  const leaveTypes = ["All", "Casual Leave", "Sick Leave", "Earned Leave", "Comp-Off"];

  const filteredRequests = requests
    .filter((r) => {
      const matchesSearch =
        r.employee_name.toLowerCase().includes(search.toLowerCase()) ||
        r.leave_type.toLowerCase().includes(search.toLowerCase());
      const matchesTab = activeTab === "All" || r.status === activeTab;
      const matchesType = selectedLeaveType === "All" || r.leave_type === selectedLeaveType;
      return matchesSearch && matchesTab && matchesType;
    })
    .sort((a, b) => {
      if (selectedSort === "Oldest First") return a.id.localeCompare(b.id);
      if (selectedSort === "Name (A-Z)") return a.employee_name.localeCompare(b.employee_name);
      return b.id.localeCompare(a.id);
    });

  const pendingCount = requests.filter((r) => r.status === "Pending").length;
  const approvedCount = requests.filter((r) => r.status === "Approved").length;
  const rejectedCount = requests.filter((r) => r.status === "Rejected").length;

  return (
    <LinearGradient colors={colors.screenGradient} style={styles.container}>
      <SafeAreaView style={{ flex: 1 }}>
        <AdminHeader
          title="Approvals Hub"
          onMenu={() => navigation.dispatch(DrawerActions.openDrawer())}
        />

        <ScrollView
          showsVerticalScrollIndicator={false}
          contentContainerStyle={styles.content}
          refreshControl={
            <RefreshControl
              refreshing={refreshing}
              onRefresh={onRefresh}
              colors={[colors.primary]}
            />
          }
        >
          {/* Summary Stats Grid */}
          <View style={styles.statsGrid}>
            <View style={[styles.statCard, { borderLeftColor: colors.warning }]}>
              <Text style={styles.statNumber}>{pendingCount}</Text>
              <Text style={styles.statLabel}>Pending</Text>
            </View>
            <View style={[styles.statCard, { borderLeftColor: "#10B981" }]}>
              <Text style={styles.statNumber}>{approvedCount}</Text>
              <Text style={styles.statLabel}>Approved</Text>
            </View>
            <View style={[styles.statCard, { borderLeftColor: colors.danger }]}>
              <Text style={styles.statNumber}>{rejectedCount}</Text>
              <Text style={styles.statLabel}>Rejected</Text>
            </View>
          </View>

          {/* Search & Filter */}
          <AdminSearchBar
            value={search}
            onChangeText={setSearch}
            placeholder="Search leave requests..."
            onFilterPress={() => setFilterModalVisible(true)}
            hasActiveFilter={hasActiveFilter}
            onClear={() => setSearch("")}
          />

          {/* Status Tabs */}
          <View style={styles.tabsRow}>
            {["Pending", "Approved", "Rejected", "All"].map((tab) => (
              <TouchableOpacity
                key={tab}
                style={[styles.tab, activeTab === tab && styles.tabActive]}
                onPress={() => setActiveTab(tab)}
              >
                <Text style={[styles.tabText, activeTab === tab && styles.tabTextActive]}>
                  {tab}
                </Text>
              </TouchableOpacity>
            ))}
          </View>

          {loading ? (
            <ActivityIndicator size="large" color="#0B6E8E" style={{ marginTop: 30 }} />
          ) : (
            filteredRequests.map((item) => (
              <View key={item.id} style={styles.requestCard}>
                <View style={styles.cardHeader}>
                  <View style={styles.avatar}>
                    <Text style={styles.avatarText}>
                      {item.employee_name ? item.employee_name.charAt(0) : "E"}
                    </Text>
                  </View>
                  <View style={{ flex: 1, marginLeft: 12 }}>
                    <Text style={styles.empName}>{item.employee_name}</Text>
                    <Text style={styles.leaveType}>{item.leave_type}</Text>
                  </View>
                  <View
                    style={[
                      styles.statusPill,
                      item.status === "Approved"
                        ? styles.pillApproved
                        : item.status === "Rejected"
                        ? styles.pillRejected
                        : styles.pillPending,
                    ]}
                  >
                    <Text
                      style={[
                        styles.statusText,
                        item.status === "Approved"
                          ? styles.textApproved
                          : item.status === "Rejected"
                          ? styles.textRejected
                          : styles.textPending,
                      ]}
                    >
                      {item.status}
                    </Text>
                  </View>
                </View>

                <View style={styles.detailsBox}>
                  <View style={styles.detailRow}>
                    <Ionicons name="calendar-outline" size={16} color="#5A6A70" />
                    <Text style={styles.detailText}>
                      {item.start_date === item.end_date || !item.end_date || item.end_date === "N/A"
                        ? `Leave Date: ${item.leave_date}`
                        : `${item.start_date} → ${item.end_date}`}
                    </Text>
                  </View>
                  {item.reason && (
                    <View style={[styles.detailRow, { marginTop: 6 }]}>
                      <Ionicons name="chatbox-ellipses-outline" size={16} color="#5A6A70" />
                      <Text style={styles.detailText}>"{item.reason}"</Text>
                    </View>
                  )}
                </View>

                {item.status === "Pending" && (
                  <View style={styles.actionsRow}>
                    <TouchableOpacity
                      style={styles.rejectBtn}
                      onPress={() => handleAction(item.id, "Rejected")}
                    >
                      <Ionicons name="close-circle-outline" size={18} color={colors.danger} />
                      <Text style={styles.rejectBtnText}>Reject</Text>
                    </TouchableOpacity>

                    <TouchableOpacity
                      style={styles.approveBtn}
                      onPress={() => handleAction(item.id, "Approved")}
                    >
                      <Ionicons name="checkmark-circle-outline" size={18} color="#FFFFFF" />
                      <Text style={styles.approveBtnText}>Approve</Text>
                    </TouchableOpacity>
                  </View>
                )}
              </View>
            ))
          )}

          <View style={{ height: 110 }} />
        </ScrollView>

        {/* Professional SaaS Filter Modal */}
        <SaasFilterSheet
          visible={filterModalVisible}
          title="Filter Leave Requests"
          statusOptions={["All", "Pending", "Approved", "Rejected"]}
          selectedStatus={activeTab}
          onSelectStatus={setActiveTab}
          deptOptions={leaveTypes}
          selectedDept={selectedLeaveType}
          onSelectDept={setSelectedLeaveType}
          sortOptions={["Newest First", "Oldest First", "Name (A-Z)"]}
          selectedSort={selectedSort}
          onSelectSort={setSelectedSort}
          onApply={() => setFilterModalVisible(false)}
          onReset={() => {
            setActiveTab("Pending");
            setSelectedLeaveType("All");
            setSelectedSort("Newest First");
          }}
          onClose={() => setFilterModalVisible(false)}
        />
      </SafeAreaView>
    </LinearGradient>
  );
}

const makeStyles = (colors) => StyleSheet.create({
  container: { flex: 1 },
  content: { paddingHorizontal: 20, paddingTop: 10 },
  statsGrid: { flexDirection: "row", justifyContent: "space-between", marginBottom: 14 },
  statCard: {
    flex: 1,
    backgroundColor: colors.card,
    borderRadius: 16,
    padding: 14,
    marginRight: 8,
    borderLeftWidth: 4,
    elevation: 2,
    alignItems: "center",
  },
  statNumber: { fontSize: 18, fontWeight: "800", color: colors.text },
  statLabel: { fontSize: 11, color: "#5A6A70", fontWeight: "600", marginTop: 2 },
  tabsRow: { flexDirection: "row", backgroundColor: colors.border, borderRadius: 14, padding: 4, marginVertical: 12 },
  tab: { flex: 1, paddingVertical: 8, alignItems: "center", borderRadius: 10 },
  tabActive: { backgroundColor: colors.card, elevation: 2 },
  tabText: { fontSize: 12, fontWeight: "600", color: "#5A6A70" },
  tabTextActive: { color: "#0B6E8E", fontWeight: "800" },
  requestCard: {
    backgroundColor: colors.card,
    borderRadius: 20,
    padding: 16,
    marginBottom: 12,
    elevation: 2,
    borderWidth: 1,
    borderColor: colors.border,
  },
  cardHeader: { flexDirection: "row", alignItems: "center" },
  avatar: { width: 44, height: 44, borderRadius: 22, backgroundColor: colors.primaryLight, justifyContent: "center", alignItems: "center" },
  avatarText: { fontSize: 15, fontWeight: "800", color: "#0B6E8E" },
  empName: { fontSize: 13, fontWeight: "700", color: colors.text },
  leaveType: { fontSize: 12, color: "#0B6E8E", fontWeight: "600", marginTop: 2 },
  statusPill: { paddingHorizontal: 10, paddingVertical: 4, borderRadius: 12 },
  pillApproved: { backgroundColor: "#DCFCE7" },
  pillRejected: { backgroundColor: "#FEE2E2" },
  pillPending: { backgroundColor: "#FEF3C7" },
  statusText: { fontSize: 11, fontWeight: "700" },
  textApproved: { color: "#166534" },
  textRejected: { color: "#991B1B" },
  textPending: { color: "#B45309" },
  detailsBox: { backgroundColor: colors.background, borderRadius: 12, padding: 12, marginTop: 12 },
  detailRow: { flexDirection: "row", alignItems: "center" },
  detailText: { fontSize: 13, color: "#2E3D43", marginLeft: 8, flex: 1 },
  actionsRow: { flexDirection: "row", justifyContent: "space-between", marginTop: 14 },
  rejectBtn: { flex: 1, flexDirection: "row", justifyContent: "center", alignItems: "center", backgroundColor: "#FFF5F5", borderWidth: 1, borderColor: "#FEE2E2", paddingVertical: 10, borderRadius: 14, marginRight: 8 },
  rejectBtnText: { color: colors.danger, fontWeight: "700", marginLeft: 6 },
  approveBtn: { flex: 1, flexDirection: "row", justifyContent: "center", alignItems: "center", backgroundColor: "#10B981", paddingVertical: 10, borderRadius: 14 },
  approveBtnText: { color: "#FFFFFF", fontWeight: "700", marginLeft: 6 },
});