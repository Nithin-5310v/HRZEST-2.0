import React, { useState, useEffect } from "react";
import {
  ScrollView,
  StyleSheet,
  View,
  Text,
  TouchableOpacity,
  RefreshControl,
  ActivityIndicator,
  Modal,
  TextInput,
  Alert,
} from "react-native";
import SafeAreaView from "../../components/SafeAreaView";
import { DrawerActions } from "@react-navigation/native";
import { LinearGradient } from "expo-linear-gradient";
import { Ionicons } from "@expo/vector-icons";
import THEME from "../../constants/theme";
import { useTheme } from "../../store/ThemeContext";
import AdminHeader from "../../components/admin/AdminHeader";
import AdminSearchBar from "../../components/admin/AdminSearchBar";
import SaasFilterSheet from "../../components/common/SaasFilterSheet";

import { fetchPerformance, submitPerformanceReview } from "../../api/client";

const today = new Date();
const CURRENT_QUARTER = Math.floor(today.getMonth() / 3) + 1;
const CURRENT_YEAR = today.getFullYear();

export default function PerformanceScreen({ navigation }) {
  const { colors } = useTheme();
  const styles = React.useMemo(() => makeStyles(colors), [colors]);
  const [search, setSearch] = useState("");
  const [selectedStatus, setSelectedStatus] = useState("All");
  const [selectedSort, setSelectedSort] = useState("Rating (High-Low)");
  const [filterModalVisible, setFilterModalVisible] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [loading, setLoading] = useState(true);
  const [reviews, setReviews] = useState([]);

  // Review submission modal -- fields match what
  // blueprints/performance.py's api_submit_performance_review() actually
  // stores (quarter/year upsert + reviewer feedback), not a
  // rating/comments/hike/bonus shape that never had a backend.
  const [reviewModalVisible, setReviewModalVisible] = useState(false);
  const [reviewTarget, setReviewTarget] = useState(null);
  const [reviewFeedback, setReviewFeedback] = useState("");
  const [submittingReview, setSubmittingReview] = useState(false);

  const loadData = async () => {
    try {
      const res = await fetchPerformance();
      if (res?.data?.ok && Array.isArray(res.data.reviews)) {
        setReviews(res.data.reviews);
      } else {
        setReviews([]);
      }
    } catch {
      setReviews([]);
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

  const openReviewModal = (item) => {
    setReviewTarget(item);
    setReviewFeedback("");
    setReviewModalVisible(true);
  };

  const handleSubmitReview = async (status) => {
    if (!reviewTarget) return;
    setSubmittingReview(true);
    let res;
    try {
      res = await submitPerformanceReview(
        reviewTarget.employee_id, CURRENT_QUARTER, CURRENT_YEAR,
        reviewFeedback.trim(), status
      );
    } catch (e) {
      res = e?.response;
    }
    setSubmittingReview(false);
    if (!res?.data?.ok) {
      Alert.alert("Save Failed", res?.data?.msg || "Could not save this review. Please try again.");
      return;
    }
    setReviewModalVisible(false);
    Alert.alert(status === "Draft" ? "Draft Saved" : "Review Finalized", `Q${CURRENT_QUARTER} ${CURRENT_YEAR} review for ${reviewTarget.employeeName} saved.`);
    loadData();
  };

  const hasActiveFilter = selectedStatus !== "All" || selectedSort !== "Rating (High-Low)";

  const statuses = ["All", "Completed", "In Progress", "Pending Review"];
  const sortOptions = ["Rating (High-Low)", "Rating (Low-High)", "Name (A-Z)"];

  const filteredReviews = reviews
    .filter((r) => {
      const matchesSearch =
        r.employeeName.toLowerCase().includes(search.toLowerCase()) ||
        r.role.toLowerCase().includes(search.toLowerCase());
      const matchesStatus =
        selectedStatus === "All" || r.status === selectedStatus;
      return matchesSearch && matchesStatus;
    })
    .sort((a, b) => {
      if (selectedSort === "Rating (Low-High)") return a.rating - b.rating;
      if (selectedSort === "Name (A-Z)") return a.employeeName.localeCompare(b.employeeName);
      return b.rating - a.rating;
    });

  return (
    <LinearGradient
      colors={colors.screenGradient}
      style={styles.container}
    >
      <SafeAreaView style={{ flex: 1 }}>
        <AdminHeader
          title="Performance & KPIs"
          onMenu={() => navigation.dispatch(DrawerActions.openDrawer())}
        />

        <ScrollView
          showsVerticalScrollIndicator={false}
          contentContainerStyle={styles.content}
          refreshControl={
            <RefreshControl
              refreshing={refreshing}
              onRefresh={onRefresh}
              colors={[THEME.colors.primary]}
            />
          }
        >
          {/* Dynamic Performance Metric Header */}
          <View style={styles.heroCard}>
            <Text style={styles.heroTitle}>Performance Overview</Text>
            <Text style={styles.heroSub}>Appraisals & Key Performance Indicators</Text>
            <View style={styles.heroStatsRow}>
              <View style={styles.heroStatItem}>
                <Text style={styles.heroStatValue}>
                  {reviews.length > 0 ? Math.round((reviews.filter(r => r.status === "Completed").length / reviews.length) * 100) + "%" : "0%"}
                </Text>
                <Text style={styles.heroStatLabel}>Reviews Done</Text>
              </View>
              <View style={styles.heroStatDivider} />
              <View style={styles.heroStatItem}>
                <Text style={styles.heroStatValue}>{reviews.length}</Text>
                <Text style={styles.heroStatLabel}>Total Staff</Text>
              </View>
              <View style={styles.heroStatDivider} />
              <View style={styles.heroStatItem}>
                <Text style={styles.heroStatValue}>
                  {reviews.filter(r => Number(r.rating || 0) >= 4.0).length}
                </Text>
                <Text style={styles.heroStatLabel}>Top Performers</Text>
              </View>
            </View>
          </View>

          {/* Search */}
          <AdminSearchBar
            value={search}
            onChangeText={setSearch}
            placeholder="Search employee performance..."
            onFilterPress={() => setFilterModalVisible(true)}
            hasActiveFilter={hasActiveFilter}
            onClear={() => setSearch("")}
          />

          <View style={styles.sectionHeader}>
            <Text style={styles.sectionTitle}>Employee Reviews</Text>
            <Text style={styles.sectionBadge}>
              {filteredReviews.length} Records
            </Text>
          </View>

          {loading ? (
            <ActivityIndicator size="large" color="#173B8C" style={{ marginTop: 30 }} />
          ) : filteredReviews.length === 0 ? (
            <View style={{ padding: 32, alignItems: "center", justifyContent: "center", backgroundColor: colors.card, borderRadius: 16, marginTop: 12, borderWidth: 1, borderColor: colors.border }}>
              <Ionicons name="ribbon-outline" size={48} color={colors.textLight} />
              <Text style={{ fontSize: 16, fontWeight: "700", color: "#334155", marginTop: 12 }}>
                No Performance Reviews Found
              </Text>
              <Text style={{ fontSize: 13, color: "#64748B", textAlign: "center", marginTop: 4 }}>
                Performance appraisal cycles and KPI reviews will appear here once initiated.
              </Text>
            </View>
          ) : null}

          {/* Review List */}
          {filteredReviews.map((item) => (
            <TouchableOpacity key={item.id} style={styles.reviewCard} activeOpacity={0.85} onPress={() => openReviewModal(item)}>
              <View style={styles.cardTop}>
                <View style={styles.avatarCircle}>
                  <Text style={styles.avatarText}>
                    {item.employeeName.charAt(0)}
                  </Text>
                </View>
                <View style={{ flex: 1, marginLeft: 12 }}>
                  <Text style={styles.empName}>{item.employeeName}</Text>
                  <Text style={styles.empRole}>{item.role}</Text>
                </View>
                <View style={styles.ratingBadge}>
                  <Ionicons name="star" size={14} color="#F59E0B" />
                  <Text style={styles.ratingText}>{item.rating}</Text>
                </View>
              </View>

              <View style={styles.cardDivider} />

              <View style={styles.cardDetailsRow}>
                <View>
                  <Text style={styles.detailLabel}>Goals Met</Text>
                  <Text style={styles.detailValue}>{item.goalsMet}</Text>
                </View>
                <View>
                  <Text style={styles.detailLabel}>Cycle</Text>
                  <Text style={styles.detailValue}>{item.lastReview}</Text>
                </View>
                <View>
                  <Text style={styles.detailLabel}>Status</Text>
                  <View
                    style={[
                      styles.statusTag,
                      item.status === "Completed"
                        ? styles.statusCompleted
                        : styles.statusProgress,
                    ]}
                  >
                    <Text
                      style={[
                        styles.statusText,
                        item.status === "Completed"
                          ? styles.statusTextCompleted
                          : styles.statusTextProgress,
                      ]}
                    >
                      {item.status}
                    </Text>
                  </View>
                </View>
              </View>
            </TouchableOpacity>
          ))}

          <View style={{ height: 100 }} />
        </ScrollView>

        {/* Professional SaaS Filter Modal */}
        <SaasFilterSheet
          visible={filterModalVisible}
          title="Filter Performance Reviews"
          statusOptions={statuses}
          selectedStatus={selectedStatus}
          onSelectStatus={setSelectedStatus}
          sortOptions={sortOptions}
          selectedSort={selectedSort}
          onSelectSort={setSelectedSort}
          onApply={() => setFilterModalVisible(false)}
          onReset={() => {
            setSelectedStatus("All");
            setSelectedSort("Rating (High-Low)");
          }}
          onClose={() => setFilterModalVisible(false)}
        />

        {/* Review Submission Modal */}
        <Modal visible={reviewModalVisible} transparent animationType="slide" onRequestClose={() => setReviewModalVisible(false)}>
          <View style={{ flex: 1, backgroundColor: "rgba(15, 23, 42, 0.8)", justifyContent: "flex-end" }}>
            <View style={{ backgroundColor: colors.card, borderTopLeftRadius: 28, borderTopRightRadius: 28, padding: 20, maxHeight: "85%" }}>
              <View style={{ flexDirection: "row", justifyContent: "space-between", alignItems: "center", marginBottom: 14, paddingBottom: 10, borderBottomWidth: 1, borderBottomColor: "#F1F5F9" }}>
                <Text style={{ fontSize: 17, fontWeight: "800", color: colors.text }}>
                  Q{CURRENT_QUARTER} {CURRENT_YEAR} Review -- {reviewTarget?.employeeName}
                </Text>
                <TouchableOpacity onPress={() => setReviewModalVisible(false)}>
                  <Ionicons name="close-circle" size={26} color="#64748B" />
                </TouchableOpacity>
              </View>

              <ScrollView showsVerticalScrollIndicator={false}>
                <Text style={{ fontSize: 11, fontWeight: "700", color: "#64748B", marginTop: 4 }}>REVIEWER FEEDBACK</Text>
                <TextInput
                  style={{ backgroundColor: colors.background, borderWidth: 1, borderColor: colors.border, borderRadius: 10, padding: 10, marginTop: 4, minHeight: 90, textAlignVertical: "top" }}
                  placeholder="Summarize this quarter's performance..."
                  value={reviewFeedback}
                  onChangeText={setReviewFeedback}
                  multiline
                />

                <Text style={{ fontSize: 11, color: colors.textLight, marginTop: 10 }}>
                  Overall rating is computed automatically from this employee's rated KPIs, same as on web.
                </Text>

                <View style={{ flexDirection: "row", gap: 10, marginTop: 22, marginBottom: 10 }}>
                  <TouchableOpacity
                    style={{ flex: 1, backgroundColor: "#F1F5F9", borderRadius: 14, paddingVertical: 14, alignItems: "center" }}
                    onPress={() => handleSubmitReview("Draft")}
                    disabled={submittingReview}
                  >
                    <Text style={{ fontWeight: "800", color: "#334155" }}>Save Draft</Text>
                  </TouchableOpacity>
                  <TouchableOpacity
                    style={{ flex: 1, backgroundColor: "#173B8C", borderRadius: 14, paddingVertical: 14, alignItems: "center" }}
                    onPress={() => handleSubmitReview("Completed")}
                    disabled={submittingReview}
                  >
                    {submittingReview ? (
                      <ActivityIndicator color="#FFFFFF" />
                    ) : (
                      <Text style={{ fontWeight: "800", color: "#FFFFFF" }}>Finalize Review</Text>
                    )}
                  </TouchableOpacity>
                </View>
              </ScrollView>
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
    backgroundColor: "#173B8C",
    borderRadius: 24,
    padding: 20,
    marginBottom: 18,
    elevation: 4,
    shadowColor: "#000",
    shadowOpacity: 0.1,
    shadowRadius: 10,
  },
  heroHeader: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    marginBottom: 20,
  },
  heroSub: { color: "rgba(255,255,255,0.7)", fontSize: 12, fontWeight: "600" },
  heroTitle: { color: "#FFFFFF", fontSize: 18, fontWeight: "800", marginTop: 4 },
  heroIconBadge: {
    width: 48,
    height: 48,
    borderRadius: 24,
    backgroundColor: "rgba(255,255,255,0.15)",
    justifyContent: "center",
    alignItems: "center",
  },
  heroStatsRow: {
    flexDirection: "row",
    justifyContent: "space-around",
    alignItems: "center",
    paddingTop: 12,
    borderTopWidth: 1,
    borderTopColor: "rgba(255,255,255,0.15)",
  },
  heroStatItem: { alignItems: "center" },
  heroStatValue: { color: "#FFFFFF", fontSize: 16, fontWeight: "800" },
  heroStatLabel: { color: "rgba(255,255,255,0.7)", fontSize: 11, marginTop: 2 },
  heroStatDivider: { width: 1, height: 28, backgroundColor: "rgba(255,255,255,0.15)" },
  sectionHeader: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    marginTop: 18,
    marginBottom: 12,
  },
  sectionTitle: { fontSize: 14, fontWeight: "800", color: colors.text },
  sectionBadge: { fontSize: 12, fontWeight: "700", color: "#173B8C" },
  reviewCard: {
    backgroundColor: colors.card,
    borderRadius: 20,
    padding: 16,
    marginBottom: 12,
    elevation: 2,
    shadowColor: "#000",
    shadowOpacity: 0.04,
    shadowRadius: 8,
    borderWidth: 1,
    borderColor: colors.border,
  },
  cardTop: { flexDirection: "row", alignItems: "center" },
  avatarCircle: {
    width: 44,
    height: 44,
    borderRadius: 22,
    backgroundColor: colors.primaryLight,
    justifyContent: "center",
    alignItems: "center",
  },
  avatarText: { fontSize: 15, fontWeight: "800", color: "#173B8C" },
  empName: { fontSize: 13, fontWeight: "700", color: colors.text },
  empRole: { fontSize: 12, color: "#64748B", marginTop: 2 },
  ratingBadge: {
    flexDirection: "row",
    alignItems: "center",
    backgroundColor: "#FEF3C7",
    paddingHorizontal: 10,
    paddingVertical: 5,
    borderRadius: 14,
  },
  ratingText: { marginLeft: 4, fontSize: 14, fontWeight: "800", color: "#B45309" },
  cardDivider: { height: 1, backgroundColor: "#F1F5F9", marginVertical: 12 },
  cardDetailsRow: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  detailLabel: { fontSize: 11, color: colors.textLight, fontWeight: "600" },
  detailValue: { fontSize: 14, fontWeight: "700", color: colors.text, marginTop: 2 },
  statusTag: { paddingHorizontal: 10, paddingVertical: 4, borderRadius: 12, marginTop: 2 },
  statusCompleted: { backgroundColor: "#DCFCE7" },
  statusProgress: { backgroundColor: "#E0F2FE" },
  statusText: { fontSize: 11, fontWeight: "700" },
  statusTextCompleted: { color: "#166534" },
  statusTextProgress: { color: "#075985" },
});
