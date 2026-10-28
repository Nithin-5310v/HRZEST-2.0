import React, { useCallback, useEffect, useState } from "react";
import {
  ScrollView,
  StyleSheet,
  View,
  Text,
  TouchableOpacity,
  RefreshControl,
  ActivityIndicator,
  Modal,
  TextInput,
  Alert,
} from "react-native";
import SafeAreaView from "../../components/SafeAreaView";
import { DrawerActions } from "@react-navigation/native";
import { LinearGradient } from "expo-linear-gradient";
import { Ionicons } from "@expo/vector-icons";
import * as ImagePicker from "expo-image-picker";
import THEME from "../../constants/theme";
import { useTheme } from "../../store/ThemeContext";
import AdminHeader from "../../components/admin/AdminHeader";
import { fetchDocuments, fetchEmployees, uploadDocumentForEmployee, deleteDocument } from "../../api/client";

const DOC_TYPES = ["Aadhaar Card", "PAN Card", "Passport", "Offer Letter", "Degree Certificate", "Other"];

// Admin-facing twin of blueprints/documents.py's session-only /documents
// page -- there was no mobile UI for this at all before (blueprints/
// documents.py's api_documents_list/upload/delete are the new Bearer routes).
export default function DocumentsScreen({ navigation }) {
  const { colors } = useTheme();
  const styles = React.useMemo(() => makeStyles(colors), [colors]);
  const [docs, setDocs] = useState([]);
  const [employees, setEmployees] = useState([]);
  const [selectedEmpId, setSelectedEmpId] = useState("");
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [deletingId, setDeletingId] = useState(null);

  const [uploadModalVisible, setUploadModalVisible] = useState(false);
  const [uploadEmpId, setUploadEmpId] = useState("");
  const [docType, setDocType] = useState(DOC_TYPES[0]);
  const [photoUri, setPhotoUri] = useState(null);
  const [uploading, setUploading] = useState(false);

  const load = useCallback(async (empId) => {
    try {
      const [docsRes, empRes] = await Promise.all([
        fetchDocuments(empId),
        employees.length ? Promise.resolve(null) : fetchEmployees().catch(() => null),
      ]);
      setDocs(docsRes?.data?.ok && Array.isArray(docsRes.data.documents) ? docsRes.data.documents : []);
      if (empRes) {
        const list = empRes?.data?.employees || (Array.isArray(empRes?.data) ? empRes.data : []);
        setEmployees(list);
      }
    } catch (e) {
      setDocs([]);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [employees.length]);

  useEffect(() => {
    load(selectedEmpId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedEmpId]);

  const onRefresh = () => {
    setRefreshing(true);
    load(selectedEmpId);
  };

  const openUploadModal = () => {
    setUploadEmpId(selectedEmpId || (employees[0]?.employee_id || employees[0]?.id || ""));
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
    const result = await ImagePicker.launchImageLibraryAsync({ mediaTypes: ImagePicker.MediaTypeOptions.Images, quality: 0.85 });
    if (!result.canceled && result.assets?.[0]?.uri) setPhotoUri(result.assets[0].uri);
  };

  const handleUpload = async () => {
    if (!uploadEmpId) {
      Alert.alert("Employee Required", "Please choose which employee this document belongs to.");
      return;
    }
    if (!photoUri) {
      Alert.alert("Photo Required", "Please attach a photo of the document.");
      return;
    }
    setUploading(true);
    const formData = new FormData();
    formData.append("employee_id", uploadEmpId);
    formData.append("doc_type", docType);
    formData.append("document", { uri: photoUri, name: `${docType.replace(/\s+/g, "_")}.jpg`, type: "image/jpeg" });

    let res;
    try {
      res = await uploadDocumentForEmployee(formData);
    } catch (e) {
      res = e?.response;
    }
    setUploading(false);
    if (!res?.data?.ok) {
      Alert.alert("Upload Failed", res?.data?.msg || "Could not upload this document.");
      return;
    }
    setUploadModalVisible(false);
    Alert.alert("Uploaded", `${docType} uploaded for ${uploadEmpId}.`);
    load(selectedEmpId);
  };

  const handleDelete = (doc) => {
    Alert.alert("Delete Document", `Remove "${doc.original_name}" (${doc.employee_name})?`, [
      { text: "Cancel", style: "cancel" },
      {
        text: "Delete",
        style: "destructive",
        onPress: async () => {
          setDeletingId(doc.id);
          let res;
          try {
            res = await deleteDocument(doc.id);
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
    <LinearGradient colors={colors.screenGradient} style={styles.container}>
      <SafeAreaView style={{ flex: 1 }}>
        <AdminHeader title="Employee Documents" onMenu={() => navigation.dispatch(DrawerActions.openDrawer())} />

        <ScrollView
          showsVerticalScrollIndicator={false}
          contentContainerStyle={styles.content}
          refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} colors={[THEME.colors.primary]} />}
        >
          <View style={styles.sectionHeader}>
            <Text style={styles.sectionTitle}>{selectedEmpId ? `Documents for ${selectedEmpId}` : `All Documents (${docs.length})`}</Text>
            <TouchableOpacity style={styles.addBtn} onPress={openUploadModal}>
              <Ionicons name="add" size={18} color="#FFFFFF" />
              <Text style={styles.addBtnText}>Upload</Text>
            </TouchableOpacity>
          </View>

          <ScrollView horizontal showsHorizontalScrollIndicator={false} style={{ marginBottom: 14 }}>
            <TouchableOpacity
              style={[styles.filterChip, !selectedEmpId && styles.filterChipActive]}
              onPress={() => setSelectedEmpId("")}
            >
              <Text style={[styles.filterChipText, !selectedEmpId && styles.filterChipTextActive]}>All</Text>
            </TouchableOpacity>
            {employees.map((e) => {
              const eid = e.employee_id || e.id;
              return (
                <TouchableOpacity
                  key={eid}
                  style={[styles.filterChip, selectedEmpId === eid && styles.filterChipActive]}
                  onPress={() => setSelectedEmpId(eid)}
                >
                  <Text style={[styles.filterChipText, selectedEmpId === eid && styles.filterChipTextActive]}>{e.name || eid}</Text>
                </TouchableOpacity>
              );
            })}
          </ScrollView>

          {loading ? (
            <ActivityIndicator size="large" color="#173B8C" style={{ marginTop: 24 }} />
          ) : docs.length === 0 ? (
            <View style={styles.emptyCard}>
              <Ionicons name="document-text-outline" size={44} color={colors.textLight} />
              <Text style={styles.emptyTitle}>No Documents</Text>
              <Text style={styles.emptySub}>Upload compliance documents for employees using the button above.</Text>
            </View>
          ) : (
            docs.map((doc) => (
              <View key={doc.id} style={styles.docCard}>
                <View style={styles.docIcon}>
                  <Ionicons name="document-text" size={22} color="#173B8C" />
                </View>
                <View style={{ flex: 1, marginLeft: 12 }}>
                  <Text style={styles.docType}>{doc.doc_type} — {doc.employee_name}</Text>
                  <Text style={styles.docName} numberOfLines={1}>{doc.original_name}</Text>
                  <Text style={styles.docMeta}>
                    Uploaded {doc.uploaded_at ? doc.uploaded_at.split(" ")[0] : "--"} by {doc.uploaded_by}
                    {doc.expiry_date ? ` • Expires ${doc.expiry_date}` : ""}
                  </Text>
                </View>
                <TouchableOpacity onPress={() => handleDelete(doc)} disabled={deletingId === doc.id} hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}>
                  {deletingId === doc.id ? <ActivityIndicator size="small" color={colors.danger} /> : <Ionicons name="trash-outline" size={20} color={colors.danger} />}
                </TouchableOpacity>
              </View>
            ))
          )}

          <View style={{ height: 100 }} />
        </ScrollView>

        <Modal visible={uploadModalVisible} transparent animationType="slide" onRequestClose={() => setUploadModalVisible(false)}>
          <View style={{ flex: 1, backgroundColor: "rgba(15, 23, 42, 0.8)", justifyContent: "flex-end" }}>
            <View style={{ backgroundColor: colors.card, borderTopLeftRadius: 28, borderTopRightRadius: 28, padding: 20, maxHeight: "85%" }}>
              <View style={{ flexDirection: "row", justifyContent: "space-between", alignItems: "center", marginBottom: 14, paddingBottom: 10, borderBottomWidth: 1, borderBottomColor: "#F1F5F9" }}>
                <Text style={{ fontSize: 18, fontWeight: "800", color: colors.text }}>Upload Document</Text>
                <TouchableOpacity onPress={() => setUploadModalVisible(false)}>
                  <Ionicons name="close-circle" size={26} color="#64748B" />
                </TouchableOpacity>
              </View>

              <ScrollView showsVerticalScrollIndicator={false}>
                <Text style={{ fontSize: 11, fontWeight: "700", color: "#64748B", marginBottom: 6 }}>EMPLOYEE ID *</Text>
                <TextInput
                  style={{ backgroundColor: colors.background, borderWidth: 1, borderColor: colors.border, borderRadius: 10, padding: 10, marginBottom: 16 }}
                  placeholder="EMP-1001"
                  value={uploadEmpId}
                  onChangeText={setUploadEmpId}
                  autoCapitalize="characters"
                />

                <Text style={{ fontSize: 11, fontWeight: "700", color: "#64748B", marginBottom: 6 }}>DOCUMENT TYPE</Text>
                <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 8, marginBottom: 16 }}>
                  {DOC_TYPES.map((t) => (
                    <TouchableOpacity
                      key={t}
                      onPress={() => setDocType(t)}
                      style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 10, backgroundColor: docType === t ? "#173B8C" : "#F1F5F9" }}
                    >
                      <Text style={{ fontSize: 12, fontWeight: "700", color: docType === t ? "#FFFFFF" : "#334155" }}>{t}</Text>
                    </TouchableOpacity>
                  ))}
                </View>

                <TouchableOpacity
                  onPress={handlePickPhoto}
                  style={{ backgroundColor: colors.background, borderWidth: 2, borderColor: "#BFDBFE", borderStyle: "dashed", borderRadius: 14, padding: 20, alignItems: "center", marginBottom: 16 }}
                >
                  <Ionicons name={photoUri ? "checkmark-circle" : "image-outline"} size={32} color={photoUri ? "#16A34A" : "#1D4ED8"} />
                  <Text style={{ fontSize: 12, fontWeight: "700", color: "#334155", marginTop: 8 }}>
                    {photoUri ? "Photo Attached — Tap to Change" : "Tap to Attach a Photo"}
                  </Text>
                </TouchableOpacity>

                <TouchableOpacity
                  style={{ backgroundColor: "#173B8C", borderRadius: 14, paddingVertical: 14, alignItems: "center", marginBottom: 10 }}
                  onPress={handleUpload}
                  disabled={uploading}
                >
                  {uploading ? <ActivityIndicator color="#FFFFFF" /> : <Text style={{ color: "#FFFFFF", fontWeight: "800", fontSize: 14 }}>Upload</Text>}
                </TouchableOpacity>
              </ScrollView>
            </View>
          </View>
        </Modal>
      </SafeAreaView>
    </LinearGradient>
  );
}

const makeStyles = (colors) => StyleSheet.create({
  container: { flex: 1 },
  content: { paddingHorizontal: 20, paddingTop: 10 },
  sectionHeader: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", marginBottom: 14 },
  sectionTitle: { fontSize: 15, fontWeight: "800", color: colors.text },
  addBtn: { flexDirection: "row", alignItems: "center", backgroundColor: "#173B8C", paddingHorizontal: 12, paddingVertical: 8, borderRadius: 10 },
  addBtnText: { color: "#FFFFFF", fontWeight: "700", fontSize: 12, marginLeft: 4 },
  filterChip: { paddingHorizontal: 14, paddingVertical: 8, borderRadius: 20, backgroundColor: "#F1F5F9", marginRight: 8 },
  filterChipActive: { backgroundColor: colors.primary },
  filterChipText: { fontSize: 12, fontWeight: "700", color: "#64748B" },
  filterChipTextActive: { color: "#FFFFFF" },
  emptyCard: { backgroundColor: colors.card, borderRadius: 20, padding: 32, alignItems: "center", borderWidth: 1, borderColor: colors.border },
  emptyTitle: { fontSize: 15, fontWeight: "700", color: "#334155", marginTop: 10 },
  emptySub: { fontSize: 12, color: "#64748B", textAlign: "center", marginTop: 4 },
  docCard: { flexDirection: "row", alignItems: "center", backgroundColor: colors.card, borderRadius: 16, padding: 14, marginBottom: 10, borderWidth: 1, borderColor: colors.border },
  docIcon: { width: 40, height: 40, borderRadius: 12, backgroundColor: colors.primaryLight, justifyContent: "center", alignItems: "center" },
  docType: { fontSize: 13, fontWeight: "700", color: colors.text },
  docName: { fontSize: 12, color: colors.textSecondary, marginTop: 2 },
  docMeta: { fontSize: 11, color: colors.textLight, marginTop: 4 },
});
