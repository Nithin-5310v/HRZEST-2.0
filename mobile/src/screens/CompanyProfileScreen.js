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
import * as ImagePicker from "expo-image-picker";

import { submitCompanyProfile } from "../api/client";
import { useTheme } from "../store/ThemeContext";
import SignupSteps from "../components/signup/SignupSteps";

// Step 3 of company signup, after the email code -- the website's company
// profile (/create_org/profile): name, GSTIN, company URL, optional logo
// and the plan. Submitting it creates the company; documents come next.
// GST_RE mirrors blueprints/org.py's _GST_RE so a typo is caught before the
// network round trip -- the server re-validates regardless.
const GST_RE = /^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$/;

const slugify = (value) => value.toLowerCase().trim().replace(/[^a-z0-9-]/g, "");

function PlanOption({ selected, onPress, icon, title, sub, styles, colors }) {
  return (
    <TouchableOpacity style={[styles.planOption, selected && styles.planOptionSelected]} onPress={onPress} activeOpacity={0.85}>
      <View style={styles.planTitleRow}>
        <Ionicons name={icon} size={15} color={selected ? "#173B8C" : colors.text} />
        <Text style={[styles.planTitle, selected && styles.planTitleSelected]}>{title}</Text>
      </View>
      <Text style={styles.planSub}>{sub}</Text>
    </TouchableOpacity>
  );
}

