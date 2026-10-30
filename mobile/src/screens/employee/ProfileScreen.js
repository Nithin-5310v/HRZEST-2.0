import React, { useState, useCallback } from "react";
import {
  View,
  Text,
  ScrollView,
  StyleSheet,
  TouchableOpacity,
  RefreshControl,
  Modal,
  TextInput,
  Alert,
  ActivityIndicator,
} from "react-native";
import { LinearGradient } from "expo-linear-gradient";
import { Ionicons } from "@expo/vector-icons";
import { useNavigation, useFocusEffect } from "@react-navigation/native";
import { useAuth } from "../../store/AuthContext";
import { useTheme } from "../../store/ThemeContext";
import * as ImagePicker from "expo-image-picker";
import { fetchEmployeeProfile, updateMyProfile, uploadEmployeePhoto, getPhotoUrl } from "../../api/client";

import ProfileHeader from "../../components/profile/ProfileHeader";
import ProfileImageCard from "../../components/profile/ProfileImageCard";
import ProfileCompletionCard from "../../components/profile/ProfileCompletionCard";
import ProfileMenuCard from "../../components/profile/ProfileMenuCard";
import DigitalIdCardModal from "../../components/employee/DigitalIdCardModal";

export default function ProfileScreen() {
  const navigation = useNavigation();
  const { user, updateUser } = useAuth();
  const { colors } = useTheme();
  const styles = React.useMemo(() => makeStyles(colors), [colors]);

  const [refreshing, setRefreshing] = useState(false);
  const [loading, setLoading] = useState(false);
  const [editModalVisible, setEditModalVisible] = useState(false);
  const [showIdCard, setShowIdCard] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const calculateCompletion = (p) => {
    if (!p) return { completionPct: 0, completedCount: 0, totalCount: 22 };
    const fields = [
      p.name || p.fullName,
      p.email || p.workEmail,
      p.employee_id || p.employeeId,
      p.role || p.designation,
      p.department,
      p.phone,
      p.gender,
      p.dob,
      p.blood_group || p.bloodGroup,
      p.address,
      p.city,
      p.state,
      p.pincode,
      p.emergency_contact_name || p.emergencyContact,
      p.emergency_contact_phone || p.emergencyPhone || p.emergency_contact,
      p.bank_name || p.bankName,
      p.bank_account || p.bankAccount || p.accountNumber,
      p.bank_ifsc || p.ifscCode,
      p.pan_number || p.panNumber,
      p.aadhar_number || p.aadharNumber,
      p.about_me || p.aboutMe,
      p.photo_url || p.photo,
    ];
    const completedCount = fields.filter((f) => {
      if (!f) return false;
      const str = String(f).trim();
      return (
        str !== "" &&
        str !== "Not Provided" &&
        str !== "Not Specified" &&
        str !== "null" &&
        str !== "undefined"
      );
    }).length;
    const totalCount = fields.length;
    const completionPct = Math.round((completedCount / totalCount) * 100);
    return { completionPct, completedCount, totalCount };
  };

  const initialStats = calculateCompletion(user);

  // Profile Form States
  const [profileData, setProfileData] = useState({
    name: user?.name || user?.employeeId || "",
    employeeId: user?.employeeId || user?.employee_id || "",
    designation: user?.role || "",
    department: user?.department || "",
    email: user?.email || "",
    phone: user?.phone || "Not Provided",
    address: user?.address || "Not Provided",
    emergencyContact: user?.emergency_contact || "Not Provided",
    completion: initialStats.completionPct,
    completedSections: initialStats.completedCount,
    totalSections: initialStats.totalCount,
    photo: user?.photo || null,
  });

  const [editName, setEditName] = useState(profileData.name);
  const [editEmail, setEditEmail] = useState(profileData.email);
  const [editPhone, setEditPhone] = useState(profileData.phone);
  const [editAddress, setEditAddress] = useState(profileData.address);
  const [editEmergency, setEditEmergency] = useState(profileData.emergencyContact);

  const loadProfile = async () => {
    try {
      const res = await fetchEmployeeProfile();
      if (res?.data?.ok && res?.data?.profile) {
        // `role` is excluded from the spread below: the backend returns it
        // display-cased (e.g. "Employee"), which would silently overwrite
        // AuthContext's `user.role` -- the lowercase "employee"/"admin"
        // discriminator App.js's RootNavigator matches on -- and bounce the
        // user to the login screen the instant this profile loads (looks
        // exactly like an unexpected logout, even though the session is
        // still valid). `designation` below already carries the display role.
        const { role: _displayRole, ...p } = res.data.profile;

        const stats = calculateCompletion(res.data.profile);

        const updated = {
          ...p,
          name: p.name || user?.name || user?.employeeId || "",
          employeeId: p.employee_id || user?.employeeId || "",
          designation: _displayRole || user?.role || "",
          department: p.department || user?.department || "",
          email: p.email || user?.email || "",
          phone: p.phone || user?.phone || "Not Provided",
          address: p.address || user?.address || "Not Provided",
          emergencyContact: p.emergency_contact_phone || user?.emergency_contact || "Not Provided",
          company: p.company_name || user?.company || "",
          completion: stats.completionPct,
          completedSections: stats.completedCount,
          totalSections: stats.totalCount,
          photo: p.employee_id ? getPhotoUrl(p.employee_id) : null,
        };
        setProfileData(updated);
        setEditName(updated.name);
        setEditEmail(updated.email);
        setEditPhone(updated.phone);
        setEditAddress(updated.address);
        setEditEmergency(updated.emergencyContact);
        if (updateUser) updateUser(updated);
      }
    } catch (_) {}
    setRefreshing(false);
    setLoading(false);
  };

  useFocusEffect(
    useCallback(() => {
      loadProfile();
    }, [])
  );

  const handleSaveProfile = async () => {
    // profileData already carries every field from the last fetch (see the
    // `...p` spread in loadProfile above) -- name/email aren't part of
    // what this endpoint accepts (the backend only ever writes the fields
    // below, same as the web portal's own form), so they're edited here
    // for display continuity but not sent.
    setSubmitting(true);
    try {
      const res = await updateMyProfile({
        ...profileData,
        phone: editPhone,
        address: editAddress,
        emergency_contact_phone: editEmergency,
      });
      if (res?.data?.ok) {
        setEditModalVisible(false);
        await loadProfile();
        Alert.alert("Saved", "Profile updated.");
      } else {
        Alert.alert("Save Failed", res?.data?.msg || "Could not update your profile.");
      }
    } catch (e) {
      Alert.alert("Save Failed", e?.response?.data?.msg || "Could not update your profile.");
    }
    setSubmitting(false);
  };

  const handleChangePhoto = async () => {
    try {
      const perm = await ImagePicker.requestMediaLibraryPermissionsAsync();
      if (!perm.granted) {
        Alert.alert("Permission Required", "Camera roll permission is required to select a face photo.");
        return;
      }
      const result = await ImagePicker.launchImageLibraryAsync({
        mediaTypes: ImagePicker.MediaTypeOptions.Images,
        allowsEditing: true,
        aspect: [1, 1],
        quality: 0.85,
      });

      if (!result.canceled && result.assets?.[0]?.uri) {
        const photoUri = result.assets[0].uri;
        setSubmitting(true);
        const formData = new FormData();
        formData.append("photo", {
          uri: photoUri,
          name: `${profileData.employeeId || "photo"}.jpg`,
          type: "image/jpeg",
        });

        let res;
        try {
          res = await uploadEmployeePhoto(formData);
        } catch (e) {
          res = e?.response;
        }
        setSubmitting(false);
        if (res?.data?.ok) {
          Alert.alert("Face Photo Registered 🎉", "Your official face photo has been registered successfully for Face Verification!");
          setProfileData((prev) => ({ ...prev, photo: photoUri }));
        } else {
          Alert.alert("Upload Failed", res?.data?.msg || "Could not upload your photo. Check your connection and try again.");
        }
      }
    } catch (_) {
      setSubmitting(false);
    }
  };

  const menuItems = [
    {
      title: "My ID Card",
      subtitle: "View & Save Employee ID Card",
      icon: "id-card-outline",
      color: "#0F766E",
      background: "#F0FDFA",
      screen: "IdCard",
    },
    {
      title: "Personal Information",
      subtitle: "Name, DOB, Gender & Identity",
      icon: "person-outline",
      color: "#0B6E8E",
      background: "#E3F2F5",
      screen: "PersonalInfo",
    },
    {
      title: "Work Information",
      subtitle: "Designation & Department",
      icon: "briefcase-outline",
      color: "#7C3AED",
      background: "#F5F3FF",
      screen: "WorkInfo",
    },
    {
      title: "Contact Details",
      subtitle: "Email, Phone & Address",
      icon: "call-outline",
      color: "#059669",
      background: "#ECFDF5",
      screen: "Contact",
    },
    {
      title: "Emergency Contact",
      subtitle: "Family Contact Details",
      icon: "people-outline",
      color: "#EA580C",
      background: "#FFF7ED",
      screen: "EmergencyContact",
    },
    {
      title: "Education",
      subtitle: "Academic Qualifications",
      icon: "school-outline",
      color: "#DC2626",
      background: "#FEF2F2",
      screen: "Education",
    },
    {
      title: "Experience",
      subtitle: "Previous Employment",
      icon: "layers-outline",
      color: "#0891B2",
      background: "#ECFEFF",
      screen: "Experience",
    },
    {
      title: "Documents",
      subtitle: "Certificates & Proofs",
      icon: "document-text-outline",
      color: "#0B6E8E",
      background: "#EEF2FF",
      screen: "Documents",
    },
    {
      title: "Bank Details",
      subtitle: "Salary Account Information",
      icon: "card-outline",
      color: "#16A34A",
      background: "#F0FDF4",
      screen: "BankDetails",
    },
  ];

  return (
    <LinearGradient colors={colors.screenGradient} style={styles.container}>
      <ProfileHeader title="My Profile" showBack={false} />

      <ScrollView
        showsVerticalScrollIndicator={false}
        contentContainerStyle={styles.content}
        refreshControl={
          <RefreshControl
            refreshing={refreshing}
            onRefresh={() => {
              setRefreshing(true);
              loadProfile();
            }}
            colors={[colors.primary]}
          />
        }
      >
        <DigitalIdCardModal
          visible={showIdCard}
          employee={profileData}
          onClose={() => setShowIdCard(false)}
        />

        <ProfileImageCard
          image={profileData.photo}
          employeeName={profileData.name}
          employeeId={profileData.employeeId}
          designation={profileData.designation}
          department={profileData.department}
          onEditProfile={() => setEditModalVisible(true)}
          onViewIdCard={() => setShowIdCard(true)}
          onChangePhoto={handleChangePhoto}
        />

        <ProfileCompletionCard
          percentage={profileData.completion}
          completed={profileData.completedSections}
          total={profileData.totalSections}
        />

        <View style={styles.section}>
          <Text style={styles.sectionTitle}>Employee Contact Details</Text>

          <View style={styles.quickCard}>
            <View style={styles.quickRow}>
              <Ionicons name="mail-outline" size={18} color={colors.primary} />
              <Text style={styles.quickText}>{profileData.email}</Text>
            </View>

            <View style={styles.divider} />

            <View style={styles.quickRow}>
              <Ionicons name="call-outline" size={18} color={colors.primary} />
              <Text style={styles.quickText}>{profileData.phone}</Text>
            </View>

            <View style={styles.divider} />

            <View style={styles.quickRow}>
              <Ionicons name="location-outline" size={18} color={colors.primary} />
              <Text style={styles.quickText}>{profileData.address}</Text>
            </View>
          </View>
        </View>

        <View style={styles.section}>
          <Text style={styles.sectionTitle}>Profile Sections</Text>

          {menuItems.map((item) => (
            <ProfileMenuCard
              key={item.title}
              title={item.title}
              subtitle={item.subtitle}
              icon={item.icon}
              color={item.color}
              background={item.background}
              onPress={() => {
                try {
                  navigation.navigate(item.screen);
                } catch (_) {
                  Alert.alert(item.title, `${item.subtitle} details.`);
                }
              }}
            />
          ))}
        </View>

        <View style={{ height: 40 }} />
      </ScrollView>

      {/* Edit Profile Modal */}
      <Modal visible={editModalVisible} transparent animationType="slide" onRequestClose={() => setEditModalVisible(false)}>
        <View style={{ flex: 1, backgroundColor: "rgba(15, 23, 42, 0.75)", justifyContent: "center", padding: 20 }}>
          <View style={{ backgroundColor: colors.card, borderRadius: 24, padding: 24 }}>
            <View style={{ flexDirection: "row", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
              <Text style={{ fontSize: 15, fontWeight: "800", color: colors.text }}>Edit Profile Details</Text>
              <TouchableOpacity onPress={() => setEditModalVisible(false)}>
                <Ionicons name="close-circle" size={24} color={colors.textSecondary} />
              </TouchableOpacity>
            </View>

            <Text style={{ fontSize: 11, fontWeight: "700", color: colors.textSecondary, marginTop: 8 }}>FULL NAME</Text>
            <TextInput
              style={styles.modalInput}
              value={editName}
              onChangeText={setEditName}
            />

            <Text style={{ fontSize: 11, fontWeight: "700", color: colors.textSecondary, marginTop: 10 }}>EMAIL ADDRESS</Text>
            <TextInput
              style={styles.modalInput}
              value={editEmail}
              onChangeText={setEditEmail}
              keyboardType="email-address"
            />

            <Text style={{ fontSize: 11, fontWeight: "700", color: colors.textSecondary, marginTop: 10 }}>PHONE NUMBER</Text>
            <TextInput
              style={styles.modalInput}
              value={editPhone}
              onChangeText={setEditPhone}
              keyboardType="phone-pad"
            />

            <Text style={{ fontSize: 11, fontWeight: "700", color: colors.textSecondary, marginTop: 10 }}>ADDRESS</Text>
            <TextInput
              style={styles.modalInput}
              value={editAddress}
              onChangeText={setEditAddress}
            />

            <TouchableOpacity
              style={styles.saveBtn}
              onPress={handleSaveProfile}
              disabled={submitting}
            >
              {submitting ? (
                <ActivityIndicator color="#FFFFFF" />
              ) : (
                <Text style={styles.saveBtnText}>Save Profile Changes</Text>
              )}
            </TouchableOpacity>
          </View>
        </View>
      </Modal>
    </LinearGradient>
  );
}

const makeStyles = (colors) => StyleSheet.create({
  container: {
    flex: 1,
  },
  content: {
    paddingHorizontal: 18,
    paddingTop: 6,
    paddingBottom: 120,
  },
  section: {
    marginTop: 18,
  },
  sectionTitle: {
    fontSize: 14,
    fontWeight: "800",
    color: colors.text,
    marginBottom: 12,
    letterSpacing: -0.2,
  },
  quickCard: {
    backgroundColor: colors.card,
    borderRadius: 20,
    padding: 18,
    borderWidth: 1,
    borderColor: colors.border,
    shadowColor: "#000",
    shadowOpacity: 0.04,
    shadowRadius: 10,
    shadowOffset: {
      width: 0,
      height: 5,
    },
    elevation: 2,
  },
  quickRow: {
    flexDirection: "row",
    alignItems: "center",
  },
  quickText: {
    marginLeft: 12,
    flex: 1,
    fontSize: 14,
    fontWeight: "600",
    color: colors.textSecondary,
  },
  divider: {
    height: 1,
    backgroundColor: colors.border,
    marginVertical: 12,
  },
  modalInput: {
    backgroundColor: colors.background,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: 10,
    padding: 10,
    marginTop: 4,
    fontSize: 14,
    color: colors.text,
  },
  saveBtn: {
    backgroundColor: colors.primary,
    borderRadius: 14,
    paddingVertical: 14,
    alignItems: "center",
    marginTop: 20,
  },
  saveBtnText: {
    color: "#FFFFFF",
    fontWeight: "700",
    fontSize: 15,
  },
});
