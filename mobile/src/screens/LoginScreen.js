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

import {
  adminLogin,
  verifyAdminLogin,
  employeeLogin,
} from "../api/client";
import { useAuth } from "../store/AuthContext";
import { useTheme } from "../store/ThemeContext";
import QRScannerModal from "./QRScannerModal";

export default function LoginScreen({ navigation }) {
  const { signIn } = useAuth();
  const { colors } = useTheme();
  const styles = React.useMemo(() => makeStyles(colors), [colors]);
  const [tab, setTab] = useState("admin"); // 'admin' | 'employee'

  // Admin login states
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");

  // Employee login states
  const [empId, setEmpId] = useState("");
  const [empPassword, setEmpPassword] = useState("");

  const [loading, setLoading] = useState(false);
  const [showPass, setShowPass] = useState(false);
  const [showScanner, setShowScanner] = useState(false);

  // Admin login step 2 (emailed code). Only the server's opaque
  // challenge id is kept here -- never the code itself.
  const [challengeId, setChallengeId] = useState(null);
  const [otpCode, setOtpCode] = useState("");

  const completeAdminSignIn = async (data, typedUser) => {
    await signIn(data.token, {
      role: "admin",
      // adminRole distinguishes an Admin account from an HR account
      // within the admin panel (both share role:'admin' for
      // top-level app routing) -- /api/login now actually returns
      // this (blueprints/core.py); defaults to 'admin' only as a
      // fallback for a server that hasn't been redeployed with that
      // fix yet, not as a guess about who's really logging in.
      adminRole: data.role || "admin",
      name: data.username || typedUser,
    });
  };

  const resetAdminCodeStep = () => {
    setChallengeId(null);
    setOtpCode("");
  };

  const handleAdminLogin = async () => {
    const trimmedUser = username.trim();
    // Passwords are sent exactly as typed -- spaces are valid characters.
    if (!trimmedUser || !password) {
      Alert.alert("Input Required", "Please enter both admin username and password.");
      return;
    }
    setLoading(true);
    try {
      const res = await adminLogin(trimmedUser, password);
      if (res?.data?.ok && res?.data?.mfa_required && res?.data?.challenge_id) {
        setChallengeId(res.data.challenge_id);
        setOtpCode("");
        setPassword("");
        setLoading(false);
        return;
      }
      if (res?.data?.ok && res?.data?.token) {
        await completeAdminSignIn(res.data, trimmedUser);
        setLoading(false);
        return;
      }
      Alert.alert("Authentication Failed", "Invalid credentials.");
    } catch (err) {
      const status = err?.response?.status;
      Alert.alert(
        "Authentication Failed",
        status === 429 ? "Too many failed attempts. Try again later." : "Invalid credentials."
      );
    }
    setLoading(false);
  };

  const handleVerifyAdminCode = async () => {
    const code = otpCode.trim();
    if (!/^\d{6}$/.test(code)) {
      Alert.alert("Input Required", "Enter the 6-digit code sent to your email.");
      return;
    }
    setLoading(true);
    try {
      const res = await verifyAdminLogin(challengeId, code);
      if (res?.data?.ok && res?.data?.token) {
        const typedUser = username.trim();
        resetAdminCodeStep();
        await completeAdminSignIn(res.data, typedUser);
        setLoading(false);
        return;
      }
      Alert.alert("Verification Failed", "Invalid or expired code.");
    } catch (_err) {
      Alert.alert("Verification Failed", "Invalid or expired code. Please sign in again if it keeps failing.");
    }
    setOtpCode("");
    setLoading(false);
  };

  const handleEmployeeLogin = async () => {
    const typedId = empId.trim().toUpperCase();
    // Passwords are sent exactly as typed -- spaces are valid characters.
    if (!typedId || !empPassword) {
      Alert.alert("Input Required", "Please enter both Employee ID and password.");
      return;
    }
    setLoading(true);
    try {
      const res = await employeeLogin(typedId, empPassword);
      if ((res?.data?.ok || res?.data?.token) && res?.data?.token) {
        await signIn(res.data.token, {
          role: "employee",
          name: res.data.name || res.data.employee?.name || typedId,
          employeeId: res.data.employee_id || typedId,
          email: res.data.email || `${typedId}@company.com`,
          company: res.data.company_name || res.data.company || "Enterprise HRMS",
          logo: res.data.company_logo || null,
        });
        setLoading(false);
        return;
      }
      const errorMsg = res?.data?.msg || "Invalid employee credentials.";
      Alert.alert("Authentication Failed", errorMsg);
    } catch (err) {
      const errorMsg = err?.response?.data?.msg || err?.message || "Invalid employee credentials. Please check Employee ID and password.";
      Alert.alert("Authentication Failed", errorMsg);
    }
    setLoading(false);
  };

  // No Bearer-token-compatible password-reset endpoint exists anywhere in
  // the backend -- only session-based web forms (/admin_forgot_password,
  // /employee_forgot_password) handle this. The old flow called
  // /api/forgot-password and /api/reset-password, which are guaranteed to
  // 404 every time -- password recovery on mobile was never actually
  // possible. Pointing users to the web portal instead of a broken form.
  const handleForgotPassword = () => {
    Alert.alert(
      "Reset Your Password",
      "Password reset is currently only available from the web login page. Please use a browser to reset your password."
    );
  };

  return (
    <LinearGradient colors={["#0F172A", "#1E3A8A", "#173B8C"]} style={styles.bg}>
      <QRScannerModal visible={showScanner} onClose={() => setShowScanner(false)} />
      <KeyboardAvoidingView
        behavior={Platform.OS === "ios" ? "padding" : undefined}
        style={{ flex: 1 }}
      >
        <ScrollView
          contentContainerStyle={styles.scroll}
          keyboardShouldPersistTaps="handled"
          showsVerticalScrollIndicator={false}
        >
          {/* Header */}
          <View style={styles.header}>
            <View style={styles.logoCircle}>
              <Ionicons
                name={tab === "admin" ? "shield-checkmark" : "people"}
                size={32}
                color="#173B8C"
              />
            </View>

            <Text style={styles.title}>Employee Attendance</Text>
            <Text style={styles.companyText}>Enterprise Workforce Platform</Text>

            <View style={styles.headerBadge}>
              <Ionicons name="lock-closed-outline" size={12} color="#FFFFFF" style={{ marginRight: 5 }} />
              <Text style={styles.badgeText}>Encrypted & Verified</Text>
            </View>
          </View>

          {/* Segmented Tab Switcher - Clean 2 Tab Design */}
          <View style={styles.tabsContainer}>
            <TouchableOpacity
              activeOpacity={0.85}
              style={[styles.tabBtn, tab === "admin" && styles.tabBtnActive]}
              onPress={() => setTab("admin")}
            >
              <Ionicons
                name="shield-outline"
                size={16}
                color={tab === "admin" ? "#173B8C" : "#94A3B8"}
              />
              <Text style={[styles.tabText, tab === "admin" && styles.tabTextActive]}>
                Admin
              </Text>
            </TouchableOpacity>

            <TouchableOpacity
              activeOpacity={0.85}
              style={[styles.tabBtn, tab === "employee" && styles.tabBtnActive]}
              onPress={() => setTab("employee")}
            >
              <Ionicons
                name="person-outline"
                size={16}
                color={tab === "employee" ? "#173B8C" : "#94A3B8"}
              />
              <Text style={[styles.tabText, tab === "employee" && styles.tabTextActive]}>
                Employee
              </Text>
            </TouchableOpacity>
          </View>

          {/* Form Card */}
          <View style={styles.card}>
            {tab === "admin" && challengeId ? (
              <>
                <View style={styles.formHeader}>
                  <Text style={styles.formTitle}>Check Your Email</Text>
                  <Text style={styles.formSubtitle}>
                    Enter the 6-digit code we sent to the email on this account. It expires in 5 minutes.
                  </Text>
                </View>

                <Text style={styles.label}>VERIFICATION CODE</Text>
                <View style={styles.inputRow}>
                  <Ionicons name="key-outline" size={18} color="#64748B" style={styles.inputIcon} />
                  <TextInput
                    style={styles.input}
                    placeholder="123456"
                    placeholderTextColor={colors.textLight}
                    value={otpCode}
                    onChangeText={(v) => setOtpCode(v.replace(/[^0-9]/g, "").slice(0, 6))}
                    keyboardType="number-pad"
                    textContentType="oneTimeCode"
                    autoComplete="one-time-code"
                    maxLength={6}
                  />
                </View>

                <TouchableOpacity
                  activeOpacity={0.85}
                  style={styles.submitBtn}
                  onPress={handleVerifyAdminCode}
                  disabled={loading}
                >
                  {loading ? (
                    <ActivityIndicator color="#FFFFFF" />
                  ) : (
                    <Text style={styles.submitBtnText}>Verify & Sign In</Text>
                  )}
                </TouchableOpacity>

                <TouchableOpacity
                  style={{ alignSelf: 'center', marginTop: 12 }}
                  onPress={resetAdminCodeStep}
                  disabled={loading}
                >
                  <Text style={{ color: '#173B8C', fontSize: 13, fontWeight: '600' }}>Start over</Text>
                </TouchableOpacity>
              </>
            ) : tab === "admin" ? (
              <>
                <View style={styles.formHeader}>
                  <Text style={styles.formTitle}>Admin Portal</Text>
                  <Text style={styles.formSubtitle}>Sign in to manage company attendance</Text>
                </View>

                <Text style={styles.label}>EMAIL OR USERNAME</Text>
                <View style={styles.inputRow}>
                  <Ionicons name="person-outline" size={18} color="#64748B" style={styles.inputIcon} />
                  <TextInput
                    style={styles.input}
                    placeholder="Enter your email or username"
                    placeholderTextColor={colors.textLight}
                    value={username}
                    onChangeText={setUsername}
                    autoCapitalize="none"
                  />
                </View>

                <Text style={styles.label}>PASSWORD</Text>
                <View style={styles.inputRow}>
                  <Ionicons name="lock-closed-outline" size={18} color="#64748B" style={styles.inputIcon} />
                  <TextInput
                    style={styles.input}
                    placeholder="Enter password"
                    placeholderTextColor={colors.textLight}
                    value={password}
                    onChangeText={setPassword}
                    secureTextEntry={!showPass}
                    autoCapitalize="none"
                  />
                  <TouchableOpacity onPress={() => setShowPass(!showPass)} style={styles.eyeBtn}>
                    <Ionicons
                      name={showPass ? "eye-off-outline" : "eye-outline"}
                      size={18}
                      color="#64748B"
                    />
                  </TouchableOpacity>
                </View>

                <TouchableOpacity
                  activeOpacity={0.85}
                  style={styles.submitBtn}
                  onPress={handleAdminLogin}
                  disabled={loading}
                >
                  {loading ? (
                    <ActivityIndicator color="#FFFFFF" />
                  ) : (
                    <Text style={styles.submitBtnText}>Sign In as Admin</Text>
                  )}
                </TouchableOpacity>

                {__DEV__ && (
                  <TouchableOpacity
                    activeOpacity={0.8}
                    style={{
                      flexDirection: "row",
                      alignItems: "center",
                      justifyContent: "center",
                      backgroundColor: "#EFF6FF",
                      borderColor: "#BFDBFE",
                      borderWidth: 1,
                      borderRadius: 10,
                      paddingVertical: 9,
                      marginTop: 12,
                    }}
                    onPress={() => {
                      setUsername("admin");
                      setPassword("admin123");
                    }}
                  >
                    <Ionicons name="flash-outline" size={15} color="#1D4ED8" style={{ marginRight: 6 }} />
                    <Text style={{ color: "#1D4ED8", fontSize: 13, fontWeight: "700" }}>
                      Quick Fill Admin (dev only)
                    </Text>
                  </TouchableOpacity>
                )}

                <TouchableOpacity
                  style={{ alignSelf: 'center', marginTop: 12 }}
                  onPress={handleForgotPassword}
                >
                  <Text style={{ color: '#173B8C', fontSize: 13, fontWeight: '600' }}>Forgot Password?</Text>
                </TouchableOpacity>
              </>
            ) : (
              <>
                <View style={styles.formHeader}>
                  <Text style={styles.formTitle}>Employee Portal</Text>
                  <Text style={styles.formSubtitle}>Sign in to check-in and access self service</Text>
                </View>

                <Text style={styles.label}>EMPLOYEE ID</Text>
                <View style={styles.inputRow}>
                  <Ionicons name="id-card-outline" size={18} color="#64748B" style={styles.inputIcon} />
                  <TextInput
                    style={styles.input}
                    placeholder="Enter employee ID"
                    placeholderTextColor={colors.textLight}
                    value={empId}
                    onChangeText={setEmpId}
                    autoCapitalize="characters"
                    autoCorrect={false}
                  />
                </View>

                <Text style={styles.label}>PASSWORD</Text>
                <View style={styles.inputRow}>
                  <Ionicons name="lock-closed-outline" size={18} color="#64748B" style={styles.inputIcon} />
                  <TextInput
                    style={styles.input}
                    placeholder="Enter password"
                    placeholderTextColor={colors.textLight}
                    value={empPassword}
                    onChangeText={setEmpPassword}
                    secureTextEntry={!showPass}
                    autoCapitalize="none"
                    autoCorrect={false}
                  />
                  <TouchableOpacity onPress={() => setShowPass(!showPass)} style={styles.eyeBtn}>
                    <Ionicons
                      name={showPass ? "eye-off-outline" : "eye-outline"}
                      size={18}
                      color="#64748B"
                    />
                  </TouchableOpacity>
                </View>

                <TouchableOpacity
                  activeOpacity={0.85}
                  style={styles.submitBtn}
                  onPress={handleEmployeeLogin}
                  disabled={loading}
                >
                  {loading ? (
                    <ActivityIndicator color="#FFFFFF" />
                  ) : (
                    <Text style={styles.submitBtnText}>Sign In as Employee</Text>
                  )}
                </TouchableOpacity>

                {__DEV__ && (
                  <TouchableOpacity
                    activeOpacity={0.8}
                    style={{
                      flexDirection: "row",
                      alignItems: "center",
                      justifyContent: "center",
                      backgroundColor: "#F0FDF4",
                      borderColor: "#BBF7D0",
                      borderWidth: 1,
                      borderRadius: 10,
                      paddingVertical: 9,
                      marginTop: 12,
                    }}
                    onPress={() => {
                      setEmpId("EMP-1001");
                      setEmpPassword("welcome123");
                    }}
                  >
                    <Ionicons name="flash-outline" size={15} color="#15803D" style={{ marginRight: 6 }} />
                    <Text style={{ color: "#15803D", fontSize: 13, fontWeight: "700" }}>
                      Quick Fill Employee (dev only)
                    </Text>
                  </TouchableOpacity>
                )}

                <TouchableOpacity
                  style={{ alignSelf: 'center', marginTop: 12 }}
                  onPress={handleForgotPassword}
                >
                  <Text style={{ color: '#173B8C', fontSize: 13, fontWeight: '600' }}>Forgot Password?</Text>
                </TouchableOpacity>

                <View style={styles.dividerRow}>
                  <View style={styles.dividerLine} />
                  <Text style={styles.dividerText}>QUICK OPTIONS</Text>
                  <View style={styles.dividerLine} />
                </View>

                <TouchableOpacity
                  activeOpacity={0.85}
                  style={styles.scanBtn}
                  onPress={() => setShowScanner(true)}
                >
                  <Ionicons name="qr-code-outline" size={18} color="#173B8C" />
                  <Text style={styles.scanBtnText}>Scan Attendance QR Code</Text>
                </TouchableOpacity>

              </>
            )}
          </View>

          <TouchableOpacity
            style={styles.registerLink}
            onPress={() => navigation.navigate("CompanySignup")}
          >
            <Ionicons name="business-outline" size={14} color="rgba(255,255,255,0.85)" style={{ marginRight: 6 }} />
            <Text style={styles.registerLinkText}>New company? Register here</Text>
          </TouchableOpacity>
        </ScrollView>
      </KeyboardAvoidingView>

    </LinearGradient>
  );
}

