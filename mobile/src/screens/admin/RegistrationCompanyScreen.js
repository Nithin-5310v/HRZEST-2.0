import React, { useEffect, useState } from "react";
import {
  View,
  Text,
  TextInput,
  TouchableOpacity,
  StyleSheet,
  ActivityIndicator,
  ScrollView,
  Alert,
  Image,
} from "react-native";
import SafeAreaView from "../../components/SafeAreaView";
import { Ionicons } from "@expo/vector-icons";
import * as ImagePicker from "expo-image-picker";

import AdminHeader from "../../components/admin/AdminHeader";
import { getRegistrationChecklist, saveCompanyDetails } from "../../api/client";
import { useTheme } from "../../store/ThemeContext";

// The registration checklist's company details (RegistrationChecklist on
// the dashboard): the GSTIN -- required, and must not belong to another
// company -- and an optional logo, as on the website's
// /settings/company_details. The server checks the GSTIN again.
const GST_RE = /^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$/;

export default function RegistrationCompanyScreen({ navigation }) {
  const { colors } = useTheme();
  const styles = React.useMemo(() => makeStyles(colors), [colors]);

  const [gstNumber, setGstNumber] = useState("");
  const [portalUrl, setPortalUrl] = useState("");
  const [logo, setLogo] = useState(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    getRegistrationChecklist()
      .then((res) => {
        if (res?.data?.ok) {
          setGstNumber((typed) => typed || res.data.gst_number || "");
          setPortalUrl(res.data.portal_url || "");
        }
      })
      .catch(() => {});
  }, []);

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

  const handleSave = async () => {
    const gst = gstNumber.replace(/\s+/g, "").toUpperCase();
    if (!GST_RE.test(gst)) {
      Alert.alert("Invalid GSTIN", "Enter a valid 15-character GSTIN, e.g. 27AAAAA0000A1Z5.");
      return;
    }
    setSaving(true);
    const formData = new FormData();
    formData.append("gst_number", gst);
    if (logo) formData.append("logo", logo);

    let res;
    try {
      res = await saveCompanyDetails(formData);
    } catch (err) {
      res = err?.response;
    }
    setSaving(false);
    if (res?.data?.ok) {
      Alert.alert("Saved", res.data.msg || "Company details saved.");
      navigation.navigate("Dashboard");
    } else {
      Alert.alert("Could Not Save", res?.data?.msg || "Please check the GSTIN and try again.");
    }
  };

  return (
    <SafeAreaView style={styles.container}>
      <AdminHeader title="Company Details" subtitle="COMPLETE REGISTRATION" navigation={navigation} />
      <ScrollView contentContainerStyle={styles.scrollContent} keyboardShouldPersistTaps="handled"
                  showsVerticalScrollIndicator={false}>
        <Text style={styles.intro}>
          Your GSTIN confirms your company is a real, distinct business. Your logo appears in your team's dashboards.
        </Text>
        <View style={styles.card}>
          {portalUrl ? (
            <View style={styles.fact}>
              <Ionicons name="globe-outline" size={15} color={colors.primary} />
              <Text style={styles.factText} numberOfLines={1}>{portalUrl}</Text>
            </View>
          ) : null}

          <Text style={styles.label}>GST / BUSINESS REGISTRATION NUMBER</Text>
          <TextInput
            style={styles.input}
            placeholder="27AAAAA0000A1Z5"
            placeholderTextColor={colors.textLight}
            value={gstNumber}
            onChangeText={(v) => setGstNumber(v.toUpperCase().replace(/\s+/g, ""))}
            autoCapitalize="characters"
            autoCorrect={false}
            maxLength={15}
          />

          <Text style={styles.label}>COMPANY LOGO (OPTIONAL)</Text>
          <TouchableOpacity style={styles.logoPick} onPress={pickLogo} activeOpacity={0.8}>
            {logo ? (
              <Image source={{ uri: logo.uri }} style={styles.logoPreview} accessibilityLabel="Chosen company logo" />
            ) : (
              <View style={styles.logoIcon}>
                <Ionicons name="image-outline" size={22} color={colors.primary} />
              </View>
            )}
            <Text style={styles.logoText}>{logo ? "Change logo" : "Choose a logo"}</Text>
            <Ionicons name="chevron-forward" size={18} color={colors.textLight} />
          </TouchableOpacity>

          <TouchableOpacity style={styles.submitBtn} onPress={handleSave} disabled={saving} activeOpacity={0.85}>
            {saving ? <ActivityIndicator color="#FFFFFF" /> : <Text style={styles.submitBtnText}>Save Company Details</Text>}
          </TouchableOpacity>
        </View>
      </ScrollView>
    </SafeAreaView>
  );
}

const makeStyles = (colors) => StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.background },
  scrollContent: { padding: 16, paddingBottom: 110 },
  intro: { fontSize: 13, color: colors.textSecondary, lineHeight: 19, marginBottom: 14 },
  card: {
    backgroundColor: colors.surface, borderRadius: 16, padding: 16,
    shadowColor: "#132026", shadowOpacity: 0.06, shadowRadius: 12, shadowOffset: { width: 0, height: 4 }, elevation: 2,
  },
  fact: {
    flexDirection: "row", alignItems: "center", padding: 12, borderRadius: 12,
    backgroundColor: colors.blueBg, marginBottom: 16,
  },
  factText: { flex: 1, marginLeft: 8, fontSize: 12.5, fontWeight: "600", color: colors.text },
  label: { fontSize: 10.5, fontWeight: "800", color: colors.textSecondary, letterSpacing: 0.6, marginBottom: 6 },
  input: {
    height: 48, borderRadius: 12, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.background,
    paddingHorizontal: 14, fontSize: 14, fontWeight: "600", color: colors.text, letterSpacing: 1, marginBottom: 16,
  },
  logoPick: {
    flexDirection: "row", alignItems: "center", padding: 12, borderRadius: 12,
    borderWidth: 1, borderColor: colors.border, backgroundColor: colors.background, marginBottom: 18,
  },
  logoIcon: { width: 40, height: 40, borderRadius: 10, backgroundColor: colors.blueBg, justifyContent: "center", alignItems: "center" },
  logoPreview: { width: 40, height: 40, borderRadius: 10, backgroundColor: "#FFFFFF" },
  logoText: { flex: 1, marginLeft: 12, fontSize: 13, fontWeight: "700", color: colors.text },
  submitBtn: {
    height: 48, borderRadius: 14, backgroundColor: colors.primary,
    justifyContent: "center", alignItems: "center",
  },
  submitBtnText: { fontSize: 13, fontWeight: "800", color: "#FFFFFF" },
});
