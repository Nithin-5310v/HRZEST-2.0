import React, { useCallback, useEffect, useState } from "react";
import {
  View,
  Text,
  ScrollView,
  StyleSheet,
  TouchableOpacity,
  Modal,
  TextInput,
  Alert,
  ActivityIndicator,
  RefreshControl,
} from "react-native";
import SafeAreaView from "../../components/SafeAreaView";
import { Ionicons } from "@expo/vector-icons";
import * as ImagePicker from "expo-image-picker";

import ProfileHeader from "../../components/profile/ProfileHeader";
import EmptyState from "../../components/ui/EmptyState";
import { fetchEmployeeDocuments, uploadDocument, deleteMyDocument } from "../../api/client";
import { useTheme } from "../../store/ThemeContext";

const DOC_TYPES = ["Aadhaar Card", "PAN Card", "Passport", "Degree Certificate", "Offer Letter", "Other"];

// Now backed by real Bearer routes (blueprints/documents.py's
// api_my_documents_list/upload/delete) -- image only (via expo-image-picker,
// already a dependency) rather than adding a new document-picker
// dependency for PDFs; a photo of the document covers the common case for
// compliance docs like Aadhaar/PAN, and PDF/doc upload still needs web.
export default function DocumentsScreen() {
  const { colors } = useTheme();
  const styles = React.useMemo(() => makeStyles(colors), [colors]);
  const [docs, setDocs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [uploadModalVisible, setUploadModalVisible] = useState(false);
  const [docType, setDocType] = useState(DOC_TYPES[0]);
  const [photoUri, setPhotoUri] = useState(null);
  const [uploading, setUploading] = useState(false);
  const [deletingId, setDeletingId] = useState(null);

  const load = useCallback(async () => {
    try {
      const res = await fetchEmployeeDocuments();
      setDocs(res?.data?.ok && Array.isArray(res.data.documents) ? res.data.documents : []);
    } catch (e) {
      setDocs([]);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const onRefresh = () => {
    setRefreshing(true);
    load();
  };

  const openUploadModal = () => {
    setDocType(DOC_TYPES[0]);
    setPhotoUri(null);
    setUploadModalVisible(true);
  };

  const handlePickPhoto = async () => {
    const perm = await ImagePicker.requestMediaLibraryPermissionsAsync();
    if (!perm.granted) {
      Alert.alert("Permission Required", "Photo library access is needed to attach a document photo.");
      return;
    }
    const result = await ImagePicker.launchImageLibraryAsync({
      mediaTypes: ImagePicker.MediaTypeOptions.Images,
      quality: 0.85,
    });
    if (!result.canceled && result.assets?.[0]?.uri) {
      setPhotoUri(result.assets[0].uri);
    }
  };

  const handleUpload = async () => {
    if (!photoUri) {
      Alert.alert("Photo Required", "Please attach a photo of the document.");
      return;
    }
    setUploading(true);
    const formData = new FormData();
    formData.append("doc_type", docType);
    formData.append("document", {
      uri: photoUri,
      name: `${docType.replace(/\s+/g, "_")}.jpg`,
      type: "image/jpeg",
    });

    let res;
    try {
      res = await uploadDocument(formData);
    } catch (e) {
      res = e?.response;
    }
    setUploading(false);
    if (!res?.data?.ok) {
      Alert.alert("Upload Failed", res?.data?.msg || "Could not upload this document.");
      return;
    }
    setUploadModalVisible(false);
    Alert.alert("Uploaded", `${docType} uploaded successfully.`);
    load();
  };

  const handleDelete = (doc) => {
    Alert.alert("Delete Document", `Remove "${doc.original_name}"?`, [
      { text: "Cancel", style: "cancel" },
      {
        text: "Delete",
        style: "destructive",
        onPress: async () => {
          setDeletingId(doc.id);
          let res;
          try {
            res = await deleteMyDocument(doc.id);
          } catch (e) {
            res = e?.response;
          }
          setDeletingId(null);
          if (!res?.data?.ok) {
            Alert.alert("Delete Failed", res?.data?.msg || "Could not delete this document.");
            return;
          }
          setDocs((prev) => prev.filter((d) => d.id !== doc.id));
        },
      },
    ]);
  };

  return (
    <SafeAreaView style={styles.container}>
      <ProfileHeader
        title="Documents & Statutory Files"
        showBack
        rightAction={
          <TouchableOpacity onPress={openUploadModal} hitSlop={{ top: 10, bottom: 10, left: 10, right: 10 }}>
            <Ionicons name="add-circle" size={26} color={colors.primary} />
          </TouchableOpacity>
        }
      />

      <ScrollView
        showsVerticalScrollIndicator={false}
        contentContainerStyle={styles.content}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} />}
      >
        {loading ? (
          <ActivityIndicator size="large" color={colors.primary} style={{ marginTop: 30 }} />
        ) : docs.length === 0 ? (
          <EmptyState
            icon="document-text-outline"
            title="No Documents Yet"
            description="Upload a photo of your ID, PAN, or other compliance documents using the + button above."
          />
        ) : (
          docs.map((doc) => (
            <View key={doc.id} style={styles.docCard}>
              <View style={styles.docIcon}>
                <Ionicons name="document-text" size={22} color={colors.primary} />
              </View>
              <View style={{ flex: 1, marginLeft: 12 }}>
                <Text style={styles.docType}>{doc.doc_type}</Text>
                <Text style={styles.docName} numberOfLines={1}>{doc.original_name}</Text>
                <Text style={styles.docMeta}>
                  Uploaded {doc.uploaded_at ? doc.uploaded_at.split(" ")[0] : "--"} by {doc.uploaded_by}
                  {doc.expiry_date ? ` • Expires ${doc.expiry_date}` : ""}
                </Text>
              </View>
              <TouchableOpacity onPress={() => handleDelete(doc)} disabled={deletingId === doc.id} hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}>
                {deletingId === doc.id ? (
                  <ActivityIndicator size="small" color={colors.danger} />
                ) : (
                  <Ionicons name="trash-outline" size={20} color={colors.danger} />
                )}
              </TouchableOpacity>
            </View>
          ))
        )}
      </ScrollView>

      <Modal visible={uploadModalVisible} transparent animationType="slide" onRequestClose={() => setUploadModalVisible(false)}>
        <View style={{ flex: 1, backgroundColor: "rgba(15, 23, 42, 0.8)", justifyContent: "flex-end" }}>
          <View style={{ backgroundColor: colors.card, borderTopLeftRadius: 28, borderTopRightRadius: 28, padding: 20 }}>
            <View style={{ flexDirection: "row", justifyContent: "space-between", alignItems: "center", marginBottom: 14, paddingBottom: 10, borderBottomWidth: 1, borderBottomColor: colors.border }}>
              <Text style={{ fontSize: 18, fontWeight: "800", color: colors.text }}>Upload Document</Text>
              <TouchableOpacity onPress={() => setUploadModalVisible(false)}>
                <Ionicons name="close-circle" size={26} color={colors.textSecondary} />
              </TouchableOpacity>
            </View>

            <Text style={{ fontSize: 11, fontWeight: "700", color: colors.textSecondary, marginBottom: 6 }}>DOCUMENT TYPE</Text>
            <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 8, marginBottom: 16 }}>
              {DOC_TYPES.map((t) => (
                <TouchableOpacity
                  key={t}
                  onPress={() => setDocType(t)}
                  style={{
                    paddingHorizontal: 12, paddingVertical: 8, borderRadius: 10,
                    backgroundColor: docType === t ? colors.primary : colors.background,
                  }}
                >
                  <Text style={{ fontSize: 12, fontWeight: "700", color: docType === t ? "#FFFFFF" : colors.text }}>{t}</Text>
                </TouchableOpacity>
              ))}
            </View>

            <TouchableOpacity
              onPress={handlePickPhoto}
              style={{ backgroundColor: colors.background, borderWidth: 2, borderColor: "#B5DFE8", borderStyle: "dashed", borderRadius: 14, padding: 20, alignItems: "center", marginBottom: 16 }}
            >
              <Ionicons name={photoUri ? "checkmark-circle" : "image-outline"} size={32} color={photoUri ? "#16A34A" : "#0A5878"} />
              <Text style={{ fontSize: 12, fontWeight: "700", color: colors.text, marginTop: 8 }}>
                {photoUri ? "Photo Attached — Tap to Change" : "Tap to Attach a Photo"}
              </Text>
            </TouchableOpacity>

            <TouchableOpacity
              style={{ backgroundColor: colors.primary, borderRadius: 14, paddingVertical: 14, alignItems: "center" }}
              onPress={handleUpload}
              disabled={uploading}
            >
              {uploading ? <ActivityIndicator color="#FFFFFF" /> : <Text style={{ color: "#FFFFFF", fontWeight: "800", fontSize: 14 }}>Upload</Text>}
            </TouchableOpacity>
          </View>
        </View>
      </Modal>
    </SafeAreaView>
  );
}

const makeStyles = (colors) => StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.background },
  content: { paddingHorizontal: 18, paddingBottom: 120, paddingTop: 18 },
  docCard: {
    flexDirection: "row", alignItems: "center", backgroundColor: colors.card,
    borderRadius: 16, padding: 14, marginBottom: 10, borderWidth: 1, borderColor: colors.border,
  },
  docIcon: { width: 40, height: 40, borderRadius: 12, backgroundColor: colors.primaryLight, justifyContent: "center", alignItems: "center" },
  docType: { fontSize: 13, fontWeight: "700", color: colors.text },
  docName: { fontSize: 12, color: colors.textSecondary, marginTop: 2 },
  docMeta: { fontSize: 11, color: colors.textLight, marginTop: 4 },
});
