import React from "react";
import {
  View,
  Text,
  TextInput,
  StyleSheet,
} from "react-native";

import { Ionicons } from "@expo/vector-icons";

export default function SubjectInput({
  value,
  onChangeText,
}) {
  return (
    <View style={styles.container}>
      <Text style={styles.label}>
        Subject
      </Text>

      <View style={styles.inputContainer}>
        <Ionicons
          name="document-text-outline"
          size={20}
          color="#5A6678"
        />

        <TextInput
          value={value}
          onChangeText={onChangeText}
          placeholder="Enter ticket subject"
          placeholderTextColor="#8794A5"
          style={styles.input}
          maxLength={100}
        />
      </View>

      <Text style={styles.helper}>
        Keep the subject short and descriptive.
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    marginBottom: 22,
  },

  label: {
    fontSize: 16,
    fontWeight: "800",
    color: "#131C2A",
    marginBottom: 12,
  },

  inputContainer: {
    flexDirection: "row",
    alignItems: "center",

    backgroundColor: "#FFFFFF",

    borderWidth: 1,
    borderColor: "#DBE3EE",

    borderRadius: 18,

    paddingHorizontal: 16,

    height: 58,

    shadowColor: "#000",
    shadowOpacity: 0.03,
    shadowRadius: 6,
    shadowOffset: {
      width: 0,
      height: 2,
    },

    elevation: 1,
  },

  input: {
    flex: 1,

    marginLeft: 12,

    fontSize: 15,

    color: "#131C2A",

    fontWeight: "600",
  },

  helper: {
    marginTop: 8,

    marginLeft: 2,

    fontSize: 12,

    color: "#8794A5",

    fontWeight: "500",
  },
});