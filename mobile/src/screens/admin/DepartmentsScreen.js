import React, { useState, useEffect } from "react";
import {
  ScrollView,
  StyleSheet,
  View,
  Text,
  ActivityIndicator,
  RefreshControl,
} from "react-native";
import SafeAreaView from "../../components/SafeAreaView";

import AdminHeader from "../../components/admin/AdminHeader";
import AdminSearchBar from "../../components/admin/AdminSearchBar";
import DashboardStatCard from "../../components/admin/DashboardStatCard";
import THEME from "../../constants/theme";
import { useTheme } from "../../store/ThemeContext";
import { fetchDepartments } from "../../api/client";

export default function DepartmentsScreen() {
  const { colors } = useTheme();
  const styles = React.useMemo(() => makeStyles(colors), [colors]);
  const [search, setSearch] = useState("");
  const [departments, setDepartments] = useState([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  const loadData = async () => {
    try {
      const res = await fetchDepartments();
      if (res?.data?.ok && Array.isArray(res.data.departments)) {
        setDepartments(res.data.departments);
      } else {
        setDepartments([]);
      }
    } catch {
      setDepartments([]);
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

  const filteredDepts = departments.filter((d) =>
    d.name.toLowerCase().includes(search.toLowerCase())
  );

  const totalEmployees = departments.reduce((acc, d) => acc + (d.count || 0), 0);

  return (
    <SafeAreaView style={styles.container}>
      <AdminHeader title="Departments" />

      <ScrollView
        showsVerticalScrollIndicator={false}
        contentContainerStyle={styles.content}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} />}
      >
        <AdminSearchBar
          value={search}
          onChangeText={setSearch}
          placeholder="Search departments..."
        />

        {/* Department Summary */}
        <View style={styles.grid}>
          <DashboardStatCard
            title="Departments"
            value={String(departments.length)}
            subtitle="Total Departments"
            icon="business-outline"
            iconColor={colors.primary}
            iconBackground={colors.blueBg}
          />

          <DashboardStatCard
            title="Employees"
            value={String(totalEmployees)}
            subtitle="Across Departments"
            icon="people-outline"
            iconColor={colors.success}
            iconBackground={colors.greenBg}
          />
        </View>

        {loading ? (
          <ActivityIndicator size="large" color={colors.primary} style={{ marginTop: 40 }} />
        ) : filteredDepts.length === 0 ? (
          <View style={styles.emptyContainer}>
            <Text style={styles.emptyText}>No departments found.</Text>
          </View>
        ) : (
          filteredDepts.map((d, i) => (
            <View key={i} style={styles.departmentCard}>
              <View style={styles.departmentInfo}>
                <Text style={styles.departmentName}>{d.name}</Text>
                <Text style={styles.departmentDetails}>Active Employees: {d.count}</Text>
              </View>

              <View style={styles.rightSection}>
                <View style={[styles.statusBadge, { backgroundColor: colors.greenBg }]}>
                  <Text style={[styles.statusText, { color: colors.success }]}>Active</Text>
                </View>
              </View>
            </View>
          ))
        )}
        <View style={{ height: 110 }} />
      </ScrollView>
    </SafeAreaView>
  );
}

const makeStyles = (colors) => StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: colors.background,
  },

  content: {
    paddingHorizontal: THEME.spacing.screenHorizontal,
    paddingTop: THEME.spacing.screenVertical,
    paddingBottom: 30,
  },

  grid: {
    flexDirection: "row",
    flexWrap: "wrap",
    justifyContent: "space-between",
    marginBottom: THEME.spacing.sectionGap,
  },

  departmentCard: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",

    backgroundColor: colors.card,

    borderRadius: THEME.radius.card,

    padding: THEME.spacing.cardPadding,

    marginBottom: THEME.spacing.cardGap,

    borderWidth: 1,
    borderColor: colors.border,

    ...THEME.shadows.sm,
  },

  departmentInfo: {
    flex: 1,
  },

  departmentName: {
    ...THEME.typography.cardTitle,
    color: colors.text,
  },

  departmentHead: {
    marginTop: 6,
    ...THEME.typography.body,
    color: colors.textSecondary,
  },

  departmentDetails: {
    marginTop: 4,
    ...THEME.typography.caption,
    color: colors.textSecondary,
  },

  departmentBudget: {
    marginTop: 8,
    ...THEME.typography.bodyMedium,
    color: colors.primary,
    fontWeight: "700",
  },

  rightSection: {
    alignItems: "flex-end",
    justifyContent: "center",
    marginLeft: 16,
  },

  statusBadge: {
    paddingHorizontal: 14,
    paddingVertical: 6,
    borderRadius: 20,
  },

  statusText: {
    fontSize: 12,
    fontWeight: "700",
  },
});