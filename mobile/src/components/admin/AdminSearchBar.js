import React from "react";
import {
  View,
  TextInput,
  TouchableOpacity,
  StyleSheet,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";
import THEME from "../../constants/theme";

export default function AdminSearchBar({
  value,
  onChangeText,
  placeholder = "Search...",
  onFilterPress,
  hasActiveFilter = false,
  onClear,
}) {
  return (
    <View style={styles.container}>
      {/* Search Box */}
      <View style={styles.searchBox}>
        <Ionicons
          name="search"
          size={20}
          color={THEME.colors.textLight}
        />
        <TextInput
          value={value}
          onChangeText={onChangeText}
          placeholder={placeholder}
          placeholderTextColor={THEME.colors.textLight}
          style={styles.input}
        />
        {value ? (
          <TouchableOpacity accessibilityRole="button" accessibilityLabel="Close" onPress={onClear || (() => onChangeText && onChangeText(""))}>
            <Ionicons name="close-circle" size={18} color={THEME.colors.textLight} />
          </TouchableOpacity>
        ) : null}
      </View>

      {/* Filter Button */}
      {onFilterPress && (
        <TouchableOpacity accessibilityRole="button" accessibilityLabel="Filter"
          activeOpacity={0.8}
          style={[styles.filterButton, hasActiveFilter && styles.filterButtonActive]}
          onPress={onFilterPress}
        >
          <Ionicons
            name="options-outline"
            size={22}
            color={hasActiveFilter ? "#FFFFFF" : THEME.colors.primary}
          />
          {hasActiveFilter && <View style={styles.activeDot} />}
        </TouchableOpacity>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flexDirection: "row",
    alignItems: "center",
    marginBottom: THEME.spacing.sectionGap,
  },
  searchBox: {
    flex: 1,
    height: 54,
    flexDirection: "row",
    alignItems: "center",
    backgroundColor: THEME.colors.surface,
    borderRadius: THEME.radius.input,
    borderWidth: 1,
    borderColor: THEME.colors.border,
    paddingHorizontal: 16,
    ...THEME.shadows.sm,
  },
  input: {
    flex: 1,
    marginLeft: 12,
    color: THEME.colors.text,
    ...THEME.typography.body,
  },
  filterButton: {
    width: 54,
    height: 54,
    marginLeft: 12,
    borderRadius: THEME.radius.input,
    backgroundColor: THEME.colors.surface,
    borderWidth: 1,
    borderColor: THEME.colors.border,
    justifyContent: "center",
    alignItems: "center",
    position: "relative",
    ...THEME.shadows.sm,
  },
  filterButtonActive: {
    backgroundColor: THEME.colors.primary,
    borderColor: THEME.colors.primary,
  },
  activeDot: {
    position: "absolute",
    top: 10,
    right: 10,
    width: 8,
    height: 8,
    borderRadius: 4,
    backgroundColor: "#22C55E",
    borderWidth: 1.5,
    borderColor: "#FFFFFF",
  },
});