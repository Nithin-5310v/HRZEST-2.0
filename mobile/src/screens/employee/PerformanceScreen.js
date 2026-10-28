import React, { useState, useCallback } from "react";
import {
  ScrollView,
  View,
  Text,
  TextInput,
  TouchableOpacity,
  ActivityIndicator,
  RefreshControl,
} from "react-native";
import SafeAreaView from "../../components/SafeAreaView";

import { StyleSheet } from "react-native";
import { useFocusEffect } from "@react-navigation/native";
import { Ionicons } from "@expo/vector-icons";

import ProfileHeader from "../../components/profile/ProfileHeader";
import EmptyState from "../../components/ui/EmptyState";
import LoadingSkeleton from "../../components/ui/LoadingSkeleton";

import EmployeePerformanceCard from "../../components/performance/EmployeePerformanceCard";

import { useAuth } from "../../store/AuthContext";
import { useTheme } from "../../store/ThemeContext";
import { fetchEmployeeProfile, fetchMyPerformance, submitMyPerformanceComment } from "../../api/client";

function ReviewCard({ review, onSubmitComment, submittingId }) {
  const { colors } = useTheme();
  const styles = React.useMemo(() => makeStyles(colors), [colors]);
  const [comment, setComment] = useState(review.employee_comment || "");
  const isSubmitting = submittingId === review.id;

  return (
    <View style={styles.reviewCard}>
      <View style={styles.reviewHeader}>
        <Text style={styles.reviewQuarter}>Q{review.quarter} {review.year}</Text>
        <View style={styles.ratingPill}>
          <Ionicons name="star" size={12} color={colors.warning} />
          <Text style={styles.ratingPillText}>{review.overall_rating_label}</Text>
        </View>
      </View>
      <Text style={styles.reviewStatus}>{review.status}</Text>

      {review.kpis.length > 0 && (
        <View style={styles.kpiSection}>
          {review.kpis.map((kpi, idx) => (
            <View key={idx} style={styles.kpiRow}>
              <View style={{ flex: 1 }}>
                <Text style={styles.kpiTitle}>{kpi.kpi_title}</Text>
                {!!kpi.target && <Text style={styles.kpiTarget}>Target: {kpi.target}{kpi.achievement ? ` · Achieved: ${kpi.achievement}` : ""}</Text>}
              </View>
              <Text style={styles.kpiRating}>{kpi.rating || 0}/5</Text>
            </View>
          ))}
        </View>
      )}

      {!!review.reviewer_feedback && (
        <View style={styles.feedbackBox}>
          <Text style={styles.feedbackLabel}>MANAGER FEEDBACK</Text>
          <Text style={styles.feedbackText}>{review.reviewer_feedback}</Text>
        </View>
      )}

      <Text style={styles.commentLabel}>YOUR COMMENT</Text>
      <TextInput
        style={styles.commentInput}
        placeholder="Add your comment on this review..."
        value={comment}
        onChangeText={setComment}
        multiline
      />
      <TouchableOpacity
        style={styles.commentBtn}
        onPress={() => onSubmitComment(review.id, comment)}
        disabled={isSubmitting}
      >
        {isSubmitting ? (
          <ActivityIndicator color="#FFFFFF" size="small" />
        ) : (
          <Text style={styles.commentBtnText}>{review.employee_comment ? "Update Comment" : "Submit Comment"}</Text>
        )}
      </TouchableOpacity>
    </View>
  );
}

