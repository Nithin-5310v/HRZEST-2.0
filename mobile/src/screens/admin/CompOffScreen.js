import React, {
  useEffect,
  useMemo,
  useState,
} from "react";

import {
  ScrollView,
  StyleSheet,
  ActivityIndicator,
  RefreshControl,
  View,
  Alert,
} from "react-native";
import SafeAreaView from "../../components/SafeAreaView";

import AdminHeader from "../../components/admin/AdminHeader";

import CompOffHeaderCard from "../../components/admin/CompOffHeaderCard";
import CompOffSummaryCard from "../../components/admin/CompOffSummaryCard";
import CompOffStatsGrid from "../../components/admin/CompOffStatsGrid";
import MonthYearSelector from "../../components/admin/MonthYearSelector";
import CompOffSegmentTabs from "../../components/admin/CompOffSegmentTabs";
import OvertimeHistoryCard from "../../components/admin/OvertimeHistoryCard";
import CompOffBalanceCard from "../../components/admin/CompOffBalanceCard";
import CompOffQuickActions from "../../components/admin/CompOffQuickActions";
import CompOffAnalyticsCard from "../../components/admin/CompOffAnalyticsCard";
import CompOffInfoCard from "../../components/admin/CompOffInfoCard";
import CompOffFilterSheet from "../../components/admin/CompOffFilterSheet";
import CompOffBottomSheet from "../../components/admin/CompOffBottomSheet";
import CompOffEmptyState from "../../components/admin/CompOffEmptyState";

import COMPOFF_THEME from "../../constants/compOffTheme";
import { fetchOvertime, fetchCompOff, overtimeAction } from "../../api/client";

const MONTH_NAMES = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];
const monthOptions = MONTH_NAMES;
const currentYearNum = new Date().getFullYear();
const yearOptions = [currentYearNum - 1, currentYearNum, currentYearNum + 1].map(String);

// No backend concept of comp-off policy text exists (same as the Policies
// screen) -- this is legitimate static reference content, not fabricated
// business data.
const compOffPolicies = [
  { title: "Overtime Eligibility", description: "Overtime must be pre-approved and logged on the date it was worked." },
  { title: "Comp-Off Conversion", description: "Approved overtime accrues as comp-off days, tracked per employee." },
  { title: "Expiry", description: "Unused comp-off balances should be used within the current quarter per company policy." },
];

