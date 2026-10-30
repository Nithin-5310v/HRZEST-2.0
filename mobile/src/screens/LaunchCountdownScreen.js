import React, { useState, useEffect } from "react";
import { View, Text, StyleSheet, TouchableOpacity, ScrollView } from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import AppTopBar from "../components/AppTopBar";
import QRScannerModal from "./QRScannerModal";
import { WEB, Wordmark, AppButton } from "../theme/webAuthStyle";

// The landing page the app opens on (design: "Final app" board "Landing
// page"): the app's top bar, the website's headline, a live clock, what
// the app does, then Sign in / Register / the attendance kiosk.
export default function LaunchCountdownScreen({ navigation, onContinue }) {
  const insets = useSafeAreaInsets();
  const [now, setNow] = useState(new Date());
  const [showScanner, setShowScanner] = useState(false);

  useEffect(() => {
    const interval = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(interval);
  }, []);

  const signIn = onContinue || (() => navigation.navigate("Login"));
  const register = () => (navigation ? navigation.navigate("CompanySignup") : signIn());

  const time = now.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: true });
  const [clock, meridiem] = time.split(" ");
  const date = now.toLocaleDateString("en-US", { weekday: "long", day: "numeric", month: "long", year: "numeric" });

  return (
    <View style={styles.page}>
      <QRScannerModal visible={showScanner} onClose={() => setShowScanner(false)} />
<<<<<<< HEAD
      <SafeAreaView style={{ flex: 1 }}>
        <WebTopBar
          style={styles.topBar}
          right={<WebNavButton title="Log In" icon="log-in-outline" onPress={onContinue} />}
        />
        <ScrollView contentContainerStyle={styles.content} showsVerticalScrollIndicator={false}>
          {/* The website's hero (templates/landing.html) */}
          <View style={styles.eyebrow}>
            <Ionicons name="shield-checkmark" size={12} color={WEB.brand} />
            <Text style={styles.eyebrowText}>HRZEST.COM</Text>
          </View>
          <Text style={styles.title}>HR, attendance, and payroll</Text>
          <Text style={[styles.title, styles.titleBrand]}>on one secure platform</Text>
          <Text style={styles.lead}>
            <Text style={styles.leadStrong}>Lead your workforce with clarity.</Text> HRzest is the all-in-one people platform that brings attendance, payroll, leave, onboarding and performance under one roof. With smart automation, secure access and a self-service portal for everyone, your team spends less time on paperwork and more time building your company.
          </Text>

          {/* Live clock, in a web card */}
          <WebCard style={styles.clockCard}>
            <View style={styles.clockHeader}>
              <Animated.View style={[styles.liveDot, { transform: [{ scale: pulseAnim }] }]} />
              <Text style={styles.clockHeaderLabel}>LIVE TIME SYNC</Text>
            </View>
            <Text style={styles.liveTime}>{timeStr || "12:00:00 AM"}</Text>
            <Text style={styles.liveDate}>{dateStr}</Text>
            <View style={styles.clockFooter}>
              <View style={styles.footerItem}>
                <Ionicons name="finger-print-outline" size={13} color={WEB.success} />
                <Text style={styles.footerText}>Biometric Enabled</Text>
              </View>
              <View style={styles.footerItem}>
                <Ionicons name="location-outline" size={13} color={WEB.brand} />
                <Text style={styles.footerText}>GPS Geofence</Text>
              </View>
            </View>
          </WebCard>

          <WebButton
            title={countdown > 0 ? `Enter Portal (${countdown}s)` : "Enter Portal"}
            onPress={onContinue}
          />
          <TouchableOpacity activeOpacity={0.85} style={styles.qrBtn} onPress={() => setShowScanner(true)}>
            <Ionicons name="qr-code-outline" size={17} color={WEB.brand} />
            <Text style={styles.qrBtnText}>Quick Attendance QR Scan</Text>
=======
      <AppTopBar
        titleNode={<Wordmark />}
        showCompany={false}
        right={
          <TouchableOpacity onPress={signIn} accessibilityRole="button" style={styles.topLink}>
            <Text style={styles.topLinkText}>Sign in</Text>
>>>>>>> bea8db0 (Update mobile navigation, authentication and API tests)
          </TouchableOpacity>
        }
      />
      <ScrollView
        contentContainerStyle={[styles.content, { paddingBottom: 20 + insets.bottom }]}
        showsVerticalScrollIndicator={false}
      >
        <View style={styles.eyebrow}>
          <Ionicons name="shield-checkmark-outline" size={13} color={WEB.brandDark} />
          <Text style={styles.eyebrowText}>HRZEST.COM</Text>
        </View>
        <Text style={styles.title}>
          HR, attendance and payroll <Text style={styles.titleBrand}>on one secure platform</Text>
        </Text>
        <Text style={styles.lead}>
          <Text style={styles.leadStrong}>Lead your workforce with clarity.</Text> Check in, apply for leave and get
          your payslip — with the same account you use on the website.
        </Text>

        <View style={styles.card}>
          <View style={styles.cardHead}>
            <Text style={styles.label}>LIVE TIME</Text>
            <View style={styles.okChip}><Text style={styles.okChipText}>In sync</Text></View>
          </View>
          <Text style={styles.time}>
            {clock} <Text style={styles.meridiem}>{meridiem}</Text>
          </Text>
          <Text style={styles.date}>{date}</Text>
          <View style={styles.chips}>
            {["Face check-in", "GPS geofence", "QR"].map((c) => (
              <View key={c} style={styles.chip}><Text style={styles.chipText}>{c}</Text></View>
            ))}
          </View>
        </View>

        <View style={styles.features}>
          {[["scan-outline", "Check in"], ["calendar-outline", "Leave"], ["cash-outline", "Payslips"]].map(([icon, label]) => (
            <View key={label} style={styles.feature}>
              <View style={styles.featureIcon}><Ionicons name={icon} size={18} color={WEB.brand} /></View>
              <Text style={styles.featureText}>{label}</Text>
            </View>
          ))}
        </View>

        <View style={styles.buttons}>
          <AppButton title="Sign in" onPress={signIn} />
          <AppButton title="Register your company" kind="outline" onPress={register} />
          <AppButton title="Quick attendance QR scan" kind="tonal" icon="scan-outline" onPress={() => setShowScanner(true)} />
        </View>

        <View style={styles.trust}>
          {[["lock-closed-outline", "Encrypted"], ["cloud-outline", "Daily backups"], ["shield-outline", "Audit log"]].map(([icon, label]) => (
            <View key={label} style={styles.trustItem}>
              <Ionicons name={icon} size={13} color={WEB.textMuted} />
              <Text style={styles.trustText}>{label}</Text>
            </View>
          ))}
        </View>
        <View style={{ flex: 1 }} />
        <Text style={styles.copyright}>© {now.getFullYear()} HRZEST.com · All rights reserved</Text>
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  page: { flex: 1, backgroundColor: WEB.page },
  topLink: { paddingHorizontal: 4, paddingVertical: 8 },
  topLinkText: { fontSize: 14, fontWeight: "700", color: WEB.brand },
  content: { flexGrow: 1, paddingHorizontal: 20, paddingTop: 16, gap: 14 },

  eyebrow: {
    flexDirection: "row", alignItems: "center", alignSelf: "flex-start", gap: 6,
    paddingHorizontal: 12, paddingVertical: 5, borderRadius: 999, backgroundColor: WEB.tint,
  },
  eyebrowText: { fontSize: 12, fontWeight: "700", letterSpacing: 0.5, color: WEB.brandDark },
  title: { fontSize: 28, lineHeight: 34, fontWeight: "700", color: WEB.text },
  titleBrand: { color: WEB.brand },
<<<<<<< HEAD
  lead: { fontSize: 14.5, lineHeight: 22, color: WEB.textMuted, marginTop: 12, marginBottom: 22 },
=======
  lead: { fontSize: 15, lineHeight: 22, color: WEB.textMuted },
>>>>>>> bea8db0 (Update mobile navigation, authentication and API tests)
  leadStrong: { fontWeight: "700", color: WEB.text },

  card: { backgroundColor: WEB.card, borderRadius: 20, borderWidth: 1, borderColor: WEB.line, padding: 16, gap: 8 },
  cardHead: { flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  label: { fontSize: 12, fontWeight: "700", letterSpacing: 0.6, color: WEB.textMuted },
  okChip: { backgroundColor: "#DDF4E6", borderRadius: 999, paddingHorizontal: 9, paddingVertical: 3 },
  okChipText: { fontSize: 12, fontWeight: "700", color: "#0B6B3A" },
  time: { fontSize: 38, fontWeight: "700", color: WEB.brandDark, fontVariant: ["tabular-nums"] },
  meridiem: { fontSize: 18, fontWeight: "500", color: WEB.textMuted },
  date: { fontSize: 14, color: WEB.textMuted },
  chips: { flexDirection: "row", flexWrap: "wrap", gap: 8, marginTop: 4 },
  chip: { backgroundColor: WEB.tint, borderRadius: 999, paddingHorizontal: 9, paddingVertical: 3 },
  chipText: { fontSize: 12, fontWeight: "700", color: WEB.brandDark },

  features: { flexDirection: "row", gap: 10 },
  feature: {
    flex: 1, alignItems: "center", gap: 6, paddingVertical: 12, borderRadius: 16,
    backgroundColor: WEB.card, borderWidth: 1, borderColor: WEB.line,
  },
  featureIcon: { width: 36, height: 36, borderRadius: 11, backgroundColor: WEB.tile, alignItems: "center", justifyContent: "center" },
  featureText: { fontSize: 13, fontWeight: "500", color: WEB.text },

  buttons: { gap: 10 },
  trust: { flexDirection: "row", justifyContent: "center", gap: 16 },
  trustItem: { flexDirection: "row", alignItems: "center", gap: 4 },
  trustText: { fontSize: 12, fontWeight: "500", color: WEB.textMuted },
  copyright: { fontSize: 11, color: WEB.textMuted, textAlign: "center", marginTop: 8 },
});
