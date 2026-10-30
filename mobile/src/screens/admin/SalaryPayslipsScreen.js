import React, { useState, useEffect } from "react";
import {
  ScrollView,
  StyleSheet,
  Alert,
  Modal,
  View,
  Text,
  TouchableOpacity,
  FlatList,
} from "react-native";
import SafeAreaView from "../../components/SafeAreaView";

import { Ionicons } from "@expo/vector-icons";

import AdminHeader from "../../components/admin/AdminHeader";
import SalaryHeader from "../../components/admin/salary/SalaryHeader";
import SalarySearchBar from "../../components/admin/salary/SalarySearchBar";
import MonthYearSelector from "../../components/admin/salary/MonthYearSelector";
import PayrollSummaryCard from "../../components/admin/salary/PayrollSummaryCard";
import SalaryStatsGrid from "../../components/admin/salary/SalaryStatsGrid";
import PayrollActionButtons from "../../components/admin/salary/PayrollActionButtons";
import EmployeeSalaryList from "../../components/admin/salary/EmployeeSalaryList";
import SALARY_THEME from "../../constants/salaryTheme";
import { fetchSalaryReport, fetchEmployees, sendPayslipEmail, fetchPayrollStatus, lockPayroll, unlockPayroll, fetchSalaryReportExport } from "../../api/client";
import { shareBase64File } from "../../utils/fileShare";
import SaasFilterSheet from "../../components/common/SaasFilterSheet";
import { useTheme } from "../../store/ThemeContext";

const MONTHS = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December"
];

const now = new Date();
// Two years back to one ahead of the current one.
const YEARS = [-2, -1, 0, 1].map((offset) => String(now.getFullYear() + offset));

