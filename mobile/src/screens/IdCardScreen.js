import React, { useCallback, useState } from "react";
import {
  ScrollView,
  StyleSheet,
  View,
  Text,
  Image,
  TouchableOpacity,
  ActivityIndicator,
  Platform,
  StatusBar,
} from "react-native";
import SafeAreaView from "../components/SafeAreaView";
import { Ionicons } from "@expo/vector-icons";
import { useFocusEffect } from "@react-navigation/native";

import { fetchMyIdCard, fetchEmployeeIdCard } from "../api/client";
import { shareBase64File } from "../utils/fileShare";
import { useTheme } from "../store/ThemeContext";

// Shared by both navigators: an employee reaches this with no params (own
// card, /api/employee/my_id_card); an admin/HR session reaches it with
// { empId, empName } from EmployeesScreen's detail modal (any employee
// assigned to them, /api/employees/<id>/id_card -- HR-scoped server-side).
// Same rendered image either way (blueprints/employees.py's
// _build_id_card_buf(), already shared by every web ID-card entry point)
// -- nothing here re-implements the card layout, just displays/shares the
// PNG the backend already generates.
export default function IdCardScreen({ navigation, route }) {
  const { colors } = useTheme();
  const styles = React.useMemo(() => makeStyles(colors), [colors]);
  const empId = route?.params?.empId;
  const empName = route?.params?.empName;
  const isViewingOther = !!empId;

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [imageBase64, setImageBase64] = useState(null);
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = isViewingOther ? await fetchEmployeeIdCard(empId) : await fetchMyIdCard();
      if (res?.data?.ok && res.data.image_base64) {
        setImageBase64(res.data.image_base64);
      } else {
        setError(res?.data?.msg || "Could not load the ID card.");
      }
    } catch (e) {
      setError(
        e?.response?.data?.msg ||
          "Could not reach the server to load the ID card. Check your connection and try again."
      );
    } finally {
      setLoading(false);
    }
  }, [empId, isViewingOther]);

  useFocusEffect(
    useCallback(() => {
      load();
    }, [load])
  );

  const handleSave = async () => {
    if (!imageBase64) return;
    setSaving(true);
    try {
      await shareBase64File(`IDCard_${empId || "MyCard"}.png`, imageBase64, "image/png");
    } catch (e) {
      // shareBase64File already best-effort-cleans its temp file on any
      // path (see its own finally block) -- nothing left to undo here,
      // just let the user know the share/save didn't go through.
      setError("Could not save or share the ID card. Please try again.");
    } finally {
      setSaving(false);
    }
  };

  return (
    <SafeAreaView style={styles.container}>
      <View style={styles.header}>
        <TouchableOpacity
          style={styles.backButton}
          onPress={() => navigation.goBack()}
          hitSlop={{ top: 10, bottom: 10, left: 10, right: 10 }}
        >
          <Ionicons name="chevron-back" size={24} color={colors.text} />
        </TouchableOpacity>
        <Text style={styles.headerTitle} numberOfLines={1}>
          {isViewingOther ? `${empName || empId}'s ID Card` : "My ID Card"}
        </Text>
        <View style={{ width: 24 }} />
      </View>

      <ScrollView contentContainerStyle={styles.content}>
        {loading ? (
          <ActivityIndicator size="large" color={colors.primary} style={{ marginTop: 60 }} />
        ) : error ? (
          <View style={styles.errorBox}>
            <Ionicons name="alert-circle-outline" size={40} color={colors.danger} />
            <Text style={styles.errorText}>{error}</Text>
            <TouchableOpacity style={styles.retryButton} onPress={load}>
              <Text style={styles.retryButtonText}>Try Again</Text>
            </TouchableOpacity>
          </View>
        ) : (
          <>
            <Image
              source={{ uri: `data:image/png;base64,${imageBase64}` }}
              style={styles.cardImage}
              resizeMode="contain"
            />
            <TouchableOpacity style={styles.saveButton} onPress={handleSave} disabled={saving}>
              {saving ? (
                <ActivityIndicator size="small" color="#FFFFFF" />
              ) : (
                <>
                  <Ionicons name="share-outline" size={18} color="#FFFFFF" />
                  <Text style={styles.saveButtonText}>Save / Share</Text>
                </>
              )}
            </TouchableOpacity>
          </>
        )}
      </ScrollView>
    </SafeAreaView>
  );
}

const makeStyles = (colors) =>
  StyleSheet.create({
    container: { flex: 1, backgroundColor: colors.background },
    header: {
      flexDirection: "row",
      alignItems: "center",
      justifyContent: "space-between",
      paddingHorizontal: 16,
      paddingTop: Platform.OS === "android" ? (StatusBar.currentHeight || 24) + 14 : 14,
      paddingBottom: 14,
      backgroundColor: colors.card,
      borderBottomWidth: 1,
      borderBottomColor: colors.border,
    },
    backButton: { width: 24 },
    headerTitle: { flex: 1, textAlign: "center", fontSize: 16, fontWeight: "800", color: colors.text },
    content: { padding: 20, alignItems: "center" },
    cardImage: { width: "100%", aspectRatio: 500 / 820, maxWidth: 420, borderRadius: 12 },
    saveButton: {
      flexDirection: "row",
      alignItems: "center",
      justifyContent: "center",
      gap: 8,
      marginTop: 20,
      backgroundColor: colors.primary,
      borderRadius: 14,
      paddingVertical: 14,
      paddingHorizontal: 28,
    },
    saveButtonText: { color: "#FFFFFF", fontWeight: "700", fontSize: 14 },
    errorBox: { alignItems: "center", marginTop: 60, paddingHorizontal: 24 },
    errorText: {
      marginTop: 12,
      color: colors.textLight,
      fontSize: 14,
      fontWeight: "600",
      textAlign: "center",
    },
    retryButton: {
      marginTop: 16,
      backgroundColor: colors.primary,
      borderRadius: 12,
      paddingVertical: 10,
      paddingHorizontal: 24,
    },
    retryButtonText: { color: "#FFFFFF", fontWeight: "700", fontSize: 13 },
  });
