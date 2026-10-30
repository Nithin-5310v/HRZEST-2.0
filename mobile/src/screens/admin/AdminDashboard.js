import React, { useState, useEffect } from "react";
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  TouchableOpacity,
  Modal,
  FlatList,
} from "react-native";
import SafeAreaView from "../../components/SafeAreaView";

import { LinearGradient } from "expo-linear-gradient";
import { Ionicons } from "@expo/vector-icons";

import AdminHeader from "../../components/admin/AdminHeader";
import AdminSearchBar from "../../components/admin/AdminSearchBar";
import DashboardHeroCard from "../../components/admin/DashboardHeroCard";
import AttendanceOverviewCard from "../../components/admin/AttendanceOverviewCard";
import QuickActionGrid from "../../components/admin/QuickActionGrid";
import PendingApprovalCard from "../../components/admin/PendingApprovalCard";
import AnalyticsOverviewCard from "../../components/admin/AnalyticsOverviewCard";
import AnnouncementCard from "../../components/admin/AnnouncementCard";
import RecentActivityList from "../../components/admin/RecentActivityList";
import RegistrationChecklistCard from "../../components/admin/RegistrationChecklistCard";
import AiChatModal from "../../components/AiChatModal";

import { fetchDashboard } from "../../api/client";
import { useAuth } from "../../store/AuthContext";
import { useTheme } from "../../store/ThemeContext";
import { mergeEmployeesWithLocal } from "../../utils/employeeStore";

