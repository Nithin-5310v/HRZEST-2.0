import React, { useState, useCallback } from "react";
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
import { LinearGradient } from "expo-linear-gradient";
import { Ionicons } from "@expo/vector-icons";
import { useFocusEffect } from "@react-navigation/native";

import AdminHeader from "../../components/admin/AdminHeader";
import AdminSearchBar from "../../components/admin/AdminSearchBar";
import SaasFilterSheet from "../../components/common/SaasFilterSheet";
import { fetchResignations, resignationAction } from "../../api/client";
import { useTheme } from "../../store/ThemeContext";

export default function ResignationsScreen() {
  const { colors } = useTheme();
  const styles = React.useMemo(() => makeStyles(colors), [colors]);
  const [search, setSearch] = useState("");
  const [activeTab, setActiveTab] = useState("Pending");
  const [selectedSort, setSelectedSort] = useState("Newest First");
  const [filterModalVisible, setFilterModalVisible] = useState(false);
  const [resignations, setResignations] = useState([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [actingId, setActingId] = useState(null);

  const loadData = async () => {
    try {
      const res = await fetchResignations();
      if (res && res.data && Array.isArray(res.data.resignations)) {
        setResignations(res.data.resignations);
      } else {
        setResignations([]);
      }
    } catch (_) {
      setResignations([]);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  };

  useFocusEffect(
    useCallback(() => {
      loadData();
    }, [])
  );

  const handleAction = (rid, action) => {
    Alert.alert(
      action === "Accepted" ? "Accept Resignation" : "Decline Resignation",
      `Are you sure you want to ${action === "Accepted" ? "accept" : "decline"} this resignation request?`,
      [
        { text: "Cancel", style: "cancel" },
        {
          text: action,
          style: action === "Accepted" ? "default" : "destructive",
          onPress: async () => {
            setActingId(rid);
            try {
              await resignationAction(rid, action);
              Alert.alert("Success", `Resignation ${action.toLowerCase()} successfully.`);
              await loadData();
            } catch (_) {
              setResignations((prev) =>
                prev.map((r) => (r.id === rid ? { ...r, status: action } : r))
              );
            } finally {
              setActingId(null);
            }
          },
        },
      ]
    );
  };

  const hasActiveFilter = activeTab !== "Pending" || selectedSort !== "Newest First";

  const filtered = resignations
    .filter((r) => {
      const name = (r.name || r.employee_name || "").toLowerCase();
      const empId = (r.employee_id || "").toLowerCase();
      const reason = (r.reason || "").toLowerCase();
      const q = search.toLowerCase();
      const matchesSearch = name.includes(q) || empId.includes(q) || reason.includes(q);

      const matchesTab =
        activeTab === "All" ||
        (activeTab === "Pending" && (r.status === "Pending" || !r.status)) ||
        (activeTab === "Accepted" && (r.status === "Accepted" || r.status === "Approved")) ||
        (activeTab === "Declined" && (r.status === "Declined" || r.status === "Rejected"));

      return matchesSearch && matchesTab;
    })
    .sort((a, b) => {
      if (selectedSort === "Oldest First") return a.id.localeCompare(b.id);
      if (selectedSort === "Name (A-Z)") return (a.name || a.employee_name || "").localeCompare(b.name || b.employee_name || "");
      return b.id.localeCompare(a.id);
    });

  const pendingCount = resignations.filter((r) => r.status === "Pending" || !r.status).length;
  const acceptedCount = resignations.filter((r) => r.status === "Accepted" || r.status === "Approved").length;
  const declinedCount = resignations.filter((r) => r.status === "Declined" || r.status === "Rejected").length;

  const renderStatusChip = (status) => {
    const isAccepted = status === "Accepted" || status === "Approved";
    const isDeclined = status === "Declined" || status === "Rejected";

    let bg = "#FEF3C7";
    let color = "#D97706";
    let icon = "time-outline";
    let label = "Pending";

    if (isAccepted) {
      bg = "#D1FAE5";
      color = "#059669";
      icon = "checkmark-circle-outline";
      label = "Accepted";
    } else if (isDeclined) {
      bg = "#FEE2E2";
      color = "#DC2626";
      icon = "close-circle-outline";
      label = "Declined";
    }

    return (
      <View style={[styles.statusChip, { backgroundColor: bg }]}>
        <Ionicons name={icon} size={12} color={color} style={{ marginRight: 4 }} />
        <Text style={[styles.statusText, { color }]}>{label}</Text>
      </View>
    );
  };

  return (
    <LinearGradient colors={colors.screenGradient} style={styles.container}>
      <SafeAreaView style={{ flex: 1 }}>
        <AdminHeader title="Resignations" subtitle="OFFBOARDING" />

        <ScrollView
          showsVerticalScrollIndicator={false}
          contentContainerStyle={styles.content}
          refreshControl={
            <RefreshControl
              refreshing={refreshing}
              onRefresh={() => {
                setRefreshing(true);
                loadData();
              }}
              tintColor="#0B6E8E"
            />
          }
        >
          {/* Stats Metrics Cards Row */}
          <View style={styles.statsRow}>
            <View style={[styles.statCard, { borderLeftColor: colors.warning }]}>
              <Text style={styles.statNumber}>{pendingCount}</Text>
              <Text style={styles.statLabel}>Pending</Text>
            </View>

            <View style={[styles.statCard, { borderLeftColor: "#10B981" }]}>
              <Text style={styles.statNumber}>{acceptedCount}</Text>
              <Text style={styles.statLabel}>Accepted</Text>
            </View>

            <View style={[styles.statCard, { borderLeftColor: colors.danger }]}>
              <Text style={styles.statNumber}>{declinedCount}</Text>
              <Text style={styles.statLabel}>Declined</Text>
            </View>
          </View>

          {/* Search Bar */}
          <AdminSearchBar
            value={search}
            onChangeText={setSearch}
            placeholder="Search by name, ID or reason..."
            onFilterPress={() => setFilterModalVisible(true)}
            hasActiveFilter={hasActiveFilter}
            onClear={() => setSearch("")}
          />

          {/* Segment Filter Tabs */}
          <View style={styles.tabContainer}>
            {["Pending", "Accepted", "Declined", "All"].map((tab) => {
              const active = activeTab === tab;
              return (
                <TouchableOpacity
                  key={tab}
                  activeOpacity={0.7}
                  style={[styles.tabButton, active && styles.tabButtonActive]}
                  onPress={() => setActiveTab(tab)}
                >
                  <Text style={[styles.tabText, active && styles.tabTextActive]}>
                    {tab}
                  </Text>
                </TouchableOpacity>
              );
            })}
          </View>

          {/* Loading Indicator */}
          {loading ? (
            <View style={styles.loadingBox}>
              <ActivityIndicator size="large" color="#0B6E8E" />
              <Text style={styles.loadingText}>Loading requests...</Text>
            </View>
          ) : filtered.length === 0 ? (
            <View style={styles.emptyCard}>
              <Ionicons name="document-text-outline" size={48} color={colors.textLight} />
              <Text style={styles.emptyTitle}>No Resignations Found</Text>
              <Text style={styles.emptySubtitle}>
                {search ? "No requests match your search criteria." : "There are currently no resignation requests."}
              </Text>
            </View>
          ) : (
            filtered.map((item) => {
              const isPending = item.status === "Pending" || !item.status;
              const empName = item.name || item.employee_name || "Employee";
              const empId = item.employee_id || "EMP";

              return (
                <View key={item.id} style={styles.card}>
                  {/* Card Top Header */}
                  <View style={styles.cardHeader}>
                    <View style={styles.userGroup}>
                      <View style={styles.avatarCircle}>
                        <Ionicons name="person" size={18} color="#0B6E8E" />
                      </View>
                      <View style={{ flex: 1 }}>
                        <Text style={styles.empName}>{empName}</Text>
                        <Text style={styles.empIdBadge}>{empId}</Text>
                      </View>
                    </View>
                    {renderStatusChip(item.status)}
                  </View>

                  <View style={styles.divider} />

                  {/* Card Details */}
                  <View style={styles.detailRow}>
                    <View style={styles.detailIcon}>
                      <Ionicons name="calendar-outline" size={16} color="#DC2626" />
                    </View>
                    <View style={{ flex: 1 }}>
                      <Text style={styles.detailLabel}>Last Working Day</Text>
                      <Text style={styles.detailValueDate}>
                        {item.last_working_day || "Not specified"}
                      </Text>
                    </View>
                  </View>

                  {!!item.reason && (
                    <View style={styles.reasonBox}>
                      <Text style={styles.reasonLabel}>REASON</Text>
                      <Text style={styles.reasonText}>{item.reason}</Text>
                    </View>
                  )}

                  {!!item.requested_at && (
                    <View style={styles.submittedRow}>
                      <Ionicons name="time-outline" size={14} color="#5A6A70" />
                      <Text style={styles.submittedText}>
                        Submitted on {item.requested_at?.slice(0, 10)}
                      </Text>
                    </View>
                  )}

                  {/* Action Buttons for Pending */}
                  {isPending && (
                    <View style={styles.actionRow}>
                      <TouchableOpacity
                        activeOpacity={0.8}
                        style={[styles.btn, styles.btnAccept]}
                        disabled={actingId === item.id}
                        onPress={() => handleAction(item.id, "Accepted")}
                      >
                        {actingId === item.id ? (
                          <ActivityIndicator size="small" color="#FFFFFF" />
                        ) : (
                          <>
                            <Ionicons name="checkmark-circle" size={18} color="#FFFFFF" style={{ marginRight: 6 }} />
                            <Text style={styles.btnText}>Accept</Text>
                          </>
                        )}
                      </TouchableOpacity>

                      <TouchableOpacity
                        activeOpacity={0.8}
                        style={[styles.btn, styles.btnDecline]}
                        disabled={actingId === item.id}
                        onPress={() => handleAction(item.id, "Declined")}
                      >
                        <Ionicons name="close-circle" size={18} color="#FFFFFF" style={{ marginRight: 6 }} />
                        <Text style={styles.btnText}>Decline</Text>
                      </TouchableOpacity>
                    </View>
                  )}
                </View>
              );
            })
          )}
        </ScrollView>

        {/* Professional SaaS Filter Modal */}
        <SaasFilterSheet
          visible={filterModalVisible}
          title="Filter Resignation Requests"
          statusOptions={["All", "Pending", "Accepted", "Declined"]}
          selectedStatus={activeTab}
          onSelectStatus={setActiveTab}
          sortOptions={["Newest First", "Oldest First", "Name (A-Z)"]}
          selectedSort={selectedSort}
          onSelectSort={setSelectedSort}
          onApply={() => setFilterModalVisible(false)}
          onReset={() => {
            setActiveTab("Pending");
            setSelectedSort("Newest First");
          }}
          onClose={() => setFilterModalVisible(false)}
        />
      </SafeAreaView>
    </LinearGradient>
  );
}

const makeStyles = (colors) => StyleSheet.create({
  container: {
    flex: 1,
  },
  content: {
    padding: 16,
    paddingBottom: 90,
  },
  statsRow: {
    flexDirection: "row",
    gap: 10,
    marginBottom: 16,
  },
  statCard: {
    flex: 1,
    backgroundColor: colors.card,
    borderRadius: 14,
    padding: 12,
    borderLeftWidth: 4,
    shadowColor: "#132026",
    shadowOpacity: 0.04,
    shadowRadius: 6,
    shadowOffset: { width: 0, height: 2 },
    elevation: 2,
  },
  statNumber: {
    fontSize: 18,
    fontWeight: "800",
    color: colors.text,
  },
  statLabel: {
    fontSize: 11,
    fontWeight: "600",
    color: "#5A6A70",
    marginTop: 2,
  },
  tabContainer: {
    flexDirection: "row",
    backgroundColor: colors.border,
    borderRadius: 12,
    padding: 3,
    marginBottom: 16,
  },
  tabButton: {
    flex: 1,
    paddingVertical: 8,
    alignItems: "center",
    borderRadius: 9,
  },
  tabButtonActive: {
    backgroundColor: colors.card,
    shadowColor: "#000",
    shadowOpacity: 0.06,
    shadowRadius: 4,
    elevation: 2,
  },
  tabText: {
    fontSize: 12,
    fontWeight: "600",
    color: "#5A6A70",
  },
  tabTextActive: {
    color: colors.text,
    fontWeight: "700",
  },
  loadingBox: {
    padding: 40,
    alignItems: "center",
  },
  loadingText: {
    marginTop: 10,
    fontSize: 12,
    color: "#5A6A70",
  },
  emptyCard: {
    backgroundColor: colors.card,
    borderRadius: 16,
    padding: 36,
    alignItems: "center",
    borderWidth: 1,
    borderColor: colors.border,
    marginTop: 10,
  },
  emptyTitle: {
    fontSize: 14,
    fontWeight: "700",
    color: colors.text,
    marginTop: 12,
  },
  emptySubtitle: {
    fontSize: 12,
    color: "#5A6A70",
    textAlign: "center",
    marginTop: 4,
  },
  card: {
    backgroundColor: colors.card,
    borderRadius: 16,
    padding: 16,
    marginBottom: 14,
    borderWidth: 1,
    borderColor: colors.border,
    shadowColor: "#132026",
    shadowOpacity: 0.04,
    shadowRadius: 8,
    shadowOffset: { width: 0, height: 3 },
    elevation: 2,
  },
  cardHeader: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
  },
  userGroup: {
    flexDirection: "row",
    alignItems: "center",
    flex: 1,
    marginRight: 10,
  },
  avatarCircle: {
    width: 38,
    height: 38,
    borderRadius: 19,
    backgroundColor: colors.blueBg,
    justifyContent: "center",
    alignItems: "center",
    marginRight: 10,
  },
  empName: {
    fontSize: 13,
    fontWeight: "700",
    color: colors.text,
  },
  empIdBadge: {
    fontSize: 11,
    fontWeight: "600",
    color: "#5A6A70",
    marginTop: 1,
  },
  statusChip: {
    flexDirection: "row",
    alignItems: "center",
    paddingHorizontal: 8,
    paddingVertical: 4,
    borderRadius: 8,
  },
  statusText: {
    fontSize: 11,
    fontWeight: "700",
  },
  divider: {
    height: 1,
    backgroundColor: "#ECF3F5",
    marginVertical: 12,
  },
  detailRow: {
    flexDirection: "row",
    alignItems: "center",
    marginBottom: 10,
  },
  detailIcon: {
    width: 28,
    height: 28,
    borderRadius: 8,
    backgroundColor: "#FEE2E2",
    justifyContent: "center",
    alignItems: "center",
    marginRight: 10,
  },
  detailLabel: {
    fontSize: 11,
    color: "#5A6A70",
    fontWeight: "500",
  },
  detailValueDate: {
    fontSize: 14,
    fontWeight: "700",
    color: "#DC2626",
    marginTop: 1,
  },
  reasonBox: {
    backgroundColor: colors.background,
    borderRadius: 10,
    padding: 10,
    marginBottom: 10,
    borderWidth: 1,
    borderColor: "#ECF3F5",
  },
  reasonLabel: {
    fontSize: 9,
    fontWeight: "800",
    color: colors.textLight,
    letterSpacing: 0.6,
    marginBottom: 2,
  },
  reasonText: {
    fontSize: 13,
    color: "#2E3D43",
    lineHeight: 18,
  },
  submittedRow: {
    flexDirection: "row",
    alignItems: "center",
    marginBottom: 12,
    gap: 4,
  },
  submittedText: {
    fontSize: 11,
    color: "#5A6A70",
  },
  actionRow: {
    flexDirection: "row",
    gap: 10,
    marginTop: 4,
  },
  btn: {
    flex: 1,
    height: 40,
    borderRadius: 10,
    flexDirection: "row",
    justifyContent: "center",
    alignItems: "center",
  },
  btnAccept: {
    backgroundColor: "#16A34A",
  },
  btnDecline: {
    backgroundColor: "#DC2626",
  },
  btnText: {
    color: "#FFFFFF",
    fontSize: 13,
    fontWeight: "700",
  },
});
