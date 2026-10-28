import React, { useState, useCallback } from "react";
import {
  ScrollView,
  StyleSheet,
  View,
  Text,
  TouchableOpacity,
  Alert,
  ActivityIndicator,
  Switch,
} from "react-native";
import SafeAreaView from "../../components/SafeAreaView";
import { LinearGradient } from "expo-linear-gradient";
import { Ionicons } from "@expo/vector-icons";
import { useFocusEffect } from "@react-navigation/native";

import ProfileHeader from "../../components/profile/ProfileHeader";
import { useTheme } from "../../store/ThemeContext";
import { fetchEmployeePortal, submitResignation } from "../../api/client";

const REASONS = [
  { icon: "trending-up-outline", label: "Career Growth" },
  { icon: "briefcase-outline", label: "Better Opportunity" },
  { icon: "home-outline", label: "Personal Reasons" },
  { icon: "fitness-outline", label: "Health Issues" },
  { icon: "navigate-outline", label: "Relocation" },
  { icon: "create-outline", label: "Other" },
];

function addDays(n) {
  const d = new Date();
  d.setDate(d.getDate() + n);
  return d.toISOString().split("T")[0];
}

export default function ResignScreen() {
  const { colors } = useTheme();
  const styles = React.useMemo(() => makeStyles(colors), [colors]);
  const [existing, setExisting] = useState(null);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);

  const [lastDay, setLastDay] = useState(addDays(30));
  const [reason, setReason] = useState("");
  const [confirmed, setConfirmed] = useState(false);

  const loadData = async () => {
    try {
      const res = await fetchEmployeePortal();
      if (res && res.data && res.data.ok) {
        setExisting(res.data.resignation);
      }
    } catch (_) {}
    setLoading(false);
  };

  useFocusEffect(
    useCallback(() => {
      loadData();
    }, [])
  );

  const handleSubmit = () => {
    if (!reason) {
      Alert.alert("Reason Required", "Please select a reason for your resignation.");
      return;
    }
    if (!confirmed) {
      Alert.alert("Confirmation Required", "Please acknowledge the notice statement before submitting.");
      return;
    }

    Alert.alert(
      "Confirm Resignation",
      `Last Working Day: ${lastDay}\nReason: ${reason}\n\nThis official action cannot be undone. Are you sure you want to submit?`,
      [
        { text: "Cancel", style: "cancel" },
        {
          text: "Submit Resignation",
          style: "destructive",
          onPress: async () => {
            setSubmitting(true);
            try {
              const res = await submitResignation(lastDay, reason);
              if (res && res.data && res.data.ok) {
                Alert.alert("Submitted", "Your resignation request has been submitted successfully.");
                await loadData();
              } else {
                Alert.alert("Error", res?.data?.msg || "Failed to submit resignation.");
              }
            } catch (e) {
              Alert.alert("Error", e.response?.data?.msg || "Failed to submit resignation.");
            } finally {
              setSubmitting(false);
            }
          },
        },
      ]
    );
  };

  const dateOptions = Array.from({ length: 61 }, (_, i) => addDays(30 + i));

  if (loading) {
    return (
      <LinearGradient colors={colors.screenGradient} style={styles.centerContainer}>
        <ActivityIndicator size="large" color={colors.primary} />
      </LinearGradient>
    );
  }

  // Show status view if active resignation exists and is not Declined
  if (existing && existing.status !== "Declined") {
    const isAccepted = existing.status === "Accepted" || existing.status === "Approved";
    const isPending = existing.status === "Pending" || !existing.status;

    return (
      <LinearGradient colors={colors.screenGradient} style={styles.container}>
        <SafeAreaView style={{ flex: 1 }}>
          <ProfileHeader title="Resignation Status" subtitle="EMPLOYEE PORTAL" />

          <ScrollView
            showsVerticalScrollIndicator={false}
            contentContainerStyle={styles.content}
          >
            {/* Status Header Banner Card */}
            <View style={[styles.card, isAccepted ? styles.cardAccepted : styles.cardPending]}>
              <View style={styles.statusHeaderRow}>
                <View style={styles.statusTitleGroup}>
                  <Text style={styles.statusHeaderLabel}>CURRENT STATUS</Text>
                  <Text style={[styles.statusHeaderTitle, { color: isAccepted ? colors.success : colors.warning }]}>
                    {existing.status}
                  </Text>
                </View>
                <View style={[styles.badgePill, { backgroundColor: isAccepted ? colors.greenBg : colors.yellowBg }]}>
                  <Ionicons
                    name={isAccepted ? "checkmark-circle" : "time"}
                    size={14}
                    color={isAccepted ? colors.success : colors.warning}
                    style={{ marginRight: 4 }}
                  />
                  <Text style={[styles.badgeText, { color: isAccepted ? colors.success : colors.warning }]}>
                    {existing.status}
                  </Text>
                </View>
              </View>

              <View style={styles.divider} />

              <View style={styles.detailItem}>
                <View style={styles.detailIconCircle}>
                  <Ionicons name="calendar-outline" size={16} color={colors.danger} />
                </View>
                <View style={{ flex: 1 }}>
                  <Text style={styles.detailLabel}>Last Working Day</Text>
                  <Text style={styles.detailValueRed}>{existing.last_working_day}</Text>
                </View>
              </View>

              {!!existing.reason && (
                <View style={styles.reasonBox}>
                  <Text style={styles.reasonBoxLabel}>REASON</Text>
                  <Text style={styles.reasonBoxText}>{existing.reason}</Text>
                </View>
              )}

              {!!existing.created_at && (
                <View style={styles.submittedRow}>
                  <Ionicons name="time-outline" size={13} color={colors.textSecondary} />
                  <Text style={styles.submittedText}>
                    Submitted on {existing.created_at?.slice(0, 10)}
                  </Text>
                </View>
              )}
            </View>

            {/* Info Hint Card */}
            {isPending && (
              <View style={styles.infoBanner}>
                <Ionicons name="hourglass-outline" size={20} color={colors.warning} style={{ marginRight: 10 }} />
                <Text style={styles.infoBannerText}>
                  Your resignation request is currently under review by management. You will be notified once a decision is made.
                </Text>
              </View>
            )}

            {isAccepted && (
              <View style={[styles.infoBanner, { backgroundColor: colors.greenBg, borderColor: "#A7F3D0" }]}>
                <Ionicons name="checkmark-done-circle-outline" size={20} color={colors.success} style={{ marginRight: 10 }} />
                <Text style={[styles.infoBannerText, { color: colors.success }]}>
                  Your resignation request has been accepted. Please coordinate with HR for handover and offboarding formalities.
                </Text>
              </View>
            )}
          </ScrollView>
        </SafeAreaView>
      </LinearGradient>
    );
  }

  return (
    <LinearGradient colors={colors.screenGradient} style={styles.container}>
      <SafeAreaView style={{ flex: 1 }}>
        <ProfileHeader title="Resignation Request" subtitle="EMPLOYEE PORTAL" />

        <ScrollView
          showsVerticalScrollIndicator={false}
          contentContainerStyle={styles.content}
        >
          {existing?.status === "Declined" && (
            <View style={styles.declinedNotice}>
              <Ionicons name="alert-circle" size={18} color={colors.danger} style={{ marginRight: 8 }} />
              <Text style={styles.declinedNoticeText}>
                Your previous resignation request was declined by management. You may submit a new request if needed.
              </Text>
            </View>
          )}

          {/* Warning Notice Card */}
          <View style={styles.warningCard}>
            <View style={styles.warningTitleRow}>
              <Ionicons name="warning" size={18} color={colors.danger} style={{ marginRight: 6 }} />
              <Text style={styles.warningTitle}>Notice Policy Guidelines</Text>
            </View>
            <Text style={styles.warningText}>• Standard 30-day notice period is required.</Text>
            <Text style={styles.warningText}>• Last working day must be at least 30 days from today.</Text>
            <Text style={styles.warningText}>• HR and management will be notified immediately.</Text>
            <Text style={styles.warningText}>• Resignation requests are binding once submitted.</Text>
          </View>

          {/* Last Working Day Picker */}
          <View style={styles.card}>
            <Text style={styles.cardHeaderTitle}>Select Last Working Day</Text>
            <Text style={styles.cardHeaderSubtitle}>Minimum 30 days notice from today</Text>

            <ScrollView
              horizontal
              showsHorizontalScrollIndicator={false}
              contentContainerStyle={styles.dateChipsRow}
            >
              {dateOptions
                .filter((_, i) => i % 3 === 0)
                .slice(0, 10)
                .map((d) => {
                  const active = lastDay === d;
                  const dateObj = new Date(d);
                  const dayNum = d.split("-")[2];
                  const monthName = dateObj.toLocaleString("default", { month: "short" });

                  return (
                    <TouchableOpacity
                      key={d}
                      activeOpacity={0.7}
                      style={[styles.dateChip, active && styles.dateChipActive]}
                      onPress={() => setLastDay(d)}
                    >
                      <Text style={[styles.dateNum, active && styles.dateNumActive]}>
                        {dayNum}
                      </Text>
                      <Text style={[styles.dateMon, active && styles.dateMonActive]}>
                        {monthName}
                      </Text>
                    </TouchableOpacity>
                  );
                })}
            </ScrollView>

            <View style={styles.selectedDateBadge}>
              <Ionicons name="calendar-sharp" size={14} color={colors.primary} style={{ marginRight: 6 }} />
              <Text style={styles.selectedDateText}>
                Selected Last Day: <Text style={{ fontWeight: "800", color: colors.primary }}>{new Date(lastDay).toDateString()}</Text>
              </Text>
            </View>
          </View>

          {/* Reason Selection */}
          <View style={styles.card}>
            <Text style={styles.cardHeaderTitle}>Reason for Resignation</Text>
            <Text style={styles.cardHeaderSubtitle}>Select the primary reason for your departure</Text>

            <View style={styles.reasonChipsGrid}>
              {REASONS.map((r) => {
                const active = reason === r.label;
                return (
                  <TouchableOpacity
                    key={r.label}
                    activeOpacity={0.7}
                    style={[styles.reasonChip, active && styles.reasonChipActive]}
                    onPress={() => setReason(r.label)}
                  >
                    <Ionicons
                      name={r.icon}
                      size={14}
                      color={active ? "#FFFFFF" : colors.textSecondary}
                      style={{ marginRight: 6 }}
                    />
                    <Text style={[styles.reasonChipText, active && styles.reasonChipTextActive]}>
                      {r.label}
                    </Text>
                  </TouchableOpacity>
                );
              })}
            </View>
          </View>

          {/* Confirmation Switch Card */}
          <View style={styles.confirmCard}>
            <Switch
              value={confirmed}
              onValueChange={setConfirmed}
              trackColor={{ true: colors.danger, false: colors.border }}
              thumbColor="#FFFFFF"
            />
            <Text style={styles.confirmText}>
              I confirm that this resignation request is official and I understand it will be logged with HR.
            </Text>
          </View>

          {/* Submit Button */}
          <TouchableOpacity
            activeOpacity={0.8}
            style={[styles.submitBtn, (!confirmed || submitting) && styles.submitBtnDisabled]}
            disabled={!confirmed || submitting}
            onPress={handleSubmit}
          >
            {submitting ? (
              <ActivityIndicator color="#FFFFFF" />
            ) : (
              <>
                <Ionicons name="paper-plane-sharp" size={18} color="#FFFFFF" style={{ marginRight: 8 }} />
                <Text style={styles.submitBtnText}>Submit Official Resignation</Text>
              </>
            )}
          </TouchableOpacity>
        </ScrollView>
      </SafeAreaView>
    </LinearGradient>
  );
}

