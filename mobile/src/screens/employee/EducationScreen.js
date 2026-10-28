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
  Alert,
  RefreshControl,
} from "react-native";
import SafeAreaView from "../../components/SafeAreaView";
import { Ionicons } from "@expo/vector-icons";
import { useFocusEffect } from "@react-navigation/native";

import ProfileHeader from "../../components/profile/ProfileHeader";
import EmptyState from "../../components/ui/EmptyState";
import { useTheme } from "../../store/ThemeContext";
import { fetchMyEducation, addMyEducation, deleteMyEducation } from "../../api/client";

export default function EducationScreen() {
  const { colors } = useTheme();
  const styles = React.useMemo(() => makeStyles(colors), [colors]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [entries, setEntries] = useState([]);
  const [modalVisible, setModalVisible] = useState(false);
  const [saving, setSaving] = useState(false);

  const [degree, setDegree] = useState("");
  const [institution, setInstitution] = useState("");
  const [yearOfPassing, setYearOfPassing] = useState("");
  const [percentage, setPercentage] = useState("");

  const load = async () => {
    try {
      const res = await fetchMyEducation();
      setEntries(res?.data?.ok ? res.data.education || [] : []);
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
    setDegree("");
    setInstitution("");
    setYearOfPassing("");
    setPercentage("");
  };

  const handleAdd = async () => {
    if (!degree.trim() || !institution.trim()) {
      Alert.alert("Missing Details", "Degree and institution are required.");
      return;
    }
    setSaving(true);
    try {
      const res = await addMyEducation({
        degree: degree.trim(),
        institution: institution.trim(),
        year_of_passing: yearOfPassing.trim(),
        percentage: percentage.trim(),
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
    Alert.alert("Remove Entry", `Remove "${entry.degree}" from your education history?`, [
      { text: "Cancel", style: "cancel" },
      {
        text: "Remove",
        style: "destructive",
        onPress: async () => {
          try {
            const res = await deleteMyEducation(entry.id);
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
        title="Education"
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
              icon="school-outline"
              title="No education records yet"
              description="Tap the + button above to add your academic qualifications."
            />
          ) : (
            entries.map((entry) => (
              <View key={entry.id} style={styles.card}>
                <View style={styles.cardIcon}>
                  <Ionicons name="school" size={22} color={colors.primary} />
                </View>
                <View style={styles.cardBody}>
                  <Text style={styles.cardTitle}>{entry.degree}</Text>
                  <Text style={styles.cardSubtitle}>{entry.institution}</Text>
                  <Text style={styles.cardMeta}>
                    {entry.year_of_passing || "Year not set"}
                    {entry.percentage ? ` • ${entry.percentage}%` : ""}
                  </Text>
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
          <View style={styles.modalContent}>
            <View style={styles.modalHeader}>
              <Text style={styles.modalTitle}>Add Education</Text>
              <TouchableOpacity onPress={() => setModalVisible(false)}>
                <Ionicons name="close-circle" size={24} color={colors.textSecondary} />
              </TouchableOpacity>
            </View>

            <Text style={styles.inputLabel}>DEGREE</Text>
            <TextInput style={styles.input} value={degree} onChangeText={setDegree} placeholder="e.g. B.Tech Computer Science" />

            <Text style={styles.inputLabel}>INSTITUTION</Text>
            <TextInput style={styles.input} value={institution} onChangeText={setInstitution} placeholder="e.g. Anna University" />

            <Text style={styles.inputLabel}>YEAR OF PASSING</Text>
            <TextInput style={styles.input} value={yearOfPassing} onChangeText={setYearOfPassing} keyboardType="number-pad" placeholder="e.g. 2020" />

            <Text style={styles.inputLabel}>PERCENTAGE / CGPA</Text>
            <TextInput style={styles.input} value={percentage} onChangeText={setPercentage} keyboardType="decimal-pad" placeholder="e.g. 78.5" />

            <TouchableOpacity style={styles.saveModalBtn} onPress={handleAdd} disabled={saving}>
              {saving ? <ActivityIndicator color="#FFFFFF" /> : <Text style={styles.saveModalBtnText}>Add Education</Text>}
            </TouchableOpacity>
          </View>
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
    alignItems: "center",
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
  cardMeta: { fontSize: 11.5, color: colors.textLight, marginTop: 3, fontWeight: "600" },
  deleteBtn: {
    width: 36,
    height: 36,
    borderRadius: 10,
    backgroundColor: colors.redBg,
    justifyContent: "center",
    alignItems: "center",
    marginLeft: 8,
  },
  modalOverlay: { flex: 1, backgroundColor: "rgba(15, 23, 42, 0.75)", justifyContent: "center", padding: 20 },
  modalContent: { backgroundColor: colors.card, borderRadius: 24, padding: 24 },
  modalHeader: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", marginBottom: 16 },
  modalTitle: { fontSize: 16, fontWeight: "800", color: colors.text },
  inputLabel: { fontSize: 11, fontWeight: "700", color: colors.textSecondary, marginTop: 10 },
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
