import React, { useState } from "react";
import {
  View,
  Text,
  TouchableOpacity,
  StyleSheet,
  ActivityIndicator,
  ScrollView,
  Alert,
} from "react-native";
import { LinearGradient } from "expo-linear-gradient";
import { Ionicons } from "@expo/vector-icons";
import * as ImagePicker from "expo-image-picker";
import * as DocumentPicker from "expo-document-picker";

import { uploadCompanyDocuments } from "../api/client";
import { useTheme } from "../store/ThemeContext";

const DOC_SLOTS = [
  { key: "registration_cert", label: "Company Registration Certificate", hint: "PDF, JPG or PNG", icon: "document-text-outline", pickType: "document" },
  { key: "address_proof", label: "Address Proof", hint: "Utility bill, lease agreement, or similar — PDF, JPG or PNG", icon: "location-outline", pickType: "document" },
  { key: "visiting_card", label: "Company Visiting Card", hint: "Photo of a business card", icon: "card-outline", pickType: "image" },
  { key: "name_board_photo", label: "Office Name-Board Photo", hint: "Photo of your office entrance/name-board", icon: "image-outline", pickType: "image" },
];

export default function CompanyDocumentUploadScreen({ navigation, route }) {
  const { colors } = useTheme();
  const styles = React.useMemo(() => makeStyles(colors), [colors]);
  const { applicationId, accessToken, companyName } = route.params;

  const [files, setFiles] = useState({});
  const [uploading, setUploading] = useState(false);

  const pickImage = async (key) => {
    const perm = await ImagePicker.requestMediaLibraryPermissionsAsync();
    if (!perm.granted) {
      Alert.alert("Permission Required", "Photo library access is needed to attach this document.");
      return;
    }
    const result = await ImagePicker.launchImageLibraryAsync({
      mediaTypes: ImagePicker.MediaTypeOptions.Images,
      quality: 0.85,
    });
    if (!result.canceled && result.assets?.[0]) {
      const asset = result.assets[0];
      setFiles((prev) => ({ ...prev, [key]: { uri: asset.uri, name: asset.fileName || `${key}.jpg`, type: "image/jpeg" } }));
    }
  };

  const pickDocument = async (key) => {
    const result = await DocumentPicker.getDocumentAsync({
      type: ["application/pdf", "image/jpeg", "image/png"],
      copyToCacheDirectory: true,
    });
    if (!result.canceled && result.assets?.[0]) {
      const asset = result.assets[0];
      setFiles((prev) => ({
        ...prev,
        [key]: { uri: asset.uri, name: asset.name || `${key}.pdf`, type: asset.mimeType || "application/pdf" },
      }));
    }
  };

  const handleSubmit = async () => {
    const missing = DOC_SLOTS.filter((slot) => !files[slot.key]);
    if (missing.length > 0) {
      Alert.alert("Missing Documents", `Please attach: ${missing.map((s) => s.label).join(", ")}`);
      return;
    }
    setUploading(true);
    const formData = new FormData();
    formData.append("application_id", String(applicationId));
    formData.append("access_token", accessToken);
    DOC_SLOTS.forEach((slot) => {
      formData.append(slot.key, files[slot.key]);
    });

    let res;
    try {
      res = await uploadCompanyDocuments(formData);
    } catch (err) {
      res = err?.response;
    }
    setUploading(false);
    if (res?.data?.ok) {
      navigation.navigate("CompanySignupPending", { applicationId, accessToken, companyName });
    } else {
      Alert.alert("Upload Failed", res?.data?.msg || "Could not submit your documents.");
    }
  };

  return (
    <LinearGradient colors={["#0F172A", "#1E3A8A", "#173B8C"]} style={styles.bg}>
      <ScrollView contentContainerStyle={styles.scroll} showsVerticalScrollIndicator={false}>
        <TouchableOpacity style={styles.backBtn} onPress={() => navigation.goBack()}>
          <Ionicons name="arrow-back" size={22} color="#FFFFFF" />
        </TouchableOpacity>

        <View style={styles.header}>
          <View style={styles.logoCircle}>
            <Ionicons name="cloud-upload-outline" size={30} color="#173B8C" />
          </View>
          <Text style={styles.title}>Upload Business Documents</Text>
          <Text style={styles.subtitle}>Almost there — we need these to verify {companyName} before your portal goes live.</Text>
        </View>

        <View style={styles.card}>
          {DOC_SLOTS.map((slot) => {
            const picked = files[slot.key];
            return (
              <TouchableOpacity
                key={slot.key}
                style={[styles.docSlot, picked && styles.docSlotFilled]}
                onPress={() => (slot.pickType === "image" ? pickImage(slot.key) : pickDocument(slot.key))}
                activeOpacity={0.8}
              >
                <View style={styles.docIcon}>
                  <Ionicons name={picked ? "checkmark-circle" : slot.icon} size={22} color={picked ? "#16A34A" : "#173B8C"} />
                </View>
                <View style={{ flex: 1 }}>
                  <Text style={styles.docLabel}>{slot.label}</Text>
                  <Text style={styles.docHint}>{picked ? (picked.name || "Attached") : slot.hint}</Text>
                </View>
                <Ionicons name="chevron-forward" size={18} color={colors.textLight} />
              </TouchableOpacity>
            );
          })}

          <TouchableOpacity style={styles.submitBtn} onPress={handleSubmit} disabled={uploading} activeOpacity={0.85}>
            {uploading ? <ActivityIndicator color="#FFFFFF" /> : <Text style={styles.submitBtnText}>Submit for Review</Text>}
          </TouchableOpacity>
        </View>
      </ScrollView>
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
    backgroundColor: colors.card, borderRadius: 24, padding: 18,
    shadowColor: "#0F172A", shadowOpacity: 0.12, shadowRadius: 20, shadowOffset: { width: 0, height: 10 }, elevation: 8,
  },
  docSlot: {
    flexDirection: "row", alignItems: "center", padding: 14, borderRadius: 14,
    backgroundColor: colors.background, borderWidth: 1, borderColor: colors.border, marginBottom: 12,
  },
  docSlotFilled: { borderColor: "#16A34A" },
  docIcon: { width: 40, height: 40, borderRadius: 12, backgroundColor: colors.blueBg, justifyContent: "center", alignItems: "center", marginRight: 12 },
  docLabel: { fontSize: 13, fontWeight: "700", color: colors.text },
  docHint: { fontSize: 11.5, color: colors.textLight, marginTop: 2 },
  submitBtn: {
    height: 48, borderRadius: 14, backgroundColor: "#173B8C",
    justifyContent: "center", alignItems: "center", marginTop: 6,
  },
  submitBtnText: { fontSize: 13, fontWeight: "800", color: "#FFFFFF" },
});
