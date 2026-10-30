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
import SafeAreaView from "../../components/SafeAreaView";
import { Ionicons } from "@expo/vector-icons";
import * as ImagePicker from "expo-image-picker";
import * as DocumentPicker from "expo-document-picker";

import AdminHeader from "../../components/admin/AdminHeader";
import { uploadRegistrationDocuments } from "../../api/client";
import { useTheme } from "../../store/ThemeContext";

// The registration checklist's business documents (RegistrationChecklist
// on the dashboard): the same four files as the website's
// /settings/company_verification, reviewed after the fact -- the company
// is already live while they're checked.
const DOC_SLOTS = [
  { key: "registration_cert", label: "Company Registration Certificate", hint: "PDF, JPG or PNG", icon: "document-text-outline", pickType: "document" },
  { key: "address_proof", label: "Address Proof", hint: "Utility bill, lease agreement, or similar — PDF, JPG or PNG", icon: "location-outline", pickType: "document" },
  { key: "visiting_card", label: "Company Visiting Card", hint: "Photo of a business card", icon: "card-outline", pickType: "image" },
  { key: "name_board_photo", label: "Office Name-Board Photo", hint: "Photo of your office entrance/name-board", icon: "image-outline", pickType: "image" },
];

export default function RegistrationDocumentsScreen({ navigation }) {
  const { colors } = useTheme();
  const styles = React.useMemo(() => makeStyles(colors), [colors]);

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
    DOC_SLOTS.forEach((slot) => {
      formData.append(slot.key, files[slot.key]);
    });

    let res;
    try {
      res = await uploadRegistrationDocuments(formData);
    } catch (err) {
      res = err?.response;
    }
    setUploading(false);
    if (res?.data?.ok) {
      setFiles({});
      Alert.alert("Documents Submitted", res.data.msg || "We'll review them shortly.");
      navigation.navigate("Dashboard");
    } else {
      Alert.alert("Upload Failed", res?.data?.msg || "Could not submit your documents.");
    }
  };

  return (
    <SafeAreaView style={styles.container}>
      <AdminHeader title="Business Documents" subtitle="COMPLETE REGISTRATION" navigation={navigation} />
      <ScrollView contentContainerStyle={styles.scrollContent} showsVerticalScrollIndicator={false}>
        <Text style={styles.intro}>
          Your company is already live — these confirm your business details for our records.
        </Text>
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
                  <Ionicons name={picked ? "checkmark-circle" : slot.icon} size={22} color={picked ? colors.success : colors.primary} />
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
  docSlot: {
    flexDirection: "row", alignItems: "center", padding: 14, borderRadius: 14,
    backgroundColor: colors.background, borderWidth: 1, borderColor: colors.border, marginBottom: 12,
  },
  docSlotFilled: { borderColor: colors.success },
  docIcon: { width: 40, height: 40, borderRadius: 12, backgroundColor: colors.blueBg, justifyContent: "center", alignItems: "center", marginRight: 12 },
  docLabel: { fontSize: 13, fontWeight: "700", color: colors.text },
  docHint: { fontSize: 11.5, color: colors.textLight, marginTop: 2 },
  submitBtn: {
    height: 48, borderRadius: 14, backgroundColor: colors.primary,
    justifyContent: "center", alignItems: "center", marginTop: 6,
  },
  submitBtnText: { fontSize: 13, fontWeight: "800", color: "#FFFFFF" },
});
