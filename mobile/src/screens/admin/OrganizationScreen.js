import React, { useCallback, useEffect, useState } from "react";
import {
  View,
  Text,
  TextInput,
  TouchableOpacity,
  StyleSheet,
  ActivityIndicator,
  ScrollView,
  RefreshControl,
  Alert,
  Image,
} from "react-native";
import SafeAreaView from "../../components/SafeAreaView";
import { Ionicons } from "@expo/vector-icons";
import * as ImagePicker from "expo-image-picker";

import AdminHeader from "../../components/admin/AdminHeader";
import PolicyEditor from "../../components/organization/PolicyEditor";
import {
  fetchOrganization,
  saveOrganization,
  uploadOrganizationLogo,
  fetchAdminPolicies,
  addPolicy,
  updatePolicy,
  setPolicyPublished,
  deletePolicy,
} from "../../api/client";
import { API_BASE_URL } from "../../config";
import { useTheme } from "../../store/ThemeContext";

// The website's Organization page in the app: the company's details
// (editable) and its own terms & policies, which employees read in their
// Policies screen. GSTIN and documents stay on the website's verification.
const FIELDS = [
  { key: "company_name", label: "Organization name", placeholder: "Your company's name", maxLength: 200 },
  { key: "company_tagline", label: "Tagline", placeholder: "A short line about your company", maxLength: 300 },
  { key: "contact_phone", label: "Phone", placeholder: "+91 98765 43210", maxLength: 30, keyboardType: "phone-pad" },
  { key: "contact_email", label: "Email", placeholder: "hr@yourcompany.com", maxLength: 255, keyboardType: "email-address" },
  { key: "website", label: "Website", placeholder: "www.yourcompany.com", maxLength: 255, keyboardType: "url" },
  { key: "address", label: "Registered address", placeholder: "Registered office address", maxLength: 500, multiline: true },
];

const errorMessage = (err, fallback) => err?.response?.data?.msg || fallback;

