import React from "react";
import {
  View,
  Text,
  StyleSheet,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";

export default function PoliciesHeaderCard() {
  return (
    <View style={styles.container}>
      <View style={styles.left}>
        <View style={styles.iconContainer}>
          <Ionicons
            name="document-text-outline"
            size={34}
            color="#0B5CAD"
          />
        </View>

        <View style={styles.content}>
          <Text style={styles.title}>
            Policies & Guidelines
          </Text>

          <Text style={styles.subtitle}>
            Company policies, HR guidelines, compliance
            documents and employee responsibilities in one
            place.
          </Text>
        </View>
      </View>

      <View style={styles.badge}>
        <Ionicons
          name="shield-checkmark"
          size={18}
          color="#16A34A"
        />

        <Text style={styles.badgeText}>
          Verified
        </Text>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    backgroundColor: "#FFFFFF",

    borderRadius: 24,

    padding: 22,

    marginBottom: 18,

    borderWidth: 1,
    borderColor: "#E7EDF5",

    shadowColor: "#000",
    shadowOpacity: 0.05,
    shadowRadius: 12,
    shadowOffset: {
      width: 0,
      height: 5,
    },

    elevation: 3,
  },

  left: {
    flexDirection: "row",
  },

  iconContainer: {
    width: 68,
    height: 68,

    borderRadius: 18,

    backgroundColor: "#E6EFFA",

    justifyContent: "center",
    alignItems: "center",
  },

  content: {
    flex: 1,

    marginLeft: 18,

    justifyContent: "center",
  },

  title: {
    fontSize: 23,

    fontWeight: "800",

    color: "#131C2A",
  },

  subtitle: {
    marginTop: 8,

    fontSize: 14,

    lineHeight: 22,

    color: "#5A6678",
  },

  badge: {
    marginTop: 20,

    alignSelf: "flex-start",

    flexDirection: "row",

    alignItems: "center",

    backgroundColor: "#ECFDF5",

    paddingHorizontal: 14,

    paddingVertical: 8,

    borderRadius: 30,
  },

  badgeText: {
    marginLeft: 6,

    color: "#16A34A",

    fontWeight: "700",

    fontSize: 13,
  },
});