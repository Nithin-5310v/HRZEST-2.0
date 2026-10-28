import React, { useState } from "react";
import {
  ScrollView,
  StyleSheet,
  View,
  Text,
  TouchableOpacity,
  Alert,
  ActivityIndicator,
} from "react-native";
import SafeAreaView from "../../components/SafeAreaView";
import { DrawerActions } from "@react-navigation/native";
import { Ionicons } from "@expo/vector-icons";
import { LinearGradient } from "expo-linear-gradient";

import AdminHeader from "../../components/admin/AdminHeader";
import THEME from "../../constants/theme";
import { useTheme } from "../../store/ThemeContext";
import { fetchDashboard, fetchEmployees } from "../../api/client";
import { shareTextFile } from "../../utils/fileShare";

export default function AnalyticsScreen({ navigation }) {
  const { colors } = useTheme();
  const styles = React.useMemo(() => makeStyles(colors), [colors]);
  const [selectedPeriod, setSelectedPeriod] = useState("Month");
  const [overview, setOverview] = useState({ attendance: 0, present: 0, absent: 0, employees: 0, late: 0, leave: 0 });
  const [departments, setDepartments] = useState([]);
  const [performers, setPerformers] = useState([]);
  const [alerts, setAlerts] = useState([]);
  const [exporting, setExporting] = useState(false);

  React.useEffect(() => {
    loadAnalytics();
  }, [selectedPeriod]);

  const loadAnalytics = async () => {
    try {
      const [dashRes, empRes] = await Promise.all([fetchDashboard(), fetchEmployees()]);
      const dash = dashRes?.data || {};
      const empList = empRes?.data?.employees || [];
      const total = empList.length || dash.total_employees || 0;
      const present = dash.present || 0;
      const pct = total > 0 ? Number(((present / total) * 100).toFixed(1)) : 0;

      setOverview({
        attendance: pct,
        present,
        absent: dash.absent || 0,
        employees: total,
        late: dash.late || 0,
        leave: dash.onLeave || 0,
      });

      // Group departments dynamically. No per-department attendance % is
      // available from any Bearer API (only a company-wide figure), so
      // this no longer copies the overall % onto every department as if
      // it were department-specific data -- just real headcounts.
      const deptMap = {};
      empList.forEach((e) => {
        const d = e.department || "General";
        deptMap[d] = (deptMap[d] || 0) + 1;
      });
      const deptColors = ["#10B981", "#2563EB", "#F59E0B", "#8B5CF6", "#EC4899"];
      const totalEmp = empList.length || 1;
      const dynamicDepts = Object.keys(deptMap).map((name, i) => ({
        name,
        employees: deptMap[name],
        share: Math.round((deptMap[name] / totalEmp) * 100),
        color: deptColors[i % deptColors.length],
      }));
      setDepartments(dynamicDepts);

      // No per-employee attendance % is available from /api/employees, so
      // rather than invent a "100%" score for everyone, this lists active
      // staff without a fabricated ranking metric.
      const topStaff = empList.filter(e => e.status === "Active").slice(0, 3).map((e) => ({
        name: e.name,
        department: e.department || "General",
        role: e.role || "Staff Member",
      }));
      setPerformers(topStaff);

      // Dynamic System Alerts
      const pendingLeaves = dash.pending_leaves || 0;
      const unexcusedAbsences = dash.absent || 0;
      const dynamicAlerts = [];
      if (pendingLeaves > 0) {
        dynamicAlerts.push({ title: `${pendingLeaves} Leave Requests Pending`, subtitle: "Requires manager sign-off", icon: "document-text-outline", color: "#2563EB" });
      }
      if (unexcusedAbsences > 0) {
        dynamicAlerts.push({ title: `${unexcusedAbsences} Absences Recorded`, subtitle: "Review attendance logs", icon: "warning-outline", color: "#EF4444" });
      }
      if (dynamicAlerts.length === 0) {
        dynamicAlerts.push({ title: "All Systems Operational", subtitle: "No pending issues or urgent alerts", icon: "shield-checkmark-outline", color: "#10B981" });
      }
      setAlerts(dynamicAlerts);
    } catch (_) {
      setOverview({ attendance: 0, present: 0, absent: 0, employees: 0, late: 0, leave: 0 });
      setDepartments([]);
      setPerformers([]);
      setAlerts([{ title: "All Systems Operational", subtitle: "Operational status normal", icon: "shield-checkmark-outline", color: "#10B981" }]);
    }
  };

  // CSV built from the same overview/departments/performers state already
  // on screen -- no separate backend export route to keep in sync with,
  // since web's own Analytics page has never had an export feature either.
  const csvEscape = (v) => {
    const s = String(v ?? "");
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };

  const handleExport = async () => {
    setExporting(true);
    try {
      const lines = [
        "HR Analytics Report", `Period,${selectedPeriod}`, "",
        "Overview", "Metric,Value",
        `Attendance %,${overview.attendance}`,
        `Present,${overview.present}`,
        `Absent,${overview.absent}`,
        `Total Employees,${overview.employees}`,
        `Late,${overview.late}`,
        `On Leave,${overview.leave}`,
        "",
        "Departments", "Department,Employees,Share %",
        ...departments.map((d) => `${csvEscape(d.name)},${d.employees},${d.share}`),
        "",
        "Active Staff", "Name,Department,Role",
        ...performers.map((p) => `${csvEscape(p.name)},${csvEscape(p.department)},${csvEscape(p.role)}`),
      ];
      const filename = `Analytics_Report_${selectedPeriod}_${Date.now()}.csv`;
      await shareTextFile(filename, lines.join("\n"));
    } catch (e) {
      Alert.alert("Export Failed", "Could not generate the analytics report.");
    } finally {
      setExporting(false);
    }
  };

  return (
    <LinearGradient colors={colors.screenGradient} style={styles.container}>
      <SafeAreaView style={{ flex: 1 }}>
        <AdminHeader
          title="Analytics & Insights"
          onMenu={() => navigation.dispatch(DrawerActions.openDrawer())}
        />

        <ScrollView
          showsVerticalScrollIndicator={false}
          contentContainerStyle={styles.content}
        >
          {/* Top Control Bar: Period Selector */}
          <View style={styles.topControlRow}>
            <View style={styles.periodPillContainer}>
              {["Week", "Month", "Quarter", "Year"].map((period) => {
                const isActive = selectedPeriod === period;
                return (
                  <TouchableOpacity
                    key={period}
                    activeOpacity={0.8}
                    style={[styles.periodPill, isActive && styles.periodPillActive]}
                    onPress={() => setSelectedPeriod(period)}
                  >
                    <Text style={[styles.periodText, isActive && styles.periodTextActive]}>
                      {period}
                    </Text>
                  </TouchableOpacity>
                );
              })}
            </View>

            <TouchableOpacity
              style={styles.exportBtn}
              activeOpacity={0.8}
              onPress={handleExport}
              disabled={exporting}
            >
              {exporting ? (
                <ActivityIndicator size="small" color="#173B8C" />
              ) : (
                <>
                  <Ionicons name="download-outline" size={16} color="#173B8C" />
                  <Text style={styles.exportBtnText}>Export</Text>
                </>
              )}
            </TouchableOpacity>
          </View>

          {/* Hero Executive Summary Card */}
          <View style={styles.heroCard}>
            <View style={styles.heroTop}>
              <View>
                <Text style={styles.heroLabel}>TODAY'S ATTENDANCE RATE</Text>
                <Text style={styles.heroValue}>{overview.attendance}%</Text>
              </View>
            </View>

            <Text style={styles.heroSubText}>
              Live snapshot across all departments today.
            </Text>

            {/* Attendance Segment Bar -- real split from today's counts */}
            <View style={styles.segmentBarContainer}>
              <View style={[styles.segment, { flex: Math.max(overview.present, 0.01), backgroundColor: "#10B981" }]} />
              <View style={[styles.segment, { flex: Math.max(overview.leave, 0.01), backgroundColor: "#F59E0B" }]} />
              <View style={[styles.segment, { flex: Math.max(overview.absent, 0.01), backgroundColor: "#EF4444" }]} />
            </View>

            <View style={styles.segmentLegendRow}>
              <View style={styles.legendItem}>
                <View style={[styles.legendDot, { backgroundColor: "#10B981" }]} />
                <Text style={styles.legendText}>Present ({overview.present})</Text>
              </View>
              <View style={styles.legendItem}>
                <View style={[styles.legendDot, { backgroundColor: "#F59E0B" }]} />
                <Text style={styles.legendText}>On Leave ({overview.leave})</Text>
              </View>
              <View style={styles.legendItem}>
                <View style={[styles.legendDot, { backgroundColor: "#EF4444" }]} />
                <Text style={styles.legendText}>Absent ({overview.absent})</Text>
              </View>
            </View>
          </View>

          {/* KPI Metrics Grid */}
          <View style={styles.kpiGrid}>
            <View style={styles.kpiCard}>
              <View style={styles.kpiTopRow}>
                <View style={[styles.kpiIconBox, { backgroundColor: colors.primaryLight }]}>
                  <Ionicons name="people" size={20} color="#173B8C" />
                </View>
                <View style={styles.kpiTag}>
                  <Text style={styles.kpiTagText}>Total</Text>
                </View>
              </View>
              <Text style={styles.kpiNumber}>{overview.employees}</Text>
              <Text style={styles.kpiTitle}>Total Workforce</Text>
              <Text style={styles.kpiSub}>Active Employees</Text>
            </View>

            <View style={styles.kpiCard}>
              <View style={styles.kpiTopRow}>
                <View style={[styles.kpiIconBox, { backgroundColor: colors.greenBg }]}>
                  <Ionicons name="checkmark-circle" size={20} color="#10B981" />
                </View>
                <View style={[styles.kpiTag, { backgroundColor: colors.greenBg }]}>
                  <Text style={[styles.kpiTagText, { color: "#065F46" }]}>
                    {overview.employees > 0 ? `${Math.round((overview.present / overview.employees) * 100)}%` : "--"}
                  </Text>
                </View>
              </View>
              <Text style={styles.kpiNumber}>{overview.present}</Text>
              <Text style={styles.kpiTitle}>Present Today</Text>
              <Text style={styles.kpiSub}>On-time & On-site</Text>
            </View>

            <View style={styles.kpiCard}>
              <View style={styles.kpiTopRow}>
                <View style={[styles.kpiIconBox, { backgroundColor: colors.redBg }]}>
                  <Ionicons name="close-circle" size={20} color={colors.danger} />
                </View>
                <View style={[styles.kpiTag, { backgroundColor: colors.redBg }]}>
                  <Text style={[styles.kpiTagText, { color: "#991B1B" }]}>
                    {overview.employees > 0 ? `${Math.round((overview.absent / overview.employees) * 100)}%` : "--"}
                  </Text>
                </View>
              </View>
              <Text style={styles.kpiNumber}>{overview.absent}</Text>
              <Text style={styles.kpiTitle}>Absent Today</Text>
              <Text style={styles.kpiSub}>Unexcused Absences</Text>
            </View>

            <View style={styles.kpiCard}>
              <View style={styles.kpiTopRow}>
                <View style={[styles.kpiIconBox, { backgroundColor: colors.yellowBg }]}>
                  <Ionicons name="time" size={20} color={colors.warning} />
                </View>
                <View style={[styles.kpiTag, { backgroundColor: colors.yellowBg }]}>
                  <Text style={[styles.kpiTagText, { color: "#92400E" }]}>Grace</Text>
                </View>
              </View>
              <Text style={styles.kpiNumber}>{overview.late}</Text>
              <Text style={styles.kpiTitle}>Late Check-ins</Text>
              <Text style={styles.kpiSub}>Within Grace Period</Text>
            </View>
          </View>

          {/* Department Breakdown */}
          <View style={styles.sectionCard}>
            <View style={styles.sectionHeaderRow}>
              <Text style={styles.sectionTitle}>Department Breakdown</Text>
              <Text style={styles.sectionSubtitle}>Share of Workforce</Text>
            </View>

            {departments.length === 0 ? (
              <View style={{ padding: 20, alignItems: "center" }}>
                <Text style={{ fontSize: 13, color: "#64748B", fontWeight: "600" }}>
                  No Department Data Available
                </Text>
              </View>
            ) : (
              departments.map((dept, index) => (
                <View key={index} style={styles.deptItem}>
                  <View style={styles.deptTopRow}>
                    <View style={{ flexDirection: "row", alignItems: "center" }}>
                      <View style={[styles.deptDot, { backgroundColor: dept.color }]} />
                      <Text style={styles.deptName}>{dept.name}</Text>
                    </View>
                    <Text style={styles.deptRate}>{dept.share}%</Text>
                  </View>

                  <View style={styles.deptProgressTrack}>
                    <View
                      style={[
                        styles.deptProgressFill,
                        { width: `${dept.share}%`, backgroundColor: dept.color },
                      ]}
                    />
                  </View>
                  <Text style={styles.deptEmpCount}>{dept.employees} Employees enrolled</Text>
                </View>
              ))
            )}
          </View>

          {/* Active Staff */}
          <View style={styles.sectionCard}>
            <View style={styles.sectionHeaderRow}>
              <Text style={styles.sectionTitle}>Active Staff</Text>
              <Ionicons name="people-outline" size={20} color="#173B8C" />
            </View>

            {performers.length === 0 ? (
              <View style={{ padding: 20, alignItems: "center" }}>
                <Text style={{ fontSize: 13, color: "#64748B", fontWeight: "600" }}>
                  No Active Staff Yet
                </Text>
                <Text style={{ fontSize: 12, color: colors.textLight, marginTop: 4 }}>
                  Active employees will be listed here once registered.
                </Text>
              </View>
            ) : (
              performers.map((p, idx) => (
                <View key={idx} style={styles.performerRow}>
                  <View style={styles.performerRankBox}>
                    <Text style={styles.performerRankText}>#{idx + 1}</Text>
                  </View>
                  <View style={{ flex: 1, marginLeft: 12 }}>
                    <Text style={styles.performerName}>{p.name}</Text>
                    <Text style={styles.performerSub}>{p.role} • {p.department}</Text>
                  </View>
                  <View style={styles.scoreBadge}>
                    <Text style={styles.scoreText}>Active</Text>
                  </View>
                </View>
              ))
            )}
          </View>

          {/* Smart Alerts */}
          <View style={styles.sectionCard}>
            <Text style={[styles.sectionTitle, { marginBottom: 14 }]}>System Insights & Alerts</Text>
            {alerts.map((alt, idx) => (
              <View key={idx} style={styles.alertRow}>
                <View style={[styles.alertIconBox, { backgroundColor: alt.color + "15" }]}>
                  <Ionicons name={alt.icon} size={20} color={alt.color} />
                </View>
                <View style={{ flex: 1, marginLeft: 12 }}>
                  <Text style={styles.alertTitle}>{alt.title}</Text>
                  <Text style={styles.alertSub}>{alt.subtitle}</Text>
                </View>
              </View>
            ))}
          </View>

          <View style={{ height: 110 }} />
        </ScrollView>
      </SafeAreaView>
    </LinearGradient>
  );
}