export default function SalaryPayslipsScreen({ navigation }) {
  const { colors } = useTheme();
  const styles = React.useMemo(() => makeStyles(colors), [colors]);
  const [search, setSearch] = useState("");
  const [selectedMonth, setSelectedMonth] = useState(MONTHS[now.getMonth()]);
  const [selectedYear, setSelectedYear] = useState(String(now.getFullYear()));
  const [selectedDept, setSelectedDept] = useState("All");
  const [selectedSort, setSelectedSort] = useState("Highest Net Pay");
  const [filterModalVisible, setFilterModalVisible] = useState(false);
  const [emailing, setEmailing] = useState(false);

  const [monthModalVisible, setMonthModalVisible] = useState(false);
  const [yearModalVisible, setYearModalVisible] = useState(false);

  const [salaryOverview, setSalaryOverview] = useState({
    totalEmployees: 0,
    totalGross: 0,
    totalNetPay: 0,
    totalDeductions: 0,
  });

  const [employees, setEmployees] = useState([]);
  const [payrollLocked, setPayrollLocked] = useState(false);
  const [payrollLoading, setPayrollLoading] = useState(false);
  const [exporting, setExporting] = useState(false);

  useEffect(() => {
    loadSalaryData();
  }, [selectedMonth, selectedYear]);

  const loadSalaryData = async () => {
    try {
      const monthIdx = MONTHS.indexOf(selectedMonth) + 1;
      const [salaryRes, empRes, statusRes] = await Promise.all([
        fetchSalaryReport(selectedYear, monthIdx),
        fetchEmployees().catch(() => null),
        fetchPayrollStatus(selectedYear, monthIdx).catch(() => null),
      ]);
      setPayrollLocked(!!statusRes?.data?.locked);
      if (salaryRes?.data?.ok && Array.isArray(salaryRes.data.salary_data)) {
        const deptByEmpId = {};
        const roleByEmpId = {};
        (empRes?.data?.employees || []).forEach((e) => {
          deptByEmpId[e.employee_id] = e.department;
          roleByEmpId[e.employee_id] = e.role;
        });
        // Real per-employee daily-rate breakdown from /api/salary_report --
        // no per-employee "Paid/Pending" payroll status exists in this API,
        // so we deliberately don't invent one (see SalaryEmployeeCard for
        // the fallback label it shows when payrollStatus is absent).
        const mapped = salaryRes.data.salary_data.map((entry) => ({
          name: entry.name,
          employeeId: entry.emp_id,
          department: deptByEmpId[entry.emp_id] || null,
          role: roleByEmpId[entry.emp_id] || "",
          gross: entry.gross,
          net: entry.net,
          deductions: entry.deduction,
          workDays: entry.billable,
          attendance: {
            full: entry.full_days,
            late: entry.late_days,
            absent: entry.absent,
          },
        }));
        setEmployees(mapped);
        setSalaryOverview({
          totalEmployees: mapped.length,
          totalGross: mapped.reduce((s, e) => s + (e.gross || 0), 0),
          totalNetPay: mapped.reduce((s, e) => s + (e.net || 0), 0),
          totalDeductions: mapped.reduce((s, e) => s + (e.deductions || 0), 0),
        });
      } else {
        setEmployees([]);
        setSalaryOverview({ totalEmployees: 0, totalGross: 0, totalNetPay: 0, totalDeductions: 0 });
      }
    } catch (_) {
      setEmployees([]);
      setSalaryOverview({ totalEmployees: 0, totalGross: 0, totalNetPay: 0, totalDeductions: 0 });
    }
  };

  const handleGeneratePayroll = () => {
    if (payrollLoading) return;
    const monthIdx = MONTHS.indexOf(selectedMonth) + 1;
    const yearNum = Number(selectedYear);

    if (payrollLocked) {
      Alert.alert(
        "Payroll Locked",
        `Payroll for ${selectedMonth} ${selectedYear} is already locked. Unlock it to allow further changes?`,
        [
          { text: "Cancel", style: "cancel" },
          {
            text: "Unlock",
            style: "destructive",
            onPress: async () => {
              setPayrollLoading(true);
              try {
                const res = await unlockPayroll(yearNum, monthIdx);
                if (res?.data?.ok) {
                  setPayrollLocked(false);
                  Alert.alert("Unlocked", res.data.msg);
                } else {
                  Alert.alert("Failed", res?.data?.msg || "Could not unlock payroll.");
                }
              } catch (e) {
                Alert.alert("Failed", e?.response?.data?.msg || "Could not unlock payroll.");
              }
              setPayrollLoading(false);
            },
          },
        ]
      );
      return;
    }

    Alert.alert(
      "Lock Payroll",
      `Lock payroll for ${selectedMonth} ${selectedYear}? This finalizes the month's payroll run.`,
      [
        { text: "Cancel", style: "cancel" },
        {
          text: "Lock",
          onPress: async () => {
            setPayrollLoading(true);
            try {
              const res = await lockPayroll(yearNum, monthIdx);
              if (res?.data?.ok) {
                setPayrollLocked(true);
                Alert.alert("Locked", res.data.msg);
              } else {
                Alert.alert("Failed", res?.data?.msg || "Could not lock payroll.");
              }
            } catch (e) {
              Alert.alert("Failed", e?.response?.data?.msg || "Could not lock payroll.");
            }
            setPayrollLoading(false);
          },
        },
      ]
    );
  };

  const handleExport = async () => {
    if (exporting) return;
    setExporting(true);
    try {
      const monthIdx = MONTHS.indexOf(selectedMonth) + 1;
      const res = await fetchSalaryReportExport(selectedYear, monthIdx);
      if (res?.data?.ok) {
        await shareBase64File(res.data.filename, res.data.content_base64, res.data.mime_type);
      } else {
        Alert.alert("Export Failed", res?.data?.msg || "Could not generate the salary report.");
      }
    } catch (e) {
      Alert.alert("Export Failed", e?.response?.data?.msg || "Could not generate the salary report.");
    } finally {
      setExporting(false);
    }
  };

  const handleEmailPayslip = async (employee) => {
    setEmailing(true);
    try {
      const monthIdx = MONTHS.indexOf(selectedMonth) + 1;
      const res = await sendPayslipEmail(employee.employeeId, selectedYear, monthIdx);
      if (res?.data?.ok) {
        Alert.alert("Email Sent", `Payslip for ${employee.name} emailed successfully.`);
      } else {
        Alert.alert("Email Failed", res?.data?.msg || `Could not send payslip for ${employee.name}.`);
      }
    } catch (e) {
      Alert.alert("Email Failed", e?.response?.data?.msg || `Could not send payslip for ${employee.name}.`);
    }
    setEmailing(false);
  };

  const handleBulkEmail = async () => {
    if (filteredEmployees.length === 0 || emailing) return;
    setEmailing(true);
    let sent = 0;
    let failed = 0;
    const monthIdx = MONTHS.indexOf(selectedMonth) + 1;
    for (const employee of filteredEmployees) {
      try {
        const res = await sendPayslipEmail(employee.employeeId, selectedYear, monthIdx);
        if (res?.data?.ok) sent += 1;
        else failed += 1;
      } catch (_) {
        failed += 1;
      }
    }
    setEmailing(false);
    Alert.alert(
      "Bulk Email Complete",
      failed > 0
        ? `${sent} payslip(s) sent. ${failed} failed (missing email or send error).`
        : `${sent} payslip(s) sent successfully.`
    );
  };

  const hasActiveFilter = selectedDept !== "All" || selectedSort !== "Highest Net Pay";

  // No per-employee "Paid/Pending" payroll status exists in the API this
  // screen has access to, so that filter dimension was removed rather than
  // left silently broken (it would've hidden every employee the moment a
  // non-"All" status was picked). Departments are derived from the real
  // employee directory instead of a hardcoded list.
  const departments = ["All", ...new Set(employees.map((e) => e.department).filter(Boolean))];
  const sortOptions = ["Highest Net Pay", "Lowest Net Pay", "Name (A-Z)"];

  const filteredEmployees = employees
    .filter((e) => {
      const matchesSearch =
        e.name.toLowerCase().includes(search.toLowerCase()) ||
        (e.role || "").toLowerCase().includes(search.toLowerCase()) ||
        (e.employeeId && e.employeeId.toLowerCase().includes(search.toLowerCase()));

      const matchesDept =
        selectedDept === "All" || e.department === selectedDept;

      return matchesSearch && matchesDept;
    })
    .sort((a, b) => {
      const netA = a.net ?? 0;
      const netB = b.net ?? 0;
      if (selectedSort === "Lowest Net Pay") return netA - netB;
      if (selectedSort === "Name (A-Z)") return a.name.localeCompare(b.name);
      return netB - netA;
    });

  return (
    <SafeAreaView style={styles.container}>
      <AdminHeader title="Salary & Payslips" navigation={navigation} />

      <ScrollView
        style={styles.scrollView}
        contentContainerStyle={styles.content}
        showsVerticalScrollIndicator={false}
      >
        <SalaryHeader
          month={selectedMonth}
          year={selectedYear}
          onSettingsPress={() => navigation.navigate("Settings")}
        />

        <SalarySearchBar
          value={search}
          onChangeText={setSearch}
          onClear={() => setSearch("")}
          onFilterPress={() => setFilterModalVisible(true)}
          hasActiveFilter={hasActiveFilter}
        />

        <MonthYearSelector
          selectedMonth={selectedMonth}
          selectedYear={selectedYear}
          onMonthPress={() => setMonthModalVisible(true)}
          onYearPress={() => setYearModalVisible(true)}
          onGeneratePress={handleGeneratePayroll}
        />

        <PayrollSummaryCard
          month={selectedMonth}
          year={selectedYear}
          totalEmployees={salaryOverview.totalEmployees}
          totalGross={salaryOverview.totalGross}
          totalNet={salaryOverview.totalNetPay}
          totalDeductions={salaryOverview.totalDeductions}
          onGeneratePayroll={handleGeneratePayroll}
        />

        <SalaryStatsGrid
          totalEmployees={salaryOverview.totalEmployees}
          grossSalary={salaryOverview.totalGross}
          deductions={salaryOverview.totalDeductions}
          netSalary={salaryOverview.totalNetPay}
        />

        <PayrollActionButtons
          onGenerate={handleGeneratePayroll}
          onExport={handleExport}
          onEmail={handleBulkEmail}
          onMore={() => Alert.alert("Options", "Additional payroll rules available under Settings.")}
        />

        <EmployeeSalaryList
          employees={filteredEmployees}
          onSelectEmployee={(emp) =>
            Alert.alert("Employee Payslip", `${emp.name}${emp.role ? ` (${emp.role})` : ""}\nNet Pay: ₹${(emp.net || 0).toLocaleString()}`)
          }
          onEmailEmployee={handleEmailPayslip}
        />
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
              data={YEARS}
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
          title="Filter Salary & Payroll"
          deptOptions={departments}
          selectedDept={selectedDept}
          onSelectDept={setSelectedDept}
          sortOptions={sortOptions}
          selectedSort={selectedSort}
          onSelectSort={setSelectedSort}
          onApply={() => setFilterModalVisible(false)}
          onReset={() => {
            setSelectedDept("All");
            setSelectedSort("Highest Net Pay");
          }}
          onClose={() => setFilterModalVisible(false)}
        />
      </SafeAreaView>
  );
}

const makeStyles = (colors) => StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: SALARY_THEME.colors.background,
  },
  scrollView: {
    flex: 1,
  },
  content: {
    paddingHorizontal: 20,
    paddingTop: 16,
    paddingBottom: 110,
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
    shadowColor: "#131C2A",
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
    fontSize: 15,
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
    fontSize: 13,
    fontWeight: "600",
    color: "#2E3A4A",
  },
  pickerOptionTextActive: {
    fontWeight: "800",
    color: colors.primary,
  },
});