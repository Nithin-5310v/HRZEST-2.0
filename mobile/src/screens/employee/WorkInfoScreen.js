import React, { useState, useEffect } from "react";
import {
  View,
  Text,
  ScrollView,
  StyleSheet,
  ActivityIndicator,
} from "react-native";
import SafeAreaView from "../../components/SafeAreaView";
import { Ionicons } from "@expo/vector-icons";
import { useAuth } from "../../store/AuthContext";
import { useTheme } from "../../store/ThemeContext";
import { fetchEmployeeProfile } from "../../api/client";
import ProfileHeader from "../../components/profile/ProfileHeader";
import DetailCard from "../../components/profile/DetailCard";

// This screen is intentionally read-only: designation, department, manager
// and joining date are organization-assigned facts an employee shouldn't be
// able to self-edit even if a mobile write endpoint existed (it doesn't --
// only a session-based web route can change these). employment_type,
// work_location, manager_name and status aren't returned by
// /api/employee/profile at all, so they're left out rather than shown as
// invented "Full-Time / Headquarters / Engineering Lead / Active" defaults.
export default function WorkInfoScreen() {
  const { user } = useAuth();
  const { colors } = useTheme();
  const styles = React.useMemo(() => makeStyles(colors), [colors]);
  const [loading, setLoading] = useState(true);

  const [workInfo, setWorkInfo] = useState({
    employeeId: user?.employeeId || user?.employee_id || "",
    employeeName: user?.name || "",
    designation: user?.role || "Not Specified",
    department: user?.department || "Not Specified",
    joiningDate: "Not Specified",
    officeEmail: user?.email || "",
  });

  useEffect(() => {
    fetchEmployeeProfile()
      .then((res) => {
        if (res?.data?.ok && res?.data?.profile) {
          const p = res.data.profile;
          setWorkInfo({
            employeeId: p.employee_id || user?.employeeId || "",
            employeeName: p.name || user?.name || "",
            designation: p.role || user?.role || "Not Specified",
            department: p.department || user?.department || "Not Specified",
            joiningDate: p.join_date || "Not Specified",
            officeEmail: p.email || "",
          });
        }
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  return (
    <SafeAreaView style={styles.container}>
      <ProfileHeader title="Work Information" showBack />

      {loading ? (
        <View style={{ flex: 1, justifyContent: "center", alignItems: "center" }}>
          <ActivityIndicator size="large" color={colors.primary} />
        </View>
      ) : (
        <ScrollView showsVerticalScrollIndicator={false} contentContainerStyle={styles.content}>
          {/* Summary Card */}
          <View style={styles.summaryCard}>
            <View style={styles.avatar}>
              <Ionicons name="briefcase" size={24} color="#FFFFFF" />
            </View>

            <View style={styles.summaryText}>
              <Text style={styles.name}>{workInfo.employeeName}</Text>
              <Text style={styles.subText}>{workInfo.designation} • {workInfo.department}</Text>
            </View>
          </View>

          {/* Details */}
          <DetailCard icon="id-card-outline" label="Employee ID" value={workInfo.employeeId} />
          <DetailCard icon="briefcase-outline" label="Designation / Role" value={workInfo.designation} />
          <DetailCard icon="business-outline" label="Department" value={workInfo.department} />
          <DetailCard icon="calendar-outline" label="Date of Joining" value={workInfo.joiningDate} />
          <DetailCard icon="mail-outline" label="Office Email" value={workInfo.officeEmail} />

          <View style={styles.infoNoteCard}>
            <Ionicons name="information-circle-outline" size={18} color={colors.primary} style={{ marginRight: 8 }} />
            <Text style={styles.infoNoteText}>
              Official role assignments are managed by HR/Admin on the web dashboard.
            </Text>
          </View>

          <View style={{ height: 40 }} />
        </ScrollView>
      )}
    </SafeAreaView>
  );
}

const makeStyles = (colors) => StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.background },
  content: { padding: 18, paddingBottom: 130 },
  summaryCard: {
    flexDirection: "row",
    alignItems: "center",
    backgroundColor: colors.card,
    borderRadius: 20,
    padding: 16,
    marginBottom: 16,
    borderWidth: 1,
    borderColor: colors.border,
  },
  avatar: {
    width: 48,
    height: 48,
    borderRadius: 24,
    backgroundColor: colors.primary,
    justifyContent: "center",
    alignItems: "center",
    marginRight: 14,
  },
  summaryText: { flex: 1 },
  name: { fontSize: 14, fontWeight: "800", color: colors.text },
  subText: { fontSize: 12, fontWeight: "600", color: colors.textSecondary, marginTop: 2 },
  infoNoteCard: {
    flexDirection: "row",
    alignItems: "center",
    backgroundColor: colors.primaryLight,
    borderRadius: 14,
    padding: 14,
    marginTop: 14,
    borderWidth: 1,
    borderColor: "#DBEAFE",
  },
  infoNoteText: {
    flex: 1,
    fontSize: 12,
    color: "#1E3A8A",
    fontWeight: "600",
    lineHeight: 18,
  },
});
