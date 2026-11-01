import React, { useCallback, useState } from "react";
import {
  ScrollView,
  StyleSheet,
  View,
  Text,
  RefreshControl,
  ActivityIndicator,
} from "react-native";
import SafeAreaView from "../../components/SafeAreaView";
import { useFocusEffect } from "@react-navigation/native";

import AdminHeader from "../../components/admin/AdminHeader";
import PendingApprovalCard from "../../components/admin/PendingApprovalCard";
import { fetchLeaveRequests, fetchResignations, fetchOvertime } from "../../api/client";
import { summarizePendingCounts } from "../../utils/managerStats";
import { useTheme } from "../../store/ThemeContext";

// Manager's home screen -- a summary of the three things a manager account
// can actually act on server-side (blueprints/leave.py's
// _LEAVE_APPROVER_ROLES = ("admin", "hr", "manager") gates leave/
// resignation/overtime approval, and nothing else is manager-scoped today).
// Deliberately does not include Employees/Payroll/Settings/Seats & Billing
// -- those are admin/HR-only server-side and a manager token would just
// get 403s from them, so there's nothing to show here.
export default function ManagerDashboard({ navigation }) {
  const { colors } = useTheme();
  const styles = React.useMemo(() => makeStyles(colors), [colors]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [counts, setCounts] = useState({ leaves: 0, resignations: 0, overtime: 0, total: 0 });

  const loadCounts = useCallback(async () => {
    try {
      const [leavesRes, resignationsRes, overtimeRes] = await Promise.all([
        fetchLeaveRequests().catch(() => ({ data: {} })),
        fetchResignations().catch(() => ({ data: {} })),
        fetchOvertime().catch(() => ({ data: {} })),
      ]);
      setCounts(
        summarizePendingCounts({
          leaves: leavesRes?.data?.leaves,
          resignations: resignationsRes?.data?.resignations,
          overtime: overtimeRes?.data?.overtime,
        })
      );
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useFocusEffect(
    useCallback(() => {
      loadCounts();
    }, [loadCounts])
  );

  const onRefresh = () => {
    setRefreshing(true);
    loadCounts();
  };

  return (
    <SafeAreaView style={styles.container}>
      <AdminHeader title="Manager Dashboard" subtitle="TEAM LEAD" />
      <ScrollView
        contentContainerStyle={styles.content}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} />}
      >
        {loading ? (
          <ActivityIndicator size="large" color={colors.primary} style={{ marginTop: 40 }} />
        ) : (
          <>
            <Text style={styles.sectionTitle}>Pending Your Approval</Text>
            <PendingApprovalCard
              title="Leave Requests"
              pending={counts.leaves}
              subtitle="Requires your approval"
              icon="document-text-outline"
              color="#F59E0B"
              background="#FEF3C7"
              onPress={() => navigation.navigate("LeaveRequests")}
            />
            <PendingApprovalCard
              title="Resignations"
              pending={counts.resignations}
              subtitle="Requires your review"
              icon="exit-outline"
              color="#EF4444"
              background="#FEE2E2"
              onPress={() => navigation.navigate("Resignations")}
            />
            <PendingApprovalCard
              title="Overtime & Comp-Off"
              pending={counts.overtime}
              subtitle="Requires your approval"
              icon="time-outline"
              color="#2563EB"
              background="#DBEAFE"
              onPress={() => navigation.navigate("CompOff")}
            />
            {counts.total === 0 && (
              <View style={styles.emptyState}>
                <Text style={styles.emptyStateText}>You're all caught up -- nothing pending approval.</Text>
              </View>
            )}
          </>
        )}
      </ScrollView>
    </SafeAreaView>
  );
}

const makeStyles = (colors) =>
  StyleSheet.create({
    container: { flex: 1, backgroundColor: colors.background },
    content: { padding: 16, paddingBottom: 32 },
    sectionTitle: {
      fontSize: 13,
      fontWeight: "800",
      color: colors.textLight,
      letterSpacing: 0.8,
      textTransform: "uppercase",
      marginBottom: 12,
    },
    emptyState: {
      alignItems: "center",
      paddingVertical: 24,
    },
    emptyStateText: {
      color: colors.textLight,
      fontSize: 13,
      fontWeight: "600",
      textAlign: "center",
    },
  });
