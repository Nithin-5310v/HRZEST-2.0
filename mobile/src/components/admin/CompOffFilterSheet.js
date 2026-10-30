import React from "react";

import {
  Modal,
  View,
 Text,
  TouchableOpacity,
  StyleSheet,
  ScrollView,
} from "react-native";

import { Ionicons } from "@expo/vector-icons";

import COMPOFF_THEME from "../../constants/compOffTheme";

const STATUS = [
  "All",
  "Approved",
  "Pending",
  "Rejected",
];

const DEPARTMENTS = [
  "All",
  "Engineering",
  "Design",
  "HR",
  "QA",
  "Marketing",
];

export default function CompOffFilterSheet({
  visible,
  selectedStatus,
  selectedDepartment,
  onSelectStatus,
  onSelectDepartment,
  onApply,
  onReset,
  onClose,
}) {
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

            <Text style={styles.title}>
              Filter Records
            </Text>

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

            {/* Status */}

            <Text style={styles.sectionTitle}>
              Status
            </Text>

            <View style={styles.chipsContainer}>

              {STATUS.map((item) => {

                const active =
                  selectedStatus === item;

                return (
                  <TouchableOpacity
                    key={item}
                    activeOpacity={0.85}
                    style={[
                      styles.chip,
                      active &&
                        styles.activeChip,
                    ]}
                    onPress={() =>
                      onSelectStatus(item)
                    }
                  >

                    <Text
                      style={[
                        styles.chipText,
                        active &&
                          styles.activeChipText,
                      ]}
                    >
                      {item}
                    </Text>

                  </TouchableOpacity>
                );
              })}

            </View>

            {/* Department */}

            <Text style={styles.sectionTitle}>
              Department
            </Text>

            <View style={styles.chipsContainer}>

              {DEPARTMENTS.map((item) => {

                const active =
                  selectedDepartment === item;

                return (
                  <TouchableOpacity
                    key={item}
                    activeOpacity={0.85}
                    style={[
                      styles.chip,
                      active &&
                        styles.activeChip,
                    ]}
                    onPress={() =>
                      onSelectDepartment(item)
                    }
                  >

                    <Text
                      style={[
                        styles.chipText,
                        active &&
                          styles.activeChipText,
                      ]}
                    >
                      {item}
                    </Text>

                  </TouchableOpacity>
                );
              })}

            </View>

            {/* Date */}

            <Text style={styles.sectionTitle}>
              Date Range
            </Text>

            <View style={styles.dateCard}>

              <Ionicons
                name="calendar-outline"
                size={22}
                color={COMPOFF_THEME.colors.primary}
              />

              <Text style={styles.dateText}>
                Current Month
              </Text>

            </View>

          </ScrollView>

          {/* Bottom Buttons */}

          <View style={styles.footer}>

            <TouchableOpacity
              style={styles.resetButton}
              onPress={onReset}
            >
              <Text style={styles.resetText}>
                Reset
              </Text>
            </TouchableOpacity>

            <TouchableOpacity
              style={styles.applyButton}
              onPress={onApply}
            >
              <Text style={styles.applyText}>
                Apply Filters
              </Text>
            </TouchableOpacity>

          </View>

        </View>

      </View>

    </Modal>
  );
}

const styles = StyleSheet.create({

  overlay: {
    flex: 1,
    backgroundColor: "rgba(15,23,42,0.35)",
    justifyContent: "flex-end",
  },

  sheet: {
    backgroundColor: "#FFFFFF",

    borderTopLeftRadius: 30,
    borderTopRightRadius: 30,

    padding: 22,

    maxHeight: "80%",
  },

  handle: {
    width: 60,
    height: 5,
    borderRadius: 3,
    backgroundColor: "#C4D2D6",
    alignSelf: "center",
    marginBottom: 18,
  },

  header: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    marginBottom: 24,
  },

  title: {
    fontSize: 22,
    fontWeight: "800",
    color: COMPOFF_THEME.colors.textPrimary,
  },

  sectionTitle: {
    marginBottom: 14,
    marginTop: 10,
    fontSize: 15,
    fontWeight: "700",
    color: COMPOFF_THEME.colors.textPrimary,
  },

  chipsContainer: {
    flexDirection: "row",
    flexWrap: "wrap",
    marginBottom: 18,
  },

  chip: {
    paddingHorizontal: 18,
    paddingVertical: 10,
    borderRadius: 30,
    backgroundColor: "#F5FAFB",
    borderWidth: 1,
    borderColor: COMPOFF_THEME.colors.border,
    marginRight: 10,
    marginBottom: 10,
  },

  activeChip: {
    backgroundColor: COMPOFF_THEME.colors.primary,
    borderColor: COMPOFF_THEME.colors.primary,
  },

  chipText: {
    fontSize: 13,
    fontWeight: "700",
    color: COMPOFF_THEME.colors.textSecondary,
  },

  activeChipText: {
    color: "#FFFFFF",
  },

  dateCard: {
    height: 56,
    borderRadius: 18,
    backgroundColor: "#F5FAFB",
    borderWidth: 1,
    borderColor: COMPOFF_THEME.colors.border,
    flexDirection: "row",
    alignItems: "center",
    paddingHorizontal: 18,
    marginBottom: 20,
  },

  dateText: {
    marginLeft: 12,
    fontSize: 15,
    fontWeight: "700",
    color: COMPOFF_THEME.colors.textPrimary,
  },

  footer: {
    flexDirection: "row",
    justifyContent: "space-between",
    marginTop: 12,
  },

  resetButton: {
    width: "30%",
    height: 54,
    borderRadius: 16,
    backgroundColor: "#ECF3F5",
    justifyContent: "center",
    alignItems: "center",
  },

  resetText: {
    fontWeight: "700",
    color: COMPOFF_THEME.colors.textPrimary,
  },

  applyButton: {
    width: "66%",
    height: 54,
    borderRadius: 16,
    backgroundColor: COMPOFF_THEME.colors.primary,
    justifyContent: "center",
    alignItems: "center",
  },

  applyText: {
    color: "#FFFFFF",
    fontWeight: "800",
    fontSize: 15,
  },

});