const makeStyles = (colors) => StyleSheet.create({
  container: {
    flex: 1,
  },
  centerContainer: {
    flex: 1,
    justifyContent: "center",
    alignItems: "center",
  },
  content: {
    padding: 16,
    paddingBottom: 90,
  },
  declinedNotice: {
    flexDirection: "row",
    alignItems: "center",
    backgroundColor: colors.redBg,
    borderRadius: 12,
    padding: 12,
    marginBottom: 16,
    borderWidth: 1,
    borderColor: "#FCA5A5",
  },
  declinedNoticeText: {
    flex: 1,
    fontSize: 12,
    color: colors.danger,
    fontWeight: "600",
  },
  warningCard: {
    backgroundColor: colors.redBg,
    borderRadius: 16,
    padding: 16,
    marginBottom: 16,
    borderWidth: 1,
    borderColor: "#FECACA",
  },
  warningTitleRow: {
    flexDirection: "row",
    alignItems: "center",
    marginBottom: 8,
  },
  warningTitle: {
    fontSize: 14,
    fontWeight: "700",
    color: colors.danger,
  },
  warningText: {
    fontSize: 12,
    color: colors.danger,
    lineHeight: 18,
    marginTop: 2,
  },
  card: {
    backgroundColor: colors.card,
    borderRadius: 16,
    padding: 16,
    marginBottom: 16,
    borderWidth: 1,
    borderColor: colors.border,
    shadowColor: "#0F172A",
    shadowOpacity: 0.04,
    shadowRadius: 8,
    shadowOffset: { width: 0, height: 3 },
    elevation: 2,
  },
  cardHeaderTitle: {
    fontSize: 15,
    fontWeight: "700",
    color: colors.text,
  },
  cardHeaderSubtitle: {
    fontSize: 12,
    color: colors.textSecondary,
    marginTop: 2,
  },
  dateChipsRow: {
    flexDirection: "row",
    gap: 8,
    marginTop: 14,
    marginBottom: 12,
  },
  dateChip: {
    width: 58,
    height: 64,
    borderRadius: 12,
    backgroundColor: colors.background,
    borderWidth: 1,
    borderColor: colors.border,
    justifyContent: "center",
    alignItems: "center",
  },
  dateChipActive: {
    backgroundColor: colors.danger,
    borderColor: colors.danger,
  },
  dateNum: {
    fontSize: 18,
    fontWeight: "800",
    color: colors.text,
  },
  dateNumActive: {
    color: "#FFFFFF",
  },
  dateMon: {
    fontSize: 11,
    fontWeight: "600",
    color: colors.textSecondary,
    marginTop: 2,
  },
  dateMonActive: {
    color: "rgba(255,255,255,0.9)",
  },
  selectedDateBadge: {
    flexDirection: "row",
    alignItems: "center",
    backgroundColor: colors.primaryLight,
    paddingHorizontal: 12,
    paddingVertical: 8,
    borderRadius: 10,
    marginTop: 4,
  },
  selectedDateText: {
    fontSize: 12,
    color: colors.textSecondary,
  },
  reasonChipsGrid: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8,
    marginTop: 14,
  },
  reasonChip: {
    flexDirection: "row",
    alignItems: "center",
    paddingHorizontal: 12,
    paddingVertical: 8,
    borderRadius: 10,
    backgroundColor: colors.background,
    borderWidth: 1,
    borderColor: colors.border,
  },
  reasonChipActive: {
    backgroundColor: colors.primary,
    borderColor: colors.primary,
  },
  reasonChipText: {
    fontSize: 12,
    fontWeight: "600",
    color: colors.textSecondary,
  },
  reasonChipTextActive: {
    color: "#FFFFFF",
    fontWeight: "700",
  },
  confirmCard: {
    flexDirection: "row",
    alignItems: "center",
    backgroundColor: colors.card,
    borderRadius: 14,
    padding: 14,
    marginBottom: 16,
    borderWidth: 1,
    borderColor: colors.border,
    gap: 12,
  },
  confirmText: {
    flex: 1,
    fontSize: 12,
    color: colors.textSecondary,
    lineHeight: 17,
  },
  submitBtn: {
    backgroundColor: colors.danger,
    height: 50,
    borderRadius: 14,
    flexDirection: "row",
    justifyContent: "center",
    alignItems: "center",
    shadowColor: colors.danger,
    shadowOpacity: 0.2,
    shadowRadius: 8,
    shadowOffset: { width: 0, height: 4 },
    elevation: 4,
  },
  submitBtnDisabled: {
    opacity: 0.5,
    shadowOpacity: 0,
    elevation: 0,
  },
  submitBtnText: {
    color: "#FFFFFF",
    fontSize: 15,
    fontWeight: "700",
  },

  // Existing Resignation Card Styles
  cardPending: {
    borderLeftWidth: 4,
    borderLeftColor: colors.warning,
  },
  cardAccepted: {
    borderLeftWidth: 4,
    borderLeftColor: colors.success,
  },
  statusHeaderRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
  },
  statusTitleGroup: {
    flex: 1,
  },
  statusHeaderLabel: {
    fontSize: 9,
    fontWeight: "800",
    color: colors.textSecondary,
    letterSpacing: 0.6,
  },
  statusHeaderTitle: {
    fontSize: 18,
    fontWeight: "800",
    marginTop: 2,
  },
  badgePill: {
    flexDirection: "row",
    alignItems: "center",
    paddingHorizontal: 8,
    paddingVertical: 4,
    borderRadius: 8,
  },
  badgeText: {
    fontSize: 11,
    fontWeight: "700",
  },
  divider: {
    height: 1,
    backgroundColor: colors.border,
    marginVertical: 12,
  },
  detailItem: {
    flexDirection: "row",
    alignItems: "center",
    marginBottom: 10,
  },
  detailIconCircle: {
    width: 32,
    height: 32,
    borderRadius: 10,
    backgroundColor: colors.redBg,
    justifyContent: "center",
    alignItems: "center",
    marginRight: 10,
  },
  detailLabel: {
    fontSize: 11,
    color: colors.textSecondary,
    fontWeight: "500",
  },
  detailValueRed: {
    fontSize: 15,
    fontWeight: "800",
    color: colors.danger,
    marginTop: 1,
  },
  reasonBox: {
    backgroundColor: colors.background,
    borderRadius: 10,
    padding: 10,
    marginBottom: 10,
    borderWidth: 1,
    borderColor: colors.border,
  },
  reasonBoxLabel: {
    fontSize: 9,
    fontWeight: "800",
    color: colors.textLight,
    letterSpacing: 0.6,
    marginBottom: 2,
  },
  reasonBoxText: {
    fontSize: 13,
    color: colors.textSecondary,
    lineHeight: 18,
  },
  submittedRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
  },
  submittedText: {
    fontSize: 11,
    color: colors.textSecondary,
  },
  infoBanner: {
    flexDirection: "row",
    alignItems: "center",
    backgroundColor: colors.yellowBg,
    borderRadius: 14,
    padding: 14,
    borderWidth: 1,
    borderColor: "#FDE68A",
  },
  infoBannerText: {
    flex: 1,
    fontSize: 12,
    color: colors.warning,
    lineHeight: 18,
    fontWeight: "500",
  },
});