export default function PerformanceScreen() {
  const { user } = useAuth();
  const { colors } = useTheme();
  const styles = React.useMemo(() => makeStyles(colors), [colors]);
  const [profile, setProfile] = useState(null);
  const [reviews, setReviews] = useState([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [submittingId, setSubmittingId] = useState(null);

  const load = useCallback(async () => {
    try {
      const [profRes, perfRes] = await Promise.all([fetchEmployeeProfile(), fetchMyPerformance()]);
      if (profRes?.data?.ok) setProfile(profRes.data.profile);
      if (perfRes?.data?.ok) setReviews(perfRes.data.reviews || []);
    } catch (_) {
      // Fall back to whatever identity AuthContext already has.
    }
  }, []);

  useFocusEffect(
    useCallback(() => {
      let cancelled = false;
      (async () => {
        setLoading(true);
        if (!cancelled) await load();
        if (!cancelled) setLoading(false);
      })();
      return () => { cancelled = true; };
    }, [load])
  );

  const onRefresh = async () => {
    setRefreshing(true);
    await load();
    setRefreshing(false);
  };

  const handleSubmitComment = async (reviewId, comment) => {
    setSubmittingId(reviewId);
    try {
      const res = await submitMyPerformanceComment(reviewId, comment);
      if (res?.data?.ok) {
        await load();
      }
    } catch (_) {
      // review keeps its previous comment state on failure
    }
    setSubmittingId(null);
  };

  const name = profile?.name || user?.name || "Employee";
  const employeeId = profile?.employee_id || user?.employeeId || "-";
  const designation = profile?.role || "-";
  const department = profile?.department || "-";

  return (
    <SafeAreaView style={styles.container}>
      <ProfileHeader
        title="My Performance"
        showBack={false}
      />

      <ScrollView
        showsVerticalScrollIndicator={false}
        contentContainerStyle={styles.content}
        refreshControl={
          <RefreshControl refreshing={refreshing} onRefresh={onRefresh} />
        }
      >
        {loading ? (
          <>
            <LoadingSkeleton height={110} radius={24} style={{ marginBottom: 22 }} />
            <LoadingSkeleton height={140} radius={24} />
          </>
        ) : (
          <>
            <EmployeePerformanceCard
              name={name}
              designation={designation}
              department={department}
              employeeId={employeeId}
            />

            {reviews.length === 0 ? (
              <EmptyState
                icon="stats-chart-outline"
                title="No performance reviews yet"
                description="Your quarterly review will appear here once your manager or HR admin creates one."
              />
            ) : (
              reviews.map((review) => (
                <ReviewCard
                  key={review.id}
                  review={review}
                  onSubmitComment={handleSubmitComment}
                  submittingId={submittingId}
                />
              ))
            )}
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

  reviewCard: {
    backgroundColor: colors.card,
    borderRadius: 18,
    padding: 16,
    marginBottom: 14,
    borderWidth: 1,
    borderColor: colors.border,
  },
  reviewHeader: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  reviewQuarter: { fontSize: 15, fontWeight: "800", color: colors.text },
  ratingPill: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    backgroundColor: colors.yellowBg,
    paddingHorizontal: 10,
    paddingVertical: 4,
    borderRadius: 20,
  },
  ratingPillText: { fontSize: 11.5, fontWeight: "700", color: colors.warning },
  reviewStatus: { fontSize: 11.5, color: colors.textSecondary, fontWeight: "600", marginTop: 3 },
  kpiSection: { marginTop: 14, borderTopWidth: 1, borderTopColor: colors.border, paddingTop: 10 },
  kpiRow: { flexDirection: "row", alignItems: "center", paddingVertical: 6 },
  kpiTitle: { fontSize: 13, fontWeight: "700", color: colors.text },
  kpiTarget: { fontSize: 11.5, color: colors.textLight, marginTop: 2 },
  kpiRating: { fontSize: 13, fontWeight: "800", color: colors.primary },
  feedbackBox: { backgroundColor: colors.primaryLight, borderRadius: 12, padding: 12, marginTop: 12 },
  feedbackLabel: { fontSize: 10.5, fontWeight: "800", color: colors.primary, letterSpacing: 0.4 },
  feedbackText: { fontSize: 13, color: colors.text, marginTop: 6, lineHeight: 18 },
  commentLabel: { fontSize: 10.5, fontWeight: "800", color: colors.textSecondary, letterSpacing: 0.4, marginTop: 14 },
  commentInput: {
    backgroundColor: colors.background,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: 10,
    padding: 10,
    marginTop: 6,
    fontSize: 13,
    color: colors.text,
    minHeight: 60,
    textAlignVertical: "top",
  },
  commentBtn: {
    backgroundColor: colors.primary,
    borderRadius: 10,
    paddingVertical: 11,
    alignItems: "center",
    marginTop: 10,
  },
  commentBtnText: { color: "#FFFFFF", fontWeight: "700", fontSize: 13 },
});
