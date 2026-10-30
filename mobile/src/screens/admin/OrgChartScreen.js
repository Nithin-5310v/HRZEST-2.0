import React, { useState, useEffect, useCallback } from "react";
import {
  ScrollView,
  StyleSheet,
  View,
  Text,
  RefreshControl,
  ActivityIndicator,
} from "react-native";
import SafeAreaView from "../../components/SafeAreaView";
import { DrawerActions } from "@react-navigation/native";
import { LinearGradient } from "expo-linear-gradient";
import { Ionicons } from "@expo/vector-icons";
import { useTheme } from "../../store/ThemeContext";
import AdminHeader from "../../components/admin/AdminHeader";
import { fetchOrgChart, fetchDashboard } from "../../api/client";

// Real manager_id-based reporting hierarchy via /api/org_chart (Bearer
// twin of blueprints/admin_views.py's session-only /api/org_chart_data),
// not the flattened department grouping this screen used to fall back to.
export default function OrgChartScreen({ navigation }) {
  const { colors } = useTheme();
  const styles = React.useMemo(() => makeStyles(colors), [colors]);
  const [refreshing, setRefreshing] = useState(false);
  const [loading, setLoading] = useState(true);
  const [tree, setTree] = useState([]);
  const [total, setTotal] = useState(0);
  const [companyName, setCompanyName] = useState("");

  const load = useCallback(async () => {
    try {
      const [chartRes, dashRes] = await Promise.all([
        fetchOrgChart(),
        fetchDashboard().catch(() => null),
      ]);
      if (chartRes?.data?.ok) {
        setTree(chartRes.data.tree || []);
        setTotal(chartRes.data.total || 0);
      }
      setCompanyName(dashRes?.data?.company_name || "");
    } catch (_) {
      setTree([]);
      setTotal(0);
    }
  }, []);

  useEffect(() => {
    setLoading(true);
    load().finally(() => setLoading(false));
  }, [load]);

  const onRefresh = async () => {
    setRefreshing(true);
    await load();
    setRefreshing(false);
  };

  return (
    <LinearGradient
      colors={colors.screenGradient}
      style={styles.container}
    >
      <SafeAreaView style={{ flex: 1 }}>
        <AdminHeader
          title="Organization Chart"
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
          {/* Company Summary Box */}
          <View style={styles.execCard}>
            <View style={styles.execBadge}>
              <Text style={styles.execBadgeText}>ORGANIZATION</Text>
            </View>
            <Text style={styles.execName}>{companyName || "Your Company"}</Text>
            <View style={styles.execStats}>
              <Text style={styles.execStatsText}>
                {total} Total Staff • {tree.length} Reporting Root{tree.length !== 1 ? "s" : ""}
              </Text>
            </View>
          </View>

          <View style={styles.treeConnector} />

          <View style={styles.sectionHeader}>
            <Text style={styles.sectionTitle}>Reporting Hierarchy</Text>
          </View>

          {loading ? (
            <ActivityIndicator size="large" color="#0B6E8E" style={{ marginTop: 20 }} />
          ) : tree.length === 0 ? (
            <View style={styles.emptyCard}>
              <Ionicons name="git-network-outline" size={40} color={colors.textLight} />
              <Text style={styles.emptyText}>No employees found yet.</Text>
            </View>
          ) : (
            tree.map((node) => <OrgNode key={node.id} node={node} depth={0} styles={styles} colors={colors} />)
          )}

          <View style={{ height: 100 }} />
        </ScrollView>
      </SafeAreaView>
    </LinearGradient>
  );
}

function OrgNode({ node, depth, styles, colors }) {
  return (
    <View style={{ marginLeft: depth * 18 }}>
      <View style={styles.nodeCard}>
        <View style={styles.nodeIconBadge}>
          <Ionicons name="person" size={18} color="#0B6E8E" />
        </View>
        <View style={{ flex: 1, marginLeft: 12 }}>
          <Text style={styles.nodeName}>{node.name}</Text>
          <Text style={styles.nodeRole}>
            {node.role}{node.department ? ` • ${node.department}` : ""}
          </Text>
        </View>
        {node.children?.length > 0 && (
          <View style={styles.countBadge}>
            <Text style={styles.countText}>{node.children.length} report{node.children.length !== 1 ? "s" : ""}</Text>
          </View>
        )}
      </View>
      {node.children?.map((child) => (
        <OrgNode key={child.id} node={child} depth={depth + 1} styles={styles} colors={colors} />
      ))}
    </View>
  );
}

const makeStyles = (colors) => StyleSheet.create({
  container: { flex: 1 },
  content: { paddingHorizontal: 20, paddingTop: 10 },
  execCard: {
    backgroundColor: "#0B6E8E",
    borderRadius: 24,
    padding: 20,
    alignItems: "center",
    elevation: 4,
  },
  execBadge: {
    backgroundColor: "rgba(255,255,255,0.15)",
    paddingHorizontal: 12,
    paddingVertical: 4,
    borderRadius: 12,
    marginBottom: 8,
  },
  execBadgeText: { color: "#A6DDE8", fontSize: 10, fontWeight: "800", letterSpacing: 1 },
  execName: { color: "#FFFFFF", fontSize: 16, fontWeight: "800" },
  execStats: { marginTop: 14, paddingTop: 10, borderTopWidth: 1, borderTopColor: "rgba(255,255,255,0.15)" },
  execStatsText: { color: "#FFFFFF", fontSize: 12, fontWeight: "600" },
  treeConnector: {
    width: 2,
    height: 24,
    backgroundColor: "#C4D2D6",
    alignSelf: "center",
    marginVertical: 4,
  },
  sectionHeader: { marginBottom: 12 },
  sectionTitle: { fontSize: 14, fontWeight: "800", color: colors.text },
  emptyCard: {
    backgroundColor: colors.card,
    borderRadius: 20,
    padding: 32,
    alignItems: "center",
    borderWidth: 1,
    borderColor: colors.border,
  },
  emptyText: { marginTop: 10, fontSize: 13, color: "#5A6A70", fontWeight: "600" },
  nodeCard: {
    flexDirection: "row",
    alignItems: "center",
    backgroundColor: colors.card,
    borderRadius: 16,
    padding: 12,
    marginBottom: 10,
    borderWidth: 1,
    borderColor: colors.border,
  },
  nodeIconBadge: {
    width: 36,
    height: 36,
    borderRadius: 18,
    backgroundColor: colors.primaryLight,
    justifyContent: "center",
    alignItems: "center",
  },
  nodeName: { fontSize: 13, fontWeight: "700", color: colors.text },
  nodeRole: { fontSize: 11, color: "#5A6A70", marginTop: 2, fontWeight: "600" },
  countBadge: {
    backgroundColor: "#ECF3F5",
    paddingHorizontal: 10,
    paddingVertical: 5,
    borderRadius: 12,
  },
  countText: { fontSize: 11, fontWeight: "700", color: "#2E3D43" },
});
