import React, { useState } from "react";
import {
  View,
  Text,
  TextInput,
  TouchableOpacity,
  StyleSheet,
  ActivityIndicator,
  ScrollView,
  KeyboardAvoidingView,
  Platform,
  Alert,
} from "react-native";
import { LinearGradient } from "expo-linear-gradient";
import { Ionicons } from "@expo/vector-icons";

import { verifyCompanySignupOtp, resendCompanySignupOtp } from "../api/client";
import { useTheme } from "../store/ThemeContext";

export default function CompanyOtpVerifyScreen({ navigation, route }) {
  const { colors } = useTheme();
  const styles = React.useMemo(() => makeStyles(colors), [colors]);
  const { applicationId, accessToken, companyName, adminEmail } = route.params;

  const [otpCode, setOtpCode] = useState("");
  const [loading, setLoading] = useState(false);
  const [resending, setResending] = useState(false);

  const handleVerify = async () => {
    if (otpCode.trim().length !== 6) {
      Alert.alert("Invalid Code", "Enter the 6-digit code we emailed you.");
      return;
    }
    setLoading(true);
    try {
      const res = await verifyCompanySignupOtp(applicationId, accessToken, otpCode.trim());
      if (res?.data?.ok) {
        navigation.navigate("CompanyDocumentUpload", { applicationId, accessToken, companyName });
      } else {
        Alert.alert("Verification Failed", res?.data?.msg || "Incorrect or expired code.");
      }
    } catch (err) {
      Alert.alert("Verification Failed", err?.response?.data?.msg || err?.message || "Network error.");
    }
    setLoading(false);
  };

  const handleResend = async () => {
    setResending(true);
    try {
      const res = await resendCompanySignupOtp(applicationId, accessToken);
      Alert.alert(res?.data?.ok ? "Code Sent" : "Could Not Resend", res?.data?.msg || "");
    } catch (err) {
      Alert.alert("Could Not Resend", err?.response?.data?.msg || err?.message || "Network error.");
    }
    setResending(false);
  };

  return (
    <LinearGradient colors={["#0F172A", "#1E3A8A", "#173B8C"]} style={styles.bg}>
      <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : undefined} style={{ flex: 1 }}>
        <ScrollView contentContainerStyle={styles.scroll} keyboardShouldPersistTaps="handled" showsVerticalScrollIndicator={false}>
          <TouchableOpacity style={styles.backBtn} onPress={() => navigation.goBack()}>
            <Ionicons name="arrow-back" size={22} color="#FFFFFF" />
          </TouchableOpacity>

          <View style={styles.header}>
            <View style={styles.logoCircle}>
              <Ionicons name="mail-open-outline" size={30} color="#173B8C" />
            </View>
            <Text style={styles.title}>Verify Your Email</Text>
            <Text style={styles.subtitle}>
              Enter the 6-digit code we sent to {adminEmail}. It expires in 5 minutes.
            </Text>
          </View>

          <View style={styles.card}>
            <TextInput
              style={styles.otpInput}
              placeholder="••••••"
              placeholderTextColor={colors.textLight}
              value={otpCode}
              onChangeText={(v) => setOtpCode(v.replace(/[^0-9]/g, "").slice(0, 6))}
              keyboardType="number-pad"
              maxLength={6}
              autoFocus
            />

            <TouchableOpacity style={styles.submitBtn} onPress={handleVerify} disabled={loading} activeOpacity={0.85}>
              {loading ? <ActivityIndicator color="#FFFFFF" /> : <Text style={styles.submitBtnText}>Verify & Continue</Text>}
            </TouchableOpacity>

            <TouchableOpacity style={styles.resendBtn} onPress={handleResend} disabled={resending}>
              <Text style={styles.resendText}>{resending ? "Sending..." : "Didn't get a code? Resend"}</Text>
            </TouchableOpacity>
          </View>
        </ScrollView>
      </KeyboardAvoidingView>
    </LinearGradient>
  );
}

const makeStyles = (colors) => StyleSheet.create({
  bg: { flex: 1 },
  scroll: { flexGrow: 1, paddingHorizontal: 22, paddingVertical: 32 },
  backBtn: { width: 40, height: 40, justifyContent: "center", marginBottom: 8 },
  header: { alignItems: "center", marginBottom: 24 },
  logoCircle: {
    width: 64, height: 64, borderRadius: 32, backgroundColor: "#FFFFFF",
    justifyContent: "center", alignItems: "center", marginBottom: 16,
  },
  title: { fontSize: 18, fontWeight: "800", color: "#FFFFFF", textAlign: "center" },
  subtitle: { fontSize: 12.5, color: "rgba(255,255,255,0.75)", textAlign: "center", marginTop: 8, lineHeight: 18, paddingHorizontal: 8 },
  card: {
    backgroundColor: colors.card, borderRadius: 24, padding: 22,
    shadowColor: "#0F172A", shadowOpacity: 0.12, shadowRadius: 20, shadowOffset: { width: 0, height: 10 }, elevation: 8,
  },
  otpInput: {
    height: 60, borderRadius: 14, backgroundColor: colors.background, borderWidth: 1, borderColor: colors.border,
    textAlign: "center", fontSize: 26, fontWeight: "800", letterSpacing: 10, color: colors.text, marginBottom: 18,
  },
  submitBtn: {
    height: 48, borderRadius: 14, backgroundColor: "#173B8C",
    justifyContent: "center", alignItems: "center", marginBottom: 12,
  },
  submitBtnText: { fontSize: 13, fontWeight: "800", color: "#FFFFFF" },
  resendBtn: { alignSelf: "center", padding: 6 },
  resendText: { fontSize: 13, fontWeight: "600", color: "#173B8C" },
});
