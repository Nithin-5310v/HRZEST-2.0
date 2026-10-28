import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  ScrollView,
  StyleSheet,
  View,
  Text,
  TouchableOpacity,
  ActivityIndicator,
  RefreshControl,
  Modal,
  Alert,
} from "react-native";
import SafeAreaView from "../../components/SafeAreaView";
import { Ionicons } from "@expo/vector-icons";
import { WebView } from "react-native-webview";

import AdminHeader from "../../components/admin/AdminHeader";
import { useTheme } from "../../store/ThemeContext";
import { fetchBillingStatus, getWebSessionLink } from "../../api/client";

// Same data/limits the web app shows on templates/employees.html (the
// seat-usage banner) and templates/seat_checkout.html (Seats & Billing
// page) -- this screen is the mobile read of the same
// company_settings.paid_employee_slots / auto_debit_mandates /
// monthly_invoices rows, via GET /api/billing_status. Buying seats or
// enabling auto-debit needs Razorpay Checkout, which isn't embedded
// natively here -- instead this opens the existing web page in a WebView,
// bridged in via a one-time login link (getWebSessionLink()) so the user
// never has to log in twice.
export default function SeatsBillingScreen() {
  const { colors } = useTheme();
  const styles = React.useMemo(() => makeStyles(colors), [colors]);
  const [status, setStatus] = useState(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [webviewUrl, setWebviewUrl] = useState(null);
  const [webviewLoading, setWebviewLoading] = useState(false);

  const load = useCallback(async () => {
    try {
      const res = await fetchBillingStatus();
      if (res?.data?.ok) {
        setStatus(res.data);
      }
    } catch (e) {
      // Fail quiet -- same posture as other admin screens' background
      // refreshes; the last-known state (or the empty state) stays visible.
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const onRefresh = () => {
    setRefreshing(true);
    load();
  };

  const openBillingWebView = async () => {
    setWebviewLoading(true);
    try {
      const res = await getWebSessionLink();
      if (res?.data?.ok && res.data.url) {
        setWebviewUrl(res.data.url);
      } else {
        Alert.alert("Could not open", res?.data?.msg || "Please try again.");
      }
    } catch (e) {
      Alert.alert("Network error", "Could not reach the server. Please try again.");
    } finally {
      setWebviewLoading(false);
    }
  };

  const closeWebView = () => {
    setWebviewUrl(null);
    // The user may have just bought seats or toggled auto-debit inside the
    // WebView -- refresh so this screen reflects it immediately.
    load();
  };

  // Memoized so an unrelated re-render (e.g. `refreshing` toggling from a
  // background pull-to-refresh) doesn't hand the WebView a new `source`
  // object reference and risk it reloading /settings/seats mid-checkout.
  // Called unconditionally, before the `loading` early return below --
  // every hook in this component must run on every render regardless of
  // `loading`, or React throws "Rendered more hooks than during the
  // previous render" the moment `loading` flips from true to false.
  const webviewSource = useMemo(() => (webviewUrl ? { uri: webviewUrl } : null), [webviewUrl]);

  if (loading) {
    return (
      <SafeAreaView style={styles.container}>
        <AdminHeader title="Seats & Billing" subtitle="ADMIN PORTAL" />
        <View style={styles.centerFill}>
          <ActivityIndicator size="large" color={colors.primary} />
        </View>
      </SafeAreaView>
    );
  }

  const employeeCount = status?.employee_count ?? 0;
  const cap = status?.paid_employee_slots;
  const isUnlimited = cap === null || cap === undefined;
  const atCap = !isUnlimited && employeeCount >= cap;
  const pct = isUnlimited ? 0 : cap > 0 ? Math.min(100, (employeeCount / cap) * 100) : 100;
  const autoDebit = status?.auto_debit;
  const autoDebitActive = autoDebit?.status === "active";

  return (
    <SafeAreaView style={styles.container}>
      <AdminHeader title="Seats & Billing" subtitle="ADMIN PORTAL" />
      <ScrollView
        contentContainerStyle={styles.scrollContent}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} />}
      >
        {/* Employee seat usage */}
        <View style={styles.card}>
          <Text style={styles.cardTitle}>Employee Seats</Text>
          <View style={styles.usageRow}>
            <Text style={styles.usageNum}>
              {employeeCount}
              {!isUnlimited ? ` / ${cap}` : ""}
            </Text>
            <Text style={styles.usageLbl}>
              {isUnlimited ? "unlimited plan" : "employees used"}
            </Text>
          </View>
          {!isUnlimited && (
            <View style={styles.barTrack}>
              <View
                style={[
                  styles.barFill,
                  { width: `${pct}%` },
                  atCap && { backgroundColor: colors.danger },
                  !atCap && pct >= 80 && { backgroundColor: colors.warning },
                ]}
              />
            </View>
          )}
          {atCap && (
            <View style={styles.warnBanner}>
              <Ionicons name="lock-closed" size={14} color="#991B1B" />
              <Text style={styles.warnText}>
                Limit reached -- new employees need more seats before they can be registered.
              </Text>
            </View>
          )}
          {!isUnlimited && (
            <TouchableOpacity
              style={[styles.primaryBtn, atCap && styles.primaryBtnDanger]}
              onPress={openBillingWebView}
              disabled={webviewLoading}
            >
              {webviewLoading ? (
                <ActivityIndicator size="small" color="#fff" />
              ) : (
                <>
                  <Ionicons name="add-circle-outline" size={16} color="#fff" />
                  <Text style={styles.primaryBtnText}>
                    {atCap ? "Buy More Seats" : "Manage Seats"}
                  </Text>
                </>
              )}
            </TouchableOpacity>
          )}
        </View>

        {/* Monthly auto-debit */}
        <View style={styles.card}>
          <Text style={styles.cardTitle}>Auto-Debit Monthly Billing</Text>
          <Text style={styles.cardSub}>
            {status?.monthly_bill_display || "₹0"} / month for {employeeCount} employee
            {employeeCount !== 1 ? "s" : ""} -- adjusts automatically as headcount changes.
          </Text>

          <View style={styles.statusRow}>
            <Ionicons
              name={
                autoDebitActive
                  ? "checkmark-circle"
                  : autoDebit?.status === "pending"
                  ? "time"
                  : "ellipse-outline"
              }
              size={16}
              color={autoDebitActive ? colors.success : autoDebit?.status === "pending" ? colors.warning : colors.textLight}
            />
            <Text style={styles.statusText}>
              {autoDebitActive
                ? "Active"
                : autoDebit?.status === "pending"
                ? "Pending authorization"
                : autoDebit?.status === "cancelled"
                ? "Cancelled"
                : "Not enabled"}
            </Text>
          </View>

          <TouchableOpacity style={styles.primaryBtn} onPress={openBillingWebView} disabled={webviewLoading}>
            {webviewLoading ? (
              <ActivityIndicator size="small" color="#fff" />
            ) : (
              <>
                <Ionicons name="repeat-outline" size={16} color="#fff" />
                <Text style={styles.primaryBtnText}>
                  {autoDebitActive ? "Manage Auto-Debit" : "Enable Auto-Debit"}
                </Text>
              </>
            )}
          </TouchableOpacity>
        </View>

        {/* Billing history */}
        {Array.isArray(status?.invoices) && status.invoices.length > 0 && (
          <View style={styles.card}>
            <Text style={styles.cardTitle}>Billing History</Text>
            {status.invoices.map((inv, idx) => (
              <View key={idx} style={styles.invoiceRow}>
                <Text style={styles.invoiceMonth}>
                  {inv.billing_period ? new Date(inv.billing_period).toLocaleDateString("en-IN", { month: "short", year: "numeric" }) : "—"}
                </Text>
                <Text style={styles.invoiceEmployees}>{inv.employee_count} emp</Text>
                <Text style={styles.invoiceAmount}>{inv.amount_display}</Text>
                <Text style={[styles.invoiceStatus, inv.status !== "paid" && { color: colors.danger }]}>
                  {inv.status}
                </Text>
              </View>
            ))}
          </View>
        )}
      </ScrollView>

      <Modal visible={!!webviewUrl} animationType="slide" onRequestClose={closeWebView}>
        <SafeAreaView style={styles.webviewContainer}>
          <View style={styles.webviewHeader}>
            <Text style={styles.webviewTitle}>Seats & Billing</Text>
            <TouchableOpacity onPress={closeWebView} hitSlop={{ top: 10, bottom: 10, left: 10, right: 10 }}>
              <Ionicons name="close" size={24} color={colors.text} />
            </TouchableOpacity>
          </View>
          {webviewSource && (
            <WebView source={webviewSource} startInLoadingState renderLoading={() => (
              <View style={styles.centerFill}>
                <ActivityIndicator size="large" color={colors.primary} />
              </View>
            )} />
          )}
        </SafeAreaView>
      </Modal>
    </SafeAreaView>
  );
}

const makeStyles = (colors) => StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.background },
  centerFill: { flex: 1, alignItems: "center", justifyContent: "center" },
  scrollContent: { padding: 16, paddingBottom: 32 },
  card: {
    backgroundColor: colors.surface,
    borderRadius: 16,
    padding: 18,
    marginBottom: 14,
    borderWidth: 1,
    borderColor: colors.border,
  },
  cardTitle: { fontSize: 15, fontWeight: "800", color: colors.text, marginBottom: 4 },
  cardSub: { fontSize: 12, color: colors.textSecondary, marginBottom: 12 },
  usageRow: { flexDirection: "row", alignItems: "baseline", marginTop: 8, marginBottom: 8 },
  usageNum: { fontSize: 24, fontWeight: "900", color: colors.text, marginRight: 8 },
  usageLbl: { fontSize: 12, color: colors.textSecondary },
  barTrack: { height: 8, borderRadius: 6, backgroundColor: colors.border, overflow: "hidden", marginBottom: 12 },
  barFill: { height: "100%", borderRadius: 6, backgroundColor: colors.primary },
  warnBanner: {
    flexDirection: "row",
    alignItems: "center",
    backgroundColor: "#FEE2E2",
    borderRadius: 10,
    padding: 10,
    marginBottom: 12,
    gap: 8,
  },
  warnText: { flex: 1, fontSize: 12, color: "#991B1B", fontWeight: "600" },
  statusRow: { flexDirection: "row", alignItems: "center", gap: 6, marginBottom: 14 },
  statusText: { fontSize: 13, fontWeight: "700", color: colors.text },
  primaryBtn: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 8,
    backgroundColor: colors.primary,
    borderRadius: 12,
    paddingVertical: 12,
  },
  primaryBtnDanger: { backgroundColor: colors.danger },
  primaryBtnText: { color: "#fff", fontWeight: "700", fontSize: 13 },
  invoiceRow: {
    flexDirection: "row",
    alignItems: "center",
    paddingVertical: 8,
    borderTopWidth: 1,
    borderTopColor: colors.border,
  },
  invoiceMonth: { flex: 1.2, fontSize: 12, color: colors.text, fontWeight: "600" },
  invoiceEmployees: { flex: 1, fontSize: 12, color: colors.textSecondary },
  invoiceAmount: { flex: 1, fontSize: 12, color: colors.text, fontWeight: "700" },
  invoiceStatus: { flex: 1, fontSize: 12, color: colors.success, fontWeight: "700", textTransform: "capitalize" },
  webviewContainer: { flex: 1, backgroundColor: colors.background },
  webviewHeader: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingHorizontal: 16,
    paddingVertical: 12,
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
    backgroundColor: colors.surface,
  },
  webviewTitle: { fontSize: 15, fontWeight: "800", color: colors.text },
});