export default function AdminDashboard({ navigation }) {
  const { user, updateUser } = useAuth();
  const { colors } = useTheme();
  const styles = React.useMemo(() => makeStyles(colors), [colors]);
  const [search, setSearch] = useState("");
  const [dashboardData, setDashboardData] = useState(null);
  const [totalEmpsCount, setTotalEmpsCount] = useState(0);
  const [announcementModalVisible, setAnnouncementModalVisible] = useState(false);
  const [activityModalVisible, setActivityModalVisible] = useState(false);
  const [showAiModal, setShowAiModal] = useState(false);
  const [verification, setVerification] = useState(null);

  useEffect(() => {
    loadDashboard();
  }, []);

  const loadDashboard = async () => {
    try {
      const [res, allEmps] = await Promise.all([
        fetchDashboard().catch(() => null),
        mergeEmployeesWithLocal([]).catch(() => []),
      ]);
      if (res?.data) {
        setDashboardData(res.data);
        if (res.data.company_name && updateUser) {
          updateUser({ company: res.data.company_name });
        }
      }
      setTotalEmpsCount(Math.max(res?.data?.total_employees || 0, allEmps.length));
    } catch (_) {}
  };

  const totalEmps = (totalEmpsCount || dashboardData?.total_employees) ?? dashboardData?.total ?? dashboardData?.employees_count ?? 0;
  const presentEmps = dashboardData?.present ?? dashboardData?.present_count ?? 0;
  const absentEmps = dashboardData?.absent ?? dashboardData?.absent_count ?? 0;
  const lateEmps = dashboardData?.late ?? dashboardData?.late_count ?? 0;
  const leaveEmps = dashboardData?.onLeave ?? dashboardData?.leave_count ?? 0;
  const pendingLeaves = dashboardData?.pending_leaves ?? 0;
  const pendingPayroll = dashboardData?.pending_payroll ?? 0;

  const attendancePct = totalEmps > 0 ? Math.round((presentEmps / totalEmps) * 100) + "%" : "0%";
  const payrollFormatted = dashboardData?.total_payroll ? "₹" + (dashboardData.total_payroll / 100000).toFixed(1) + "L" : "₹0";

  return (
    <LinearGradient colors={[colors.screenGradient[0], colors.screenGradient[1]]} style={styles.container}>
      <SafeAreaView style={styles.container}>
        <AdminHeader title="Admin Dashboard" navigation={navigation} />

        <ScrollView
          style={styles.container}
          contentContainerStyle={styles.content}
          showsVerticalScrollIndicator={false}
        >
          <DashboardHeroCard
            adminName={user?.name || dashboardData?.admin_name || "Administrator"}
            company={user?.company || dashboardData?.company_name || "Workforce Portal"}
            present={presentEmps}
            totalEmployees={totalEmps}
            attendance={attendancePct}
            payroll={payrollFormatted}
            profileImage={user?.logo || dashboardData?.company_logo}
            verification={verification}
          />

          <RegistrationChecklistCard navigation={navigation} onVerification={setVerification} />

          <AdminSearchBar
            value={search}
            onChangeText={setSearch}
            placeholder="Search employees or features..."
            onFilterPress={() => navigation.navigate("Employees")}
            onClear={() => setSearch("")}
          />

          <View style={styles.sectionSpacing} />

          <AttendanceOverviewCard
            present={presentEmps}
            absent={absentEmps}
            late={lateEmps}
            onLeave={leaveEmps}
            navigation={navigation}
          />

          <QuickActionGrid navigation={navigation} />

          {/* Pending Leave Approval Card */}
          <PendingApprovalCard
            title="Leave Requests"
            pending={pendingLeaves}
            subtitle={pendingLeaves > 0 ? "Requires your approval" : "No pending approvals"}
            icon="document-text-outline"
            color={colors.warning}
            background="#FEF3C7"
            onPress={() => navigation.navigate("LeaveRequests")}
          />

          {/* Pending Payroll Approval Card */}
          <PendingApprovalCard
            title="Payroll Approval"
            pending={pendingPayroll}
            subtitle={pendingPayroll > 0 ? "Waiting for verification" : "All payroll clear"}
            icon="wallet-outline"
            color="#7C3AED"
            background="#EDE9FE"
            onPress={() => navigation.navigate("Payroll")}
          />

          <AnalyticsOverviewCard navigation={navigation} />

          {/* Announcements Card with Working View All */}
          <AnnouncementCard
            announcements={dashboardData?.announcements || []}
            onViewAll={() => setAnnouncementModalVisible(true)}
          />

          <RecentActivityList
            onViewAll={() => setActivityModalVisible(true)}
          />

          <View style={styles.bottomSpacing} />
        </ScrollView>

        {/* Announcements Modal */}
        <Modal
          visible={announcementModalVisible}
          animationType="slide"
          transparent={true}
          onRequestClose={() => setAnnouncementModalVisible(false)}
        >
          <View style={styles.modalOverlay}>
            <View style={styles.modalContent}>
              <View style={styles.modalHeader}>
                <View style={{ flexDirection: "row", alignItems: "center" }}>
                  <Ionicons name="megaphone" size={22} color={colors.primary} />
                  <Text style={styles.modalTitle}>Company Announcements</Text>
                </View>
                <TouchableOpacity
                  style={styles.closeBtn}
                  onPress={() => setAnnouncementModalVisible(false)}
                >
                  <Ionicons name="close" size={20} color={colors.text} />
                </TouchableOpacity>
              </View>

              <FlatList
                data={dashboardData?.announcements || []}
                keyExtractor={(item, index) => item.id || String(index)}
                showsVerticalScrollIndicator={false}
                ListEmptyComponent={
                  <View style={{ padding: 24, alignItems: "center" }}>
                    <Ionicons name="notifications-off-outline" size={36} color={colors.textLight} />
                    <Text style={{ fontSize: 14, fontWeight: "600", color: "#5A6A70", marginTop: 8 }}>
                      No Company Announcements Yet
                    </Text>
                  </View>
                }
                renderItem={({ item }) => (
                  <View style={styles.announcementItem}>
                    <View style={[styles.itemIconBox, { backgroundColor: item.bg || colors.blueBg }]}>
                      <Ionicons name={item.icon || "megaphone-outline"} size={22} color={item.color || "#0B6E8E"} />
                    </View>
                    <View style={{ flex: 1, marginLeft: 14 }}>
                      <View style={styles.itemMetaRow}>
                        <Text style={styles.itemTitle}>{item.title}</Text>
                        {item.category ? (
                          <View style={[styles.categoryBadge, { backgroundColor: item.bg || colors.blueBg }]}>
                            <Text style={[styles.categoryText, { color: item.color || "#0B6E8E" }]}>
                              {item.category}
                            </Text>
                          </View>
                        ) : null}
                      </View>
                      <Text style={styles.itemMsg}>{item.message}</Text>
                      {item.date ? <Text style={styles.itemDate}>📅 {item.date}</Text> : null}
                    </View>
                  </View>
                )}
              />
            </View>
          </View>
        </Modal>

        {/* System Recent Activity & Audit Logs Modal */}
        <Modal
          visible={activityModalVisible}
          animationType="slide"
          transparent={true}
          onRequestClose={() => setActivityModalVisible(false)}
        >
          <View style={styles.modalOverlay}>
            <View style={styles.modalContent}>
              <View style={styles.modalHeader}>
                <View style={{ flexDirection: "row", alignItems: "center" }}>
                  <Ionicons name="time" size={22} color={colors.primary} />
                  <Text style={styles.modalTitle}>System Activity & Audit Logs</Text>
                </View>
                <TouchableOpacity
                  style={styles.closeBtn}
                  onPress={() => setActivityModalVisible(false)}
                >
                  <Ionicons name="close" size={20} color={colors.text} />
                </TouchableOpacity>
              </View>

              <FlatList
                data={dashboardData?.recent_activities || []}
                keyExtractor={(item, index) => item.id || String(index)}
                showsVerticalScrollIndicator={false}
                ListEmptyComponent={
                  <View style={{ padding: 24, alignItems: "center" }}>
                    <Ionicons name="time-outline" size={36} color={colors.textLight} />
                    <Text style={{ fontSize: 14, fontWeight: "600", color: "#5A6A70", marginTop: 8 }}>
                      No System Activities Logged Yet
                    </Text>
                  </View>
                }
                renderItem={({ item }) => (
                  <View style={styles.announcementItem}>
                    <View style={[styles.itemIconBox, { backgroundColor: "#ECF3F5" }]}>
                      <Ionicons name={item.icon || "list"} size={20} color={item.color || "#0B6E8E"} />
                    </View>
                    <View style={{ flex: 1, marginLeft: 14 }}>
                      <Text style={styles.itemTitle}>{item.title}</Text>
                      <Text style={styles.itemMsg}>{item.description || item.subtitle || "System activity event"}</Text>
                      {item.time ? <Text style={styles.itemDate}>⏱ {item.time}</Text> : null}
                    </View>
                  </View>
                )}
              />
            </View>
          </View>
        </Modal>

        {/* Floating AI HR Assistant Button */}
        <TouchableOpacity
          activeOpacity={0.9}
          style={{
            position: "absolute",
            bottom: 24,
            right: 20,
            width: 54,
            height: 54,
            borderRadius: 27,
            backgroundColor: colors.primary,
            justifyContent: "center",
            alignItems: "center",
            elevation: 8,
            shadowColor: "#0C3054",
            shadowOpacity: 0.4,
            shadowRadius: 10,
            shadowOffset: { width: 0, height: 4 },
          }}
          onPress={() => setShowAiModal(true)}
        >
          <Ionicons name="sparkles" size={24} color="#FFFFFF" />
        </TouchableOpacity>

        <AiChatModal visible={showAiModal} onClose={() => setShowAiModal(false)} />
      </SafeAreaView>
    </LinearGradient>
  );
}


