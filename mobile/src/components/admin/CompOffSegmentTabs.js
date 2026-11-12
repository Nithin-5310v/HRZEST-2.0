import React from "react";

import {
  View,
  Text,
  TouchableOpacity,
  StyleSheet,
} from "react-native";

import { Ionicons } from "@expo/vector-icons";

import COMPOFF_THEME from "../../constants/compOffTheme";

const TABS = [
  {
    key: "overtime",
    title: "Overtime",
    icon: "time-outline",
  },
  {
    key: "compoff",
    title: "Comp-off",
    icon: "calendar-clear-outline",
  },
  {
    key: "analytics",
    title: "Analytics",
    icon: "bar-chart-outline",
  },
];

export default function CompOffSegmentTabs({
  selectedTab,
  onChangeTab,
}) {
  return (
    <View style={styles.container}>
      {TABS.map((tab) => {
        const active = selectedTab === tab.key;

        return (
          <TouchableOpacity accessibilityRole="button"
            key={tab.key}
            activeOpacity={0.85}
            style={[
              styles.tab,
              active && styles.activeTab,
            ]}
            onPress={() => onChangeTab(tab.key)}
          >
            <Ionicons
              name={tab.icon}
              size={18}
              color={
                active
                  ? "#FFFFFF"
                  : COMPOFF_THEME.colors.textMuted
              }
            />

            <Text
              style={[
                styles.title,
                active && styles.activeTitle,
              ]}
            >
              {tab.title}
            </Text>
          </TouchableOpacity>
        );
      })}
    </View>
  );
}

const styles = StyleSheet.create({

  container: {
    flexDirection: "row",

    backgroundColor: "#FFFFFF",

    borderRadius: 18,

    padding: 6,

    marginBottom: 22,

    borderWidth: 1,

    borderColor: COMPOFF_THEME.colors.border,

    ...COMPOFF_THEME.shadow,
  },

  tab: {
    flex: 1,

    height: 48,

    borderRadius: 14,

    flexDirection: "row",

    justifyContent: "center",

    alignItems: "center",
  },

  activeTab: {
    backgroundColor:
      COMPOFF_THEME.colors.primary,
  },

  title: {
    marginLeft: 8,

    fontSize: 13,

    fontWeight: "700",

    color:
      COMPOFF_THEME.colors.textMuted,
  },

  activeTitle: {
    color: "#FFFFFF",
  },

});