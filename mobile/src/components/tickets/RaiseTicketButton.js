import React from "react";
import {
  TouchableOpacity,
  Text,
  ActivityIndicator,
  StyleSheet,
} from "react-native";

import { Ionicons } from "@expo/vector-icons";

export default function RaiseTicketButton({
  loading = false,
  onPress = () => {},
}) {
  return (
    <TouchableOpacity
      activeOpacity={0.9}
      style={[
        styles.button,
        loading && styles.disabled,
      ]}
      disabled={loading}
      onPress={onPress}
    >
      {loading ? (
        <>
          <ActivityIndicator
            color="#FFFFFF"
            size="small"
          />

          <Text style={styles.text}>
            Raising Ticket...
          </Text>
        </>
      ) : (
        <>
          <Ionicons
            name="paper-plane"
            size={22}
            color="#FFFFFF"
          />

          <Text style={styles.text}>
            Raise Ticket
          </Text>
        </>
      )}
    </TouchableOpacity>
  );
}

const styles = StyleSheet.create({
  button: {
    height: 60,

    borderRadius: 18,

    backgroundColor: "#0B5CAD",

    flexDirection: "row",

    justifyContent: "center",

    alignItems: "center",

    marginBottom: 24,

    shadowColor: "#0B5CAD",
    shadowOpacity: 0.30,
    shadowRadius: 14,
    shadowOffset: {
      width: 0,
      height: 8,
    },

    elevation: 6,
  },

  disabled: {
    opacity: 0.7,
  },

  text: {
    marginLeft: 10,

    color: "#FFFFFF",

    fontSize: 17,

    fontWeight: "800",

    letterSpacing: 0.3,
  },
});