const makeStyles = (colors) => StyleSheet.create({
  bg: { flex: 1 },
  scroll: {
    flexGrow: 1,
    justifyContent: "center",
    paddingHorizontal: 22,
    paddingVertical: 32,
  },

  header: {
    alignItems: "center",
    marginBottom: 24,
  },
  logoCircle: {
    width: 68,
    height: 68,
    borderRadius: 34,
    backgroundColor: "#FFFFFF",
    justifyContent: "center",
    alignItems: "center",
    marginBottom: 16,
    shadowColor: "#000",
    shadowOpacity: 0.15,
    shadowRadius: 12,
    shadowOffset: { width: 0, height: 6 },
    elevation: 6,
  },
  title: {
    fontSize: 18,
    fontWeight: "800",
    color: "#FFFFFF",
    letterSpacing: -0.4,
  },
  companyText: {
    fontSize: 12,
    color: "rgba(255, 255, 255, 0.78)",
    marginTop: 4,
  },
  headerBadge: {
    flexDirection: "row",
    alignItems: "center",
    marginTop: 12,
    backgroundColor: "rgba(255, 255, 255, 0.12)",
    paddingHorizontal: 12,
    paddingVertical: 5,
    borderRadius: 20,
  },
  badgeText: {
    fontSize: 11,
    fontWeight: "700",
    color: "#FFFFFF",
    letterSpacing: 0.4,
  },

  // Tabs
  tabsContainer: {
    flexDirection: "row",
    backgroundColor: "rgba(255, 255, 255, 0.12)",
    borderRadius: 16,
    padding: 4,
    marginBottom: 20,
  },
  tabBtn: {
    flex: 1,
    flexDirection: "row",
    justifyContent: "center",
    alignItems: "center",
    paddingVertical: 10,
    borderRadius: 12,
  },
  tabBtnActive: {
    backgroundColor: "#FFFFFF",
  },
  tabText: {
    fontSize: 12,
    fontWeight: "700",
    color: "rgba(255, 255, 255, 0.75)",
    marginLeft: 4,
  },
  tabTextActive: {
    color: "#173B8C",
  },

  // Card
  card: {
    backgroundColor: colors.card,
    borderRadius: 24,
    padding: 22,
    shadowColor: "#0F172A",
    shadowOpacity: 0.12,
    shadowRadius: 20,
    shadowOffset: { width: 0, height: 10 },
    elevation: 8,
  },
  formHeader: {
    marginBottom: 20,
  },
  formTitle: {
    fontSize: 16,
    fontWeight: "800",
    color: colors.text,
    letterSpacing: -0.3,
  },
  formSubtitle: {
    fontSize: 12,
    color: "#64748B",
    marginTop: 3,
  },
  label: {
    fontSize: 10,
    fontWeight: "800",
    color: "#64748B",
    letterSpacing: 0.8,
    marginBottom: 6,
  },
  inputRow: {
    flexDirection: "row",
    alignItems: "center",
    height: 48,
    borderRadius: 14,
    backgroundColor: colors.background,
    borderWidth: 1,
    borderColor: colors.border,
    paddingHorizontal: 14,
    marginBottom: 16,
  },
  inputIcon: {
    marginRight: 10,
  },
  input: {
    flex: 1,
    height: "100%",
    fontSize: 13,
    color: colors.text,
    fontWeight: "600",
  },
  eyeBtn: {
    padding: 6,
  },
  submitBtn: {
    height: 48,
    borderRadius: 14,
    backgroundColor: "#173B8C",
    justifyContent: "center",
    alignItems: "center",
    marginTop: 8,
  },
  submitBtnText: {
    fontSize: 13,
    fontWeight: "800",
    color: "#FFFFFF",
  },

  dividerRow: {
    flexDirection: "row",
    alignItems: "center",
    marginVertical: 18,
  },
  dividerLine: {
    flex: 1,
    height: 1,
    backgroundColor: colors.border,
  },
  dividerText: {
    fontSize: 10,
    fontWeight: "800",
    color: colors.textLight,
    paddingHorizontal: 10,
  },
  scanBtn: {
    height: 48,
    borderRadius: 14,
    backgroundColor: colors.primaryLight,
    flexDirection: "row",
    justifyContent: "center",
    alignItems: "center",
    borderWidth: 1,
    borderColor: "#DBEAFE",
  },
  scanBtnText: {
    fontSize: 13,
    fontWeight: "700",
    color: "#173B8C",
    marginLeft: 8,
  },

  registerLink: {
    flexDirection: "row",
    justifyContent: "center",
    alignItems: "center",
    marginTop: 22,
  },
  registerLinkText: {
    fontSize: 12.5,
    fontWeight: "700",
    color: "rgba(255, 255, 255, 0.85)",
  },
});
