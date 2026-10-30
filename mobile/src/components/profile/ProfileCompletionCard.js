import React from "react";
import {
  View,
  Text,
  StyleSheet,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";

export default function ProfileCompletionCard({
  percentage = 82,
  completed = 18,
  total = 22,
}) {
  return (
    <View style={styles.card}>
      {/* Header */}

      <View style={styles.header}>
        <View>
          <Text style={styles.title}>
            Profile Completion
          </Text>

          <Text style={styles.subtitle}>
            Complete your profile for a better experience
          </Text>
        </View>

        <View style={styles.percentBox}>
          <Text style={styles.percent}>
            {percentage}%
          </Text>
        </View>
      </View>

      {/* Progress */}

      <View style={styles.progressTrack}>
        <View
          style={[
            styles.progressFill,
            {
              width: `${percentage}%`,
            },
          ]}
        />
      </View>

      {/* Footer */}

      <View style={styles.footer}>
        <View style={styles.footerLeft}>
          <View style={styles.checkIcon}>
            <Ionicons
              name="checkmark-circle"
              size={18}
              color="#16A34A"
            />
          </View>

          <View>
            <Text style={styles.completed}>
              {completed} of {total} sections completed
            </Text>

            <Text style={styles.remaining}>
              {total - completed} sections remaining
            </Text>
          </View>
        </View>

        <Ionicons
          name="chevron-forward"
          size={18}
          color="#8A9AA0"
        />
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: "#FFFFFF",

    borderRadius: 22,

    padding: 20,

    marginBottom: 20,

    borderWidth: 1,
    borderColor: "#E3ECEF",

    shadowColor: "#132026",
    shadowOpacity: 0.05,
    shadowRadius: 12,
    shadowOffset: {
      width: 0,
      height: 6,
    },

    elevation: 3,
  },

  header: {
    flexDirection: "row",

    justifyContent: "space-between",

    alignItems: "center",
  },

  title: {
    fontSize: 18,

    fontWeight: "700",

    color: "#132026",
  },

  subtitle: {
    marginTop: 4,

    fontSize: 13,

    color: "#5A6A70",

    lineHeight: 18,
  },

  percentBox: {
    width: 64,
    height: 64,

    borderRadius: 18,

    backgroundColor: "#E3F2F5",

    justifyContent: "center",

    alignItems: "center",
  },

  percent: {
    fontSize: 20,

    fontWeight: "800",

    color: "#0B6E8E",
  },

  progressTrack: {
    marginTop: 22,

    height: 8,

    borderRadius: 20,

    backgroundColor: "#DCE6E9",

    overflow: "hidden",
  },

  progressFill: {
    height: "100%",

    backgroundColor: "#0B6E8E",

    borderRadius: 20,
  },

  footer: {
    marginTop: 22,

    flexDirection: "row",

    justifyContent: "space-between",

    alignItems: "center",
  },

  footerLeft: {
    flexDirection: "row",

    alignItems: "center",

    flex: 1,
  },

  checkIcon: {
    width: 42,
    height: 42,

    borderRadius: 14,

    backgroundColor: "#ECFDF5",

    justifyContent: "center",

    alignItems: "center",

    marginRight: 12,
  },

  completed: {
    fontSize: 14,

    fontWeight: "700",

    color: "#132026",
  },

  remaining: {
    marginTop: 3,

    fontSize: 12,

    color: "#5A6A70",
  },
});