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
import { fetchEmployeeProfile, updateMyBankDetails } from "../../api/client";
import ProfileHeader from "../../components/profile/ProfileHeader";
import DetailCard from "../../components/profile/DetailCard";

export default function BankDetailsScreen() {
  const { user } = useAuth();
  const { colors } = useTheme();
  const styles = React.useMemo(() => makeStyles(colors), [colors]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [modalVisible, setModalVisible] = useState(false);
  // The bank-details endpoint replaces aadhar/pan/bank_name/bank_account/
  // bank_ifsc/uan_number together in one call -- uan_number has no field
  // in this screen's UI, so it's round-tripped from the last fetch rather
  // than silently nulled out on every save.
  const [rawProfile, setRawProfile] = useState({});

  const [bankInfo, setBankInfo] = useState({
    accountName: user?.name || "",
    bankName: user?.bank_name || "Not Provided",
    accountNumber: user?.bank_account || "Not Provided",
    ifscCode: user?.bank_ifsc || "Not Provided",
    panNumber: user?.pan_number || "Not Provided",
    aadharNumber: user?.aadhar_number || "Not Provided",
  });

  const [editBankName, setEditBankName] = useState(bankInfo.bankName);
  const [editAccountName, setEditAccountName] = useState(bankInfo.accountName);
  const [editAccountNumber, setEditAccountNumber] = useState(bankInfo.accountNumber);
  const [editIfscCode, setEditIfscCode] = useState(bankInfo.ifscCode);
  const [editPanNumber, setEditPanNumber] = useState(bankInfo.panNumber);
  const [editAadharNumber, setEditAadharNumber] = useState(bankInfo.aadharNumber);

  useEffect(() => {
    fetchEmployeeProfile()
      .then((res) => {
        if (res?.data?.ok && res?.data?.profile) {
          const p = res.data.profile;
          setRawProfile(p);
          const updated = {
            accountName: p.name || user?.name || "",
            bankName: p.bank_name || user?.bank_name || "Not Provided",
            accountNumber: p.bank_account || user?.bank_account || "Not Provided",
            ifscCode: p.bank_ifsc || user?.bank_ifsc || "Not Provided",
            panNumber: p.pan_number || user?.pan_number || "Not Provided",
            aadharNumber: p.aadhar_number || user?.aadhar_number || "Not Provided",
          };
          setBankInfo(updated);
          setEditBankName(updated.bankName);
          setEditAccountName(updated.accountName);
          setEditAccountNumber(updated.accountNumber);
          setEditIfscCode(updated.ifscCode);
          setEditPanNumber(updated.panNumber);
          setEditAadharNumber(updated.aadharNumber);
        }
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  const handleSave = async () => {
    setSaving(true);
    try {
      const res = await updateMyBankDetails({
        aadhar_number: editAadharNumber,
        pan_number: editPanNumber,
        bank_name: editBankName,
        bank_account: editAccountNumber,
        bank_ifsc: editIfscCode,
        uan_number: rawProfile.uan_number,
      });
      if (res?.data?.ok) {
        setBankInfo((prev) => ({
          ...prev, bankName: editBankName, accountNumber: editAccountNumber,
          ifscCode: editIfscCode, panNumber: editPanNumber, aadharNumber: editAadharNumber,
        }));
        setModalVisible(false);
        Alert.alert("Saved", "Bank and identity details updated.");
      } else {
        Alert.alert("Save Failed", res?.data?.msg || "Could not update bank details.");
      }
    } catch (e) {
      Alert.alert("Save Failed", e?.response?.data?.msg || "Could not update bank details.");
    }
    setSaving(false);
  };

  return (
    <SafeAreaView style={styles.container}>
      <ProfileHeader title="Bank Details" showBack />

      {loading ? (
        <View style={{ flex: 1, justifyContent: "center", alignItems: "center" }}>
          <ActivityIndicator size="large" color={colors.primary} />
        </View>
      ) : (
        <ScrollView showsVerticalScrollIndicator={false} contentContainerStyle={styles.content}>
          {/* Summary Card */}
          <View style={styles.summaryCard}>
            <View style={styles.avatar}>
              <Ionicons name="card" size={24} color="#FFFFFF" />
            </View>

            <View style={styles.summaryText}>
              <Text style={styles.name}>{bankInfo.bankName}</Text>
              <Text style={styles.subText}>{bankInfo.accountName}</Text>
            </View>

            <TouchableOpacity style={styles.editBtn} onPress={() => setModalVisible(true)}>
              <Ionicons name="create-outline" size={20} color={colors.primary} />
            </TouchableOpacity>
          </View>

          {/* Details */}
          <DetailCard icon="business-outline" label="Bank Name" value={bankInfo.bankName} />
          <DetailCard icon="person-outline" label="Account Holder" value={bankInfo.accountName} />
          <DetailCard icon="card-outline" label="Account Number" value={bankInfo.accountNumber} />
          <DetailCard icon="qr-code-outline" label="IFSC Code" value={bankInfo.ifscCode} />
          <DetailCard icon="document-text-outline" label="PAN Number" value={bankInfo.panNumber} />
          <DetailCard icon="id-card-outline" label="Aadhar Number" value={bankInfo.aadharNumber} />
        </ScrollView>
      )}

      {/* Edit Bank Details Modal */}
      <Modal visible={modalVisible} transparent animationType="slide" onRequestClose={() => setModalVisible(false)}>
        <View style={styles.modalOverlay}>
          <View style={styles.modalContent}>
            <View style={styles.modalHeader}>
              <Text style={styles.modalTitle}>Edit Bank & Identity Details</Text>
              <TouchableOpacity onPress={() => setModalVisible(false)}>
                <Ionicons name="close-circle" size={24} color={colors.textSecondary} />
              </TouchableOpacity>
            </View>

            <Text style={styles.inputLabel}>BANK NAME</Text>
            <TextInput style={styles.input} value={editBankName} onChangeText={setEditBankName} placeholder="e.g. HDFC Bank" />

            <Text style={styles.inputLabel}>ACCOUNT HOLDER NAME</Text>
            <TextInput style={styles.input} value={editAccountName} onChangeText={setEditAccountName} />

            <Text style={styles.inputLabel}>ACCOUNT NUMBER</Text>
            <TextInput style={styles.input} value={editAccountNumber} onChangeText={setEditAccountNumber} keyboardType="number-pad" />

            <Text style={styles.inputLabel}>IFSC CODE</Text>
            <TextInput style={styles.input} value={editIfscCode} onChangeText={setEditIfscCode} autoCapitalize="characters" />

            <Text style={styles.inputLabel}>PAN NUMBER</Text>
            <TextInput style={styles.input} value={editPanNumber} onChangeText={setEditPanNumber} autoCapitalize="characters" />

            <Text style={styles.inputLabel}>AADHAR NUMBER</Text>
            <TextInput style={styles.input} value={editAadharNumber} onChangeText={setEditAadharNumber} keyboardType="number-pad" />

            <TouchableOpacity style={styles.saveModalBtn} onPress={handleSave} disabled={saving}>
              {saving ? (
                <ActivityIndicator color="#FFFFFF" />
              ) : (
                <Text style={styles.saveModalBtnText}>Save Bank Details</Text>
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
    backgroundColor: "#16A34A",
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