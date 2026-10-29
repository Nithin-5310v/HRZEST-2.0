import React, { useState, useEffect } from "react";
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  TouchableOpacity,
  FlatList,
  Modal,
  RefreshControl,
} from "react-native";
import SafeAreaView from "../../components/SafeAreaView";
import { DrawerActions } from "@react-navigation/native";
import { Ionicons } from "@expo/vector-icons";
import { LinearGradient } from "expo-linear-gradient";

import AdminHeader from "../../components/admin/AdminHeader";
import AdminSearchBar from "../../components/admin/AdminSearchBar";
import SaasFilterSheet from "../../components/common/SaasFilterSheet";
import { fetchEmployees, fetchDashboard, fetchMonthlyReport } from "../../api/client";
import { useTheme } from "../../store/ThemeContext";

const MONTHS = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December"
];

// Opens on the current month; the year list follows the calendar.
const yearsAround = (year) => [-2, -1, 0, 1].map((offset) => String(year + offset));

export default function AttendanceScreen({ navigation }) {
  const { colors } = useTheme();
  const styles = React.useMemo(() => makeStyles(colors), [colors]);
  const [search, setSearch] = useState("");
  const [selectedMonth, setSelectedMonth] = useState(() => MONTHS[new Date().getMonth()]);
  const [selectedYear, setSelectedYear] = useState(() => String(new Date().getFullYear()));
  const years = React.useMemo(() => yearsAround(new Date().getFullYear()), []);
  const [statusFilter, setStatusFilter] = useState("All");
  const [selectedSort, setSelectedSort] = useState("Default");
  const [filterModalVisible, setFilterModalVisible] = useState(false);

  const [monthModalVisible, setMonthModalVisible] = useState(false);
  const [yearModalVisible, setYearModalVisible] = useState(false);

  const [employees, setEmployees] = useState([]);
  const [summary, setSummary] = useState({
    employees: 0,
    workingDays: 26,
    attendance: 0,
    holidays: 0,
  });

  const [refreshing, setRefreshing] = useState(false);

  useEffect(() => {
    loadData();
  }, [selectedMonth, selectedYear]);

  const onRefresh = async () => {
    setRefreshing(true);
    await loadData();
    setRefreshing(false);
  };

  const loadData = async () => {
    try {
      const monthIdx = MONTHS.indexOf(selectedMonth) + 1;
      const [empRes, dashRes, reportRes] = await Promise.all([
        fetchEmployees().catch(() => null),
        fetchDashboard().catch(() => null),
        fetchMonthlyReport(parseInt(selectedYear, 10), monthIdx).catch(() => null),
      ]);
      const empList = empRes?.data?.employees || [];
      const dash = dashRes?.data || {};

      if (reportRes?.data?.ok && Array.isArray(reportRes.data.report)) {
        const repList = reportRes.data.report;
        const total = repList.length || empList.length || 0;
        const avgPct = total > 0 ? Math.round(repList.reduce((acc, curr) => acc + (curr.pct || 0), 0) / total) : 0;

        setSummary({
          employees: total,
          workingDays: reportRes.data.total_working || 26,
          attendance: avgPct,
          holidays: reportRes.data.holiday_count || 0,
        });

        const mapped = repList.map((item, i) => ({
          id: item.employee_id || `EMP-${1001 + i}`,
          name: item.name,
          full: item.full_days || 0,
          late: item.late_days || 0,
          half: item.half_days || 0,
          absent: item.absent || 0,
          working: item.billable || 26,
          percent: Math.round(item.pct || 0),
          status: item.pct > 0 ? "Present" : "Absent",
        }));
        setEmployees(mapped);
      } else {
        // The real monthly report call failed or returned an unexpected
        // shape -- this used to fabricate a full month of attendance
        // (22 full days, 100%) for every "Active" employee based on
        // nothing but their status flag. Showing an honest empty state
        // instead of invented per-employee attendance records.
        const total = empList.length || dash.total_employees || 0;
        setSummary({
          employees: total,
          workingDays: dash.working_days || 26,
          attendance: 0,
          holidays: dash.holidays || 0,
        });
        setEmployees([]);
      }
    } catch (e) {
      setEmployees([]);
      setSummary({ employees: 0, workingDays: 26, attendance: 0, holidays: 0 });
    }
  };

  const hasActiveFilter = statusFilter !== "All" || selectedSort !== "Default";

  const filteredEmployees = employees
    .filter((e) => {
      const matchesSearch =
        e.name.toLowerCase().includes(search.toLowerCase()) ||
        e.id.toLowerCase().includes(search.toLowerCase());
      const matchesStatus =
        statusFilter === "All" ||
        (statusFilter === "Present" && (e.status === "Present" || e.full > 20)) ||
        (statusFilter === "Late" && (e.status === "Late" || e.late > 0)) ||
        (statusFilter === "Absent" && (e.status === "Absent" || e.absent > 0));
      return matchesSearch && matchesStatus;
    })
    .sort((a, b) => {
      if (selectedSort === "Attendance (High-Low)") return b.percent - a.percent;
      if (selectedSort === "Attendance (Low-High)") return a.percent - b.percent;
      if (selectedSort === "Name (A-Z)") return a.name.localeCompare(b.name);
      return 0;
    });

  const renderEmployee = ({ item }) => (
    <View style={styles.employeeCard}>
      <View style={styles.employeeHeader}>
        <View>
          <Text style={styles.employeeName}>{item.name}</Text>
          <Text style={styles.employeeId}>{item.id}</Text>
        </View>

        <View style={styles.percentBadge}>
          <Text style={styles.percentText}>{item.percent}%</Text>
        </View>
      </View>

      <View style={styles.progressBackground}>
        <View style={[styles.progressFill, { width: `${item.percent}%` }]} />
      </View>

      <View style={styles.statsRow}>
        <View style={styles.statChipGreen}>
          <Text style={styles.chipValue}>{item.full}</Text>
          <Text style={styles.chipLabel}>Full</Text>
        </View>

        <View style={styles.statChipOrange}>
          <Text style={styles.chipValue}>{item.late}</Text>
          <Text style={styles.chipLabel}>Late</Text>
        </View>

        <View style={styles.statChipBlue}>
          <Text style={styles.chipValue}>{item.half}</Text>
          <Text style={styles.chipLabel}>Half</Text>
        </View>

        <View style={styles.statChipRed}>
          <Text style={styles.chipValue}>{item.absent}</Text>
          <Text style={styles.chipLabel}>Absent</Text>
        </View>
      </View>
    </View>
  );

  return (
    <LinearGradient colors={colors.screenGradient} style={styles.container}>
      <SafeAreaView style={{ flex: 1 }}>
        <AdminHeader
          title="Daily Attendance"
          onMenu={() => navigation.dispatch(DrawerActions.openDrawer())}
        />

        <ScrollView
          showsVerticalScrollIndicator={false}
          contentContainerStyle={styles.content}
          refreshControl={
            <RefreshControl refreshing={refreshing} onRefresh={onRefresh} />
          }
        >
          {/* Quick Mark Attendance Banner */}
          <TouchableOpacity
            style={styles.markBanner}
            activeOpacity={0.85}
            onPress={() => navigation.navigate("AdminTabs", { screen: "MarkAttendance" })}
          >
            <View style={styles.markBannerLeft}>
              <Ionicons name="create-outline" size={24} color="#FFFFFF" />
              <View style={{ marginLeft: 12 }}>
                <Text style={styles.markBannerTitle}>Mark Bulk Attendance</Text>
                <Text style={styles.markBannerSub}>Quick manual attendance entry for staff</Text>
              </View>
            </View>
            <Ionicons name="chevron-forward" size={20} color="#FFFFFF" />
          </TouchableOpacity>

          {/* Month & Year Selectors */}
          <View style={styles.selectorRow}>
            <TouchableOpacity
              style={styles.selectorPill}
              activeOpacity={0.8}
              onPress={() => setMonthModalVisible(true)}
            >
              <Ionicons name="calendar-outline" size={16} color={colors.primary} />
              <Text style={styles.selectorPillText}>{selectedMonth}</Text>
              <Ionicons name="chevron-down" size={14} color="#64748B" />
            </TouchableOpacity>

            <TouchableOpacity
              style={styles.selectorPill}
              activeOpacity={0.8}
              onPress={() => setYearModalVisible(true)}
            >
              <Ionicons name="time-outline" size={16} color={colors.primary} />
              <Text style={styles.selectorPillText}>{selectedYear}</Text>
              <Ionicons name="chevron-down" size={14} color="#64748B" />
            </TouchableOpacity>
          </View>

          {/* Search */}
          <AdminSearchBar
            value={search}
            onChangeText={setSearch}
            placeholder="Search employee by name or ID..."
            onFilterPress={() => setFilterModalVisible(true)}
            hasActiveFilter={hasActiveFilter}
            onClear={() => setSearch("")}
          />

          {/* Status Filter Chips */}
          <View style={styles.filterRow}>
            {["All", "Present", "Late", "Absent"].map((f) => (
              <TouchableOpacity
                key={f}
                style={[
                  styles.filterChip,
                  statusFilter === f && styles.filterChipActive,
                ]}
                onPress={() => setStatusFilter(f)}
              >
                <Text
                  style={[
                    styles.filterChipText,
                    statusFilter === f && styles.filterChipTextActive,
                  ]}
                >
                  {f}
                </Text>
              </TouchableOpacity>
            ))}
          </View>

          {/* Summary Grid */}
          <View style={styles.summaryGrid}>
            <View style={styles.summaryCard}>
              <Ionicons name="people" size={24} color={colors.primary} />
              <Text style={styles.summaryValue}>{summary.employees}</Text>
              <Text style={styles.summaryLabel}>Total Staff</Text>
            </View>

            <View style={styles.summaryCard}>
              <Ionicons name="calendar" size={24} color="#16A34A" />
              <Text style={styles.summaryValue}>{summary.workingDays}</Text>
              <Text style={styles.summaryLabel}>Working Days</Text>
            </View>

            <View style={styles.summaryCard}>
              <Ionicons name="checkmark-circle" size={24} color={colors.warning} />
              <Text style={styles.summaryValue}>{summary.attendance}%</Text>
              <Text style={styles.summaryLabel}>Avg Attendance</Text>
            </View>

            <View style={styles.summaryCard}>
              <Ionicons name="gift" size={24} color={colors.danger} />
              <Text style={styles.summaryValue}>{summary.holidays}</Text>
              <Text style={styles.summaryLabel}>Holidays</Text>
            </View>
          </View>

          <View style={styles.sectionHeader}>
            <Text style={styles.sectionTitle}>Employee Records</Text>
            <Text style={styles.sectionBadge}>{filteredEmployees.length} Found</Text>
          </View>

          <FlatList
            data={filteredEmployees}
            keyExtractor={(item) => item.id}
            renderItem={renderEmployee}
            scrollEnabled={false}
          />

          <View style={{ height: 110 }} />
        </ScrollView>

        {/* Month Selector Modal */}
        <Modal
          visible={monthModalVisible}
          transparent={true}
          animationType="fade"
          onRequestClose={() => setMonthModalVisible(false)}
        >
          <View style={styles.modalOverlay}>
            <View style={styles.modalCard}>
              <View style={styles.modalHeader}>
                <Text style={styles.modalTitle}>Select Month</Text>
                <TouchableOpacity onPress={() => setMonthModalVisible(false)}>
                  <Ionicons name="close" size={22} color={colors.text} />
                </TouchableOpacity>
              </View>
              <FlatList
                data={MONTHS}
                keyExtractor={(item) => item}
                renderItem={({ item }) => (
                  <TouchableOpacity
                    style={[
                      styles.pickerOption,
                      selectedMonth === item && styles.pickerOptionActive,
                    ]}
                    onPress={() => {
                      setSelectedMonth(item);
                      setMonthModalVisible(false);
                    }}
                  >
                    <Text
                      style={[
                        styles.pickerOptionText,
                        selectedMonth === item && styles.pickerOptionTextActive,
                      ]}
                    >
                      {item}
                    </Text>
                    {selectedMonth === item && (
                      <Ionicons name="checkmark-circle" size={18} color={colors.primary} />
                    )}
                  </TouchableOpacity>
                )}
              />
            </View>
          </View>
        </Modal>

        {/* Year Selector Modal */}
        <Modal
          visible={yearModalVisible}
          transparent={true}
          animationType="fade"
          onRequestClose={() => setYearModalVisible(false)}
        >
          <View style={styles.modalOverlay}>
            <View style={styles.modalCard}>
              <View style={styles.modalHeader}>
                <Text style={styles.modalTitle}>Select Year</Text>
                <TouchableOpacity onPress={() => setYearModalVisible(false)}>
                  <Ionicons name="close" size={22} color={colors.text} />
                </TouchableOpacity>
              </View>
              <FlatList
                data={years}
                keyExtractor={(item) => item}
                renderItem={({ item }) => (
                  <TouchableOpacity
                    style={[
                      styles.pickerOption,
                      selectedYear === item && styles.pickerOptionActive,
                    ]}
                    onPress={() => {
                      setSelectedYear(item);
                      setYearModalVisible(false);
                    }}
                  >
                    <Text
                      style={[
                        styles.pickerOptionText,
                        selectedYear === item && styles.pickerOptionTextActive,
                      ]}
                    >
                      {item}
                    </Text>
                    {selectedYear === item && (
                      <Ionicons name="checkmark-circle" size={18} color={colors.primary} />
                    )}
                  </TouchableOpacity>
                )}
              />
            </View>
          </View>
        </Modal>

        {/* Professional SaaS Filter Modal */}
        <SaasFilterSheet
          visible={filterModalVisible}
          title="Filter Attendance Records"
          statusOptions={["All", "Present", "Late", "Absent"]}
          selectedStatus={statusFilter}
          onSelectStatus={setStatusFilter}
          sortOptions={["Default", "Attendance (High-Low)", "Attendance (Low-High)", "Name (A-Z)"]}
          selectedSort={selectedSort}
          onSelectSort={setSelectedSort}
          onApply={() => setFilterModalVisible(false)}
          onReset={() => {
            setStatusFilter("All");
            setSelectedSort("Default");
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
    paddingHorizontal: 18,
    paddingTop: 16,
  },
  markBanner: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    backgroundColor: colors.primary,
    borderRadius: 20,
    padding: 16,
    marginBottom: 16,
    elevation: 4,
    shadowColor: "#0B2253",
    shadowOpacity: 0.25,
    shadowRadius: 10,
    shadowOffset: { width: 0, height: 4 },
  },
  markBannerLeft: {
    flexDirection: "row",
    alignItems: "center",
  },
  markBannerTitle: {
    fontSize: 16,
    fontWeight: "800",
    color: "#FFFFFF",
  },
  markBannerSub: {
    fontSize: 12,
    color: "rgba(255, 255, 255, 0.8)",
    marginTop: 2,
  },
  selectorRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    marginBottom: 14,
  },
  selectorPill: {
    flex: 0.48,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    backgroundColor: colors.card,
    paddingHorizontal: 14,
    paddingVertical: 10,
    borderRadius: 14,
    borderWidth: 1,
    borderColor: colors.border,
  },
  selectorPillText: {
    fontSize: 14,
    fontWeight: "700",
    color: colors.text,
  },
  filterRow: {
    flexDirection: "row",
    marginVertical: 12,
  },
  filterChip: {
    paddingHorizontal: 16,
    paddingVertical: 8,
    borderRadius: 20,
    backgroundColor: colors.card,
    marginRight: 8,
    borderWidth: 1,
    borderColor: colors.border,
  },
  filterChipActive: {
    backgroundColor: colors.primary,
    borderColor: colors.primary,
  },
  filterChipText: {
    fontSize: 13,
    fontWeight: "700",
    color: "#64748B",
  },
  filterChipTextActive: {
    color: "#FFFFFF",
  },
  summaryGrid: {
    flexDirection: "row",
    flexWrap: "wrap",
    justifyContent: "space-between",
    marginBottom: 16,
  },
  summaryCard: {
    width: "48.5%",
    backgroundColor: colors.card,
    borderRadius: 18,
    padding: 16,
    marginBottom: 12,
    borderWidth: 1,
    borderColor: colors.border,
    alignItems: "center",
  },
  summaryValue: {
    fontSize: 18,
    fontWeight: "800",
    color: colors.text,
    marginTop: 8,
  },
  summaryLabel: {
    fontSize: 11,
    color: "#64748B",
    marginTop: 2,
    fontWeight: "600",
  },
  sectionHeader: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    marginVertical: 12,
  },
  sectionTitle: {
    fontSize: 14,
    fontWeight: "800",
    color: colors.text,
  },
  sectionBadge: {
    fontSize: 12,
    fontWeight: "700",
    color: colors.primary,
    backgroundColor: colors.blueBg,
    paddingHorizontal: 10,
    paddingVertical: 4,
    borderRadius: 12,
  },
  employeeCard: {
    backgroundColor: colors.card,
    borderRadius: 20,
    padding: 16,
    marginBottom: 12,
    borderWidth: 1,
    borderColor: colors.border,
  },
  employeeHeader: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    marginBottom: 12,
  },
  employeeName: {
    fontSize: 16,
    fontWeight: "800",
    color: colors.text,
  },
  employeeId: {
    fontSize: 12,
    color: "#64748B",
    marginTop: 2,
  },
  percentBadge: {
    backgroundColor: colors.greenBg,
    paddingHorizontal: 10,
    paddingVertical: 4,
    borderRadius: 12,
  },
  percentText: {
    color: "#16A34A",
    fontWeight: "800",
    fontSize: 13,
  },
  progressBackground: {
    height: 6,
    borderRadius: 3,
    backgroundColor: "#F1F5F9",
    marginBottom: 14,
    overflow: "hidden",
  },
  progressFill: {
    height: "100%",
    backgroundColor: colors.success,
    borderRadius: 3,
  },
  statsRow: {
    flexDirection: "row",
    justifyContent: "space-between",
  },
  statChipGreen: {
    flex: 1,
    backgroundColor: colors.greenBg,
    borderRadius: 10,
    paddingVertical: 6,
    alignItems: "center",
    marginRight: 4,
  },
  statChipOrange: {
    flex: 1,
    backgroundColor: colors.yellowBg,
    borderRadius: 10,
    paddingVertical: 6,
    alignItems: "center",
    marginRight: 4,
  },
  statChipBlue: {
    flex: 1,
    backgroundColor: colors.blueBg,
    borderRadius: 10,
    paddingVertical: 6,
    alignItems: "center",
    marginRight: 4,
  },
  statChipRed: {
    flex: 1,
    backgroundColor: colors.redBg,
    borderRadius: 10,
    paddingVertical: 6,
    alignItems: "center",
  },
  chipValue: {
    fontSize: 14,
    fontWeight: "800",
    color: colors.text,
  },
  chipLabel: {
    fontSize: 10,
    color: "#64748B",
    fontWeight: "700",
  },
  modalOverlay: {
    flex: 1,
    backgroundColor: "rgba(15, 23, 42, 0.5)",
    justifyContent: "center",
    alignItems: "center",
    paddingHorizontal: 24,
  },
  modalCard: {
    width: "100%",
    maxHeight: 380,
    backgroundColor: colors.card,
    borderRadius: 22,
    padding: 20,
    elevation: 10,
    shadowColor: "#0F172A",
    shadowOpacity: 0.2,
    shadowRadius: 16,
  },
  modalHeader: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    marginBottom: 14,
    paddingBottom: 10,
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
  },
  modalTitle: {
    fontSize: 18,
    fontWeight: "800",
    color: colors.text,
  },
  pickerOption: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    paddingVertical: 14,
    paddingHorizontal: 12,
    borderRadius: 12,
    marginBottom: 4,
  },
  pickerOptionActive: {
    backgroundColor: colors.blueBg,
  },
  pickerOptionText: {
    fontSize: 15,
    fontWeight: "600",
    color: "#334155",
  },
  pickerOptionTextActive: {
    fontWeight: "800",
    color: colors.primary,
  },
});