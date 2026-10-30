import React from "react";
import {
  View,
  Text,
  StyleSheet,
} from "react-native";

import { Ionicons } from "@expo/vector-icons";

export default function SectionTitle({
  icon = "document-text-outline",
  title,
  subtitle = "",
}) {
  return (
    <View style={styles.container}>
      <View style={styles.left}>
        <View style={styles.iconContainer}>
          <Ionicons
            name={icon}
            size={22}
            color="#0B6E8E"
          />
        </View>

        <View style={styles.textContainer}>
          <Text style={styles.title}>
            {title}
          </Text>

          {subtitle !== "" && (
            <Text style={styles.subtitle}>
              {subtitle}
            </Text>
          )}
        </View>
      </View>

      <View style={styles.line} />
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    marginTop: 10,
    marginBottom: 18,
  },

  left: {
    flexDirection: "row",
    alignItems: "center",
  },

  iconContainer: {
    width: 46,
    height: 46,

    borderRadius: 14,

    backgroundColor: "#E3F2F5",

    justifyContent: "center",
    alignItems: "center",

    marginRight: 14,

    borderWidth: 1,
    borderColor: "#DCE8FF",
  },

  textContainer: {
    flex: 1,
  },

  title: {
    fontSize: 20,

    fontWeight: "800",

    color: "#132026",

    letterSpacing: -0.3,
  },

  subtitle: {
    marginTop: 4,

    fontSize: 13,

    color: "#5A6A70",

    lineHeight: 20,
  },

  line: {
    marginTop: 16,

    height: 1,

    backgroundColor: "#DCE6E9",
  },
});