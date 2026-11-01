import React from "react";

import {
  View,
  Text,
  TouchableOpacity,
  StyleSheet,
} from "react-native";

import { Ionicons } from "@expo/vector-icons";

import COMPOFF_THEME from "../../constants/compOffTheme";

export default function CompOffQuickActions({
  onApplyOT,
  onRequestCompOff,
  onHistory,
  onExport,
}) {
  const actions = [
    {
      title: "Apply OT",
      subtitle: "New OT Entry",
      icon: "time-outline",
      color: "#2563EB",
      background: "#EEF4FF",
      onPress: onApplyOT,
    },
    {
      title: "Comp-off",
      subtitle: "Raise Request",
      icon: "calendar-clear-outline",
      color: "#10B981",
      background: "#ECFDF5",
      onPress: onRequestCompOff,
    },
    {
      title: "History",
      subtitle: "Past Records",
      icon: "document-text-outline",
      color: "#7C3AED",
      background: "#F3E8FF",
      onPress: onHistory,
    },
    {
      title: "Export",
      subtitle: "Download",
      icon: "download-outline",
      color: "#F59E0B",
      background: "#FEF3C7",
      onPress: onExport,
    },
  ];

  return (
    <View style={styles.container}>

      <Text style={styles.heading}>
        Quick Actions
      </Text>

      <View style={styles.grid}>

        {actions.map((item) => (

          <TouchableOpacity
            key={item.title}
            activeOpacity={0.85}
            style={styles.card}
            onPress={item.onPress}
          >

            <View
              style={[
                styles.iconContainer,
                {
                  backgroundColor:
                    item.background,
                },
              ]}
            >

              <Ionicons
                name={item.icon}
                size={24}
                color={item.color}
              />

            </View>

            <Text style={styles.title}>
              {item.title}
            </Text>

            <Text style={styles.subtitle}>
              {item.subtitle}
            </Text>

          </TouchableOpacity>

        ))}

      </View>

    </View>
  );
}

const styles = StyleSheet.create({

  container: {
    marginBottom: 24,
  },

  heading: {
    fontSize: 20,

    fontWeight: "800",

    color:
      COMPOFF_THEME.colors.textPrimary,

    marginBottom: 16,
  },

  grid: {
    flexDirection: "row",

    flexWrap: "wrap",

    justifyContent: "space-between",
  },

  card: {
    width: "48%",

    backgroundColor: "#FFFFFF",

    borderRadius: 22,

    paddingVertical: 22,

    paddingHorizontal: 18,

    marginBottom: 16,

    borderWidth: 1,

    borderColor:
      COMPOFF_THEME.colors.border,

    ...COMPOFF_THEME.shadow,
  },

  iconContainer: {
    width: 58,

    height: 58,

    borderRadius: 18,

    justifyContent: "center",

    alignItems: "center",

    marginBottom: 18,
  },

  title: {
    fontSize: 16,

    fontWeight: "800",

    color:
      COMPOFF_THEME.colors.textPrimary,
  },

  subtitle: {
    marginTop: 6,

    fontSize: 13,

    lineHeight: 18,

    color:
      COMPOFF_THEME.colors.textMuted,

    fontWeight: "600",
  },

});