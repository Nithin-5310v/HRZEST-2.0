import React from "react";
import { Modal, View, Text, StyleSheet, TouchableOpacity, Image, ActivityIndicator } from "react-native";
import { LinearGradient } from "expo-linear-gradient";
import { Ionicons } from "@expo/vector-icons";

import { fetchMyQrCode } from "../../api/client";
import { useAuth } from "../../store/AuthContext";

export default function DigitalIdCardModal({ visible, employee, onClose }) {
  const { user } = useAuth();
  // Hooks must run unconditionally on every render -- the early
  // `if (!employee) return null` used to sit above this useState call,
  // which throws a "Rendered fewer hooks than expected" error the moment
  // `employee` is ever null on one render and non-null on another (e.g.
  // while ProfileScreen's data is still loading).
  const empId = employee?.employeeId || employee?.employee_id || "EMP-1001";
  const [qrBase64, setQrBase64] = React.useState(null);
  const [qrFailed, setQrFailed] = React.useState(false);

  // The attendance QR encodes a signed, permanently-replayable check-in
  // credential for this employee (see qr_generator.py's _qr_signature) --
  // it used to be fetched from a public, predictable static/ path, with a
  // fallback to a third-party QR-generator service that would have encoded
  // the bare, unsigned employee ID (forgeable by anyone). Both were real
  // vulnerabilities. Now fetched the same authenticated, base64-JSON way
  // every other binary asset in this app is (see IdCardScreen.js) -- if it
  // can't be fetched, this shows a plain "unavailable" state instead of
  // ever falling back to an insecure alternative.
  React.useEffect(() => {
    if (!visible) return;
    let cancelled = false;
    setQrBase64(null);
    setQrFailed(false);
    fetchMyQrCode()
      .then((res) => {
        if (!cancelled && res?.data?.ok) setQrBase64(res.data.image_base64);
        else if (!cancelled) setQrFailed(true);
      })
      .catch(() => {
        if (!cancelled) setQrFailed(true);
      });
    return () => { cancelled = true; };
  }, [visible]);

  if (!employee) return null;

  const companyName = (employee.company || user?.company || "Enterprise Workforce Platform").toUpperCase();

  return (
    <Modal visible={visible} transparent animationType="fade" onRequestClose={onClose}>
      <View style={styles.overlay}>
        <View style={styles.cardContainer}>
          <LinearGradient colors={["#0F2027", "#203A43", "#2C5364"]} style={styles.cardHeader}>
            <View style={styles.headerRow}>
              <Ionicons name="business" size={22} color="#4C9BE8" />
              <Text style={styles.companyName}>{companyName}</Text>
            </View>
            <Text style={styles.cardSubtitle}>OFFICIAL IDENTIFICATION CARD</Text>
          </LinearGradient>

          <View style={styles.cardBody}>
            <View style={styles.avatarBorder}>
              {employee.photo ? (
                <Image source={{ uri: employee.photo }} style={styles.avatarImage} />
              ) : (
                <View style={styles.avatarFallback}>
                  <Text style={styles.avatarText}>{employee.name ? employee.name.charAt(0) : "E"}</Text>
                </View>
              )}
            </View>

            <Text style={styles.empName}>{employee.name || "Employee Name"}</Text>
            <Text style={styles.empRole}>{employee.role || "Software Engineer"}</Text>
            <View style={styles.badgeRow}>
              <View style={styles.deptBadge}>
                <Text style={styles.deptBadgeText}>{employee.department || "Engineering"}</Text>
              </View>
            </View>

            <View style={styles.detailsTable}>
              <View style={styles.detailRow}>
                <Text style={styles.detailLabel}>EMP ID:</Text>
                <Text style={styles.detailValue}>{empId}</Text>
              </View>
              <View style={styles.detailRow}>
                <Text style={styles.detailLabel}>STATUS:</Text>
                <Text style={[styles.detailValue, { color: "#10B981" }]}>Active Staff</Text>
              </View>
            </View>

            <View style={styles.qrContainer}>
              {qrBase64 ? (
                <Image
                  source={{ uri: `data:image/png;base64,${qrBase64}` }}
                  style={{ width: 120, height: 120, borderRadius: 12 }}
                />
              ) : qrFailed ? (
                <View style={styles.qrUnavailable}>
                  <Ionicons name="qr-code-outline" size={32} color="#8794A5" />
                  <Text style={styles.qrUnavailableText}>QR unavailable</Text>
                </View>
              ) : (
                <View style={styles.qrUnavailable}>
                  <ActivityIndicator size="small" color="#8794A5" />
                </View>
              )}
              <Text style={styles.qrText}>Scan for Digital Verification</Text>
            </View>
          </View>

          <TouchableOpacity style={styles.closeBtn} onPress={onClose}>
            <Text style={styles.closeBtnText}>Close ID Card</Text>
          </TouchableOpacity>
        </View>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  overlay: { flex: 1, backgroundColor: "rgba(15, 23, 42, 0.8)", justifyContent: "center", alignItems: "center", padding: 20 },
  cardContainer: { width: "90%", backgroundColor: "#FFFFFF", borderRadius: 24, overflow: "hidden", elevation: 12 },
  cardHeader: { paddingVertical: 18, paddingHorizontal: 20, alignItems: "center" },
  headerRow: { flexDirection: "row", alignItems: "center" },
  companyName: { fontSize: 15, fontWeight: "800", color: "#FFFFFF", marginLeft: 8, letterSpacing: 0.5 },
  cardSubtitle: { fontSize: 10, fontWeight: "700", color: "#8794A5", letterSpacing: 1.2, marginTop: 4 },
  cardBody: { alignItems: "center", padding: 20 },
  avatarBorder: { width: 90, height: 90, borderRadius: 45, borderWidth: 3, borderColor: "#4C9BE8", justifyContent: "center", alignItems: "center", marginTop: -40, backgroundColor: "#FFFFFF", elevation: 4 },
  avatarImage: { width: 84, height: 84, borderRadius: 42 },
  avatarFallback: { width: 84, height: 84, borderRadius: 42, backgroundColor: "#0B5CAD", justifyContent: "center", alignItems: "center" },
  avatarText: { fontSize: 24, fontWeight: "800", color: "#FFFFFF" },
  empName: { fontSize: 16, fontWeight: "800", color: "#131C2A", marginTop: 10 },
  empRole: { fontSize: 12, fontWeight: "600", color: "#5A6678", marginTop: 2 },
  badgeRow: { marginTop: 8 },
  deptBadge: { backgroundColor: "#E0F2FE", paddingHorizontal: 12, paddingVertical: 4, borderRadius: 12 },
  deptBadgeText: { fontSize: 11, fontWeight: "700", color: "#0369A1" },
  detailsTable: { width: "100%", marginTop: 16, borderTopWidth: 1, borderTopColor: "#EDF2F8", paddingTop: 12 },
  detailRow: { flexDirection: "row", justifyContent: "space-between", marginBottom: 6 },
  detailLabel: { fontSize: 12, fontWeight: "700", color: "#5A6678" },
  detailValue: { fontSize: 12, fontWeight: "700", color: "#131C2A" },
  qrContainer: { alignItems: "center", marginTop: 14, paddingTop: 12, borderTopWidth: 1, borderTopColor: "#EDF2F8", width: "100%" },
  qrText: { fontSize: 11, color: "#8794A5", marginTop: 4 },
  qrUnavailable: { width: 120, height: 120, borderRadius: 12, backgroundColor: "#F5F8FC", justifyContent: "center", alignItems: "center", gap: 6 },
  qrUnavailableText: { fontSize: 10, color: "#8794A5", fontWeight: "600" },
  closeBtn: { backgroundColor: "#EDF2F8", paddingVertical: 14, alignItems: "center" },
  closeBtnText: { color: "#5A6678", fontWeight: "700", fontSize: 13 },
});