const makeStyles = (colors) => StyleSheet.create({
  container: {
    flex: 1,
  },
  content: {
    paddingHorizontal: 20,
    paddingBottom: 120,
  },
  sectionSpacing: {
    height: 16,
  },
  bottomSpacing: {
    height: 40,
  },
  modalOverlay: {
    flex: 1,
    backgroundColor: "rgba(15, 23, 42, 0.5)",
    justifyContent: "flex-end",
  },
  modalContent: {
    backgroundColor: colors.card,
    borderTopLeftRadius: 26,
    borderTopRightRadius: 26,
    maxHeight: "82%",
    padding: 20,
  },
  modalHeader: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    marginBottom: 18,
    paddingBottom: 12,
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
  },
  modalTitle: {
    fontSize: 15,
    fontWeight: "800",
    color: colors.text,
    marginLeft: 10,
  },
  closeBtn: {
    width: 34,
    height: 34,
    borderRadius: 17,
    backgroundColor: "#ECF3F5",
    justifyContent: "center",
    alignItems: "center",
  },
  announcementItem: {
    flexDirection: "row",
    backgroundColor: colors.background,
    borderRadius: 18,
    padding: 16,
    marginBottom: 12,
    borderWidth: 1,
    borderColor: colors.border,
  },
  itemIconBox: {
    width: 44,
    height: 44,
    borderRadius: 14,
    justifyContent: "center",
    alignItems: "center",
  },
  itemMetaRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "flex-start",
  },
  itemTitle: {
    flex: 1,
    fontSize: 13,
    fontWeight: "700",
    color: colors.text,
    marginRight: 6,
  },
  categoryBadge: {
    paddingHorizontal: 8,
    paddingVertical: 2,
    borderRadius: 8,
  },
  categoryText: {
    fontSize: 10,
    fontWeight: "800",
  },
  itemMsg: {
    marginTop: 6,
    fontSize: 12,
    color: colors.textSecondary,
    lineHeight: 17,
  },
  itemDate: {
    marginTop: 8,
    fontSize: 11,
    color: colors.textLight,
    fontWeight: "600",
  },
});