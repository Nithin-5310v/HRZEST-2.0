import React, { useState, useCallback } from "react";
import {
  ScrollView,
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
  TextInput,
  ActivityIndicator,
  RefreshControl,
} from "react-native";
import SafeAreaView from "../../components/SafeAreaView";
import { Ionicons } from "@expo/vector-icons";
import { useFocusEffect } from "@react-navigation/native";

import ProfileHeader from "../../components/profile/ProfileHeader";
import EmptyState from "../../components/ui/EmptyState";
import LoadingSkeleton from "../../components/ui/LoadingSkeleton";
import OnboardingStatusCard from "../../components/onboarding/OnboardingStatusCard";

import { useAuth } from "../../store/AuthContext";
import { useTheme } from "../../store/ThemeContext";
import { fetchEmployeeProfile, fetchMyOnboarding, completeMyOnboardingTask } from "../../api/client";

export default function OnboardingScreen() {
  const { user } = useAuth();
  const { colors } = useTheme();
  const styles = React.useMemo(() => makeStyles(colors), [colors]);
  const [profile, setProfile] = useState(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [onboarding, setOnboarding] = useState(null);
  const [tasks, setTasks] = useState([]);
  const [noteDrafts, setNoteDrafts] = useState({});
  const [completingId, setCompletingId] = useState(null);

  const load = async () => {
    try {
      const [profRes, obRes] = await Promise.all([fetchEmployeeProfile(), fetchMyOnboarding()]);
      if (profRes?.data?.ok) setProfile(profRes.data.profile);
      if (obRes?.data?.ok) {
        const ob = (obRes.data.onboardings || []).find((o) => o.id === obRes.data.selected_ob_id) || null;
        setOnboarding(ob);
        setTasks(obRes.data.tasks || []);
      } else {
        setOnboarding(null);
        setTasks([]);
      }
    } catch (_) {
      setOnboarding(null);
      setTasks([]);
    }
    setLoading(false);
    setRefreshing(false);
  };

  useFocusEffect(
    useCallback(() => {
      load();
    }, [])
  );

  const handleCompleteTask = async (task) => {
    if (!onboarding) return;
    setCompletingId(task.id);
    try {
      const res = await completeMyOnboardingTask(task.id, onboarding.id, noteDrafts[task.id] || "");
      if (res?.data?.ok) {
        await load();
      }
    } catch (_) {
      // stays pending -- the list simply won't reflect a change
    }
    setCompletingId(null);
  };

  const progress = onboarding && onboarding.total_tasks > 0
    ? Math.round((onboarding.done_tasks / onboarding.total_tasks) * 100)
    : 0;

  return (
    <SafeAreaView style={styles.container}>
      <ProfileHeader
        title="My Onboarding"
        showBack={false}
      />

      <ScrollView
        showsVerticalScrollIndicator={false}
        contentContainerStyle={styles.content}
        refreshControl={
          <RefreshControl refreshing={refreshing} onRefresh={() => { setRefreshing(true); load(); }} colors={[colors.primary]} />
        }
      >
        {loading ? (
          <LoadingSkeleton height={110} radius={24} />
        ) : !onboarding ? (
          <>
            <EmptyState
              icon="checkmark-done-circle-outline"
              title="No onboarding assigned yet"
              description="Your HR team hasn't assigned an onboarding checklist to your account. Check back later or reach out to HR."
            />
          </>
        ) : (
          <>
            <OnboardingStatusCard
              employeeName={profile?.name || user?.name || "Employee"}
              employeeId={profile?.employee_id || user?.employeeId || "-"}
              status={onboarding.status}
              progress={progress}
              department={profile?.department || "—"}
              joinedDate={profile?.join_date || "—"}
            />

            <Text style={styles.sectionTitle}>
              {onboarding.template_name} &middot; {onboarding.done_tasks}/{onboarding.total_tasks} done
            </Text>

            {tasks.map((task) => {
              const isDone = task.status === "Done";
              return (
                <View key={task.id} style={[styles.taskCard, isDone && styles.taskCardDone]}>
                  <View style={styles.taskHeader}>
                    <Ionicons
                      name={isDone ? "checkmark-circle" : "ellipse-outline"}
                      size={22}
                      color={isDone ? "#16A34A" : colors.textLight}
                    />
                    <Text style={[styles.taskTitle, isDone && styles.taskTitleDone]}>{task.task_title}</Text>
                  </View>
                  {!!task.task_description && <Text style={styles.taskDesc}>{task.task_description}</Text>}

                  {isDone ? (
                    <Text style={styles.completedAt}>
                      Completed {task.completed_at ? new Date(task.completed_at).toLocaleDateString("en-IN") : ""}
                      {task.employee_note ? ` — "${task.employee_note}"` : ""}
                    </Text>
                  ) : (
                    <View style={styles.taskAction}>
                      <TextInput
                        style={styles.noteInput}
                        placeholder="Add a note (optional)"
                        value={noteDrafts[task.id] || ""}
                        onChangeText={(t) => setNoteDrafts((prev) => ({ ...prev, [task.id]: t }))}
                      />
                      <TouchableOpacity
                        style={styles.doneBtn}
                        onPress={() => handleCompleteTask(task)}
                        disabled={completingId === task.id}
                      >
                        {completingId === task.id ? (
                          <ActivityIndicator color="#FFFFFF" size="small" />
                        ) : (
                          <Text style={styles.doneBtnText}>Mark Done</Text>
                        )}
                      </TouchableOpacity>
                    </View>
                  )}
                </View>
              );
            })}
          </>
        )}
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
    paddingHorizontal: 18,
    paddingBottom: 120,
    paddingTop: 18,
  },

  sectionTitle: {
    fontSize: 13,
    fontWeight: "700",
    color: colors.textSecondary,
    marginBottom: 12,
  },

  taskCard: {
    backgroundColor: colors.card,
    borderRadius: 16,
    padding: 16,
    marginBottom: 12,
    borderWidth: 1,
    borderColor: colors.border,
  },
  taskCardDone: {
    backgroundColor: "#F0FDF4",
    borderColor: "#BBF7D0",
  },
  taskHeader: { flexDirection: "row", alignItems: "center", gap: 10 },
  taskTitle: { fontSize: 14, fontWeight: "700", color: colors.text, flex: 1 },
  taskTitleDone: { color: "#166534", textDecorationLine: "line-through" },
  taskDesc: { fontSize: 12.5, color: colors.textSecondary, marginTop: 6, marginLeft: 32 },
  completedAt: { fontSize: 11.5, color: "#16A34A", marginTop: 8, marginLeft: 32, fontWeight: "600" },
  taskAction: { marginTop: 12, marginLeft: 32 },
  noteInput: {
    backgroundColor: colors.background,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: 10,
    padding: 9,
    fontSize: 13,
    color: colors.text,
    marginBottom: 8,
  },
  doneBtn: {
    backgroundColor: colors.primary,
    borderRadius: 10,
    paddingVertical: 10,
    alignItems: "center",
  },
  doneBtnText: { color: "#FFFFFF", fontWeight: "700", fontSize: 13 },
});
