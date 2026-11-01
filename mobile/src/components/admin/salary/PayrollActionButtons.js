import React from "react";

import {
  View,
  Text,
  TouchableOpacity,
  StyleSheet,
} from "react-native";

import { Ionicons } from "@expo/vector-icons";

import SALARY_THEME from "../../../constants/salaryTheme";

const ActionButton = ({
  icon,
  title,
  color,
  background,
  onPress,
}) => (
  <TouchableOpacity
    activeOpacity={0.85}
    style={[
      styles.actionButton,
      {
        backgroundColor: background,
      },
    ]}
    onPress={onPress}
  >
    <View
      style={[
        styles.iconContainer,
        {
          backgroundColor: "#FFFFFF",
        },
      ]}
    >
      <Ionicons
        name={icon}
        size={20}
        color={color}
      />
    </View>

    <Text style={styles.actionText}>
      {title}
    </Text>
  </TouchableOpacity>
);

export default function PayrollActionButtons({
  onGenerate,
  onExport,
  onEmail,
  onMore,
}) {
  return (
    <View style={styles.container}>
      <Text style={styles.sectionTitle}>
        Quick Actions
      </Text>

      <View style={styles.grid}>

        <ActionButton
          title="Generate"
          icon="flash-outline"
          color={SALARY_THEME.colors.primary}
          background="#EFF6FF"
          onPress={onGenerate}
        />

        <ActionButton
          title="Export"
          icon="download-outline"
          color={SALARY_THEME.colors.success}
          background="#ECFDF5"
          onPress={onExport}
        />

        <ActionButton
          title="Email"
          icon="mail-outline"
          color={SALARY_THEME.colors.warning}
          background="#FFFBEB"
          onPress={onEmail}
        />

        <ActionButton
          title="More"
          icon="ellipsis-horizontal"
          color={SALARY_THEME.colors.purple}
          background="#F5F3FF"
          onPress={onMore}
        />

      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    marginBottom: 20,
  },

  sectionTitle: {
    fontSize: 18,
    fontWeight: "700",
    color:
      SALARY_THEME.colors.textPrimary,
    marginBottom: 16,
  },

  grid: {
    flexDirection: "row",
    justifyContent: "space-between",
  },

  actionButton: {
    width: "23%",

    paddingVertical: 16,

    borderRadius:
      SALARY_THEME.radius.lg,

    alignItems: "center",

    borderWidth: 1,

    borderColor:
      SALARY_THEME.colors.border,
  },

  iconContainer: {
    width: 46,

    height: 46,

    borderRadius: 14,

    justifyContent: "center",

    alignItems: "center",

    marginBottom: 12,
  },

  actionText: {
    fontSize: 12,

    fontWeight: "700",

    color:
      SALARY_THEME.colors.textPrimary,

    textAlign: "center",
  },
});