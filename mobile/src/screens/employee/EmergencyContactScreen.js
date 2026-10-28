import React, { useState, useEffect } from "react";
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
} from "react-native";
import SafeAreaView from "../../components/SafeAreaView";
import { Ionicons } from "@expo/vector-icons";
import { useAuth } from "../../store/AuthContext";
import { useTheme } from "../../store/ThemeContext";
import { fetchEmployeeProfile, updateMyProfile } from "../../api/client";
import ProfileHeader from "../../components/profile/ProfileHeader";
import DetailCard from "../../components/profile/DetailCard";

export default function EmergencyContactScreen() {
  const { user } = useAuth();
  const { colors } = useTheme();
  const styles = React.useMemo(() => makeStyles(colors), [colors]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [modalVisible, setModalVisible] = useState(false);
  // See PersonalInfoScreen.js's rawProfile comment.
  const [rawProfile, setRawProfile] = useState({});

  const [emergency, setEmergency] = useState({
    primaryName: user?.emergency_contact_name || "Not Provided",
    primaryRelation: user?.emergency_contact_relation || "Not Provided",
    primaryPhone: user?.emergency_contact_phone || "Not Provided",
    address: user?.address || "Not Provided",
  });

  const [editName, setEditName] = useState(emergency.primaryName);
  const [editRelation, setEditRelation] = useState(emergency.primaryRelation);
  const [editPhone, setEditPhone] = useState(emergency.primaryPhone);
  const [editAddress, setEditAddress] = useState(emergency.address);

  useEffect(() => {
    fetchEmployeeProfile()
      .then((res) => {
        if (res?.data?.ok && res?.data?.profile) {
          const p = res.data.profile;
          setRawProfile(p);
          const updated = {
            primaryName: p.emergency_contact_name || user?.emergency_contact_name || "Not Provided",
            primaryRelation: p.emergency_contact_relation || user?.emergency_contact_relation || "Not Provided",
            primaryPhone: p.emergency_contact_phone || user?.emergency_contact_phone || "Not Provided",
            address: p.address || user?.address || "Not Provided",
          };
          setEmergency(updated);
          setEditName(updated.primaryName);
          setEditRelation(updated.primaryRelation);
          setEditPhone(updated.primaryPhone);
          setEditAddress(updated.address);
        }
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  const handleSave = async () => {
    setSaving(true);
    try {
      const res = await updateMyProfile({
        ...rawProfile,
        emergency_contact_name: editName,
        emergency_contact_relation: editRelation,
        emergency_contact_phone: editPhone,
        address: editAddress,
      });
      if (res?.data?.ok) {
        setEmergency((prev) => ({
          ...prev, primaryName: editName, primaryRelation: editRelation, primaryPhone: editPhone, address: editAddress,
        }));
        setRawProfile((prev) => ({
          ...prev, emergency_contact_name: editName, emergency_contact_relation: editRelation,
          emergency_contact_phone: editPhone, address: editAddress,
        }));
        setModalVisible(false);
        Alert.alert("Saved", "Emergency contact updated.");
      } else {
        Alert.alert("Save Failed", res?.data?.msg || "Could not update emergency contact.");
      }
    } catch (e) {
      Alert.alert("Save Failed", e?.response?.data?.msg || "Could not update emergency contact.");
    }
    setSaving(false);
  };

  return (
    <SafeAreaView style={styles.container}>
      <ProfileHeader title="Emergency Contact" showBack />

      {loading ? (
        <View style={{ flex: 1, justifyContent: "center", alignItems: "center" }}>
          <ActivityIndicator size="large" color={colors.primary} />
        </View>
      ) : (
        <ScrollView showsVerticalScrollIndicator={false} contentContainerStyle={styles.content}>
          {/* Summary Card */}
          <View style={styles.summaryCard}>
            <View style={styles.avatar}>
              <Ionicons name="medical" size={24} color="#FFFFFF" />
            </View>

            <View style={styles.summaryText}>
              <Text style={styles.name}>{emergency.primaryName}</Text>
              <Text style={styles.subText}>{emergency.primaryRelation} • {emergency.primaryPhone}</Text>
            </View>

            <TouchableOpacity style={styles.editBtn} onPress={() => setModalVisible(true)}>
              <Ionicons name="create-outline" size={20} color={colors.primary} />
            </TouchableOpacity>
          </View>

          {/* Details */}
          <DetailCard icon="person-outline" label="Contact Name" value={emergency.primaryName} />
          <DetailCard icon="people-outline" label="Relationship" value={emergency.primaryRelation} />
          <DetailCard icon="call-outline" label="Phone Number" value={emergency.primaryPhone} />
          <DetailCard icon="location-outline" label="Family Address" value={emergency.address} />
        </ScrollView>
      )}

      {/* Edit Emergency Contact Modal */}
      <Modal visible={modalVisible} transparent animationType="slide" onRequestClose={() => setModalVisible(false)}>
        <View style={styles.modalOverlay}>
          <View style={styles.modalContent}>
            <View style={styles.modalHeader}>
              <Text style={styles.modalTitle}>Edit Emergency Contact</Text>
              <TouchableOpacity onPress={() => setModalVisible(false)}>
                <Ionicons name="close-circle" size={24} color={colors.textSecondary} />
              </TouchableOpacity>
            </View>

            <Text style={styles.inputLabel}>CONTACT NAME</Text>
            <TextInput style={styles.input} value={editName} onChangeText={setEditName} placeholder="Family member name" />

            <Text style={styles.inputLabel}>RELATIONSHIP</Text>
            <TextInput style={styles.input} value={editRelation} onChangeText={setEditRelation} placeholder="Parent / Spouse / Sibling" />

            <Text style={styles.inputLabel}>PHONE NUMBER</Text>
            <TextInput style={styles.input} value={editPhone} onChangeText={setEditPhone} keyboardType="phone-pad" />

            <Text style={styles.inputLabel}>FAMILY ADDRESS</Text>
            <TextInput style={styles.input} value={editAddress} onChangeText={setEditAddress} multiline />

            <TouchableOpacity style={styles.saveModalBtn} onPress={handleSave} disabled={saving}>
              {saving ? (
                <ActivityIndicator color="#FFFFFF" />
              ) : (
                <Text style={styles.saveModalBtnText}>Save Emergency Contact</Text>
              )}
            </TouchableOpacity>
          </View>
        </View>
      </Modal>
    </SafeAreaView>
  );
}

const makeStyles = (colors) => StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.background },
  content: { padding: 18, paddingBottom: 130 },
  summaryCard: {
    flexDirection: "row",
    alignItems: "center",
    backgroundColor: colors.card,
    borderRadius: 20,
    padding: 16,
    marginBottom: 16,
    borderWidth: 1,
    borderColor: colors.border,
  },
  avatar: {
    width: 48,
    height: 48,
    borderRadius: 24,
    backgroundColor: colors.danger,
    justifyContent: "center",
    alignItems: "center",
    marginRight: 14,
  },
  summaryText: { flex: 1 },
  name: { fontSize: 16, fontWeight: "800", color: colors.text },
  subText: { fontSize: 13, fontWeight: "600", color: colors.textSecondary, marginTop: 2 },
  editBtn: {
    width: 40,
    height: 40,
    borderRadius: 12,
    backgroundColor: colors.primaryLight,
    justifyContent: "center",
    alignItems: "center",
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