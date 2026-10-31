import React from "react";
import {
  ScrollView,
  TouchableOpacity,
  Text,
  StyleSheet,
} from "react-native";

import { Ionicons } from "@expo/vector-icons";

const TAB_ICONS = {
  Terms: "document-text-outline",
  Rules: "list-outline",
  Limitations: "warning-outline",
  Instructions: "reader-outline",
  POSH: "shield-checkmark-outline",
  Resignation: "exit-outline",
};

export default function PolicyTabs({
  tabs = [],
  selectedTab,
  onSelectTab,
}) {
  return (
    <ScrollView
      horizontal
      showsHorizontalScrollIndicator={false}
      contentContainerStyle={styles.container}
    >
      {tabs.map((tab) => {
        const active = selectedTab === tab;

        return (
          <TouchableOpacity
            key={tab}
            activeOpacity={0.85}
            style={[
              styles.tab,
              active && styles.activeTab,
            ]}
            onPress={() => onSelectTab(tab)}
          >
            <Ionicons
              name={TAB_ICONS[tab] || "document-outline"}
              size={18}
              color={active ? "#FFFFFF" : "#0B5CAD"}
            />

            <Text
              style={[
                styles.text,
                active && styles.activeText,
              ]}
            >
              {tab}
            </Text>
          </TouchableOpacity>
        );
      })}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: {
    paddingBottom: 18,
    paddingRight: 20,
  },

  tab: {
    flexDirection: "row",

    alignItems: "center",

    backgroundColor: "#FFFFFF",

    borderWidth: 1,

    borderColor: "#DCE6F2",

    paddingHorizontal: 18,

    paddingVertical: 12,

    borderRadius: 30,

    marginRight: 12,

    shadowColor: "#000",

    shadowOpacity: 0.04,

    shadowRadius: 8,

    shadowOffset: {
      width: 0,
      height: 3,
    },

    elevation: 2,
  },

  activeTab: {
    backgroundColor: "#0B5CAD",

    borderColor: "#0B5CAD",
  },

  text: {
    marginLeft: 8,

    fontSize: 14,

    fontWeight: "700",

    color: "#0B5CAD",
  },

  activeText: {
    color: "#FFFFFF",
  },
});