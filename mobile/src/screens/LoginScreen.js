import React, { useState } from "react";
import {
  View,
  Text,
  TextInput,
  TouchableOpacity,
  StyleSheet,
  ScrollView,
  KeyboardAvoidingView,
  Platform,
  Alert,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import {
  adminLogin,
  verifyAdminLogin,
  employeeLogin,
} from "../api/client";
import { useAuth } from "../store/AuthContext";
import QRScannerModal from "./QRScannerModal";
import { WEB, WebPage, WebTopBar, WebNavButton, WebCard, WebCardHeader, WebButton, webAuthStyles } from "../theme/webAuthStyle";

export default function LoginScreen({ navigation }) {
  const { signIn } = useAuth();
  const insets = useSafeAreaInsets();
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
    <WebPage>
      <QRScannerModal visible={showScanner} onClose={() => setShowScanner(false)} />
      <WebTopBar
        style={{ paddingTop: insets.top + 14 }}
        right={<WebNavButton title="Register a Company" icon="business-outline" onPress={() => navigation.navigate("CompanySignup")} />}
      />
      <KeyboardAvoidingView
        behavior={Platform.OS === "ios" ? "padding" : undefined}
        style={{ flex: 1 }}
      >
        <ScrollView
          contentContainerStyle={[webAuthStyles.scroll, styles.scroll]}
          keyboardShouldPersistTaps="handled"
          showsVerticalScrollIndicator={false}
        >
          {/* Admin / Employee: which sign-in the card below runs */}
          <View style={styles.tabs}>
            {[["admin", "shield-checkmark-outline", "Admin"], ["employee", "person-outline", "Employee"]].map(([key, icon, label]) => (
              <TouchableOpacity
                key={key}
                activeOpacity={0.85}
                style={[styles.tab, tab === key && styles.tabActive]}
                onPress={() => setTab(key)}
              >
                <Ionicons name={icon} size={15} color={tab === key ? WEB.brand : WEB.textSubtle} />
                <Text style={[styles.tabText, tab === key && styles.tabTextActive]}>{label}</Text>
              </TouchableOpacity>
            ))}
          </View>

          <WebCard>
            {tab === "admin" && challengeId ? (
              <>
                <WebCardHeader
                  icon="mail-open-outline"
                  title="Check Your Email"
                  subtitle="Enter the 6-digit code we sent to the email on this account. It expires in 5 minutes."
                />

                <View style={webAuthStyles.field}>
                  <Text style={webAuthStyles.label}>Verification Code</Text>
                  <TextInput
                    style={[webAuthStyles.input, styles.codeInput]}
                    placeholder="000000"
                    placeholderTextColor={WEB.textSubtle}
                    value={otpCode}
                    onChangeText={(v) => setOtpCode(v.replace(/[^0-9]/g, "").slice(0, 6))}
                    keyboardType="number-pad"
                    textContentType="oneTimeCode"
                    autoComplete="one-time-code"
                    maxLength={6}
                  />
                </View>

                <WebButton title="Verify & Continue" onPress={handleVerifyAdminCode} loading={loading} />

                <TouchableOpacity style={styles.centerLink} onPress={resetAdminCodeStep} disabled={loading}>
                  <Text style={webAuthStyles.linkMuted}>← Start over</Text>
                </TouchableOpacity>
              </>
            ) : (
              <>
                <WebCardHeader
                  icon="shield-checkmark"
                  title="Sign In to Your Workspace"
                  subtitle={tab === "admin" ? "Admin, HR and manager accounts" : "Employee self service and check-in"}
                />

                {tab === "admin" ? (
                  <>
                    <View style={webAuthStyles.field}>
                      <Text style={webAuthStyles.label}>Email or Username</Text>
                      <TextInput
                        style={webAuthStyles.input}
                        placeholder="Enter your email or username"
                        placeholderTextColor={WEB.textSubtle}
                        value={username}
                        onChangeText={setUsername}
                        autoCapitalize="none"
                        autoCorrect={false}
                      />
                    </View>
                    <View style={webAuthStyles.field}>
                      <Text style={webAuthStyles.label}>Password</Text>
                      <View>
                        <TextInput
                          style={[webAuthStyles.input, styles.passwordInput]}
                          placeholder="Enter your password"
                          placeholderTextColor={WEB.textSubtle}
                          value={password}
                          onChangeText={setPassword}
                          secureTextEntry={!showPass}
                          autoCapitalize="none"
                        />
                        <TouchableOpacity onPress={() => setShowPass(!showPass)} style={styles.eyeBtn}>
                          <Ionicons name={showPass ? "eye-off-outline" : "eye-outline"} size={18} color={WEB.textSubtle} />
                        </TouchableOpacity>
                      </View>
                    </View>
                    <WebButton title="Sign In" onPress={handleAdminLogin} loading={loading} />
                    {__DEV__ && (
                      <TouchableOpacity
                        style={styles.devFill}
                        onPress={() => {
                          setUsername("admin");
                          setPassword("admin123");
                        }}
                      >
                        <Ionicons name="flash-outline" size={14} color={WEB.brand} style={{ marginRight: 6 }} />
                        <Text style={styles.devFillText}>Quick Fill Admin (dev only)</Text>
                      </TouchableOpacity>
                    )}
                  </>
                ) : (
                  <>
                    <View style={webAuthStyles.field}>
                      <Text style={webAuthStyles.label}>Employee ID</Text>
                      <TextInput
                        style={webAuthStyles.input}
                        placeholder="Enter your employee ID"
                        placeholderTextColor={WEB.textSubtle}
                        value={empId}
                        onChangeText={setEmpId}
                        autoCapitalize="characters"
                        autoCorrect={false}
                      />
                    </View>
                    <View style={webAuthStyles.field}>
                      <Text style={webAuthStyles.label}>Password</Text>
                      <View>
                        <TextInput
                          style={[webAuthStyles.input, styles.passwordInput]}
                          placeholder="Enter your password"
                          placeholderTextColor={WEB.textSubtle}
                          value={empPassword}
                          onChangeText={setEmpPassword}
                          secureTextEntry={!showPass}
                          autoCapitalize="none"
                          autoCorrect={false}
                        />
                        <TouchableOpacity onPress={() => setShowPass(!showPass)} style={styles.eyeBtn}>
                          <Ionicons name={showPass ? "eye-off-outline" : "eye-outline"} size={18} color={WEB.textSubtle} />
                        </TouchableOpacity>
                      </View>
                    </View>
                    <WebButton title="Sign In" onPress={handleEmployeeLogin} loading={loading} />
                    {__DEV__ && (
                      <TouchableOpacity
                        style={styles.devFill}
                        onPress={() => {
                          setEmpId("EMP-1001");
                          setEmpPassword("welcome123");
                        }}
                      >
                        <Ionicons name="flash-outline" size={14} color={WEB.brand} style={{ marginRight: 6 }} />
                        <Text style={styles.devFillText}>Quick Fill Employee (dev only)</Text>
                      </TouchableOpacity>
                    )}
                  </>
                )}

                {/* The website's links row: kiosk on the left, password reset on the right */}
                <View style={styles.linksRow}>
                  {tab === "employee" ? (
                    <TouchableOpacity onPress={() => setShowScanner(true)} style={styles.kioskLink}>
                      <Ionicons name="qr-code-outline" size={14} color={WEB.textSubtle} style={{ marginRight: 5 }} />
                      <Text style={webAuthStyles.linkMuted}>Scan Attendance QR</Text>
                    </TouchableOpacity>
                  ) : (
                    <View />
                  )}
                  <TouchableOpacity onPress={handleForgotPassword}>
                    <Text style={webAuthStyles.link}>Forgot Password?</Text>
                  </TouchableOpacity>
                </View>

                <View style={webAuthStyles.helpBox}>
                  <Text style={webAuthStyles.helpText}>
                    <Text style={webAuthStyles.helpStrong}>Sign In Help</Text> — Use your{" "}
                    {tab === "admin" ? (
                      <><Text style={webAuthStyles.helpStrong}>email</Text> or <Text style={webAuthStyles.helpStrong}>username</Text></>
                    ) : (
                      <Text style={webAuthStyles.helpStrong}>employee ID</Text>
                    )}{" "}
                    to continue.
                  </Text>
                </View>
              </>
            )}
          </WebCard>
        </ScrollView>
      </KeyboardAvoidingView>
    </WebPage>
  );
}

