import React, { useState } from "react";
import {
  ScrollView,
  StyleSheet,
  View,
  Text,
  TouchableOpacity,
  RefreshControl,
  ActivityIndicator,
  Modal,
  Alert,
} from "react-native";
import SafeAreaView from "../../components/SafeAreaView";
import { DrawerActions } from "@react-navigation/native";
import { LinearGradient } from "expo-linear-gradient";
import { Ionicons } from "@expo/vector-icons";
import { useTheme } from "../../store/ThemeContext";
import AdminHeader from "../../components/admin/AdminHeader";
import AdminSearchBar from "../../components/admin/AdminSearchBar";

import { fetchOnboarding, fetchOnboardingTasks, updateOnboardingTaskStatus } from "../../api/client";

export default function OnboardingScreen({ navigation }) {
  const { colors } = useTheme();
  const styles = React.useMemo(() => makeStyles(colors), [colors]);
  const [search, setSearch] = useState("");
  const [refreshing, setRefreshing] = useState(false);
  const [loading, setLoading] = useState(true);
  const [onboardings, setOnboardings] = useState([]);

  // Task checklist modal -- real per-task view/toggle via
  // /api/onboarding/<id>/tasks and /api/onboarding/task/<id>/update
  // (blueprints/onboarding.py), where this screen used to show only a
  // read-only progress bar with no way to see or complete individual tasks.
  const [taskModalVisible, setTaskModalVisible] = useState(false);
  const [taskModalOnboarding, setTaskModalOnboarding] = useState(null);
  const [tasks, setTasks] = useState([]);
  const [tasksLoading, setTasksLoading] = useState(false);
  const [updatingTaskId, setUpdatingTaskId] = useState(null);

  const loadData = async () => {
    try {
      const res = await fetchOnboarding();
      if (res?.data?.ok && Array.isArray(res.data.onboardings)) {
        setOnboardings(res.data.onboardings);
      } else {
        setOnboardings([]);
      }
    } catch {
      setOnboardings([]);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  };

  React.useEffect(() => {
    loadData();
  }, []);

  const onRefresh = () => {
    setRefreshing(true);
    loadData();
  };

  const openTaskModal = async (item) => {
    setTaskModalOnboarding(item);
    setTaskModalVisible(true);
    setTasksLoading(true);
    try {
      const res = await fetchOnboardingTasks(item.id);
      setTasks(res?.data?.ok && Array.isArray(res.data.tasks) ? res.data.tasks : []);
    } catch (e) {
      setTasks([]);
    } finally {
      setTasksLoading(false);
    }
  };

  const toggleTask = async (task) => {
    const nextStatus = task.status === "Done" ? "Pending" : "Done";
    setUpdatingTaskId(task.id);
    let res;
    try {
      res = await updateOnboardingTaskStatus(task.id, nextStatus);
    } catch (e) {
      res = e?.response;
    }
    setUpdatingTaskId(null);
    if (!res?.data?.ok) {
      Alert.alert("Update Failed", res?.data?.msg || "Could not update this task.");
      return;
    }
    setTasks((prev) => prev.map((t) => (t.id === task.id ? { ...t, status: nextStatus } : t)));
    if (res.data.onboarding_completed) {
      Alert.alert("Onboarding Complete", `All tasks done for ${taskModalOnboarding?.employeeName}.`);
    }
    loadData();
  };

  const filteredOnboardings = onboardings.filter(
    (o) =>
      o.employeeName.toLowerCase().includes(search.toLowerCase()) ||
      o.role.toLowerCase().includes(search.toLowerCase())
  );

  const activeCount = onboardings.length;
  const totalTasks = onboardings.reduce((sum, item) => sum + Number(item.totalTasks || 0), 0);
  const completedTasks = onboardings.reduce((sum, item) => sum + Number(item.tasksCompleted || 0), 0);
  const avgProgress = totalTasks > 0 ? Math.round((completedTasks / totalTasks) * 100) : 0;

  return (
    <LinearGradient
      colors={colors.screenGradient}
      style={styles.container}
    >
      <SafeAreaView style={{ flex: 1 }}>
        <AdminHeader
          title="Onboarding & Tasks"
          onMenu={() => navigation.dispatch(DrawerActions.openDrawer())}
        />

        <ScrollView
          showsVerticalScrollIndicator={false}
          contentContainerStyle={styles.content}
          refreshControl={
            <RefreshControl
              refreshing={refreshing}
              onRefresh={onRefresh}
              colors={[colors.primary]}
            />
          }
        >
          {/* Summary Hero Card */}
          <LinearGradient
            colors={[colors.primary, "#0B5CAD"]}
            start={{ x: 0, y: 0 }}
            end={{ x: 1, y: 1 }}
            style={styles.heroCard}
          >
            <Text style={styles.heroSub}>New Hire Workflow</Text>
            <Text style={styles.heroTitle}>{activeCount} Active Onboardings</Text>
            <View style={styles.heroStatsRow}>
              <View style={styles.heroStatItem}>
                <Text style={styles.heroStatValue}>{completedTasks} / {totalTasks}</Text>
                <Text style={styles.heroStatLabel}>Tasks Done</Text>
              </View>
              <View style={styles.heroStatDivider} />
              <View style={styles.heroStatItem}>
                <Text style={styles.heroStatValue}>{avgProgress}%</Text>
                <Text style={styles.heroStatLabel}>Avg Progress</Text>
              </View>
            </View>
          </LinearGradient>

          <AdminSearchBar
            value={search}
            onChangeText={setSearch}
            placeholder="Search new hires..."
          />

          <View style={styles.sectionHeader}>
            <Text style={styles.sectionTitle}>Onboarding Pipeline</Text>
            <Text style={styles.sectionBadge}>
              {filteredOnboardings.length} Active
            </Text>
          </View>

          {loading ? (
            <ActivityIndicator size="large" color="#0B5CAD" style={{ marginTop: 24 }} />
          ) : filteredOnboardings.length === 0 ? (
            <View style={{ backgroundColor: colors.card, borderRadius: 16, padding: 32, alignItems: "center", justifyContent: "center", borderWidth: 1, borderColor: colors.border, marginTop: 10 }}>
              <Ionicons name="person-add-outline" size={48} color={colors.textLight} />
              <Text style={{ fontSize: 16, fontWeight: "700", color: "#2E3A4A", marginTop: 12 }}>
                No Active Onboardings
              </Text>
              <Text style={{ fontSize: 13, color: "#5A6678", textAlign: "center", marginTop: 4 }}>
                Newly registered staff members undergoing onboarding tasks will appear here.
              </Text>
            </View>
          ) : (
            filteredOnboardings.map((item) => {
              const progress = (item.tasksCompleted / item.totalTasks) * 100;

              return (
                <TouchableOpacity key={item.id} style={styles.card} activeOpacity={0.85} onPress={() => openTaskModal(item)}>
                  <View style={styles.cardHeader}>
                    <View style={styles.iconCircle}>
                      <Ionicons name="person-add-outline" size={20} color="#0B5CAD" />
                    </View>
                    <View style={{ flex: 1, marginLeft: 12 }}>
                      <Text style={styles.name}>{item.employeeName}</Text>
                      <Text style={styles.role}>{item.role}</Text>
                    </View>
                    <View
                      style={[
                        styles.statusPill,
                        item.status === "Completed"
                          ? styles.pillCompleted
                          : item.status === "In Progress"
                          ? styles.pillProgress
                          : styles.pillPending,
                      ]}
                    >
                      <Text
                        style={[
                          styles.statusText,
                          item.status === "Completed"
                            ? styles.textCompleted
                            : item.status === "In Progress"
                            ? styles.textProgress
                            : styles.textPending,
                        ]}
                      >
                        {item.status}
                      </Text>
                    </View>
                  </View>

                  <View style={styles.progressContainer}>
                    <View style={styles.progressLabelRow}>
                      <Text style={styles.progressLabel}>Task Completion</Text>
                      <Text style={styles.progressValue}>
                        {item.tasksCompleted} / {item.totalTasks} ({progress.toFixed(0)}%)
                      </Text>
                    </View>
                    <View style={styles.progressBarTrack}>
                      <View
                        style={[
                          styles.progressBarFill,
                          { width: `${progress}%` },
                        ]}
                      />
                    </View>
                  </View>

                  <View style={styles.cardFooter}>
                    <Ionicons name="calendar-outline" size={14} color="#5A6678" />
                    <Text style={styles.footerText}>
                      Join Date: {item.startDate}
                    </Text>
                  </View>
                </TouchableOpacity>
              );
            })
          )}

          <View style={{ height: 100 }} />
        </ScrollView>

        {/* Task Checklist Modal */}
        <Modal visible={taskModalVisible} transparent animationType="slide" onRequestClose={() => setTaskModalVisible(false)}>
          <View style={{ flex: 1, backgroundColor: "rgba(15, 23, 42, 0.8)", justifyContent: "flex-end" }}>
            <View style={{ backgroundColor: colors.card, borderTopLeftRadius: 28, borderTopRightRadius: 28, padding: 20, maxHeight: "85%" }}>
              <View style={{ flexDirection: "row", justifyContent: "space-between", alignItems: "center", marginBottom: 14, paddingBottom: 10, borderBottomWidth: 1, borderBottomColor: "#EDF2F8" }}>
                <Text style={{ fontSize: 17, fontWeight: "800", color: colors.text }}>
                  {taskModalOnboarding?.employeeName}'s Tasks
                </Text>
                <TouchableOpacity onPress={() => setTaskModalVisible(false)}>
                  <Ionicons name="close-circle" size={26} color="#5A6678" />
                </TouchableOpacity>
              </View>

              {tasksLoading ? (
                <ActivityIndicator size="large" color="#0B5CAD" style={{ marginVertical: 30 }} />
              ) : tasks.length === 0 ? (
                <Text style={{ textAlign: "center", color: "#5A6678", marginVertical: 30 }}>No tasks assigned.</Text>
              ) : (
                <ScrollView showsVerticalScrollIndicator={false}>
                  {tasks.map((task) => (
                    <View key={task.id} style={{ flexDirection: "row", alignItems: "flex-start", paddingVertical: 12, borderBottomWidth: 1, borderBottomColor: "#EDF2F8" }}>
                      <TouchableOpacity
                        onPress={() => toggleTask(task)}
                        disabled={updatingTaskId === task.id}
                        style={{
                          width: 26, height: 26, borderRadius: 8, marginRight: 12, marginTop: 2,
                          alignItems: "center", justifyContent: "center",
                          backgroundColor: task.status === "Done" ? "#16A34A" : "#EDF2F8",
                          borderWidth: task.status === "Done" ? 0 : 1, borderColor: "#C3CFDD",
                        }}
                      >
                        {updatingTaskId === task.id ? (
                          <ActivityIndicator size="small" color={task.status === "Done" ? "#FFFFFF" : "#0B5CAD"} />
                        ) : task.status === "Done" ? (
                          <Ionicons name="checkmark" size={16} color="#FFFFFF" />
                        ) : null}
                      </TouchableOpacity>
                      <View style={{ flex: 1 }}>
                        <Text style={{ fontSize: 14, fontWeight: "700", color: colors.text, textDecorationLine: task.status === "Done" ? "line-through" : "none" }}>
                          {task.title}
                        </Text>
                        {!!task.description && (
                          <Text style={{ fontSize: 12, color: "#5A6678", marginTop: 2 }}>{task.description}</Text>
                        )}
                        {task.requires_document && (
                          <Text style={{ fontSize: 11, color: "#B45309", marginTop: 4, fontWeight: "700" }}>Requires document upload</Text>
                        )}
                      </View>
                    </View>
                  ))}
                </ScrollView>
              )}
            </View>
          </View>
        </Modal>
      </SafeAreaView>
    </LinearGradient>
  );
}

const makeStyles = (colors) => StyleSheet.create({
  container: { flex: 1 },
  content: { paddingHorizontal: 20, paddingTop: 10 },
  heroCard: {
    backgroundColor: "#1E293B",
    borderRadius: 24,
    padding: 20,
    marginBottom: 18,
    elevation: 4,
  },
  heroSub: { color: colors.textLight, fontSize: 12, fontWeight: "600" },
  heroTitle: { color: "#FFFFFF", fontSize: 18, fontWeight: "800", marginTop: 4 },
  heroStatsRow: {
    flexDirection: "row",
    justifyContent: "space-around",
    marginTop: 18,
    paddingTop: 14,
    borderTopWidth: 1,
    borderTopColor: "rgba(255,255,255,0.1)",
  },
  heroStatItem: { alignItems: "center" },
  heroStatValue: { color: "#6AAEF0", fontSize: 16, fontWeight: "800" },
  heroStatLabel: { color: colors.textLight, fontSize: 11, marginTop: 2 },
  heroStatDivider: { width: 1, height: 28, backgroundColor: "rgba(255,255,255,0.1)" },
  sectionHeader: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    marginTop: 18,
    marginBottom: 12,
  },
  sectionTitle: { fontSize: 14, fontWeight: "800", color: colors.text },
  sectionBadge: { fontSize: 12, fontWeight: "700", color: "#0B5CAD" },
  card: {
    backgroundColor: colors.card,
    borderRadius: 20,
    padding: 16,
    marginBottom: 12,
    elevation: 2,
    borderWidth: 1,
    borderColor: colors.border,
  },
  cardHeader: { flexDirection: "row", alignItems: "center" },
  iconCircle: {
    width: 42,
    height: 42,
    borderRadius: 21,
    backgroundColor: colors.blueBg,
    justifyContent: "center",
    alignItems: "center",
  },
  name: { fontSize: 13, fontWeight: "700", color: colors.text },
  role: { fontSize: 13, color: "#5A6678", marginTop: 2 },
  statusPill: { paddingHorizontal: 10, paddingVertical: 4, borderRadius: 12 },
  pillCompleted: { backgroundColor: "#DCFCE7" },
  pillProgress: { backgroundColor: "#E0F2FE" },
  pillPending: { backgroundColor: "#FEF3C7" },
  statusText: { fontSize: 11, fontWeight: "700" },
  textCompleted: { color: "#166534" },
  textProgress: { color: "#075985" },
  textPending: { color: "#B45309" },
  progressContainer: { marginTop: 14 },
  progressLabelRow: { flexDirection: "row", justifyContent: "space-between", marginBottom: 6 },
  progressLabel: { fontSize: 12, color: "#5A6678", fontWeight: "600" },
  progressValue: { fontSize: 12, color: colors.text, fontWeight: "700" },
  progressBarTrack: { height: 8, backgroundColor: "#EDF2F8", borderRadius: 4, overflow: "hidden" },
  progressBarFill: { height: "100%", backgroundColor: colors.employee, borderRadius: 4 },
  cardFooter: { flexDirection: "row", alignItems: "center", marginTop: 12, paddingTop: 10, borderTopWidth: 1, borderTopColor: colors.background },
  footerText: { marginLeft: 6, fontSize: 12, color: "#5A6678" },
});
