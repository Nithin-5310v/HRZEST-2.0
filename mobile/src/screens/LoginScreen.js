import React, { useEffect, useState } from "react";
import {
  View,
  Text,
  TextInput,
  TouchableOpacity,
  Image,
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
  signInAccount,
  getCompanySlug,
  parseCompanySlug,
  resolveCompany,
  setCompanySlug,
} from "../api/client";
import { useAuth } from "../store/AuthContext";
import { getLastCompanySlug } from "../utils/preferences";
import AppTopBar from "../components/AppTopBar";
import QRScannerModal from "./QRScannerModal";
import { WEB, AppButton } from "../theme/webAuthStyle";

// The sign-in page (design: "Final app" board "Sign in"): one box for an
// email, username or employee ID, like the website's /login -- the server
// works out whether it's an admin or an employee account.
export default function LoginScreen({ navigation }) {
  const { signIn } = useAuth();
  const insets = useSafeAreaInsets();

  // The company's address (its URL slug, e.g. "acme" from
  // https://<server>/acme/login) -- which company the sign-in goes to.
  // Blank means the server's default company. Filled in with the last one used.
  const [companyUrl, setCompanyUrl] = useState("");
  useEffect(() => {
    getLastCompanySlug().then((slug) => slug && setCompanyUrl((typed) => typed || slug)).catch(() => {});
  }, []);

  const [identifier, setIdentifier] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [showPass, setShowPass] = useState(false);
  const [showScanner, setShowScanner] = useState(false);
  const [focused, setFocused] = useState(null);

  // Admin sign-in step 2 (emailed code). Only the server's opaque
  // challenge id is kept here -- never the code itself.
  const [challengeId, setChallengeId] = useState(null);
  const [otpCode, setOtpCode] = useState("");

  // Every sign-in call below goes to this company (api/client.js prefixes it).
  // The server resolves what was typed -- a link, the slug or the company's
  // name -- so a wrong company is reported as that, not as "Invalid
  // credentials" (an unknown /<slug>/ prefix never reaches a login route).
  // Returns false when there's no such company (already explained).
  const applyTypedCompany = async () => {
    const typed = companyUrl.trim();
    if (!typed) {
      setCompanySlug("");
      return true;
    }
    try {
      const res = await resolveCompany(typed);
      setCompanySlug(res?.data?.slug || parseCompanySlug(typed));
      return true;
    } catch (err) {
      if (err?.response?.status === 404 && err?.response?.data?.ok === false) {
        Alert.alert("Company Not Found", err.response.data.msg || "We couldn't find that company. Check the company.");
        return false;
      }
      // A server without the lookup yet, or a network blip: use the address as typed.
      setCompanySlug(parseCompanySlug(typed));
      return true;
    }
  };

  // What a failed sign-in tells the person, by what actually went wrong.
  const signInErrorMessage = (err) => {
    const status = err?.response?.status;
    if (status === 429) return "Too many failed attempts. Try again later.";
    // err is null when the server answered but said no (ok: false).
    if (err && !err.response) return "Can't reach HRzest. Check your internet connection and try again.";
    // The server's firewall / automatic IP ban (app.py: "Access denied.",
    // "Request blocked by security policy.") -- not a company problem.
    if (status === 403 && /denied|blocked/i.test(err?.response?.data?.msg || "")) {
      return "HRzest paused sign-ins from this network after too many attempts. Wait 15 minutes, then try again.";
    }
    if (status === 403 || status === 404) return "That company isn't right. Check the Company box.";
    let msg = "Wrong email, username, employee ID or password.";
    if (!companyUrl.trim()) msg += " Did you enter your company?";
    return msg;
  };

  // A server not yet updated with /api/signin answers 404 "Page Not Found":
  // try the admin sign-in, then the employee one, as the old tabs did.
  const legacySignIn = async (typed) => {
    try {
      return { account: "admin", data: (await adminLogin(typed, password)).data };
    } catch (err) {
      if (err?.response?.status !== 401) throw err;
    }
    return { account: "employee", data: (await employeeLogin(typed.toUpperCase(), password)).data };
  };

  const finishSignIn = async (account, data, typed) => {
    if (account === "employee") {
      const empId = data.employee_id || typed.toUpperCase();
      await signIn(data.token, {
        role: "employee",
        name: data.name || empId,
        employeeId: empId,
        email: data.email || "",
        company: data.company_name || data.company || "",
        logo: data.company_logo || null,
        companySlug: getCompanySlug(),
      });
      return;
    }
    await signIn(data.token, {
      role: "admin",
      // adminRole distinguishes an Admin account from an HR account
      // within the admin panel (both share role:'admin' for top-level
      // app routing).
      adminRole: data.role || "admin",
      name: data.username || typed,
      companySlug: getCompanySlug(),
    });
  };

  const handleSignIn = async () => {
    const typed = identifier.trim();
    // Passwords are sent exactly as typed -- spaces are valid characters.
    if (!typed || !password) {
      Alert.alert("Input Required", "Enter your email, username or employee ID, and your password.");
      return;
    }
    setLoading(true);
    if (!(await applyTypedCompany())) {
      setLoading(false);
      return;
    }
    try {
      let account;
      let data;
      try {
        data = (await signInAccount(typed, password)).data;
        account = data?.account || (data?.employee_id ? "employee" : "admin");
      } catch (err) {
        const notThere = err?.response?.status === 404 && /not found/i.test(err?.response?.data?.msg || "");
        if (!notThere) throw err;
        ({ account, data } = await legacySignIn(typed));
      }
      if (data?.ok && data?.mfa_required && data?.challenge_id) {
        setChallengeId(data.challenge_id);
        setOtpCode("");
        setPassword("");
        setLoading(false);
        return;
      }
      if (data?.ok && data?.token) {
        await finishSignIn(account, data, typed);
        setLoading(false);
        return;
      }
      Alert.alert("Sign-in Failed", data?.msg && !/invalid credentials/i.test(data.msg) ? data.msg : signInErrorMessage(null));
    } catch (err) {
      // Keep a specific server message (e.g. a password reset by the admin);
      // replace the generic ones with what actually went wrong.
      const serverMsg = err?.response?.data?.msg;
      const status = err?.response?.status;
      const generic = !serverMsg || (!err?.response?.data?.password_change_required &&
        ([403, 404, 429].includes(status) || /invalid (employee )?credentials/i.test(serverMsg)));
      Alert.alert("Sign-in Failed", generic ? signInErrorMessage(err) : serverMsg);
    }
    setLoading(false);
  };

  const handleVerifyCode = async () => {
    const code = otpCode.trim();
    if (!/^\d{6}$/.test(code)) {
      Alert.alert("Input Required", "Enter the 6-digit code sent to your email.");
      return;
    }
    setLoading(true);
    try {
      const res = await verifyAdminLogin(challengeId, code);
      if (res?.data?.ok && res?.data?.token) {
        const typed = identifier.trim();
        setChallengeId(null);
        setOtpCode("");
        await finishSignIn("admin", res.data, typed);
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

  // No Bearer-token-compatible password-reset endpoint exists in the
  // backend -- only the website's forms (/admin_forgot_password,
  // /employee_forgot_password) -- so point people there.
  const handleForgotPassword = () => {
    Alert.alert(
      "Reset Your Password",
      "Password reset is currently only available from the web login page. Please use a browser to reset your password."
    );
  };

  const goBack = navigation?.canGoBack?.() ? () => navigation.goBack() : undefined;
  const fieldStyle = (name) => [styles.field, focused === name && styles.fieldFocused];

  return (
    <View style={styles.page}>
      <QRScannerModal visible={showScanner} onClose={() => setShowScanner(false)} />
      <AppTopBar
        title={challengeId ? "Check your email" : "Sign in"}
        onBack={challengeId ? () => setChallengeId(null) : goBack}
        showCompany={false}
      />
      <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : undefined} style={{ flex: 1 }}>
        <ScrollView
          contentContainerStyle={[styles.content, { paddingBottom: 24 + insets.bottom }]}
          keyboardShouldPersistTaps="handled"
          showsVerticalScrollIndicator={false}
        >
          {challengeId ? (
            <>
              <View style={styles.hero}>
                <View style={styles.mailIcon}>
                  <Ionicons name="mail-outline" size={34} color={WEB.brand} />
                </View>
                <Text style={styles.heading}>Check your email</Text>
                <Text style={styles.sub}>Enter the 6-digit code we sent to the email on this account. It expires in 5 minutes.</Text>
              </View>
              <Text style={styles.label}>VERIFICATION CODE</Text>
              <TextInput
                style={[fieldStyle("code"), styles.codeInput]}
                placeholder="000000"
                placeholderTextColor={WEB.textSubtle}
                value={otpCode}
                onChangeText={(v) => setOtpCode(v.replace(/[^0-9]/g, "").slice(0, 6))}
                onFocus={() => setFocused("code")}
                onBlur={() => setFocused(null)}
                keyboardType="number-pad"
                textContentType="oneTimeCode"
                autoComplete="one-time-code"
                maxLength={6}
              />
              <AppButton title="Verify and continue" onPress={handleVerifyCode} loading={loading} />
              <TouchableOpacity style={styles.centerLink} onPress={() => setChallengeId(null)} disabled={loading}>
                <Text style={styles.link}>Start over</Text>
              </TouchableOpacity>
            </>
          ) : (
            <>
              <View style={styles.hero}>
                <Image source={require("../../assets/favicon.png")} style={styles.logo} accessibilityLabel="HRZEST" />
                <Text style={styles.heading}>Welcome back</Text>
                <Text style={styles.sub}>Use the same details you use on the website.</Text>
              </View>

              <View>
                <Text style={styles.label}>COMPANY</Text>
                <View style={fieldStyle("company")}>
                  <Ionicons name="business-outline" size={20} color={WEB.textMuted} />
                  <TextInput
                    style={styles.input}
                    placeholder="Company name or web address"
                    placeholderTextColor={WEB.textSubtle}
                    value={companyUrl}
                    onChangeText={setCompanyUrl}
                    onFocus={() => setFocused("company")}
                    onBlur={() => setFocused(null)}
                    autoCapitalize="none"
                    autoCorrect={false}
                    keyboardType="url"
                  />
                </View>
                <Text style={styles.hint}>Your company's name or its web address, from your welcome email.</Text>
              </View>

              <View>
                <Text style={styles.label}>EMAIL, USERNAME OR EMPLOYEE ID</Text>
                <View style={fieldStyle("id")}>
                  <Ionicons name="person-outline" size={20} color={WEB.textMuted} />
                  <TextInput
                    style={styles.input}
                    placeholder="e.g. you@company.com or EMP-1001"
                    placeholderTextColor={WEB.textSubtle}
                    value={identifier}
                    onChangeText={setIdentifier}
                    onFocus={() => setFocused("id")}
                    onBlur={() => setFocused(null)}
                    autoCapitalize="none"
                    autoCorrect={false}
                  />
                </View>
              </View>

              <View>
                <Text style={styles.label}>PASSWORD</Text>
                <View style={fieldStyle("password")}>
                  <Ionicons name="lock-closed-outline" size={20} color={WEB.textMuted} />
                  <TextInput
                    style={styles.input}
                    placeholder="Your password"
                    placeholderTextColor={WEB.textSubtle}
                    value={password}
                    onChangeText={setPassword}
                    onFocus={() => setFocused("password")}
                    onBlur={() => setFocused(null)}
                    secureTextEntry={!showPass}
                    autoCapitalize="none"
                    autoCorrect={false}
                  />
                  <TouchableOpacity
                    onPress={() => setShowPass(!showPass)}
                    accessibilityRole="button"
                    accessibilityLabel={showPass ? "Hide password" : "Show password"}
                  >
                    <Ionicons name={showPass ? "eye-off-outline" : "eye-outline"} size={20} color={WEB.textMuted} />
                  </TouchableOpacity>
                </View>
              </View>

              <View style={styles.linksRow}>
                <TouchableOpacity onPress={() => setShowScanner(true)} style={styles.inline}>
                  <Ionicons name="scan-outline" size={16} color={WEB.brand} />
                  <Text style={styles.link}>Scan attendance QR</Text>
                </TouchableOpacity>
                <TouchableOpacity onPress={handleForgotPassword}>
                  <Text style={styles.link}>Forgot password?</Text>
                </TouchableOpacity>
              </View>

              <AppButton title="Sign in" onPress={handleSignIn} loading={loading} />
              {__DEV__ && (
                <TouchableOpacity
                  style={styles.centerLink}
                  onPress={() => {
                    setIdentifier("admin");
                    setPassword("admin123");
                  }}
                >
                  <Text style={styles.link}>Quick fill admin (dev only)</Text>
                </TouchableOpacity>
              )}

              <View style={{ flex: 1 }} />
              <Text style={styles.footer}>
                New to HRZEST?{" "}
                <Text style={styles.footerLink} onPress={() => navigation.navigate("CompanySignup")} accessibilityRole="link">
                  Register your company
                </Text>
              </Text>
            </>
          )}
        </ScrollView>
      </KeyboardAvoidingView>
    </View>
  );
}

const styles = StyleSheet.create({
  page: { flex: 1, backgroundColor: WEB.page },
  content: { flexGrow: 1, paddingHorizontal: 20, paddingTop: 16, gap: 16 },
  hero: { alignItems: "center", gap: 6, paddingTop: 4 },
  logo: { width: 56, height: 56, resizeMode: "contain" },
  mailIcon: {
    width: 72, height: 72, borderRadius: 36, backgroundColor: WEB.tint, alignItems: "center", justifyContent: "center",
  },
  heading: { fontSize: 24, fontWeight: "700", color: WEB.text },
  sub: { fontSize: 14, color: WEB.textMuted, textAlign: "center", lineHeight: 20 },
  label: { fontSize: 12, fontWeight: "700", letterSpacing: 0.4, color: WEB.textMuted, marginBottom: 6 },
  field: {
    height: 52, flexDirection: "row", alignItems: "center", gap: 10, paddingHorizontal: 14,
    borderRadius: 14, backgroundColor: WEB.field, borderWidth: 2, borderColor: "transparent",
  },
  fieldFocused: { borderColor: WEB.brand, backgroundColor: WEB.card },
  input: { flex: 1, fontSize: 15, color: WEB.text, paddingVertical: 0 },
  codeInput: { textAlign: "center", fontSize: 24, fontWeight: "700", letterSpacing: 8, color: WEB.text },
  hint: { fontSize: 12, color: WEB.textMuted, marginTop: 6, lineHeight: 17 },
  linksRow: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  inline: { flexDirection: "row", alignItems: "center", gap: 5 },
  link: { fontSize: 14, fontWeight: "700", color: WEB.brand },
  centerLink: { alignSelf: "center", paddingVertical: 4 },
  footer: { fontSize: 14, color: WEB.textMuted, textAlign: "center" },
  footerLink: { fontWeight: "700", color: WEB.brand },
});