const styles = StyleSheet.create({
  scroll: { justifyContent: "center", paddingTop: 20 },
  tabs: {
    flexDirection: "row", padding: 4, marginBottom: 16, borderRadius: WEB.radiusMd,
    backgroundColor: "rgba(255,255,255,0.7)", borderWidth: 1, borderColor: WEB.border,
  },
  tab: {
    flex: 1, flexDirection: "row", alignItems: "center", justifyContent: "center",
    paddingVertical: 10, borderRadius: 10,
  },
  tabActive: {
    backgroundColor: WEB.card,
    shadowColor: "#0F172A", shadowOpacity: 0.08, shadowRadius: 6, shadowOffset: { width: 0, height: 2 }, elevation: 2,
  },
  tabText: { marginLeft: 6, fontSize: 13, fontWeight: "700", color: WEB.textSubtle },
  tabTextActive: { color: WEB.brand },
  codeInput: { textAlign: "center", fontSize: 22, fontWeight: "800", letterSpacing: 8 },
  passwordInput: { paddingRight: 44 },
  eyeBtn: { position: "absolute", right: 10, top: 0, bottom: 0, justifyContent: "center", paddingHorizontal: 4 },
  centerLink: { alignSelf: "center", marginTop: 16 },
  linksRow: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", marginTop: 20 },
  kioskLink: { flexDirection: "row", alignItems: "center" },
  devFill: {
    flexDirection: "row", alignItems: "center", justifyContent: "center", marginTop: 12, paddingVertical: 9,
    borderRadius: WEB.radiusSm, borderWidth: 1, borderColor: WEB.borderGlow, backgroundColor: WEB.brandTint,
  },
  devFillText: { fontSize: 12.5, fontWeight: "700", color: WEB.brand },
});
