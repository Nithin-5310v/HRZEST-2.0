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

import { resendCompanySignupOtp, startCompanySignup, verifyCompanySignupOtp } from "../api/client";
import { API_BASE_URL } from "../config";
import { useAuth } from "../store/AuthContext";
import { WEB, WebPage, WebTopBar, WebNavButton, WebCard, WebCardHeader, WebButton, webAuthStyles } from "../theme/webAuthStyle";

// Company signup is one step, the same as the website's (/create_org): the
// company's name and the admin's account, then -- on this same card -- the
// code emailed to verify the address. The code creates the company and
// signs the admin straight in (the server answers with a token and the
// company's URL slug); GSTIN, documents and payment are finished from the
// dashboard's registration checklist. These mirror blueprints/org.py's
// checks so a typo is caught before the network round trip -- the server
// re-validates regardless.
const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
const MOBILE_RE = /^\+?[0-9]{10,15}$/;
const MAX_EMAIL_LENGTH = 50; // admin_users.username, which the email becomes

// Mirrors org.py's _slug_for_company(); the server adds -2, -3 ... if it's taken.
const previewSlug = (name) =>
  name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 40).replace(/-+$/, "");

export default function CompanySignupScreen({ navigation }) {
  const { signIn } = useAuth();
  const insets = useSafeAreaInsets();

  const [companyName, setCompanyName] = useState("");
  const [adminEmail, setAdminEmail] = useState("");
  const [adminMobile, setAdminMobile] = useState("");
  const [adminPassword, setAdminPassword] = useState("");
  const [showPass, setShowPass] = useState(false);
  const [loading, setLoading] = useState(false);

  // Set once the code is sent: { applicationId, accessToken, email }.
  const [pending, setPending] = useState(null);
  const [otpCode, setOtpCode] = useState("");

  const handleSendCode = async () => {
    const name = companyName.replace(/\s+/g, " ").trim();
    const email = adminEmail.trim().toLowerCase();
    const mobile = adminMobile.replace(/[\s\-.()]/g, "");
    const password = adminPassword.trim();

    if (!name || !email || !mobile || !password) {
      Alert.alert("Missing Information", "Company name, email, mobile number and password are all required.");
      return;
    }
    if (!previewSlug(name)) {
      Alert.alert("Invalid Company Name", "Company name must contain at least one letter or digit.");
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
      const res = await startCompanySignup({
        company_name: name, admin_email: email, admin_mobile: mobile, admin_password: password,
      });
      if (res?.data?.ok) {
        setPending({ applicationId: res.data.application_id, accessToken: res.data.access_token, email });
        setOtpCode("");
      } else {
        Alert.alert("Could Not Register", res?.data?.msg || "Please check your details and try again.");
      }
    } catch (err) {
      Alert.alert("Could Not Register", err?.response?.data?.msg || err?.message || "Network error.");
    }
    setLoading(false);
  };

  const handleVerify = async () => {
    const code = otpCode.trim();
    if (!/^\d{6}$/.test(code)) {
      Alert.alert("Input Required", "Enter the 6-digit code we emailed you.");
      return;
    }
    setLoading(true);
    try {
      const res = await verifyCompanySignupOtp(pending.applicationId, pending.accessToken, code);
      const data = res?.data;
      if (data?.ok && data.step === "done" && data.token) {
        // Lands in the admin dashboard, under the new company.
        await signIn(data.token, {
          role: "admin",
          adminRole: data.role || "admin",
          name: data.username,
          company: data.company_name,
          companySlug: data.company_slug,
        });
        return;
      }
      Alert.alert("Could Not Verify", data?.msg || "Please try again.");
    } catch (err) {
      Alert.alert("Could Not Verify", err?.response?.data?.msg || err?.message || "Network error.");
    }
    setLoading(false);
  };

  const handleResend = async () => {
    try {
      const res = await resendCompanySignupOtp(pending.applicationId, pending.accessToken);
      Alert.alert("Code Sent", res?.data?.msg || "A new code has been sent to your email.");
    } catch (err) {
      Alert.alert("Could Not Resend", err?.response?.data?.msg || "Please wait a moment and try again.");
    }
  };

  const slug = previewSlug(companyName);

  return (
    <WebPage>
      <WebTopBar
        style={{ paddingTop: insets.top + 14 }}
        right={<WebNavButton title="Sign In" icon="log-in-outline" onPress={() => navigation.goBack()} />}
      />
      <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : undefined} style={{ flex: 1 }}>
        <ScrollView contentContainerStyle={[webAuthStyles.scroll, styles.scroll]} keyboardShouldPersistTaps="handled"
                    showsVerticalScrollIndicator={false}>
          <WebCard>
            {pending ? (
              <>
                <WebCardHeader
                  icon="mail-open-outline"
                  title="Start Your Organisation"
                  subtitle={`Enter the 6-digit code we emailed to ${pending.email}. It expires in 5 minutes.`}
                />

                <View style={styles.entered}>
                  <View style={styles.enteredRow}>
                    <Ionicons name="business-outline" size={15} color={WEB.brand} />
                    <Text style={styles.enteredText} numberOfLines={1}>{companyName.replace(/\s+/g, " ").trim()}</Text>
                  </View>
                  <View style={styles.enteredRow}>
                    <Ionicons name="mail-outline" size={15} color={WEB.brand} />
                    <Text style={styles.enteredText} numberOfLines={1}>{pending.email}</Text>
                  </View>
                  <TouchableOpacity onPress={() => setPending(null)} disabled={loading}>
                    <Text style={[webAuthStyles.link, styles.changeLink]}>Change details</Text>
                  </TouchableOpacity>
                </View>

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

                <WebButton title="Verify & Open My Dashboard" onPress={handleVerify} loading={loading} />

                <TouchableOpacity style={styles.centerLink} onPress={handleResend} disabled={loading}>
                  <Text style={webAuthStyles.link}>Didn't get a code? Resend</Text>
                </TouchableOpacity>
              </>
            ) : (
              <>
                <WebCardHeader
                  icon="rocket-outline"
                  title="Start Your Organisation"
                  subtitle="Name your company and create your admin account. We'll email you a code, and you're in."
                />

                <View style={webAuthStyles.note}>
                  <Ionicons name="shield-checkmark-outline" size={16} color={WEB.brand} />
                  <Text style={webAuthStyles.noteText}>
                    Your email is also your login username. Once you enter the code we email to it, you're in your
                    dashboard — add your GSTIN, business documents and payment details from there whenever you're ready.
                  </Text>
                </View>

                <View style={webAuthStyles.field}>
                  <Text style={webAuthStyles.label}>Company Name</Text>
                  <TextInput
                    style={webAuthStyles.input}
                    placeholder="e.g. Acme Corporation"
                    placeholderTextColor={WEB.textSubtle}
                    value={companyName}
                    onChangeText={setCompanyName}
                    maxLength={200}
                  />
                  <Text style={webAuthStyles.hint}>Your team's portal: {API_BASE_URL}/{slug || "your-company"}/login</Text>
                </View>

                <View style={webAuthStyles.field}>
                  <Text style={webAuthStyles.label}>Email</Text>
                  <TextInput
                    style={webAuthStyles.input}
                    placeholder="you@company.com"
                    placeholderTextColor={WEB.textSubtle}
                    value={adminEmail}
                    onChangeText={setAdminEmail}
                    autoCapitalize="none"
                    autoCorrect={false}
                    keyboardType="email-address"
                    maxLength={MAX_EMAIL_LENGTH}
                  />
                  <Text style={webAuthStyles.hint}>You'll sign in with this email, and your verification code goes here.</Text>
                </View>

                <View style={webAuthStyles.field}>
                  <Text style={webAuthStyles.label}>Mobile Number</Text>
                  <TextInput
                    style={webAuthStyles.input}
                    placeholder="+91 98765 43210"
                    placeholderTextColor={WEB.textSubtle}
                    value={adminMobile}
                    onChangeText={setAdminMobile}
                    keyboardType="phone-pad"
                    maxLength={20}
                  />
                </View>

                <View style={webAuthStyles.field}>
                  <Text style={webAuthStyles.label}>Password</Text>
                  <View>
                    <TextInput
                      style={[webAuthStyles.input, styles.passwordInput]}
                      placeholder="At least 8 characters"
                      placeholderTextColor={WEB.textSubtle}
                      value={adminPassword}
                      onChangeText={setAdminPassword}
                      secureTextEntry={!showPass}
                      autoCapitalize="none"
                    />
                    <TouchableOpacity onPress={() => setShowPass(!showPass)} style={styles.eyeBtn}>
                      <Ionicons name={showPass ? "eye-off-outline" : "eye-outline"} size={18} color={WEB.textSubtle} />
                    </TouchableOpacity>
                  </View>
                </View>

                <WebButton title="Send Verification Code" onPress={handleSendCode} loading={loading} />

                <TouchableOpacity style={styles.centerLink} onPress={() => navigation.goBack()}>
                  <Text style={webAuthStyles.linkMuted}>Already registered? Sign in to your workspace →</Text>
                </TouchableOpacity>
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
  entered: {
    backgroundColor: WEB.field, borderWidth: 1, borderColor: WEB.border, borderRadius: WEB.radiusSm,
    paddingHorizontal: 12, paddingVertical: 10, marginBottom: 18,
  },
  enteredRow: { flexDirection: "row", alignItems: "center", paddingVertical: 3 },
  enteredText: { flex: 1, marginLeft: 8, fontSize: 13.5, color: WEB.text },
  changeLink: { marginTop: 6 },
  codeInput: { textAlign: "center", fontSize: 22, fontWeight: "800", letterSpacing: 8 },
  passwordInput: { paddingRight: 44 },
  eyeBtn: { position: "absolute", right: 10, top: 0, bottom: 0, justifyContent: "center", paddingHorizontal: 4 },
  centerLink: { alignSelf: "center", marginTop: 16 },
});
