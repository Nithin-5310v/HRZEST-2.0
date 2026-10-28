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

export default function PersonalInfoScreen() {
  const { user } = useAuth();
  const { colors } = useTheme();
  const styles = React.useMemo(() => makeStyles(colors), [colors]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [modalVisible, setModalVisible] = useState(false);
  // The update endpoint replaces every profile field in one call (it
  // mirrors the web form, which submits the whole section at once), so
  // the raw fetch response is kept here and merged with this screen's own
  // edits at save time -- otherwise saving gender/dob/blood group here
  // would silently null out phone/address/bank details/etc.
  const [rawProfile, setRawProfile] = useState({});

  // marital_status, nationality and father_name aren't tracked anywhere in
  // this database (no such columns on the employees table), so they've
  // been removed rather than shown as invented "Single / Indian / Not
  // Specified" defaults.
  const [profile, setProfile] = useState({
    employeeId: user?.employeeId || user?.employee_id || "",
    fullName: user?.name || "",
    gender: user?.gender || "Not Specified",
    dob: user?.dob || "Not Specified",
    bloodGroup: user?.blood_group || "Not Specified",
  });

  const [editGender, setEditGender] = useState(profile.gender);
  const [editDob, setEditDob] = useState(profile.dob);
  const [editBloodGroup, setEditBloodGroup] = useState(profile.bloodGroup);

  useEffect(() => {
    fetchEmployeeProfile()
      .then((res) => {
        if (res?.data?.ok && res?.data?.profile) {
          const p = res.data.profile;
          setRawProfile(p);
          const updated = {
            employeeId: p.employee_id || user?.employeeId || "",
            fullName: p.name || user?.name || "",
            gender: p.gender || "Not Specified",
            dob: p.dob || "Not Specified",
            bloodGroup: p.blood_group || "Not Specified",
          };
          setProfile(updated);
          setEditGender(updated.gender);
          setEditDob(updated.dob);
          setEditBloodGroup(updated.bloodGroup);
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
        gender: editGender,
        dob: editDob,
        blood_group: editBloodGroup,
      });
      if (res?.data?.ok) {
        setProfile((prev) => ({ ...prev, gender: editGender, dob: editDob, bloodGroup: editBloodGroup }));
        setRawProfile((prev) => ({ ...prev, gender: editGender, dob: editDob, blood_group: editBloodGroup }));
        setModalVisible(false);
        Alert.alert("Saved", "Personal information updated.");
      } else {
        Alert.alert("Save Failed", res?.data?.msg || "Could not update personal information.");
      }
    } catch (e) {
      Alert.alert("Save Failed", e?.response?.data?.msg || "Could not update personal information.");
    }
    setSaving(false);
  };

  return (
    <SafeAreaView style={styles.container}>
      <ProfileHeader title="Personal Information" showBack />

      {loading ? (
        <View style={{ flex: 1, justifyContent: "center", alignItems: "center" }}>
          <ActivityIndicator size="large" color={colors.primary} />
        </View>
      ) : (
        <ScrollView showsVerticalScrollIndicator={false} contentContainerStyle={styles.content}>
          {/* Summary Card */}
          <View style={styles.summaryCard}>
            <View style={styles.avatar}>
              <Text style={{ fontSize: 18, fontWeight: "900", color: "#FFFFFF" }}>
                {profile.fullName.charAt(0).toUpperCase()}
              </Text>
            </View>

            <View style={styles.summaryText}>
              <Text style={styles.name}>{profile.fullName}</Text>
              <Text style={styles.idText}>ID • {profile.employeeId}</Text>
            </View>

            <TouchableOpacity style={styles.editBtn} onPress={() => setModalVisible(true)}>
              <Ionicons name="create-outline" size={20} color={colors.primary} />
            </TouchableOpacity>
          </View>

          {/* Details */}
          <DetailCard icon="id-card-outline" label="Employee ID" value={profile.employeeId} />
          <DetailCard icon="person-outline" label="Full Name" value={profile.fullName} />
          <DetailCard icon="male-female-outline" label="Gender" value={profile.gender} />
          <DetailCard icon="calendar-outline" label="Date of Birth" value={profile.dob} />
          <DetailCard icon="water-outline" label="Blood Group" value={profile.bloodGroup} />
        </ScrollView>
      )}

      {/* Edit Personal Info Modal */}
      <Modal visible={modalVisible} transparent animationType="slide" onRequestClose={() => setModalVisible(false)}>
        <View style={styles.modalOverlay}>
          <View style={styles.modalContent}>
            <View style={styles.modalHeader}>
              <Text style={styles.modalTitle}>Edit Personal Info</Text>
              <TouchableOpacity onPress={() => setModalVisible(false)}>
                <Ionicons name="close-circle" size={24} color={colors.textSecondary} />
              </TouchableOpacity>
            </View>

            <Text style={styles.inputLabel}>GENDER</Text>
            <TextInput style={styles.input} value={editGender} onChangeText={setEditGender} placeholder="Male / Female / Other" />

            <Text style={styles.inputLabel}>DATE OF BIRTH</Text>
            <TextInput style={styles.input} value={editDob} onChangeText={setEditDob} placeholder="YYYY-MM-DD" />

            <Text style={styles.inputLabel}>BLOOD GROUP</Text>
            <TextInput style={styles.input} value={editBloodGroup} onChangeText={setEditBloodGroup} placeholder="e.g. O+" />

            <TouchableOpacity style={styles.saveModalBtn} onPress={handleSave} disabled={saving}>
              {saving ? (
                <ActivityIndicator color="#FFFFFF" />
              ) : (
                <Text style={styles.saveModalBtnText}>Save Personal Details</Text>
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
  name: { fontSize: 14, fontWeight: "800", color: colors.text },
  idText: { fontSize: 12, fontWeight: "600", color: colors.textSecondary, marginTop: 2 },
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