export default function OrganizationScreen({ navigation }) {
  const { colors } = useTheme();
  const styles = React.useMemo(() => makeStyles(colors), [colors]);

  const [tab, setTab] = useState("details");
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  const [org, setOrg] = useState(null);
  const [verification, setVerification] = useState("");
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState({});
  const [saving, setSaving] = useState(false);
  const [logoBusy, setLogoBusy] = useState(false);

  const [policies, setPolicies] = useState([]);
  const [categories, setCategories] = useState([]);
  const [templates, setTemplates] = useState([]);
  const [editor, setEditor] = useState(null); // null | { policy: null (new) | policy }
  const [policySaving, setPolicySaving] = useState(false);

  const load = useCallback(async () => {
    try {
      const [orgRes, polRes] = await Promise.all([fetchOrganization(), fetchAdminPolicies()]);
      if (orgRes?.data?.ok) {
        setOrg(orgRes.data.organization);
        setVerification(orgRes.data.verification || "");
      }
      if (polRes?.data?.ok) {
        setPolicies(polRes.data.policies || []);
        setCategories(polRes.data.categories || []);
        setTemplates(polRes.data.templates || []);
      }
    } catch (err) {
      Alert.alert("Could Not Load", errorMessage(err, "Please check your connection and try again."));
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const categoryLabel = (key) => categories.find((c) => c.key === key)?.label || key;

  // ── Details ──
  const startEdit = () => {
    setDraft(Object.fromEntries(FIELDS.map((f) => [f.key, org?.[f.key] || ""])));
    setEditing(true);
  };

  const saveDetails = async () => {
    if (!String(draft.company_name || "").trim()) {
      Alert.alert("Name Required", "Enter your organization's name.");
      return;
    }
    setSaving(true);
    try {
      const res = await saveOrganization(draft);
      setOrg(res.data.organization);
      setEditing(false);
      Alert.alert("Saved", res.data.msg || "Organization details saved.");
    } catch (err) {
      Alert.alert("Could Not Save", errorMessage(err, "Please check the details and try again."));
    } finally {
      setSaving(false);
    }
  };

  const changeLogo = async () => {
    const perm = await ImagePicker.requestMediaLibraryPermissionsAsync();
    if (!perm.granted) {
      Alert.alert("Permission Required", "Photo library access is needed to change your logo.");
      return;
    }
    const result = await ImagePicker.launchImageLibraryAsync({
      mediaTypes: ImagePicker.MediaTypeOptions.Images,
      quality: 0.85,
    });
    if (result.canceled || !result.assets?.[0]) return;
    const asset = result.assets[0];
    const formData = new FormData();
    formData.append("logo", { uri: asset.uri, name: asset.fileName || "logo.jpg", type: asset.mimeType || "image/jpeg" });
    setLogoBusy(true);
    try {
      const res = await uploadOrganizationLogo(formData);
      setOrg(res.data.organization);
    } catch (err) {
      Alert.alert("Could Not Upload", errorMessage(err, "Please choose a JPG, PNG or WEBP image."));
    } finally {
      setLogoBusy(false);
    }
  };

  // ── Policies ──
  const savePolicy = async (fields) => {
    setPolicySaving(true);
    try {
      if (editor.policy) await updatePolicy(editor.policy.id, fields);
      else await addPolicy(fields);
      setEditor(null);
      await load();
    } catch (err) {
      Alert.alert("Could Not Save", errorMessage(err, "Please try again."));
    } finally {
      setPolicySaving(false);
    }
  };

  const togglePublished = async (policy) => {
    try {
      await setPolicyPublished(policy.id, !policy.is_published);
      setPolicies((list) => list.map((p) => (p.id === policy.id ? { ...p, is_published: !p.is_published } : p)));
    } catch (err) {
      Alert.alert("Could Not Update", errorMessage(err, "Please try again."));
    }
  };

  const confirmDelete = (policy) => {
    Alert.alert("Delete Policy", `Delete "${policy.title}"? Employees will no longer see it.`, [
      { text: "Cancel", style: "cancel" },
      {
        text: "Delete",
        style: "destructive",
        onPress: async () => {
          try {
            await deletePolicy(policy.id);
            setPolicies((list) => list.filter((p) => p.id !== policy.id));
          } catch (err) {
            Alert.alert("Could Not Delete", errorMessage(err, "Please try again."));
          }
        },
      },
    ]);
  };

  const initials = (org?.company_name || "?").split(/\s+/).slice(0, 2).map((w) => w[0]).join("").toUpperCase();

  const renderDetails = () => (
    <>
      <View style={[styles.card, styles.identity]}>
        <TouchableOpacity onPress={changeLogo} disabled={logoBusy} accessibilityLabel="Change logo">
          <View style={styles.logo}>
            {logoBusy ? (
              <ActivityIndicator color={colors.primary} />
            ) : org?.logo_url ? (
              <Image source={{ uri: `${API_BASE_URL}${org.logo_url}` }} style={styles.logoImage} accessibilityLabel="Company logo" />
            ) : (
              <Text style={styles.logoInitials}>{initials}</Text>
            )}
          </View>
          <View style={styles.logoBadge}><Ionicons name="camera" size={12} color="#FFFFFF" /></View>
        </TouchableOpacity>
        <View style={{ flex: 1, marginLeft: 14 }}>
          <Text style={styles.orgName}>{org?.company_name}</Text>
          {org?.company_tagline ? <Text style={styles.orgTagline}>{org.company_tagline}</Text> : null}
          {org?.company_code ? <Text style={styles.orgCode}>{org.company_code}</Text> : null}
        </View>
      </View>

      <View style={styles.card}>
        <View style={styles.cardHead}>
          <Text style={styles.cardTitle}>Company details</Text>
          {!editing ? (
            <TouchableOpacity style={styles.editBtn} onPress={startEdit}>
              <Ionicons name="create-outline" size={15} color={colors.primary} />
              <Text style={styles.editBtnText}>Edit</Text>
            </TouchableOpacity>
          ) : null}
        </View>

        {editing ? (
          <>
            {FIELDS.map((f) => (
              <View key={f.key}>
                <Text style={styles.label}>{f.label.toUpperCase()}</Text>
                <TextInput
                  style={[styles.input, f.multiline && styles.inputMulti]}
                  placeholder={f.placeholder}
                  placeholderTextColor={colors.textLight}
                  value={draft[f.key]}
                  onChangeText={(v) => setDraft((d) => ({ ...d, [f.key]: v }))}
                  maxLength={f.maxLength}
                  keyboardType={f.keyboardType || "default"}
                  autoCapitalize={f.keyboardType ? "none" : "sentences"}
                  multiline={!!f.multiline}
                  textAlignVertical={f.multiline ? "top" : "center"}
                />
              </View>
            ))}
            <View style={styles.actions}>
              <TouchableOpacity style={[styles.btn, styles.btnGhost]} onPress={() => setEditing(false)} disabled={saving}>
                <Text style={styles.btnGhostText}>Cancel</Text>
              </TouchableOpacity>
              <TouchableOpacity style={[styles.btn, styles.btnPrimary]} onPress={saveDetails} disabled={saving}>
                {saving ? <ActivityIndicator color="#FFFFFF" /> : <Text style={styles.btnPrimaryText}>Save changes</Text>}
              </TouchableOpacity>
            </View>
          </>
        ) : (
          FIELDS.map((f) => (
            <View key={f.key} style={styles.row}>
              <Text style={styles.rowLabel}>{f.label}</Text>
              <Text style={[styles.rowValue, !org?.[f.key] && styles.rowEmpty]}>{org?.[f.key] || "Not added"}</Text>
            </View>
          ))
        )}
      </View>

      <View style={styles.note}>
        <Ionicons name="information-circle-outline" size={16} color={colors.primary} />
        <Text style={styles.noteText}>
          GSTIN and business documents{verification === "approved" ? " (verified)" : ""} are updated through
          document verification on the website. Work schedule and office location are in Settings.
        </Text>
      </View>
    </>
  );

  const renderPolicies = () => (
    <>
      {editor ? (
        <PolicyEditor
          key={editor.policy?.id || "new"}
          policy={editor.policy}
          categories={categories}
          templates={templates}
          saving={policySaving}
          onSave={savePolicy}
          onCancel={() => setEditor(null)}
        />
      ) : (
        <TouchableOpacity style={styles.addBtn} onPress={() => setEditor({ policy: null })}>
          <Ionicons name="add-circle-outline" size={18} color="#FFFFFF" />
          <Text style={styles.addBtnText}>Add a policy</Text>
        </TouchableOpacity>
      )}

      {!policies.length && !editor ? (
        <View style={[styles.card, styles.empty]}>
          <Ionicons name="document-text-outline" size={40} color={colors.primary} />
          <Text style={styles.emptyTitle}>No policies yet</Text>
          <Text style={styles.emptyText}>
            Add your company's terms & conditions, rules and HR policies. Published ones appear in every employee's Policies screen.
          </Text>
        </View>
      ) : null}

      {policies.map((p) => (
        <View key={p.id} style={styles.card}>
          <View style={styles.policyTop}>
            <Text style={styles.policyCategory}>{categoryLabel(p.category)}</Text>
            <View style={[styles.status, p.is_published ? styles.statusLive : styles.statusDraft]}>
              <Text style={[styles.statusText, { color: p.is_published ? colors.success : colors.warning }]}>
                {p.is_published ? "Published" : "Draft"}
              </Text>
            </View>
          </View>
          <Text style={styles.policyTitle}>{p.title}</Text>
          <Text style={styles.policyBody} numberOfLines={3}>{p.body}</Text>
          <View style={styles.policyActions}>
            <TouchableOpacity style={styles.policyAction} onPress={() => setEditor({ policy: p })}>
              <Ionicons name="create-outline" size={15} color={colors.primary} />
              <Text style={styles.policyActionText}>Edit</Text>
            </TouchableOpacity>
            <TouchableOpacity style={styles.policyAction} onPress={() => togglePublished(p)}>
              <Ionicons name={p.is_published ? "eye-off-outline" : "eye-outline"} size={15} color={colors.primary} />
              <Text style={styles.policyActionText}>{p.is_published ? "Unpublish" : "Publish"}</Text>
            </TouchableOpacity>
            <TouchableOpacity style={styles.policyAction} onPress={() => confirmDelete(p)}>
              <Ionicons name="trash-outline" size={15} color={colors.danger} />
              <Text style={[styles.policyActionText, { color: colors.danger }]}>Delete</Text>
            </TouchableOpacity>
          </View>
        </View>
      ))}
    </>
  );

  return (
    <SafeAreaView style={styles.container}>
      <AdminHeader title="Organization" subtitle="COMPANY PROFILE" navigation={navigation} />
      <View style={styles.segment}>
        {[["details", "Details"], ["policies", "Terms & Policies"]].map(([key, label]) => (
          <TouchableOpacity
            key={key}
            style={[styles.segmentItem, tab === key && styles.segmentActive]}
            onPress={() => setTab(key)}
            accessibilityRole="tab"
            accessibilityState={{ selected: tab === key }}
          >
            <Text style={[styles.segmentText, tab === key && styles.segmentTextActive]}>{label}</Text>
          </TouchableOpacity>
        ))}
      </View>
      {loading ? (
        <ActivityIndicator style={{ marginTop: 40 }} color={colors.primary} />
      ) : (
        <ScrollView
          contentContainerStyle={styles.scrollContent}
          keyboardShouldPersistTaps="handled"
          showsVerticalScrollIndicator={false}
          refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => { setRefreshing(true); load(); }} />}
        >
          {tab === "details" ? renderDetails() : renderPolicies()}
        </ScrollView>
      )}
    </SafeAreaView>
  );
}

const makeStyles = (colors) => StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.background },
  scrollContent: { padding: 16, paddingBottom: 110 },
  segment: {
    flexDirection: "row", marginHorizontal: 16, marginTop: 12, padding: 4, borderRadius: 999,
    backgroundColor: colors.blueBg,
  },
  segmentItem: { flex: 1, paddingVertical: 9, borderRadius: 999, alignItems: "center" },
  segmentActive: { backgroundColor: colors.primary },
  segmentText: { fontSize: 13, fontWeight: "700", color: colors.primary },
  segmentTextActive: { color: "#FFFFFF" },
  card: {
    backgroundColor: colors.surface, borderRadius: 16, padding: 16, marginBottom: 14,
    shadowColor: "#131C2A", shadowOpacity: 0.06, shadowRadius: 12, shadowOffset: { width: 0, height: 4 }, elevation: 2,
  },
  identity: { flexDirection: "row", alignItems: "center" },
  logo: {
    width: 64, height: 64, borderRadius: 16, backgroundColor: colors.blueBg, overflow: "hidden",
    justifyContent: "center", alignItems: "center",
  },
  logoImage: { width: 64, height: 64, resizeMode: "contain", backgroundColor: "#FFFFFF" },
  logoInitials: { fontSize: 22, fontWeight: "800", color: colors.primary },
  logoBadge: {
    position: "absolute", right: -4, bottom: -4, width: 22, height: 22, borderRadius: 11,
    backgroundColor: colors.primary, justifyContent: "center", alignItems: "center",
    borderWidth: 2, borderColor: colors.surface,
  },
  orgName: { fontSize: 18, fontWeight: "800", color: colors.text },
  orgTagline: { fontSize: 13, color: colors.textSecondary, marginTop: 3 },
  orgCode: { fontSize: 11.5, fontWeight: "700", color: colors.textLight, marginTop: 4, letterSpacing: 0.6 },
  cardHead: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", marginBottom: 10 },
  cardTitle: { fontSize: 15, fontWeight: "800", color: colors.text },
  editBtn: {
    flexDirection: "row", alignItems: "center", paddingHorizontal: 12, paddingVertical: 6,
    borderRadius: 999, backgroundColor: colors.blueBg,
  },
  editBtnText: { marginLeft: 5, fontSize: 12.5, fontWeight: "700", color: colors.primary },
  row: { paddingVertical: 10, borderTopWidth: 1, borderTopColor: colors.border },
  rowLabel: { fontSize: 11.5, fontWeight: "700", color: colors.textSecondary, marginBottom: 3 },
  rowValue: { fontSize: 14, color: colors.text },
  rowEmpty: { color: colors.textLight },
  label: { fontSize: 10.5, fontWeight: "800", color: colors.textSecondary, letterSpacing: 0.6, marginBottom: 6 },
  input: {
    minHeight: 46, borderRadius: 12, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.background,
    paddingHorizontal: 14, paddingVertical: 10, fontSize: 14, color: colors.text, marginBottom: 14,
  },
  inputMulti: { minHeight: 84 },
  actions: { flexDirection: "row", justifyContent: "flex-end" },
  btn: { height: 44, borderRadius: 12, paddingHorizontal: 18, justifyContent: "center", alignItems: "center", marginLeft: 10 },
  btnGhost: { borderWidth: 1, borderColor: colors.border },
  btnGhostText: { fontSize: 13, fontWeight: "700", color: colors.textSecondary },
  btnPrimary: { backgroundColor: colors.primary, minWidth: 130 },
  btnPrimaryText: { fontSize: 13, fontWeight: "800", color: "#FFFFFF" },
  note: { flexDirection: "row", padding: 12, borderRadius: 12, backgroundColor: colors.blueBg },
  noteText: { flex: 1, marginLeft: 8, fontSize: 12, lineHeight: 17, color: colors.textSecondary },
  addBtn: {
    flexDirection: "row", alignItems: "center", justifyContent: "center", height: 46, borderRadius: 999,
    backgroundColor: colors.primary, marginBottom: 14,
  },
  addBtnText: { marginLeft: 8, fontSize: 14, fontWeight: "800", color: "#FFFFFF" },
  empty: { alignItems: "center", paddingVertical: 28 },
  emptyTitle: { fontSize: 16, fontWeight: "800", color: colors.text, marginTop: 10 },
  emptyText: { fontSize: 13, color: colors.textSecondary, textAlign: "center", lineHeight: 19, marginTop: 6 },
  policyTop: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", marginBottom: 6 },
  policyCategory: { fontSize: 11, fontWeight: "800", color: colors.primary, letterSpacing: 0.5, textTransform: "uppercase" },
  status: { paddingHorizontal: 10, paddingVertical: 3, borderRadius: 999 },
  statusLive: { backgroundColor: colors.greenBg },
  statusDraft: { backgroundColor: colors.yellowBg },
  statusText: { fontSize: 11, fontWeight: "800" },
  policyTitle: { fontSize: 15, fontWeight: "800", color: colors.text, marginBottom: 4 },
  policyBody: { fontSize: 13, lineHeight: 19, color: colors.textSecondary },
  policyActions: { flexDirection: "row", marginTop: 12, borderTopWidth: 1, borderTopColor: colors.border, paddingTop: 10 },
  policyAction: { flexDirection: "row", alignItems: "center", marginRight: 18 },
  policyActionText: { marginLeft: 5, fontSize: 12.5, fontWeight: "700", color: colors.primary },
});
