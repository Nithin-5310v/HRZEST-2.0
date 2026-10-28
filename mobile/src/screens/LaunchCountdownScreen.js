import React, { useState, useEffect } from "react";
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
  SafeAreaView,
  Animated,
  ScrollView,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";

import QRScannerModal from "./QRScannerModal";
import { WEB, WebPage, WebTopBar, WebNavButton, WebCard, WebButton } from "../theme/webAuthStyle";

// The screen the app opens on, styled as the website's landing hero
// (templates/landing.html at phone width).

export default function LaunchCountdownScreen({ onContinue }) {
  const [timeStr, setTimeStr] = useState("");
  const [dateStr, setDateStr] = useState("");
  const [countdown, setCountdown] = useState(3);
  const [autoNavigate, setAutoNavigate] = useState(false);
  const [showScanner, setShowScanner] = useState(false);

  const pulseAnim = useState(new Animated.Value(1))[0];

  useEffect(() => {
    const updateClock = () => {
      const now = new Date();
      setTimeStr(
        now.toLocaleTimeString("en-US", {
          hour: "2-digit",
          minute: "2-digit",
          second: "2-digit",
          hour12: true,
        })
      );
      setDateStr(
        now
          .toLocaleDateString("en-US", {
            weekday: "long",
            day: "numeric",
            month: "long",
            year: "numeric",
          })
          .toUpperCase()
      );
    };

    updateClock();
    const interval = setInterval(updateClock, 1000);
    return () => clearInterval(interval);
  }, []);

  // Pulsing ring animation
  useEffect(() => {
    Animated.loop(
      Animated.sequence([
        Animated.timing(pulseAnim, {
          toValue: 1.15,
          duration: 1000,
          useNativeDriver: true,
        }),
        Animated.timing(pulseAnim, {
          toValue: 1,
          duration: 1000,
          useNativeDriver: true,
        }),
      ])
    ).start();
  }, []);

  // Auto countdown logic
  useEffect(() => {
    const timer = setInterval(() => {
      setCountdown((prev) => {
        if (prev <= 1) {
          clearInterval(timer);
          setAutoNavigate(true);
          return 0;
        }
        return prev - 1;
      });
    }, 1000);
    return () => clearInterval(timer);
  }, []);

  return (
    <WebPage>
      <QRScannerModal visible={showScanner} onClose={() => setShowScanner(false)} />
      <SafeAreaView style={{ flex: 1 }}>
        <WebTopBar
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
            Face recognition and passkey check-ins, GPS geo-fencing, automated payroll, and multi-tenant
            security — built for teams that need it to just work.
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
          </TouchableOpacity>

          {/* The website hero's trust chips */}
          <View style={styles.trustRow}>
            {[["lock-closed-outline", "AES-256 Encryption"], ["shield-checkmark-outline", "Role-Based Access"],
              ["time-outline", "Full Audit Trail"]].map(([icon, label]) => (
              <View key={label} style={styles.trustChip}>
                <Ionicons name={icon} size={12} color={WEB.success} />
                <Text style={styles.trustText}>{label}</Text>
              </View>
            ))}
          </View>

          <Text style={styles.copyRightText}>© 2026 HRzest.com • All Rights Reserved</Text>
        </ScrollView>
      </SafeAreaView>
    </WebPage>
  );
}

const styles = StyleSheet.create({
  content: { flexGrow: 1, justifyContent: "center", paddingHorizontal: 22, paddingTop: 24, paddingBottom: 20 },

  eyebrow: {
    flexDirection: "row", alignItems: "center", alignSelf: "flex-start",
    paddingHorizontal: 12, paddingVertical: 6, borderRadius: 999, marginBottom: 16,
    backgroundColor: "rgba(79,70,229,0.08)", borderWidth: 1, borderColor: WEB.borderGlow,
  },
  eyebrowText: { marginLeft: 6, fontSize: 11, fontWeight: "700", letterSpacing: 0.5, color: WEB.brand },
  title: { fontSize: 30, lineHeight: 35, fontWeight: "800", letterSpacing: -0.8, color: WEB.text },
  titleBrand: { color: WEB.brand },
  lead: { fontSize: 14.5, lineHeight: 22, color: WEB.textMuted, marginTop: 12, marginBottom: 22 },

  clockCard: { alignItems: "center", paddingTop: 26, paddingBottom: 20, marginBottom: 20 },
  clockHeader: { flexDirection: "row", alignItems: "center", marginBottom: 8 },
  liveDot: { width: 7, height: 7, borderRadius: 4, backgroundColor: WEB.success },
  clockHeaderLabel: { marginLeft: 7, fontSize: 10.5, fontWeight: "800", letterSpacing: 0.8, color: WEB.textMuted },
  liveTime: { fontSize: 32, fontWeight: "800", letterSpacing: 1, color: WEB.text },
  liveDate: { fontSize: 11, fontWeight: "600", letterSpacing: 0.5, color: WEB.textSubtle, marginTop: 4 },
  clockFooter: {
    flexDirection: "row", justifyContent: "space-between", alignSelf: "stretch",
    marginTop: 16, paddingTop: 14, borderTopWidth: 1, borderTopColor: WEB.border,
  },
  footerItem: { flexDirection: "row", alignItems: "center" },
  footerText: { marginLeft: 5, fontSize: 11.5, fontWeight: "600", color: WEB.textMuted },

  qrBtn: {
    flexDirection: "row", alignItems: "center", justifyContent: "center", marginTop: 12, paddingVertical: 13,
    borderRadius: WEB.radiusSm, borderWidth: 1.5, borderColor: WEB.borderGlow, backgroundColor: WEB.card,
  },
  qrBtnText: { marginLeft: 8, fontSize: 13.5, fontWeight: "700", color: WEB.brand },

  trustRow: { flexDirection: "row", flexWrap: "wrap", justifyContent: "center", marginTop: 20 },
  trustChip: {
    flexDirection: "row", alignItems: "center", margin: 4, paddingHorizontal: 10, paddingVertical: 6,
    borderRadius: 999, backgroundColor: "rgba(255,255,255,0.7)", borderWidth: 1, borderColor: WEB.border,
  },
  trustText: { marginLeft: 5, fontSize: 11.5, fontWeight: "600", color: WEB.textMuted },

  copyRightText: { fontSize: 10.5, color: WEB.textSubtle, textAlign: "center", marginTop: 20 },
});
