import React from "react";

import {
  View,
  Text,
  TouchableOpacity,
  StyleSheet,
} from "react-native";

import { Ionicons } from "@expo/vector-icons";

import COMPOFF_THEME from "../../constants/compOffTheme";

export default function CompOffEmptyState({
  title = "No Records Found",
  description = "There are no overtime or comp-off records available for the selected filters.",
  buttonTitle = "Refresh",
  onPress,
}) {
  return (
    <View style={styles.card}>

      {/* Illustration */}

      <View style={styles.iconContainer}>

        <View style={styles.iconCircle}>

          <Ionicons
            name="document-text-outline"
            size={64}
            color={COMPOFF_THEME.colors.primary}
          />

        </View>

      </View>

      {/* Title */}

      <Text style={styles.title}>
        {title}
      </Text>

      {/* Description */}

      <Text style={styles.description}>
        {description}
      </Text>

      {/* Button */}

      <TouchableOpacity
        activeOpacity={0.85}
        style={styles.button}
        onPress={onPress}
      >

        <Ionicons
          name="refresh-outline"
          size={20}
          color="#FFFFFF"
        />

        <Text style={styles.buttonText}>
          {buttonTitle}
        </Text>

      </TouchableOpacity>

    </View>
  );
}

const styles = StyleSheet.create({

  card: {

    backgroundColor: "#FFFFFF",

    borderRadius: 28,

    paddingVertical: 40,

    paddingHorizontal: 28,

    alignItems: "center",

    borderWidth: 1,

    borderColor:
      COMPOFF_THEME.colors.border,

    ...COMPOFF_THEME.shadow,
  },

  iconContainer: {

    width: 130,

    height: 130,

    borderRadius: 65,

    backgroundColor: "#F5FAFB",

    justifyContent: "center",

    alignItems: "center",

    marginBottom: 24,
  },

  iconCircle: {

    width: 94,

    height: 94,

    borderRadius: 47,

    backgroundColor:
      COMPOFF_THEME.colors.primaryLight,

    justifyContent: "center",

    alignItems: "center",
  },

  title: {

    fontSize: 24,

    fontWeight: "800",

    color:
      COMPOFF_THEME.colors.textPrimary,

    textAlign: "center",
  },

  description: {

    marginTop: 12,

    textAlign: "center",

    fontSize: 15,

    lineHeight: 24,

    color:
      COMPOFF_THEME.colors.textMuted,

    paddingHorizontal: 12,
  },

  button: {

    marginTop: 30,

    height: 54,

    paddingHorizontal: 28,

    borderRadius: 16,

    backgroundColor:
      COMPOFF_THEME.colors.primary,

    flexDirection: "row",

    justifyContent: "center",

    alignItems: "center",

    ...COMPOFF_THEME.shadow,
  },

  buttonText: {

    marginLeft: 10,

    fontSize: 15,

    fontWeight: "800",

    color: "#FFFFFF",
  },

});