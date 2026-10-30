import React from "react";

import {
  View,
  Text,
 TouchableOpacity,
  StyleSheet,
} from "react-native";

import { Ionicons } from "@expo/vector-icons";

import COMPOFF_THEME from "../../constants/compOffTheme";
import CompOffStatusChip from "./CompOffStatusChip";

export default function OvertimeHistoryCard({
  item,
  onPress,
}) {
  return (
    <TouchableOpacity
      activeOpacity={0.9}
      style={styles.card}
      onPress={() => onPress?.(item)}
    >
      {/* Header */}

      <View style={styles.header}>

        <View style={styles.employeeSection}>

          <View style={styles.avatar}>

            <Ionicons
              name="person"
              size={22}
              color={COMPOFF_THEME.colors.primary}
            />

          </View>

          <View style={styles.employeeInfo}>

            <Text style={styles.name}>
              {item.name}
            </Text>

            <Text style={styles.department}>
              {item.department}
            </Text>

          </View>

        </View>

        <CompOffStatusChip
          status={item.status}
        />

      </View>

      {/* Information */}

      <View style={styles.infoContainer}>

        <View style={styles.infoItem}>

          <Ionicons
            name="calendar-outline"
            size={18}
            color={COMPOFF_THEME.colors.textMuted}
          />

          <Text style={styles.infoText}>
            {item.date}
          </Text>

        </View>

        <View style={styles.infoItem}>

          <Ionicons
            name="time-outline"
            size={18}
            color={COMPOFF_THEME.colors.textMuted}
          />

          <Text style={styles.infoText}>
            {item.shift_end || "--"} - {item.actual_logout || "--"}
          </Text>

        </View>

      </View>

      {/* Notes -- only present once approved/rejected with a note attached */}

      {!!item.notes && (
        <View style={styles.reasonCard}>

          <Text style={styles.reasonTitle}>
            Notes
          </Text>

          <Text style={styles.reason}>
            {item.notes}
          </Text>

        </View>
      )}

      {/* Bottom Stats */}

      <View style={styles.footer}>

        <View style={styles.stat}>

          <Text style={styles.statValue}>
            {item.hours}h
          </Text>

          <Text style={styles.statLabel}>
            OT Hours
          </Text>

        </View>

        <View style={styles.divider} />

        <View style={styles.stat}>

          <Text
            style={[
              styles.statValue,
              {
                color:
                  COMPOFF_THEME.colors.success,
              },
            ]}
          >
            ₹{item.ot_pay}
          </Text>

          <Text style={styles.statLabel}>
            OT Pay
          </Text>

        </View>

      </View>

    </TouchableOpacity>
  );
}

const styles = StyleSheet.create({

  card: {
    backgroundColor: "#FFFFFF",

    borderRadius: 24,

    padding: 18,

    marginBottom: 18,

    borderWidth: 1,

    borderColor:
      COMPOFF_THEME.colors.border,

    ...COMPOFF_THEME.shadow,
  },

  header: {
    flexDirection: "row",

    justifyContent: "space-between",

    alignItems: "center",
  },

  employeeSection: {
    flexDirection: "row",

    alignItems: "center",

    flex: 1,
  },

  avatar: {
    width: 56,

    height: 56,

    borderRadius: 18,

    backgroundColor:
      COMPOFF_THEME.colors.primaryLight,

    justifyContent: "center",

    alignItems: "center",
  },

  employeeInfo: {
    marginLeft: 14,

    flex: 1,
  },

  name: {
    fontSize: 17,

    fontWeight: "800",

    color:
      COMPOFF_THEME.colors.textPrimary,
  },

  department: {
    marginTop: 4,

    fontSize: 13,

    color:
      COMPOFF_THEME.colors.textMuted,
  },

  infoContainer: {
    marginTop: 18,
  },

  infoItem: {
    flexDirection: "row",

    alignItems: "center",

    marginBottom: 10,
  },

  infoText: {
    marginLeft: 8,

    fontSize: 13,

    color:
      COMPOFF_THEME.colors.textSecondary,
  },

  reasonCard: {
    marginTop: 8,

    backgroundColor: "#F5FAFB",

    borderRadius: 16,

    padding: 14,
  },

  reasonTitle: {
    fontSize: 12,

    fontWeight: "700",

    color:
      COMPOFF_THEME.colors.textMuted,
  },

  reason: {
    marginTop: 6,

    fontSize: 14,

    lineHeight: 22,

    color:
      COMPOFF_THEME.colors.textPrimary,
  },

  footer: {
    flexDirection: "row",

    alignItems: "center",

    justifyContent: "space-between",

    marginTop: 18,

    paddingTop: 18,

    borderTopWidth: 1,

    borderTopColor:
      COMPOFF_THEME.colors.divider,
  },

  stat: {
    flex: 1,

    alignItems: "center",
  },

  divider: {
    width: 1,

    height: 42,

    backgroundColor:
      COMPOFF_THEME.colors.divider,
  },

  statValue: {
    fontSize: 18,

    fontWeight: "800",

    color:
      COMPOFF_THEME.colors.primary,
    letterSpacing: -0.3,
  },

  statLabel: {
    marginTop: 5,

    fontSize: 12,

    fontWeight: "600",

    color:
      COMPOFF_THEME.colors.textMuted,
  },

});