import React from "react";
import {
  View,
  Text,
  TextInput,
  StyleSheet,
} from "react-native";

import { Ionicons } from "@expo/vector-icons";

export default function DescriptionInput({
  value,
  onChangeText,
}) {
  return (
    <View style={styles.container}>
      <Text style={styles.label}>
        Description
      </Text>

      <View style={styles.inputContainer}>
        <View style={styles.iconContainer}>
          <Ionicons
            name="create-outline"
            size={22}
            color="#5A6A70"
          />
        </View>

        <TextInput
          value={value}
          onChangeText={onChangeText}
          placeholder="Describe your issue in detail..."
          placeholderTextColor="#8A9AA0"
          multiline
          textAlignVertical="top"
          numberOfLines={7}
          maxLength={500}
          style={styles.input}
        />
      </View>

      <View style={styles.footer}>
        <Text style={styles.helper}>
          Include all relevant details for faster resolution.
        </Text>

        <Text style={styles.counter}>
          {value.length}/500
        </Text>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    marginBottom: 24,
  },

  label: {
    fontSize: 16,
    fontWeight: "800",
    color: "#132026",
    marginBottom: 12,
  },

  inputContainer: {
    flexDirection: "row",

    backgroundColor: "#FFFFFF",

    borderWidth: 1,
    borderColor: "#DCE6E9",

    borderRadius: 20,

    padding: 16,

    minHeight: 170,

    shadowColor: "#000",
    shadowOpacity: 0.03,
    shadowRadius: 6,
    shadowOffset: {
      width: 0,
      height: 2,
    },

    elevation: 2,
  },

  iconContainer: {
    marginTop: 2,
    marginRight: 12,
  },

  input: {
    flex: 1,

    fontSize: 15,

    color: "#132026",

    fontWeight: "500",

    lineHeight: 24,

    paddingTop: 0,
  },

  footer: {
    marginTop: 10,

    flexDirection: "row",

    justifyContent: "space-between",

    alignItems: "center",
  },

  helper: {
    flex: 1,

    fontSize: 12,

    color: "#8A9AA0",

    fontWeight: "500",
  },

  counter: {
    fontSize: 12,

    fontWeight: "700",

    color: "#5A6A70",
  },
});