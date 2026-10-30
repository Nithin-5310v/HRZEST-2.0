import React from "react";

import {
  Modal,
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
  ScrollView,
} from "react-native";

import { Ionicons } from "@expo/vector-icons";

import COMPOFF_THEME from "../../constants/compOffTheme";
import CompOffStatusChip from "./CompOffStatusChip";

export default function CompOffBottomSheet({
  visible,
  record,
  onClose,
  onApprove,
  onReject,
  actionLoading,
}) {
  if (!record) return null;
  const isPending = record.status === "Pending";

  return (
    <Modal
      visible={visible}
      transparent
      animationType="slide"
      onRequestClose={onClose}
    >
      <View style={styles.overlay}>

        <View style={styles.sheet}>

          {/* Handle */}

          <View style={styles.handle} />

          {/* Header */}

          <View style={styles.header}>

            <View>

              <Text style={styles.title}>
                Overtime Details
              </Text>

              <Text style={styles.subtitle}>
                Employee Record
              </Text>

            </View>

            <TouchableOpacity onPress={onClose}>

              <Ionicons
                name="close"
                size={24}
                color={COMPOFF_THEME.colors.textPrimary}
              />

            </TouchableOpacity>

          </View>

          <ScrollView
            showsVerticalScrollIndicator={false}
          >

            {/* Employee */}

            <View style={styles.employeeCard}>

              <View style={styles.avatar}>

                <Ionicons
                  name="person"
                  size={28}
                  color={COMPOFF_THEME.colors.primary}
                />

              </View>

              <View style={{ flex: 1 }}>

                <Text style={styles.employeeName}>
                  {record.name}
                </Text>

                <Text style={styles.department}>
                  {record.department}
                </Text>

              </View>

              <CompOffStatusChip
                status={record.status}
              />

            </View>

            {/* Information */}

            <View style={styles.infoCard}>

              <InfoRow
                icon="calendar-outline"
                label="Date"
                value={record.date}
              />

              <InfoRow
                icon="time-outline"
                label="Working Hours"
                value={`${record.shift_end || "--"} - ${record.actual_logout || "--"}`}
              />

              <InfoRow
                icon="hourglass-outline"
                label="OT Hours"
                value={`${record.hours} Hours`}
              />

              <InfoRow
                icon="cash-outline"
                label="OT Pay"
                value={`₹${record.ot_pay}`}
              />

            </View>

            {/* Notes */}

            {!!record.notes && (
              <View style={styles.reasonCard}>

                <Text style={styles.sectionTitle}>
                  Notes
                </Text>

                <Text style={styles.reason}>
                  {record.notes}
                </Text>

              </View>
            )}

          </ScrollView>

          {/* Buttons */}

          <View style={styles.footer}>

            <TouchableOpacity
              style={isPending ? styles.secondaryButton : styles.wideSecondaryButton}
              onPress={onClose}
              disabled={actionLoading}
            >

              <Text style={styles.secondaryText}>
                Close
              </Text>

            </TouchableOpacity>

            {isPending && (
              <>
                <TouchableOpacity
                  style={styles.rejectButton}
                  onPress={onReject}
                  disabled={actionLoading}
                >
                  <Text style={styles.rejectText}>
                    Reject
                  </Text>
                </TouchableOpacity>

                <TouchableOpacity
                  style={styles.primaryButton}
                  onPress={onApprove}
                  disabled={actionLoading}
                >
                  <Ionicons
                    name="checkmark-circle-outline"
                    size={18}
                    color="#FFFFFF"
                  />
                  <Text style={styles.primaryText}>
                    Approve
                  </Text>
                </TouchableOpacity>
              </>
            )}

          </View>

        </View>

      </View>

    </Modal>
  );
}

function InfoRow({
  icon,
  label,
  value,
}) {
  return (
    <View style={styles.row}>

      <View style={styles.left}>

        <Ionicons
          name={icon}
          size={20}
          color={COMPOFF_THEME.colors.primary}
        />

        <Text style={styles.label}>
          {label}
        </Text>

      </View>

      <Text style={styles.value}>
        {value}
      </Text>

    </View>
  );
}

