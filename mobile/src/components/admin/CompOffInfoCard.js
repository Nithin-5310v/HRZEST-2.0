import React from "react";

import {
  View,
  Text,
  StyleSheet,
} from "react-native";

import { Ionicons } from "@expo/vector-icons";

import COMPOFF_THEME from "../../constants/compOffTheme";

export default function CompOffInfoCard({
  policies = [],
}) {
  return (
    <View style={styles.card}>

      {/* Header */}

      <View style={styles.header}>

        <View style={styles.headerLeft}>

          <View style={styles.iconContainer}>

            <Ionicons
              name="information-circle"
              size={22}
              color={COMPOFF_THEME.colors.primary}
            />

          </View>

          <View>

            <Text style={styles.title}>
              OT & Comp-off Policy
            </Text>

            <Text style={styles.subtitle}>
              Company Guidelines
            </Text>

          </View>

        </View>

      </View>

      {/* Policies */}

      <View style={styles.policyContainer}>

        {policies.map((item, index) => (

          <View
            key={item.id}
            style={[
              styles.policyRow,
              index === policies.length - 1 && {
                borderBottomWidth: 0,
              },
            ]}
          >

            <View style={styles.leftSection}>

              <View style={styles.bullet} />

              <Text style={styles.policyTitle}>
                {item.title}
              </Text>

            </View>

            <Text style={styles.policyValue}>
              {item.value}
            </Text>

          </View>

        ))}

      </View>

      {/* Footer */}

      <View style={styles.noteCard}>

        <Ionicons
          name="shield-checkmark"
          size={20}
          color={COMPOFF_THEME.colors.success}
        />

        <Text style={styles.note}>
          Overtime requests must be approved by
          the Reporting Manager and HR before
          compensation or Comp-off credits are
          generated.
        </Text>

      </View>

    </View>
  );
}

const styles = StyleSheet.create({

  card: {

    backgroundColor: "#FFFFFF",

    borderRadius: 24,

    padding: 22,

    marginBottom: 22,

    borderWidth: 1,

    borderColor:
      COMPOFF_THEME.colors.border,

    ...COMPOFF_THEME.shadow,
  },

  header: {

    marginBottom: 22,
  },

  headerLeft: {

    flexDirection: "row",

    alignItems: "center",
  },

  iconContainer: {

    width: 52,

    height: 52,

    borderRadius: 16,

    backgroundColor:
      COMPOFF_THEME.colors.primaryLight,

    justifyContent: "center",

    alignItems: "center",

    marginRight: 14,
  },

  title: {

    fontSize: 20,

    fontWeight: "800",

    color:
      COMPOFF_THEME.colors.textPrimary,
  },

  subtitle: {

    marginTop: 4,

    fontSize: 13,

    color:
      COMPOFF_THEME.colors.textMuted,
  },

  policyContainer: {

    backgroundColor: "#F5FAFB",

    borderRadius: 18,

    paddingHorizontal: 16,
  },

  policyRow: {

    flexDirection: "row",

    justifyContent: "space-between",

    alignItems: "center",

    paddingVertical: 18,

    borderBottomWidth: 1,

    borderBottomColor:
      COMPOFF_THEME.colors.divider,
  },

  leftSection: {

    flexDirection: "row",

    alignItems: "center",

    flex: 1,
  },

  bullet: {

    width: 10,

    height: 10,

    borderRadius: 5,

    backgroundColor:
      COMPOFF_THEME.colors.primary,

    marginRight: 12,
  },

  policyTitle: {

    fontSize: 15,

    fontWeight: "700",

    color:
      COMPOFF_THEME.colors.textPrimary,
  },

  policyValue: {

    fontSize: 14,

    fontWeight: "800",

    color:
      COMPOFF_THEME.colors.primary,
  },

  noteCard: {

    marginTop: 22,

    flexDirection: "row",

    alignItems: "flex-start",

    backgroundColor:
      COMPOFF_THEME.colors.successLight,

    borderRadius: 18,

    padding: 16,
  },

  note: {

    flex: 1,

    marginLeft: 12,

    fontSize: 13,

    lineHeight: 22,

    color:
      COMPOFF_THEME.colors.textSecondary,
  },

});