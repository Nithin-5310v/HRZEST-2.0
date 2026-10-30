import React from "react";

import {
  View,
  Text,
  TouchableOpacity,
  StyleSheet,
} from "react-native";

import { Ionicons } from "@expo/vector-icons";

import COMPOFF_THEME from "../../constants/compOffTheme";

export default function MonthYearSelector({

  month,

  year,

  months = [],

  years = [],

  onMonthChange,

  onYearChange,

  onFilterPress,

}) {

  const changeMonth = () => {

    if (!months.length) return;

    const currentIndex = months.indexOf(month);

    const nextIndex =
      (currentIndex + 1) % months.length;

    onMonthChange?.(months[nextIndex]);

  };

  const changeYear = () => {

    if (!years.length) return;

    const currentIndex = years.indexOf(year);

    const nextIndex =
      (currentIndex + 1) % years.length;

    onYearChange?.(years[nextIndex]);

  };

  return (

    <View style={styles.container}>

      <TouchableOpacity
        activeOpacity={0.9}
        style={styles.selectorCard}
        onPress={changeMonth}
      >

        <Ionicons
          name="calendar-outline"
          size={20}
          color={COMPOFF_THEME.colors.primary}
        />

        <View style={styles.textContainer}>

          <Text style={styles.label}>
            Month
          </Text>

          <Text style={styles.value}>
            {month}
          </Text>

        </View>

        <Ionicons
          name="chevron-down"
          size={18}
          color={COMPOFF_THEME.colors.textSecondary}
        />

      </TouchableOpacity>

      <TouchableOpacity
        activeOpacity={0.9}
        style={styles.selectorCard}
        onPress={changeYear}
      >

        <Ionicons
          name="today-outline"
          size={20}
          color={COMPOFF_THEME.colors.primary}
        />

        <View style={styles.textContainer}>

          <Text style={styles.label}>
            Year
          </Text>

          <Text style={styles.value}>
            {year}
          </Text>

        </View>

        <Ionicons
          name="chevron-down"
          size={18}
          color={COMPOFF_THEME.colors.textSecondary}
        />

      </TouchableOpacity>

      <TouchableOpacity
        activeOpacity={0.9}
        style={styles.filterButton}
        onPress={onFilterPress}
      >

        <Ionicons
          name="options-outline"
          size={22}
          color="#FFFFFF"
        />

      </TouchableOpacity>

    </View>

  );

}

const styles = StyleSheet.create({
      container: {
    flexDirection: "row",

    alignItems: "center",

    marginBottom: 18,
  },

  selectorCard: {
    flex: 1,

    flexDirection: "row",

    alignItems: "center",

    backgroundColor: "#FFFFFF",

    borderRadius: 20,

    paddingHorizontal: 16,

    paddingVertical: 16,

    borderWidth: 1,

    borderColor: "#DBE3EE",

    shadowColor: "#131C2A",

    shadowOpacity: 0.05,

    shadowRadius: 10,

    shadowOffset: {
      width: 0,
      height: 4,
    },

    elevation: 3,
  },

  textContainer: {
    flex: 1,

    marginLeft: 12,
  },

  label: {
    fontSize: 12,

    fontWeight: "700",

    color:
      COMPOFF_THEME.colors.textMuted,
  },

  value: {
    marginTop: 4,

    fontSize: 16,

    fontWeight: "800",

    color:
      COMPOFF_THEME.colors.textPrimary,
  },

  filterButton: {
    width: 58,

    height: 58,

    marginLeft: 12,

    borderRadius: 18,

    justifyContent: "center",

    alignItems: "center",

    backgroundColor:
      COMPOFF_THEME.colors.primary,

    shadowColor:
      COMPOFF_THEME.colors.primary,

    shadowOpacity: 0.22,

    shadowRadius: 12,

    shadowOffset: {
      width: 0,
      height: 5,
    },

    elevation: 6,
  },

});