export default function CompOffScreen({
  navigation,
}) {
  const now = new Date();
  const [selectedMonth, setSelectedMonth] = useState(MONTH_NAMES[now.getMonth()]);
  const [selectedYear, setSelectedYear] = useState(String(now.getFullYear()));
  const [selectedTab, setSelectedTab] = useState("overtime");
  const [filterVisible, setFilterVisible] = useState(false);
  const [selectedStatus, setSelectedStatus] = useState("All");
  const [selectedDepartment, setSelectedDepartment] = useState("All");
  const [selectedRecord, setSelectedRecord] = useState(null);
  const [detailsVisible, setDetailsVisible] = useState(false);

  const [overtimeData, setOvertimeData] = useState([]);
  const [balancesData, setBalancesData] = useState([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [actionLoading, setActionLoading] = useState(false);

  const loadData = async () => {
    try {
      const [otRes, compRes] = await Promise.all([
        fetchOvertime().catch(() => null),
        fetchCompOff().catch(() => null),
      ]);
      if (otRes?.data?.ok && Array.isArray(otRes.data.overtime)) {
        setOvertimeData(otRes.data.overtime);
      } else {
        setOvertimeData([]);
      }
      if (compRes?.data?.ok && Array.isArray(compRes.data.balances)) {
        setBalancesData(compRes.data.balances);
      } else {
        setBalancesData([]);
      }
    } catch {
      setOvertimeData([]);
      setBalancesData([]);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  };

  useEffect(() => {
    loadData();
  }, []);

  const compOffSummary = useMemo(() => {
    const totalOtHours = overtimeData.reduce((acc, curr) => acc + Number(curr.hours || 0), 0);
    const approvedRequests = overtimeData.filter((item) => item.status === "Approved").length;
    const pendingApproval = overtimeData.filter((item) => item.status === "Pending").length;
    const rejectedRequests = overtimeData.filter((item) => item.status === "Rejected").length;
    // /api/compoff returns balance_days per employee -- no per-employee OT
    // pay rate is exposed by any Bearer-token API, so there's no honest way
    // to compute a rupee OT-pay figure here (it used to be a hardcoded
    // ₹250/hr guess, which has been removed rather than kept as fiction).
    const compOffAvailable = balancesData.reduce((acc, curr) => acc + Number(curr.balance_days || 0), 0);
    return {
      totalOtHours,
      approvedRequests,
      pendingApproval,
      rejectedRequests,
      compOffAvailable,
    };
  }, [overtimeData, balancesData]);

  const analytics = useMemo(() => {
    const totalRecords = overtimeData.length;
    const averageHours = totalRecords > 0 ? (compOffSummary.totalOtHours / totalRecords).toFixed(1) : "0.0";
    const approvalRate = totalRecords > 0
      ? Math.round((compOffSummary.approvedRequests / totalRecords) * 100)
      : 0;
    const weekAgo = new Date();
    weekAgo.setDate(weekAgo.getDate() - 7);
    const weeklyHours = overtimeData
      .filter((item) => item.date && new Date(item.date) >= weekAgo)
      .reduce((acc, curr) => acc + Number(curr.hours || 0), 0);
    const monthlyHours = overtimeData
      .filter((item) => {
        if (!item.date) return false;
        const d = new Date(item.date);
        return d.getMonth() === MONTH_NAMES.indexOf(selectedMonth) && String(d.getFullYear()) === selectedYear;
      })
      .reduce((acc, curr) => acc + Number(curr.hours || 0), 0);
    return { averageHours, approvalRate, weeklyHours, monthlyHours };
  }, [overtimeData, compOffSummary, selectedMonth, selectedYear]);

  const onRefresh = () => {
    setRefreshing(true);
    loadData();
  };

  const filteredHistory = useMemo(() => {
    return overtimeData.filter((item) => {
      const statusMatch =
        selectedStatus === "All" ||
        item.status === selectedStatus;
      const departmentMatch =
        selectedDepartment === "All" ||
        item.department === selectedDepartment;
      return statusMatch && departmentMatch;
    });
  }, [overtimeData, selectedStatus, selectedDepartment]);

  const openRecord = (item) => {
    setSelectedRecord(item);
    setDetailsVisible(true);
  };

  const handleOvertimeAction = async (action) => {
    if (!selectedRecord) return;
    setActionLoading(true);
    let res;
    try {
      res = await overtimeAction(selectedRecord.id, action);
    } catch (e) {
      res = e?.response;
    }
    setActionLoading(false);
    if (!res?.data?.ok) {
      Alert.alert("Action Failed", res?.data?.msg || "Could not update this overtime request.");
      return;
    }
    setDetailsVisible(false);
    setSelectedRecord(null);
    // compoff_minutes_credited is legitimately 0 when approved OT falls
    // below the configured minimum threshold -- that's still a successful
    // approval, not a no-op, so it needs its own message rather than no
    // alert at all.
    if (action === "approve") {
      if (res.data.compoff_minutes_credited) {
        Alert.alert("Approved", `Overtime approved -- ${Math.round(res.data.compoff_minutes_credited / 60)} hour(s) credited to comp-off balance.`);
      } else {
        Alert.alert("Approved", "Overtime approved.");
      }
    } else {
      Alert.alert("Rejected", "Overtime request rejected.");
    }
    loadData();
  };

  return (

    <SafeAreaView style={styles.container}>

      <AdminHeader
        title="OT & Comp-off"
        onMenu={() =>
          navigation.openDrawer()
        }
      />

      <ScrollView
        showsVerticalScrollIndicator={false}
        contentContainerStyle={
          styles.content
        }
      >

        <CompOffHeaderCard
          month={selectedMonth}
          year={selectedYear}
          totalHours={
            compOffSummary.totalOtHours
          }
          availableCompOff={
            compOffSummary.compOffAvailable
          }
          onSettings={() => {}}
        />

        <CompOffSummaryCard
          totalHours={
            compOffSummary.totalOtHours
          }
          totalRecords={overtimeData.length}
          pendingApproval={
            compOffSummary.pendingApproval
          }
          availableCompOff={
            compOffSummary.compOffAvailable
          }
        />

        <CompOffStatsGrid
          approvedRequests={
            compOffSummary.approvedRequests
          }
          pendingRequests={
            compOffSummary.pendingApproval
          }
          rejectedRequests={
            compOffSummary.rejectedRequests
          }
          averageHours={
            analytics.averageHours
          }
        />
                <CompOffQuickActions
          onApplyOT={() => {}}
          onRequestCompOff={() => {}}
          onHistory={() =>
            setSelectedTab("overtime")
          }
          onExport={() => {}}
        />

        <CompOffSegmentTabs
          selectedTab={selectedTab}
          onChangeTab={setSelectedTab}
        />

        <MonthYearSelector
          month={selectedMonth}
          year={selectedYear}
          months={monthOptions}
          years={yearOptions}
          onMonthChange={setSelectedMonth}
          onYearChange={setSelectedYear}
          onFilterPress={() =>
            setFilterVisible(true)
          }
        />

        {/* ==========================
            OVERTIME TAB
        ========================== */}

        {selectedTab === "overtime" && (

          filteredHistory.length > 0 ? (

            filteredHistory.map((item) => (

              <OvertimeHistoryCard
                key={item.id}
                item={item}
                onPress={openRecord}
              />

            ))

          ) : (

            <CompOffEmptyState
              title="No Overtime Records"
              description="There are no overtime entries available for the selected filters."
              buttonTitle="Clear Filters"
              onPress={() => {
                setSelectedStatus("All");
                setSelectedDepartment("All");
              }}
            />

          )

        )}

        {/* ==========================
            COMP-OFF TAB
        ========================== */}

        {selectedTab === "compoff" && (

          <>

            {balancesData.length === 0 ? (
              <CompOffEmptyState
                title="No Comp-off Balances"
                description="No employees have a comp-off balance on record yet."
              />
            ) : (
              balancesData.map((balance) => (

                <CompOffBalanceCard
                  key={balance.employee_id}
                  employeeName={balance.name}
                  department={balance.department}
                  availableDays={
                    balance.earned_days
                  }
                  usedDays={
                    balance.used_days
                  }
                  remainingDays={
                    balance.balance_days
                  }
                />

              ))
            )}

            <CompOffInfoCard
              policies={compOffPolicies}
            />

          </>

        )}
                {/* ==========================
            ANALYTICS TAB
        ========================== */}

        {selectedTab === "analytics" && (

          <>

            <CompOffAnalyticsCard
              weeklyHours={analytics.weeklyHours}
              monthlyHours={analytics.monthlyHours}
              averageHours={analytics.averageHours}
              approvalRate={analytics.approvalRate}
            />

            <CompOffInfoCard
              policies={compOffPolicies}
            />

          </>

        )}

      </ScrollView>

      {/* ==========================
          FILTER SHEET
      ========================== */}

      <CompOffFilterSheet
        visible={filterVisible}
        selectedStatus={selectedStatus}
        selectedDepartment={selectedDepartment}
        onSelectStatus={setSelectedStatus}
        onSelectDepartment={
          setSelectedDepartment
        }
        onApply={() =>
          setFilterVisible(false)
        }
        onReset={() => {
          setSelectedStatus("All");
          setSelectedDepartment("All");
        }}
        onClose={() =>
          setFilterVisible(false)
        }
      />

      {/* ==========================
          DETAILS SHEET
      ========================== */}

      <CompOffBottomSheet
        visible={detailsVisible}
        record={selectedRecord}
        actionLoading={actionLoading}
        onApprove={() => handleOvertimeAction("approve")}
        onReject={() => handleOvertimeAction("reject")}
        onClose={() => {
          setDetailsVisible(false);
          setSelectedRecord(null);
        }}
      />

    </SafeAreaView>

  );

}

const styles = StyleSheet.create({

  container: {

    flex: 1,

    backgroundColor:
      COMPOFF_THEME.colors.background,
  },

  content: {

    paddingHorizontal: 18,

    paddingTop: 12,

    paddingBottom: 120,
  },

});