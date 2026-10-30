import React from "react";
import {
  TouchableOpacity,
  Text,
  StyleSheet,
  ActivityIndicator,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";

export default function SaveButton({
  title = "Save Changes",
  icon = "checkmark-circle-outline",
  loading = false,
  disabled = false,
  onPress = () => {},
}) {
  const isDisabled = disabled || loading;

  return (
    <TouchableOpacity
      activeOpacity={0.85}
      disabled={isDisabled}
      onPress={onPress}
      style={[
        styles.button,
        isDisabled && styles.disabledButton,
      ]}
    >
      {loading ? (
        <ActivityIndicator
          color="#FFFFFF"
          size="small"
        />
      ) : (
        <>
          <Ionicons
            name={icon}
            size={20}
            color="#FFFFFF"
          />

          <Text style={styles.text}>
            {title}
          </Text>
        </>
      )}
    </TouchableOpacity>
  );
}

const styles = StyleSheet.create({
  button: {
    height: 56,

    borderRadius: 18,

    backgroundColor: "#0B6E8E",

    flexDirection: "row",

    justifyContent: "center",

    alignItems: "center",

    marginTop: 24,

    shadowColor: "#0B6E8E",

    shadowOpacity: 0.25,

    shadowRadius: 14,

    shadowOffset: {
      width: 0,
      height: 8,
    },

    elevation: 5,
  },

  disabledButton: {
    backgroundColor: "#8A9AA0",

    shadowOpacity: 0,

    elevation: 0,
  },

  text: {
    marginLeft: 10,

    color: "#FFFFFF",

    fontSize: 16,

    fontWeight: "700",

    letterSpacing: 0.2,
  },
});