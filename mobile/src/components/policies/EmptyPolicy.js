import React from "react";
import {
  View,
  Text,
  TouchableOpacity,
  StyleSheet,
} from "react-native";

import { Ionicons } from "@expo/vector-icons";

export default function EmptyPolicy({
  title = "No Policy Available",
  message = "There are currently no policy documents available for this category. Please check again later or contact Human Resources.",
  buttonText = "Contact HR",
  onPress = () => {},
}) {
  return (
    <View style={styles.container}>
      <View style={styles.iconWrapper}>
        <Ionicons
          name="document-text-outline"
          size={70}
          color="#0B5CAD"
        />
      </View>

      <Text style={styles.title}>
        {title}
      </Text>

      <Text style={styles.message}>
        {message}
      </Text>

      <TouchableOpacity
        activeOpacity={0.9}
        style={styles.button}
        onPress={onPress}
      >
        <Ionicons
          name="mail-outline"
          size={20}
          color="#FFFFFF"
        />

        <Text style={styles.buttonText}>
          {buttonText}
        </Text>
      </TouchableOpacity>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    backgroundColor: "#FFFFFF",

    borderRadius: 22,

    paddingVertical: 36,
    paddingHorizontal: 24,

    alignItems: "center",

    borderWidth: 1,
    borderColor: "#E3EAF3",

    shadowColor: "#131C2A",
    shadowOpacity: 0.05,
    shadowRadius: 10,
    shadowOffset: {
      width: 0,
      height: 5,
    },

    elevation: 2,

    marginBottom: 24,
  },

  iconWrapper: {
    width: 110,
    height: 110,

    borderRadius: 55,

    backgroundColor: "#E6EFFA",

    justifyContent: "center",
    alignItems: "center",

    marginBottom: 24,
  },

  title: {
    fontSize: 22,

    fontWeight: "800",

    color: "#131C2A",

    textAlign: "center",

    marginBottom: 12,
  },

  message: {
    fontSize: 15,

    lineHeight: 24,

    color: "#5A6678",

    textAlign: "center",

    marginBottom: 30,
  },

  button: {
    flexDirection: "row",

    alignItems: "center",

    justifyContent: "center",

    backgroundColor: "#0B5CAD",

    paddingHorizontal: 26,
    paddingVertical: 14,

    borderRadius: 16,

    shadowColor: "#0B5CAD",
    shadowOpacity: 0.25,
    shadowRadius: 12,
    shadowOffset: {
      width: 0,
      height: 6,
    },

    elevation: 4,
  },

  buttonText: {
    marginLeft: 8,

    color: "#FFFFFF",

    fontSize: 16,

    fontWeight: "700",
  },
});