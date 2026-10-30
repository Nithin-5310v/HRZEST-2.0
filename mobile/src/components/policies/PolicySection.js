import React from "react";
import {
  View,
  Text,
  StyleSheet,
} from "react-native";

import { Ionicons } from "@expo/vector-icons";
import PolicyBullet from "./PolicyBullet";

export default function PolicySection({
  title,
  bullets = [],
  icon = "document-text-outline",
}) {
  return (
    <View style={styles.container}>
      {/* Section Header */}

      <View style={styles.header}>
        <View style={styles.iconContainer}>
          <Ionicons
            name={icon}
            size={20}
            color="#0B6E8E"
          />
        </View>

        <Text style={styles.title}>
          {title}
        </Text>
      </View>

      {/* Divider */}

      <View style={styles.divider} />

      {/* Bullets */}

      <View style={styles.content}>
        {bullets.map((item, index) => {
          if (typeof item === "string") {
            return (
              <PolicyBullet
                key={index}
                text={item}
              />
            );
          }

          return (
            <PolicyBullet
              key={index}
              text={item.text}
              type={item.type}
            />
          );
        })}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    backgroundColor: "#FFFFFF",

    borderRadius: 22,

    padding: 22,

    marginBottom: 22,

    borderWidth: 1,
    borderColor: "#E3ECEF",

    shadowColor: "#132026",
    shadowOpacity: 0.05,
    shadowRadius: 10,
    shadowOffset: {
      width: 0,
      height: 5,
    },

    elevation: 2,
  },

  header: {
    flexDirection: "row",

    alignItems: "center",

    marginBottom: 16,
  },

  iconContainer: {
    width: 44,
    height: 44,

    borderRadius: 14,

    backgroundColor: "#E3F2F5",

    justifyContent: "center",
    alignItems: "center",

    marginRight: 14,
  },

  title: {
    flex: 1,

    fontSize: 19,

    fontWeight: "800",

    color: "#132026",

    letterSpacing: -0.3,
  },

  divider: {
    height: 1,

    backgroundColor: "#E9F1F3",

    marginBottom: 18,
  },

  content: {
    paddingLeft: 2,
  },
});