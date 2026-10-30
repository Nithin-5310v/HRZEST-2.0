import React from "react";
import {
  View,
  Text,
  StyleSheet,
} from "react-native";

import { Ionicons } from "@expo/vector-icons";

export default function NotificationEmpty() {
  return (
    <View style={styles.container}>
      <View style={styles.iconContainer}>
        <Ionicons
          name="notifications-off-outline"
          size={70}
          color="#C3CFDD"
        />
      </View>

      <Text style={styles.title}>
        No Notifications
      </Text>

      <Text style={styles.description}>
        You're all caught up.
        {"\n"}
        There are no notifications available
        for the selected category.
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    backgroundColor: "#FFFFFF",

    borderRadius: 24,

    paddingVertical: 55,
    paddingHorizontal: 28,

    alignItems: "center",

    borderWidth: 1,
    borderColor: "#E3EAF3",

    shadowColor: "#000",
    shadowOpacity: 0.05,
    shadowRadius: 10,
    shadowOffset: {
      width: 0,
      height: 5,
    },

    elevation: 2,

    marginTop: 20,
  },

  iconContainer: {
    width: 120,
    height: 120,

    borderRadius: 60,

    backgroundColor: "#F5F8FC",

    justifyContent: "center",
    alignItems: "center",

    marginBottom: 24,
  },

  title: {
    fontSize: 22,

    fontWeight: "800",

    color: "#131C2A",
  },

  description: {
    marginTop: 14,

    fontSize: 15,

    lineHeight: 24,

    textAlign: "center",

    color: "#5A6678",

    paddingHorizontal: 12,
  },
});