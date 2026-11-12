import React from "react";
import {
  View,
  TextInput,
  TouchableOpacity,
  StyleSheet,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";
import SALARY_THEME from "../../../constants/salaryTheme";

export default function SalarySearchBar({
  value,
  onChangeText,
  onClear,
  placeholder = "Search employee...",
  onFilterPress,
  hasActiveFilter = false,
}) {
  return (
    <View style={styles.container}>
      <View style={styles.searchContainer}>
        <Ionicons
          name="search-outline"
          size={20}
          color={SALARY_THEME.colors.textLight}
        />

        <TextInput
          value={value}
          onChangeText={onChangeText}
          placeholder={placeholder}
          placeholderTextColor={SALARY_THEME.colors.textLight}
          style={styles.input}
          autoCapitalize="none"
          autoCorrect={false}
          returnKeyType="search"
        />

        {value?.length > 0 && (
          <TouchableOpacity accessibilityRole="button" accessibilityLabel="Close"
            activeOpacity={0.7}
            onPress={onClear}
            style={styles.clearButton}
          >
            <Ionicons
              name="close-circle"
              size={20}
              color={SALARY_THEME.colors.textMuted}
            />
          </TouchableOpacity>
        )}
      </View>

      {onFilterPress && (
        <TouchableOpacity accessibilityRole="button" accessibilityLabel="Filter"
          activeOpacity={0.8}
          style={[styles.filterButton, hasActiveFilter && styles.filterButtonActive]}
          onPress={onFilterPress}
        >
          <Ionicons
            name="options-outline"
            size={22}
            color={hasActiveFilter ? "#FFFFFF" : SALARY_THEME.colors.primary}
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
    marginBottom: 18,
  },

  searchContainer: {
    flex: 1,
    height: 54,
    flexDirection: "row",
    alignItems: "center",
    backgroundColor: SALARY_THEME.colors.surface,
    borderRadius: SALARY_THEME.radius.lg,
    paddingHorizontal: 16,
    borderWidth: 1,
    borderColor: SALARY_THEME.colors.border,
    ...SALARY_THEME.shadow,
  },

  input: {
    flex: 1,
    marginLeft: 10,
    fontSize: 15,
    color: SALARY_THEME.colors.textPrimary,
  },

  clearButton: {
    marginLeft: 8,
    padding: 2,
  },

  filterButton: {
    width: 54,
    height: 54,
    marginLeft: 12,
    borderRadius: SALARY_THEME.radius.lg,
    backgroundColor: SALARY_THEME.colors.surface,
    borderWidth: 1,
    borderColor: SALARY_THEME.colors.border,
    justifyContent: "center",
    alignItems: "center",
    position: "relative",
    ...SALARY_THEME.shadow,
  },

  filterButtonActive: {
    backgroundColor: SALARY_THEME.colors.primary,
    borderColor: SALARY_THEME.colors.primary,
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