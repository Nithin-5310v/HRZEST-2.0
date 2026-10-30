import React from "react";
import {
  ScrollView,
  TouchableOpacity,
  Text,
  StyleSheet,
} from "react-native";

export default function NotificationFilter({
  filters = [],
  selectedFilter,
  onSelectFilter,
}) {
  return (
    <ScrollView
      horizontal
      showsHorizontalScrollIndicator={false}
      contentContainerStyle={styles.container}
    >
      {filters.map((filter) => {
        const active =
          selectedFilter === filter;

        return (
          <TouchableOpacity
            key={filter}
            activeOpacity={0.85}
            onPress={() =>
              onSelectFilter(filter)
            }
            style={[
              styles.chip,
              active && styles.activeChip,
            ]}
          >
            <Text
              style={[
                styles.chipText,
                active &&
                  styles.activeChipText,
              ]}
            >
              {filter}
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
    paddingRight: 18,
  },

  chip: {
    paddingHorizontal: 18,
    paddingVertical: 10,

    borderRadius: 24,

    backgroundColor: "#FFFFFF",

    marginRight: 12,

    borderWidth: 1,
    borderColor: "#DBE3EE",

    shadowColor: "#000",
    shadowOpacity: 0.03,
    shadowRadius: 6,
    shadowOffset: {
      width: 0,
      height: 3,
    },

    elevation: 1,
  },

  activeChip: {
    backgroundColor: "#0B5CAD",
    borderColor: "#0B5CAD",
  },

  chipText: {
    fontSize: 14,
    fontWeight: "700",
    color: "#455163",
  },

  activeChipText: {
    color: "#FFFFFF",
  },
});