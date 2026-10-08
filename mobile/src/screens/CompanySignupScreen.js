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

import { startCompanySignup } from "../api/client";
import { useTheme } from "../store/ThemeContext";

export default function CompanySignupScreen({ navigation }) {
  const { colors } = useTheme();
  const styles = React.useMemo(() => makeStyles(colors), [colors]);

  const [companyName, setCompanyName] = useState("");
  const [subdomain, setSubdomain] = useState("");
  const [adminEmail, setAdminEmail] = useState("");
  const [adminPassword, setAdminPassword] = useState("");
  const [gstNumber, setGstNumber] = useState("");
  const [showPass, setShowPass] = useState(false);
  const [loading, setLoading] = useState(false);

  const handleCompanyNameChange = (value) => {
    setCompanyName(value);
    setSubdomain((prev) => (prev ? prev : value.toLowerCase().replace(/[^a-z0-9-]/g, "")));
  };

  // Mirrors blueprints/org.py's _GST_RE -- checked here only so a typo is
  // caught before the network round trip; the server re-validates this
  // exact format regardless (never trust a client-side check alone).
  const GST_RE = /^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$/;

  const handleSubmit = async () => {
    const cleanSubdomain = subdomain.trim().toLowerCase().replace(/[^a-z0-9-]/g, "");
    const trimmedEmail = adminEmail.trim();
    const trimmedPassword = adminPassword.trim();
    const cleanGst = gstNumber.trim().toUpperCase().replace(/\s+/g, "");
    const atIndex = trimmedEmail.indexOf("@");

    if (!companyName.trim() || !cleanSubdomain || !trimmedEmail || !trimmedPassword || !cleanGst) {
      Alert.alert("Missing Information", "Please fill in every field.");
      return;
    }
    if (atIndex < 1) {
      Alert.alert("Invalid Email", "Enter a valid admin email address.");
      return;
    }
    if (trimmedPassword.length < 8) {
      Alert.alert("Weak Password", "Password must be at least 8 characters.");
      return;
    }
    if (!GST_RE.test(cleanGst)) {
      Alert.alert("Invalid GSTIN", "Enter a valid 15-character GSTIN (e.g. 27AAAAA0000A1Z5).");
      return;
    }

    const adminUsername = trimmedEmail.slice(0, atIndex).toLowerCase().replace(/[^a-z0-9_.-]/g, "");
    const emailDomain = trimmedEmail.slice(atIndex + 1).toLowerCase();

    setLoading(true);
    try {
      const res = await startCompanySignup({
        company_name: companyName.trim(),
        subdomain: cleanSubdomain,
        admin_username: adminUsername,
        admin_email: trimmedEmail,
        admin_password: trimmedPassword,
        email_domain: emailDomain,
        gst_number: cleanGst,
      });
      if (res?.data?.ok) {
        navigation.navigate("CompanyOtpVerify", {
          applicationId: res.data.application_id,
          accessToken: res.data.access_token,
          companyName: companyName.trim(),
          adminEmail: trimmedEmail,
        });
      } else {
        Alert.alert("Could Not Register", res?.data?.msg || "Please check your details and try again.");
      }
    } catch (err) {
      Alert.alert("Could Not Register", err?.response?.data?.msg || err?.message || "Network error.");
    }
    setLoading(false);
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
              <Ionicons name="business-outline" size={30} color="#173B8C" />
            </View>
            <Text style={styles.title}>Register Your Company</Text>
            <Text style={styles.subtitle}>
              We verify every new company before it goes live — you'll confirm your email and upload a few business documents next.
            </Text>
          </View>

          <View style={styles.card}>
            <Text style={styles.label}>COMPANY NAME</Text>
            <View style={styles.inputRow}>
              <Ionicons name="business-outline" size={18} color="#64748B" style={styles.inputIcon} />
              <TextInput
                style={styles.input}
                placeholder="e.g. Acme Corporation"
                placeholderTextColor={colors.textLight}
                value={companyName}
                onChangeText={handleCompanyNameChange}
              />
            </View>

            <Text style={styles.label}>SUBDOMAIN</Text>
            <View style={styles.inputRow}>
              <Ionicons name="globe-outline" size={18} color="#64748B" style={styles.inputIcon} />
              <TextInput
                style={styles.input}
                placeholder="acme"
                placeholderTextColor={colors.textLight}
                value={subdomain}
                onChangeText={(v) => setSubdomain(v.toLowerCase().replace(/[^a-z0-9-]/g, ""))}
                autoCapitalize="none"
              />
            </View>

            <Text style={styles.label}>ADMIN EMAIL</Text>
            <View style={styles.inputRow}>
              <Ionicons name="mail-outline" size={18} color="#64748B" style={styles.inputIcon} />
              <TextInput
                style={styles.input}
                placeholder="admin@acme.com"
                placeholderTextColor={colors.textLight}
                value={adminEmail}
                onChangeText={setAdminEmail}
                autoCapitalize="none"
                keyboardType="email-address"
              />
            </View>

            <Text style={styles.label}>GST NUMBER</Text>
            <View style={styles.inputRow}>
              <Ionicons name="document-text-outline" size={18} color="#64748B" style={styles.inputIcon} />
              <TextInput
                style={styles.input}
                placeholder="27AAAAA0000A1Z5"
                placeholderTextColor={colors.textLight}
                value={gstNumber}
                onChangeText={(v) => setGstNumber(v.toUpperCase().replace(/\s+/g, ""))}
                autoCapitalize="characters"
                maxLength={15}
              />
            </View>

            <Text style={styles.label}>ADMIN PASSWORD</Text>
            <View style={styles.inputRow}>
              <Ionicons name="lock-closed-outline" size={18} color="#64748B" style={styles.inputIcon} />
              <TextInput
                style={styles.input}
                placeholder="At least 8 characters"
                placeholderTextColor={colors.textLight}
                value={adminPassword}
                onChangeText={setAdminPassword}
                secureTextEntry={!showPass}
                autoCapitalize="none"
              />
              <TouchableOpacity onPress={() => setShowPass(!showPass)} style={styles.eyeBtn}>
                <Ionicons name={showPass ? "eye-off-outline" : "eye-outline"} size={18} color="#64748B" />
              </TouchableOpacity>
            </View>

            <TouchableOpacity style={styles.submitBtn} onPress={handleSubmit} disabled={loading} activeOpacity={0.85}>
              {loading ? <ActivityIndicator color="#FFFFFF" /> : <Text style={styles.submitBtnText}>Continue to Verification</Text>}
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
  label: { fontSize: 10, fontWeight: "800", color: "#64748B", letterSpacing: 0.8, marginBottom: 6 },
  inputRow: {
    flexDirection: "row", alignItems: "center", height: 48, borderRadius: 14,
    backgroundColor: colors.background, borderWidth: 1, borderColor: colors.border,
    paddingHorizontal: 14, marginBottom: 16,
  },
  inputIcon: { marginRight: 10 },
  input: { flex: 1, height: "100%", fontSize: 13, color: colors.text, fontWeight: "600" },
  eyeBtn: { padding: 6 },
  submitBtn: {
    height: 48, borderRadius: 14, backgroundColor: "#173B8C",
    justifyContent: "center", alignItems: "center", marginTop: 8,
  },
  submitBtnText: { fontSize: 13, fontWeight: "800", color: "#FFFFFF" },
});