export default function CompanyProfileScreen({ navigation, route }) {
  const { colors } = useTheme();
  const styles = React.useMemo(() => makeStyles(colors), [colors]);
  const { applicationId, accessToken } = route.params;

  const [companyName, setCompanyName] = useState("");
  const [gstNumber, setGstNumber] = useState("");
  const [subdomain, setSubdomain] = useState("");
  const [subdomainEdited, setSubdomainEdited] = useState(false);
  const [plan, setPlan] = useState("pay_now");
  const [logo, setLogo] = useState(null);
  const [loading, setLoading] = useState(false);

  const handleCompanyNameChange = (value) => {
    setCompanyName(value);
    if (!subdomainEdited) setSubdomain(slugify(value));
  };

  const pickLogo = async () => {
    const perm = await ImagePicker.requestMediaLibraryPermissionsAsync();
    if (!perm.granted) {
      Alert.alert("Permission Required", "Photo library access is needed to add your logo.");
      return;
    }
    const result = await ImagePicker.launchImageLibraryAsync({
      mediaTypes: ImagePicker.MediaTypeOptions.Images,
      quality: 0.85,
    });
    if (!result.canceled && result.assets?.[0]) {
      const asset = result.assets[0];
      setLogo({ uri: asset.uri, name: asset.fileName || "logo.jpg", type: asset.mimeType || "image/jpeg" });
    }
  };

  const handleSubmit = async () => {
    const name = companyName.trim();
    const slug = slugify(subdomain);
    const gst = gstNumber.trim().toUpperCase().replace(/\s+/g, "");

    if (!name || !slug) {
      Alert.alert("Missing Information", "Company name and company URL are required.");
      return;
    }
    if (!GST_RE.test(gst)) {
      Alert.alert("Invalid GSTIN", "Enter a valid 15-character GSTIN (e.g. 27AAAAA0000A1Z5).");
      return;
    }

    setLoading(true);
    const formData = new FormData();
    formData.append("application_id", String(applicationId));
    formData.append("access_token", accessToken);
    formData.append("company_name", name);
    formData.append("gst_number", gst);
    formData.append("subdomain", slug);
    formData.append("signup_plan", plan);
    if (logo) formData.append("logo", logo);

    let res;
    try {
      res = await submitCompanyProfile(formData);
    } catch (err) {
      res = err?.response;
    }
    setLoading(false);
    if (res?.data?.ok) {
      navigation.navigate("CompanyDocumentUpload", { applicationId, accessToken, companyName: name });
    } else {
      Alert.alert("Could Not Create Company", res?.data?.msg || "Please check your details and try again.");
    }
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
            <Text style={styles.title}>Set Up Your Company Profile</Text>
            <Text style={styles.subtitle}>
              Your email is verified. Tell us about your company to create its portal — business documents come next.
            </Text>
          </View>

          <SignupSteps current={3} />

          <View style={styles.card}>
            <View style={styles.planRow}>
              <PlanOption selected={plan === "pay_now"} onPress={() => setPlan("pay_now")} icon="card-outline"
                          title="Pay Now" sub="Activate immediately, billed this month." styles={styles} colors={colors} />
              <PlanOption selected={plan === "trial"} onPress={() => setPlan("trial")} icon="gift-outline"
                          title="2-Day Free Trial" sub="Full access, card required, cancel anytime." styles={styles} colors={colors} />
            </View>
            {plan === "trial" && (
              <View style={styles.disclosure}>
                <Ionicons name="information-circle-outline" size={15} color="#065F46" style={styles.noteIcon} />
                <Text style={styles.disclosureText}>
                  You'll authorize a payment method after approval. Your 2-day trial starts the first time you log in,
                  and billing starts automatically when it ends unless you cancel before then.
                </Text>
              </View>
            )}

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

            <Text style={styles.label}>GST / BUSINESS REGISTRATION NUMBER</Text>
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
            <Text style={styles.hint}>Verifies your company is a real, distinct business.</Text>

            <Text style={styles.label}>COMPANY URL</Text>
            <View style={styles.inputRow}>
              <Text style={styles.urlPrefix}>hrzest.com/</Text>
              <TextInput
                style={styles.input}
                placeholder="acme"
                placeholderTextColor={colors.textLight}
                value={subdomain}
                onChangeText={(v) => { setSubdomainEdited(true); setSubdomain(slugify(v)); }}
                autoCapitalize="none"
                autoCorrect={false}
              />
            </View>
            <Text style={styles.hint}>
              Your team will sign in at <Text style={styles.hintStrong}>hrzest.com/{subdomain || "acme"}/login</Text>
            </Text>

            <Text style={styles.label}>COMPANY LOGO (OPTIONAL)</Text>
            <TouchableOpacity style={[styles.inputRow, logo && styles.inputRowFilled]} onPress={pickLogo} activeOpacity={0.8}>
              <Ionicons name={logo ? "checkmark-circle" : "image-outline"} size={18} color={logo ? "#16A34A" : "#64748B"} style={styles.inputIcon} />
              <Text style={[styles.input, styles.logoText]} numberOfLines={1}>{logo ? logo.name : "Choose an image"}</Text>
            </TouchableOpacity>
            <Text style={styles.hint}>Shown in your team's dashboards. You can skip this and add it later.</Text>

            <TouchableOpacity style={styles.submitBtn} onPress={handleSubmit} disabled={loading} activeOpacity={0.85}>
              {loading ? <ActivityIndicator color="#FFFFFF" /> : <Text style={styles.submitBtnText}>Create My Company</Text>}
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
  planRow: { flexDirection: "row", marginBottom: 14 },
  planOption: {
    flex: 1, borderWidth: 1.5, borderColor: colors.border, borderRadius: 12, padding: 10,
    backgroundColor: colors.background, marginHorizontal: 3,
  },
  planOptionSelected: { borderColor: "#173B8C", backgroundColor: "rgba(23,59,140,0.06)" },
  planTitleRow: { flexDirection: "row", alignItems: "center" },
  planTitle: { fontSize: 12.5, fontWeight: "800", color: colors.text, marginLeft: 6 },
  planTitleSelected: { color: "#173B8C" },
  planSub: { fontSize: 10.5, color: "#94A3B8", marginTop: 4, lineHeight: 14 },
  disclosure: {
    flexDirection: "row", alignItems: "flex-start", backgroundColor: "rgba(5,150,105,0.07)",
    borderWidth: 1, borderColor: "rgba(5,150,105,0.22)", borderRadius: 12, padding: 10, marginBottom: 16,
  },
  noteIcon: { marginRight: 8, marginTop: 1 },
  disclosureText: { flex: 1, fontSize: 11, lineHeight: 16, color: "#065F46" },
  label: { fontSize: 10, fontWeight: "800", color: "#64748B", letterSpacing: 0.8, marginBottom: 6 },
  inputRow: {
    flexDirection: "row", alignItems: "center", height: 48, borderRadius: 14,
    backgroundColor: colors.background, borderWidth: 1, borderColor: colors.border,
    paddingHorizontal: 14, marginBottom: 16,
  },
  inputRowFilled: { borderColor: "#16A34A" },
  urlPrefix: { fontSize: 13, fontWeight: "700", color: "#64748B", marginRight: 2 },
  hint: { fontSize: 11, color: "#94A3B8", marginTop: -10, marginBottom: 16 },
  hintStrong: { fontWeight: "700", color: "#173B8C" },
  inputIcon: { marginRight: 10 },
  input: { flex: 1, height: "100%", fontSize: 13, color: colors.text, fontWeight: "600" },
  logoText: { height: undefined, color: colors.textLight },
  submitBtn: {
    height: 48, borderRadius: 14, backgroundColor: "#173B8C",
    justifyContent: "center", alignItems: "center", marginTop: 8,
  },
  submitBtnText: { fontSize: 13, fontWeight: "800", color: "#FFFFFF" },
});