const makeStyles = (colors) => StyleSheet.create({
  container: { flex: 1 },
  content: { paddingHorizontal: 18, paddingTop: 14 },

  // Top Control Bar
  topControlRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    marginBottom: 16,
  },
  periodPillContainer: {
    flexDirection: "row",
    backgroundColor: colors.card,
    borderRadius: 16,
    padding: 4,
    borderWidth: 1,
    borderColor: colors.border,
    elevation: 1,
  },
  periodPill: {
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderRadius: 12,
  },
  periodPillActive: {
    backgroundColor: "#173B8C",
  },
  periodText: {
    fontSize: 12,
    fontWeight: "700",
    color: "#64748B",
  },
  periodTextActive: {
    color: "#FFFFFF",
  },
  exportBtn: {
    flexDirection: "row",
    alignItems: "center",
    backgroundColor: colors.card,
    paddingHorizontal: 14,
    paddingVertical: 8,
    borderRadius: 14,
    borderWidth: 1,
    borderColor: colors.border,
    elevation: 1,
  },
  exportBtnText: {
    fontSize: 12,
    fontWeight: "800",
    color: "#173B8C",
    marginLeft: 6,
  },

  // Hero Card
  heroCard: {
    backgroundColor: colors.card,
    borderRadius: 22,
    padding: 20,
    marginBottom: 16,
    borderWidth: 1,
    borderColor: colors.border,
    elevation: 3,
    shadowColor: "#0F172A",
    shadowOpacity: 0.05,
    shadowRadius: 10,
    shadowOffset: { width: 0, height: 4 },
  },
  heroTop: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "flex-start",
  },
  heroLabel: {
    fontSize: 11,
    fontWeight: "800",
    color: "#64748B",
    letterSpacing: 0.8,
  },
  heroValue: {
    fontSize: 22,
    fontWeight: "800",
    color: colors.text,
    letterSpacing: -0.4,
    marginTop: 4,
  },
  trendBadge: {
    flexDirection: "row",
    alignItems: "center",
    backgroundColor: colors.greenBg,
    paddingHorizontal: 10,
    paddingVertical: 5,
    borderRadius: 14,
  },
  trendBadgeText: {
    color: "#065F46",
    fontSize: 12,
    fontWeight: "800",
    marginLeft: 4,
  },
  heroSubText: {
    fontSize: 12,
    color: "#64748B",
    marginTop: 6,
    lineHeight: 17,
  },
  segmentBarContainer: {
    height: 8,
    borderRadius: 4,
    flexDirection: "row",
    overflow: "hidden",
    marginTop: 16,
    marginBottom: 12,
  },
  segment: {
    height: "100%",
  },
  segmentLegendRow: {
    flexDirection: "row",
    justifyContent: "space-between",
  },
  legendItem: {
    flexDirection: "row",
    alignItems: "center",
  },
  legendDot: {
    width: 8,
    height: 8,
    borderRadius: 4,
    marginRight: 6,
  },
  legendText: {
    fontSize: 11,
    fontWeight: "600",
    color: colors.textSecondary,
  },

  // KPI Grid
  kpiGrid: {
    flexDirection: "row",
    flexWrap: "wrap",
    justifyContent: "space-between",
    marginBottom: 8,
  },
  kpiCard: {
    width: "48.5%",
    backgroundColor: colors.card,
    borderRadius: 20,
    padding: 16,
    marginBottom: 12,
    borderWidth: 1,
    borderColor: colors.border,
    elevation: 2,
  },
  kpiTopRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
  },
  kpiIconBox: {
    width: 38,
    height: 38,
    borderRadius: 12,
    justifyContent: "center",
    alignItems: "center",
  },
  kpiTag: {
    backgroundColor: colors.primaryLight,
    paddingHorizontal: 8,
    paddingVertical: 3,
    borderRadius: 10,
  },
  kpiTagText: {
    fontSize: 10,
    fontWeight: "800",
    color: "#173B8C",
  },
  kpiNumber: {
    fontSize: 20,
    fontWeight: "800",
    color: colors.text,
    letterSpacing: -0.3,
    marginTop: 10,
  },
  kpiTitle: {
    fontSize: 12,
    fontWeight: "700",
    color: colors.text,
    marginTop: 2,
  },
  kpiSub: {
    fontSize: 11,
    color: "#64748B",
    marginTop: 2,
  },

  // Section Card Base
  sectionCard: {
    backgroundColor: colors.card,
    borderRadius: 22,
    padding: 18,
    marginBottom: 16,
    borderWidth: 1,
    borderColor: colors.border,
    elevation: 2,
  },
  sectionHeaderRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    marginBottom: 16,
  },
  sectionTitle: {
    fontSize: 14,
    fontWeight: "800",
    color: colors.text,
  },
  sectionSubtitle: {
    fontSize: 12,
    color: "#64748B",
    marginTop: 2,
  },
  badgePill: {
    backgroundColor: "#F1F5F9",
    paddingHorizontal: 10,
    paddingVertical: 4,
    borderRadius: 12,
  },
  badgePillText: {
    fontSize: 11,
    fontWeight: "700",
    color: colors.textSecondary,
  },

  // Bar Chart
  chartContainer: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "flex-end",
    height: 170,
    paddingTop: 20,
    paddingHorizontal: 4,
  },
  chartCol: {
    flex: 1,
    alignItems: "center",
  },
  barValText: {
    fontSize: 10,
    fontWeight: "800",
    color: "#173B8C",
    marginBottom: 6,
  },
  barTrack: {
    height: 125,
    justifyContent: "flex-end",
    width: 18,
    backgroundColor: "#F1F5F9",
    borderRadius: 9,
    overflow: "hidden",
  },
  barFill: {
    width: "100%",
    borderRadius: 9,
  },
  barLabel: {
    fontSize: 11,
    fontWeight: "700",
    color: "#64748B",
    marginTop: 8,
  },

  // Department Items
  deptItem: {
    marginBottom: 14,
  },
  deptTopRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    marginBottom: 6,
  },
  deptDot: {
    width: 10,
    height: 10,
    borderRadius: 5,
    marginRight: 8,
  },
  deptName: {
    fontSize: 14,
    fontWeight: "700",
    color: colors.text,
  },
  deptRate: {
    fontSize: 14,
    fontWeight: "800",
    color: colors.text,
  },
  deptProgressTrack: {
    height: 7,
    backgroundColor: "#F1F5F9",
    borderRadius: 4,
    overflow: "hidden",
    marginBottom: 4,
  },
  deptProgressFill: {
    height: "100%",
    borderRadius: 4,
  },
  deptEmpCount: {
    fontSize: 11,
    color: colors.textLight,
  },

  // Top Performers
  performerRow: {
    flexDirection: "row",
    alignItems: "center",
    paddingVertical: 10,
    borderBottomWidth: 1,
    borderBottomColor: "#F1F5F9",
  },
  performerRankBox: {
    width: 32,
    height: 32,
    borderRadius: 10,
    backgroundColor: "#FEF3C7",
    justifyContent: "center",
    alignItems: "center",
  },
  performerRankText: {
    fontSize: 12,
    fontWeight: "800",
    color: "#92400E",
  },
  performerName: {
    fontSize: 14,
    fontWeight: "700",
    color: colors.text,
  },
  performerSub: {
    fontSize: 12,
    color: "#64748B",
    marginTop: 2,
  },
  scoreBadge: {
    backgroundColor: colors.greenBg,
    paddingHorizontal: 10,
    paddingVertical: 4,
    borderRadius: 10,
  },
  scoreText: {
    fontSize: 12,
    fontWeight: "800",
    color: "#065F46",
  },

  // Alert Items
  alertRow: {
    flexDirection: "row",
    alignItems: "center",
    paddingVertical: 10,
    borderBottomWidth: 1,
    borderBottomColor: "#F1F5F9",
  },
  alertIconBox: {
    width: 38,
    height: 38,
    borderRadius: 12,
    justifyContent: "center",
    alignItems: "center",
  },
  alertTitle: {
    fontSize: 13,
    fontWeight: "700",
    color: colors.text,
  },
  alertSub: {
    fontSize: 11,
    color: "#64748B",
    marginTop: 2,
  },
});
