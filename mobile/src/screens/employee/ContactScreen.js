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

export default function ContactScreen() {
  const { user } = useAuth();
  const { colors } = useTheme();
  const styles = React.useMemo(() => makeStyles(colors), [colors]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [modalVisible, setModalVisible] = useState(false);
  // See PersonalInfoScreen.js's rawProfile comment -- the update endpoint
  // replaces the whole profile row, so unedited fields have to be
  // round-tripped from the last fetch rather than omitted.
  const [rawProfile, setRawProfile] = useState({});

  const [contactInfo, setContactInfo] = useState({
    workEmail: user?.email || "",
    phone: user?.phone || "Not Provided",
    address: user?.address || "Not Provided",
    city: user?.city || "Not Provided",
    state: user?.state || "Not Provided",
    pincode: user?.pincode || "Not Provided",
    emergencyContact: user?.emergency_contact_name || "Not Provided",
    emergencyPhone: user?.emergency_contact_phone || "Not Provided",
  });

  const [editPhone, setEditPhone] = useState(contactInfo.phone);
  const [editAddress, setEditAddress] = useState(contactInfo.address);
  const [editCity, setEditCity] = useState(contactInfo.city);
  const [editState, setEditState] = useState(contactInfo.state);
  const [editPincode, setEditPincode] = useState(contactInfo.pincode);

  useEffect(() => {
    fetchEmployeeProfile()
      .then((res) => {
        if (res?.data?.ok && res?.data?.profile) {
          const p = res.data.profile;
          setRawProfile(p);
          const updated = {
            workEmail: p.email || user?.email || "",
            phone: p.phone || user?.phone || "Not Provided",
            address: p.address || user?.address || "Not Provided",
            city: p.city || user?.city || "Not Provided",
            state: p.state || user?.state || "Not Provided",
            pincode: p.pincode || user?.pincode || "Not Provided",
            emergencyContact: p.emergency_contact_name || user?.emergency_contact_name || "Not Provided",
            emergencyPhone: p.emergency_contact_phone || user?.emergency_contact_phone || "Not Provided",
          };
          setContactInfo(updated);
          setEditPhone(updated.phone);
          setEditAddress(updated.address);
          setEditCity(updated.city);
          setEditState(updated.state);
          setEditPincode(updated.pincode);
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
        phone: editPhone,
        address: editAddress,
        city: editCity,
        state: editState,
        pincode: editPincode,
      });
      if (res?.data?.ok) {
        setContactInfo((prev) => ({
          ...prev, phone: editPhone, address: editAddress, city: editCity, state: editState, pincode: editPincode,
        }));
        setRawProfile((prev) => ({
          ...prev, phone: editPhone, address: editAddress, city: editCity, state: editState, pincode: editPincode,
        }));
        setModalVisible(false);
        Alert.alert("Saved", "Contact details updated.");
      } else {
        Alert.alert("Save Failed", res?.data?.msg || "Could not update contact details.");
      }
    } catch (e) {
      Alert.alert("Save Failed", e?.response?.data?.msg || "Could not update contact details.");
    }
    setSaving(false);
  };

  return (
    <SafeAreaView style={styles.container}>
      <ProfileHeader title="Contact Information" showBack />

      {loading ? (
        <View style={{ flex: 1, justifyContent: "center", alignItems: "center" }}>
          <ActivityIndicator size="large" color={colors.primary} />
        </View>
      ) : (
        <ScrollView showsVerticalScrollIndicator={false} contentContainerStyle={styles.content}>
          {/* Summary Card */}
          <View style={styles.summaryCard}>
            <View style={styles.avatar}>
              <Ionicons name="call" size={24} color="#FFFFFF" />
            </View>

            <View style={styles.summaryText}>
              <Text style={styles.name}>{contactInfo.workEmail}</Text>
              <Text style={styles.subText}>{contactInfo.phone}</Text>
            </View>

            <TouchableOpacity style={styles.editBtn} onPress={() => setModalVisible(true)}>
              <Ionicons name="create-outline" size={20} color={colors.primary} />
            </TouchableOpacity>
          </View>

          {/* Details */}
          <DetailCard icon="mail-outline" label="Work Email" value={contactInfo.workEmail} />
          <DetailCard icon="call-outline" label="Phone Number" value={contactInfo.phone} />
          <DetailCard icon="location-outline" label="Residential Address" value={contactInfo.address} />
          <DetailCard icon="business-outline" label="City & State" value={`${contactInfo.city}, ${contactInfo.state}`} />
          <DetailCard icon="map-outline" label="Pincode" value={contactInfo.pincode} />
          <DetailCard icon="people-outline" label="Emergency Contact" value={`${contactInfo.emergencyContact} (${contactInfo.emergencyPhone})`} />
        </ScrollView>
      )}

      {/* Edit Contact Modal */}
      <Modal visible={modalVisible} transparent animationType="slide" onRequestClose={() => setModalVisible(false)}>
        <View style={styles.modalOverlay}>
          <View style={styles.modalContent}>
            <View style={styles.modalHeader}>
              <Text style={styles.modalTitle}>Edit Contact Details</Text>
              <TouchableOpacity onPress={() => setModalVisible(false)}>
                <Ionicons name="close-circle" size={24} color={colors.textSecondary} />
              </TouchableOpacity>
            </View>

            <Text style={styles.inputLabel}>PHONE NUMBER</Text>
            <TextInput style={styles.input} value={editPhone} onChangeText={setEditPhone} keyboardType="phone-pad" />

            <Text style={styles.inputLabel}>RESIDENTIAL ADDRESS</Text>
            <TextInput style={styles.input} value={editAddress} onChangeText={setEditAddress} multiline placeholder="House no., Street, Area..." />

            <Text style={styles.inputLabel}>CITY</Text>
            <TextInput style={styles.input} value={editCity} onChangeText={setEditCity} />

            <Text style={styles.inputLabel}>STATE</Text>
            <TextInput style={styles.input} value={editState} onChangeText={setEditState} />

            <Text style={styles.inputLabel}>PINCODE</Text>
            <TextInput style={styles.input} value={editPincode} onChangeText={setEditPincode} keyboardType="number-pad" />

            <TouchableOpacity style={styles.saveModalBtn} onPress={handleSave} disabled={saving}>
              {saving ? (
                <ActivityIndicator color="#FFFFFF" />
              ) : (
                <Text style={styles.saveModalBtnText}>Save Contact Details</Text>
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
    backgroundColor: colors.primary,
    justifyContent: "center",
    alignItems: "center",
    marginRight: 14,
  },
  summaryText: { flex: 1 },
  name: { fontSize: 15, fontWeight: "800", color: colors.text },
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