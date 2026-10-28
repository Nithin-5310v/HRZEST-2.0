import React, { useState, useCallback } from "react";
import {
  View,
  Text,
  ScrollView,
  StyleSheet,
  ActivityIndicator,
  TouchableOpacity,
  Modal,
  TextInput,
  Switch,
  Alert,
  RefreshControl,
} from "react-native";
import SafeAreaView from "../../components/SafeAreaView";
import { Ionicons } from "@expo/vector-icons";
import { useFocusEffect } from "@react-navigation/native";

import ProfileHeader from "../../components/profile/ProfileHeader";
import EmptyState from "../../components/ui/EmptyState";
import { useTheme } from "../../store/ThemeContext";
import { fetchMyExperience, addMyExperience, deleteMyExperience } from "../../api/client";

export default function ExperienceScreen() {
  const { colors } = useTheme();
  const styles = React.useMemo(() => makeStyles(colors), [colors]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [entries, setEntries] = useState([]);
  const [modalVisible, setModalVisible] = useState(false);
  const [saving, setSaving] = useState(false);

  const [company, setCompany] = useState("");
  const [designation, setDesignation] = useState("");
  const [fromYear, setFromYear] = useState("");
  const [toYear, setToYear] = useState("");
  const [isCurrent, setIsCurrent] = useState(false);
  const [description, setDescription] = useState("");

  const load = async () => {
    try {
      const res = await fetchMyExperience();
      setEntries(res?.data?.ok ? res.data.experience || [] : []);
    } catch (_) {
      setEntries([]);
    }
    setLoading(false);
    setRefreshing(false);
  };

  useFocusEffect(
    useCallback(() => {
      load();
    }, [])
  );

  const resetForm = () => {
    setCompany("");
    setDesignation("");
    setFromYear("");
    setToYear("");
    setIsCurrent(false);
    setDescription("");
  };

  const handleAdd = async () => {
    if (!company.trim() || !designation.trim() || !fromYear.trim()) {
      Alert.alert("Missing Details", "Company, designation, and start year are required.");
      return;
    }
    setSaving(true);
    try {
      const res = await addMyExperience({
        company: company.trim(),
        designation: designation.trim(),
        from_year: fromYear.trim(),
        to_year: isCurrent ? "" : toYear.trim(),
        is_current: isCurrent,
        description: description.trim(),
      });
      if (res?.data?.ok) {
        setModalVisible(false);
        resetForm();
        load();
      } else {
        Alert.alert("Could Not Add", res?.data?.msg || "Please check the details and try again.");
      }
    } catch (e) {
      Alert.alert("Could Not Add", e?.response?.data?.msg || "Please check the details and try again.");
    }
    setSaving(false);
  };

  const handleDelete = (entry) => {
    Alert.alert("Remove Entry", `Remove "${entry.designation} @ ${entry.company}" from your experience?`, [
      { text: "Cancel", style: "cancel" },
      {
        text: "Remove",
        style: "destructive",
        onPress: async () => {
          try {
            const res = await deleteMyExperience(entry.id);
            if (res?.data?.ok) {
              setEntries((prev) => prev.filter((e) => e.id !== entry.id));
            } else {
              Alert.alert("Could Not Remove", res?.data?.msg || "Please try again.");
            }
          } catch (e) {
            Alert.alert("Could Not Remove", e?.response?.data?.msg || "Please try again.");
          }
        },
      },
    ]);
  };

  return (
    <SafeAreaView style={styles.container}>
      <ProfileHeader
        title="Experience"
        showBack
        rightAction={
          <TouchableOpacity onPress={() => setModalVisible(true)}>
            <Ionicons name="add-circle-outline" size={26} color={colors.primary} />
          </TouchableOpacity>
        }
      />

      {loading ? (
        <View style={styles.centerFill}>
          <ActivityIndicator size="large" color={colors.primary} />
        </View>
      ) : (
        <ScrollView
          showsVerticalScrollIndicator={false}
          contentContainerStyle={styles.content}
          refreshControl={
            <RefreshControl refreshing={refreshing} onRefresh={() => { setRefreshing(true); load(); }} colors={[colors.primary]} />
          }
        >
          {entries.length === 0 ? (
            <EmptyState
              icon="layers-outline"
              title="No experience records yet"
              description="Tap the + button above to add your previous employment history."
            />
          ) : (
            entries.map((entry) => (
              <View key={entry.id} style={styles.card}>
                <View style={styles.cardIcon}>
                  <Ionicons name="briefcase" size={20} color={colors.primary} />
                </View>
                <View style={styles.cardBody}>
                  <Text style={styles.cardTitle}>{entry.designation}</Text>
                  <Text style={styles.cardSubtitle}>{entry.company}</Text>
                  <View style={styles.cardMetaRow}>
                    <Text style={styles.cardMeta}>
                      {entry.from_year} - {entry.is_current ? "Present" : (entry.to_year || "—")}
                    </Text>
                    {entry.is_current ? (
                      <View style={styles.currentBadge}>
                        <Text style={styles.currentBadgeText}>CURRENT</Text>
                      </View>
                    ) : null}
                  </View>
                  {!!entry.description && <Text style={styles.cardDesc}>{entry.description}</Text>}
                </View>
                <TouchableOpacity style={styles.deleteBtn} onPress={() => handleDelete(entry)}>
                  <Ionicons name="trash-outline" size={18} color={colors.danger} />
                </TouchableOpacity>
              </View>
            ))
          )}
        </ScrollView>
      )}

      <Modal visible={modalVisible} transparent animationType="slide" onRequestClose={() => setModalVisible(false)}>
        <View style={styles.modalOverlay}>
          <ScrollView style={styles.modalContent} contentContainerStyle={{ paddingBottom: 8 }}>
            <View style={styles.modalHeader}>
              <Text style={styles.modalTitle}>Add Experience</Text>
              <TouchableOpacity onPress={() => setModalVisible(false)}>
                <Ionicons name="close-circle" size={24} color={colors.textSecondary} />
              </TouchableOpacity>
            </View>

            <Text style={styles.inputLabel}>COMPANY</Text>
            <TextInput style={styles.input} value={company} onChangeText={setCompany} placeholder="e.g. Acme Tech Solutions" />

            <Text style={styles.inputLabel}>DESIGNATION</Text>
            <TextInput style={styles.input} value={designation} onChangeText={setDesignation} placeholder="e.g. Software Engineer" />

            <Text style={styles.inputLabel}>START YEAR</Text>
            <TextInput style={styles.input} value={fromYear} onChangeText={setFromYear} keyboardType="number-pad" placeholder="e.g. 2019" />

            <View style={styles.currentRow}>
              <Text style={styles.inputLabel}>I CURRENTLY WORK HERE</Text>
              <Switch value={isCurrent} onValueChange={setIsCurrent} trackColor={{ true: colors.primary }} />
            </View>

            {!isCurrent && (
              <>
                <Text style={styles.inputLabel}>END YEAR</Text>
                <TextInput style={styles.input} value={toYear} onChangeText={setToYear} keyboardType="number-pad" placeholder="e.g. 2022" />
              </>
            )}

            <Text style={styles.inputLabel}>DESCRIPTION (OPTIONAL)</Text>
            <TextInput
              style={[styles.input, { height: 80, textAlignVertical: "top" }]}
              value={description}
              onChangeText={setDescription}
              multiline
              placeholder="What did you work on?"
            />

            <TouchableOpacity style={styles.saveModalBtn} onPress={handleAdd} disabled={saving}>
              {saving ? <ActivityIndicator color="#FFFFFF" /> : <Text style={styles.saveModalBtnText}>Add Experience</Text>}
            </TouchableOpacity>
          </ScrollView>
        </View>
      </Modal>
    </SafeAreaView>
  );
}

const makeStyles = (colors) => StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.background },
  centerFill: { flex: 1, justifyContent: "center", alignItems: "center" },
  content: { paddingHorizontal: 18, paddingBottom: 120, paddingTop: 18 },
  card: {
    flexDirection: "row",
    backgroundColor: colors.card,
    borderRadius: 18,
    padding: 16,
    marginBottom: 12,
    borderWidth: 1,
    borderColor: colors.border,
  },
  cardIcon: {
    width: 44,
    height: 44,
    borderRadius: 14,
    backgroundColor: colors.primaryLight,
    justifyContent: "center",
    alignItems: "center",
    marginRight: 14,
  },
  cardBody: { flex: 1 },
  cardTitle: { fontSize: 14, fontWeight: "800", color: colors.text },
  cardSubtitle: { fontSize: 12.5, fontWeight: "600", color: colors.textSecondary, marginTop: 2 },
  cardMetaRow: { flexDirection: "row", alignItems: "center", marginTop: 4, gap: 8 },
  cardMeta: { fontSize: 11.5, color: colors.textLight, fontWeight: "600" },
  cardDesc: { fontSize: 12, color: colors.textSecondary, marginTop: 6, lineHeight: 17 },
  currentBadge: {
    backgroundColor: colors.greenBg,
    paddingHorizontal: 8,
    paddingVertical: 2,
    borderRadius: 8,
  },
  currentBadgeText: { fontSize: 9.5, fontWeight: "800", color: "#16A34A", letterSpacing: 0.4 },
  deleteBtn: {
    width: 36,
    height: 36,
    borderRadius: 10,
    backgroundColor: colors.redBg,
    justifyContent: "center",
    alignItems: "center",
    marginLeft: 8,
    alignSelf: "flex-start",
  },
  modalOverlay: { flex: 1, backgroundColor: "rgba(15, 23, 42, 0.75)", justifyContent: "center", padding: 20 },
  modalContent: { backgroundColor: colors.card, borderRadius: 24, padding: 24, maxHeight: "85%" },
  modalHeader: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", marginBottom: 16 },
  modalTitle: { fontSize: 16, fontWeight: "800", color: colors.text },
  inputLabel: { fontSize: 11, fontWeight: "700", color: colors.textSecondary, marginTop: 10 },
  currentRow: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", marginTop: 14 },
  input: {
    backgroundColor: colors.background,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: 10,
    padding: 10,
    marginTop: 4,
    fontSize: 14,
    color: colors.text,
  },
  saveModalBtn: {
    backgroundColor: colors.primary,
    borderRadius: 14,
    paddingVertical: 14,
    alignItems: "center",
    marginTop: 20,
  },
  saveModalBtnText: { color: "#FFFFFF", fontWeight: "700", fontSize: 15 },
});
