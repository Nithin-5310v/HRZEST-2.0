import React from "react";
import {
  ScrollView,
  StyleSheet,
  RefreshControl,
} from "react-native";
import { LinearGradient } from "expo-linear-gradient";
import { useNavigation } from "@react-navigation/native";
import { fetchMyOvertime, fetchEmployeeProfile } from "../../api/client";
import { useAuth } from "../../store/AuthContext";
import { useTheme } from "../../store/ThemeContext";

import CompOffHeaderCard from "../../components/compoff/CompOffHeaderCard";
import CompOffStatsGrid from "../../components/compoff/CompOffStatsGrid";
import CompOffInfoCard from "../../components/compoff/CompOffInfoCard";
import OvertimeHistoryCard from "../../components/compoff/OvertimeHistoryCard";
import ProfileHeader from "../../components/profile/ProfileHeader";
import { reportError } from "../../utils/crashReporting";

export default function CompOffScreen() {
  const { user } = useAuth();
  const { colors } = useTheme();
  const styles = React.useMemo(() => makeStyles(colors), [colors]);
  const [loading, setLoading] = React.useState(false);
  const [refreshing, setRefreshing] = React.useState(false);
  const [overtimeRecords, setOvertimeRecords] = React.useState([]);
  const [profile, setProfile] = React.useState(null);

  const load = async () => {
    try {
      const [otRes, profRes] = await Promise.all([
        fetchMyOvertime(),
        fetchEmployeeProfile().catch(() => null),
      ]);
      if (otRes?.data?.ok && Array.isArray(otRes.data.records)) {
        setOvertimeRecords(otRes.data.records);
      }
      if (profRes?.data?.ok && profRes?.data?.profile) {
        setProfile(profRes.data.profile);
      }
    } catch (e) { reportError(e, { where: "CompOffScreen" }); }
  };

  React.useEffect(() => {
    setLoading(true);
    load().finally(() => setLoading(false));
  }, []);

  const onRefresh = async () => {
    setRefreshing(true);
    await load();
    setRefreshing(false);
  };

  // Real total from actual overtime records. No employee-facing API exposes
  // a comp-off day balance (only an admin-only endpoint does), so that
  // figure is shown as "--" rather than a fabricated number.
  const totalOtHours = (
    overtimeRecords.reduce((sum, r) => sum + (r.ot_minutes || 0), 0) / 60
  ).toFixed(1);

  // Map the real /api/employee/my_overtime shape onto what
  // OvertimeHistoryCard renders. It also displays "Comp-off Earned" and
  // "Approved By" -- neither exists in this API response, so those cells
  // are left blank rather than filled with invented values.
  const historyItems = overtimeRecords.map((r) => ({
    ...r,
    day: r.date ? new Date(r.date).toLocaleDateString("en-US", { weekday: "short" }) : "",
    hours: r.ot_minutes ? (r.ot_minutes / 60).toFixed(1) : "0.0",
  }));

  return (
  <LinearGradient
    colors={colors.screenGradient}
    style={styles.container}
  >

    <ProfileHeader title="Comp-Off Requests" subtitle="EMPLOYEE PORTAL" />

    <ScrollView
      showsVerticalScrollIndicator={false}
      contentContainerStyle={styles.content}
      refreshControl={
        <RefreshControl refreshing={refreshing} onRefresh={onRefresh} />
      }
    >

      <CompOffHeaderCard
        employeeName={profile?.name || user?.name}
        designation={profile?.role}
        department={profile?.department}
        availableDays="--"
      />

      <CompOffStatsGrid
        otHours={totalOtHours}
        availableDays="--"
        usedDays="--"
        earnedDays="--"
      />

      <CompOffInfoCard />

      <OvertimeHistoryCard
        records={historyItems}
      />

    </ScrollView>

  </LinearGradient>
);
}

const makeStyles = (colors) => StyleSheet.create({
  container: {
    flex: 1,
  },

  content: {
  paddingHorizontal: 18,
  paddingTop: 0,
  paddingBottom: 120,
},
});
