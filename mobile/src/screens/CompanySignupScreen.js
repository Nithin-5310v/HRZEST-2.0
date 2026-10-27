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
import SignupSteps from "../components/signup/SignupSteps";

// Step 1 of company signup, the same as the website's (/create_org): just
// the admin's account. The email is also their login username; the
// company itself is described after the email code (CompanyProfileScreen).
// These mirror blueprints/org.py's checks so a typo is caught before the
// network round trip -- the server re-validates regardless.
const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
const MOBILE_RE = /^\+?[0-9]{10,15}$/;
const MAX_EMAIL_LENGTH = 50; // admin_users.username, which the email becomes

export default function CompanySignupScreen({ navigation }) {
  const { colors } = useTheme();
  const styles = React.useMemo(() => makeStyles(colors), [colors]);

  const [adminEmail, setAdminEmail] = useState("");
  const [adminMobile, setAdminMobile] = useState("");
  const [adminPassword, setAdminPassword] = useState("");
  const [showPass, setShowPass] = useState(false);
  const [loading, setLoading] = useState(false);

  const handleSubmit = async () => {
    const email = adminEmail.trim().toLowerCase();
    const mobile = adminMobile.replace(/[\s\-.()]/g, "");
    const password = adminPassword.trim();

    if (!email || !mobile || !password) {
      Alert.alert("Missing Information", "Email, mobile number and password are all required.");
      return;
    }
    if (!EMAIL_RE.test(email)) {
      Alert.alert("Invalid Email", "Enter a valid email address.");
      return;
    }
    if (email.length > MAX_EMAIL_LENGTH) {
      Alert.alert("Email Too Long", `Use an email address of at most ${MAX_EMAIL_LENGTH} characters — it's also your login username.`);
      return;
    }
    if (!MOBILE_RE.test(mobile)) {
      Alert.alert("Invalid Mobile Number", "Enter 10 to 15 digits, optionally starting with +.");
      return;
    }
    if (password.length < 8) {
      Alert.alert("Weak Password", "Password must be at least 8 characters.");
      return;
    }

    setLoading(true);
    try {
      const res = await startCompanySignup({ admin_email: email, admin_mobile: mobile, admin_password: password });
      if (res?.data?.ok) {
        navigation.navigate("CompanyOtpVerify", {
          applicationId: res.data.application_id,
          accessToken: res.data.access_token,
          adminEmail: email,
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
              <Ionicons name="rocket-outline" size={30} color="#173B8C" />
            </View>
            <Text style={styles.title}>Start Your Organisation</Text>
            <Text style={styles.subtitle}>
              Create your admin account with your email, mobile number and a password. We'll email you a code to verify your address.
            </Text>
          </View>

          <SignupSteps current={1} />

          <View style={styles.card}>
            <View style={styles.note}>
              <Ionicons name="shield-checkmark-outline" size={16} color="#173B8C" style={styles.noteIcon} />
              <Text style={styles.noteText}>
                Your email is also your login username. We'll email it a verification code, then you'll set up your
                company profile and upload a few business documents.
              </Text>
            </View>

            <Text style={styles.label}>EMAIL</Text>
            <View style={styles.inputRow}>
              <Ionicons name="mail-outline" size={18} color="#64748B" style={styles.inputIcon} />
              <TextInput
                style={styles.input}
                placeholder="you@company.com"
                placeholderTextColor={colors.textLight}
                value={adminEmail}
                onChangeText={setAdminEmail}
                autoCapitalize="none"
                autoCorrect={false}
                keyboardType="email-address"
                maxLength={MAX_EMAIL_LENGTH}
              />
            </View>
            <Text style={styles.hint}>You'll sign in with this email, and your verification code goes here.</Text>

            <Text style={styles.label}>MOBILE NUMBER</Text>
            <View style={styles.inputRow}>
              <Ionicons name="call-outline" size={18} color="#64748B" style={styles.inputIcon} />
              <TextInput
                style={styles.input}
                placeholder="+91 98765 43210"
                placeholderTextColor={colors.textLight}
                value={adminMobile}
                onChangeText={setAdminMobile}
                keyboardType="phone-pad"
                maxLength={20}
              />
            </View>

            <Text style={styles.label}>PASSWORD</Text>
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
              {loading ? <ActivityIndicator color="#FFFFFF" /> : <Text style={styles.submitBtnText}>Continue to Email Verification</Text>}
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
  header: { alignItems: "center", marginBottom: 20 },
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
  note: {
    flexDirection: "row", alignItems: "flex-start", backgroundColor: "rgba(23,59,140,0.06)",
    borderWidth: 1, borderColor: "rgba(23,59,140,0.18)", borderRadius: 12, padding: 12, marginBottom: 18,
  },
  noteIcon: { marginRight: 8, marginTop: 1 },
  noteText: { flex: 1, fontSize: 11.5, lineHeight: 17, color: "#64748B" },
  label: { fontSize: 10, fontWeight: "800", color: "#64748B", letterSpacing: 0.8, marginBottom: 6 },
  inputRow: {
    flexDirection: "row", alignItems: "center", height: 48, borderRadius: 14,
    backgroundColor: colors.background, borderWidth: 1, borderColor: colors.border,
    paddingHorizontal: 14, marginBottom: 16,
  },
  hint: { fontSize: 11, color: "#94A3B8", marginTop: -10, marginBottom: 16 },
  inputIcon: { marginRight: 10 },
  input: { flex: 1, height: "100%", fontSize: 13, color: colors.text, fontWeight: "600" },
  eyeBtn: { padding: 6 },
  submitBtn: {
    height: 48, borderRadius: 14, backgroundColor: "#173B8C",
    justifyContent: "center", alignItems: "center", marginTop: 8,
  },
  submitBtnText: { fontSize: 13, fontWeight: "800", color: "#FFFFFF" },
});