const styles = StyleSheet.create({

  overlay: {
    flex: 1,
    justifyContent: "flex-end",
    backgroundColor: "rgba(15,23,42,0.35)",
  },

  sheet: {
    backgroundColor: "#FFFFFF",

    borderTopLeftRadius: 30,
    borderTopRightRadius: 30,

    padding: 22,

    maxHeight: "88%",
  },

  handle: {
    width: 60,
    height: 5,
    borderRadius: 10,
    backgroundColor: "#C3CFDD",
    alignSelf: "center",
    marginBottom: 18,
  },

  header: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    marginBottom: 22,
  },

  title: {
    fontSize: 22,
    fontWeight: "800",
    color: COMPOFF_THEME.colors.textPrimary,
  },

  subtitle: {
    marginTop: 4,
    color: COMPOFF_THEME.colors.textMuted,
    fontSize: 13,
  },

  employeeCard: {
    flexDirection: "row",
    alignItems: "center",
    backgroundColor: "#F5F8FC",
    borderRadius: 20,
    padding: 18,
    marginBottom: 20,
  },

  avatar: {
    width: 58,
    height: 58,
    borderRadius: 18,
    backgroundColor: COMPOFF_THEME.colors.primaryLight,
    justifyContent: "center",
    alignItems: "center",
    marginRight: 14,
  },

  employeeName: {
    fontSize: 18,
    fontWeight: "800",
    color: COMPOFF_THEME.colors.textPrimary,
  },

  department: {
    marginTop: 4,
    color: COMPOFF_THEME.colors.textMuted,
    fontSize: 13,
  },

  infoCard: {
    backgroundColor: "#FFFFFF",
    borderRadius: 20,
    borderWidth: 1,
    borderColor: COMPOFF_THEME.colors.border,
    padding: 18,
    marginBottom: 20,
  },

  row: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    paddingVertical: 14,
    borderBottomWidth: 1,
    borderBottomColor: "#E9EFF6",
  },

  left: {
    flexDirection: "row",
    alignItems: "center",
  },

  label: {
    marginLeft: 10,
    fontSize: 14,
    fontWeight: "600",
    color: COMPOFF_THEME.colors.textSecondary,
  },

  value: {
    fontSize: 14,
    fontWeight: "700",
    color: COMPOFF_THEME.colors.textPrimary,
  },

  reasonCard: {
    backgroundColor: "#F5F8FC",
    borderRadius: 20,
    padding: 18,
    marginBottom: 24,
  },

  sectionTitle: {
    fontSize: 16,
    fontWeight: "800",
    color: COMPOFF_THEME.colors.textPrimary,
    marginBottom: 10,
  },

  reason: {
    fontSize: 14,
    lineHeight: 22,
    color: COMPOFF_THEME.colors.textSecondary,
  },

  footer: {
    flexDirection: "row",
    justifyContent: "space-between",
    gap: 10,
    marginTop: 10,
  },

  secondaryButton: {
    flex: 1,
    height: 54,
    borderRadius: 16,
    backgroundColor: "#EDF2F8",
    justifyContent: "center",
    alignItems: "center",
  },

  wideSecondaryButton: {
    flex: 1,
    height: 54,
    borderRadius: 16,
    backgroundColor: "#EDF2F8",
    justifyContent: "center",
    alignItems: "center",
  },

  secondaryText: {
    fontWeight: "700",
    color: COMPOFF_THEME.colors.textPrimary,
  },

  rejectButton: {
    flex: 1,
    height: 54,
    borderRadius: 16,
    backgroundColor: "#FEE2E2",
    justifyContent: "center",
    alignItems: "center",
  },

  rejectText: {
    fontWeight: "700",
    color: "#DC2626",
  },

  primaryButton: {
    flex: 1.4,
    height: 54,
    borderRadius: 16,
    backgroundColor: COMPOFF_THEME.colors.primary,
    flexDirection: "row",
    justifyContent: "center",
    alignItems: "center",
  },

  primaryText: {
    marginLeft: 8,
    color: "#FFFFFF",
    fontWeight: "800",
    fontSize: 15,
  },

});