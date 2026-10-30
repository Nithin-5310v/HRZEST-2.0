import React from "react";
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";

export default function DetailCard({
  icon = "person-outline",
  iconColor = "#0B5CAD",
  iconBackground = "#E6EFFA",
  label = "Full Name",
  value = "John Doe",
  editable = false,
  onPress,
}) {
  return (
    <TouchableOpacity
      activeOpacity={editable ? 0.85 : 1}
      onPress={onPress}
      style={styles.card}
    >
      {/* Left */}

      <View style={styles.left}>
        <View
          style={[
            styles.iconContainer,
            {
              backgroundColor: iconBackground,
            },
          ]}
        >
          <Ionicons
            name={icon}
            size={20}
            color={iconColor}
          />
        </View>

        <View style={styles.textContainer}>
          <Text style={styles.label}>
            {label}
          </Text>

          <Text
            numberOfLines={2}
            style={styles.value}
          >
            {value || "--"}
          </Text>
        </View>
      </View>

      {/* Right */}

      {editable && (
        <View style={styles.editButton}>
          <Ionicons
            name="create-outline"
            size={18}
            color="#0B5CAD"
          />
        </View>
      )}
    </TouchableOpacity>
  );
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: "#FFFFFF",

    borderRadius: 18,

    paddingHorizontal: 18,
    paddingVertical: 16,

    marginBottom: 14,

    flexDirection: "row",

    alignItems: "center",

    justifyContent: "space-between",

    borderWidth: 1,

    borderColor: "#E3EAF3",

    shadowColor: "#131C2A",

    shadowOpacity: 0.04,

    shadowRadius: 10,

    shadowOffset: {
      width: 0,
      height: 4,
    },

    elevation: 2,
  },

  left: {
    flexDirection: "row",

    alignItems: "center",

    flex: 1,
  },

  iconContainer: {
    width: 48,
    height: 48,

    borderRadius: 14,

    justifyContent: "center",

    alignItems: "center",
  },

  textContainer: {
    flex: 1,

    marginLeft: 16,
  },

  label: {
    fontSize: 12,

    fontWeight: "600",

    color: "#8794A5",

    textTransform: "uppercase",

    letterSpacing: 0.5,
  },

  value: {
    marginTop: 5,

    fontSize: 16,

    fontWeight: "700",

    color: "#131C2A",

    lineHeight: 22,
  },

  editButton: {
    width: 42,
    height: 42,

    borderRadius: 12,

    backgroundColor: "#F5F8FC",

    borderWidth: 1,

    borderColor: "#E3EAF3",

    justifyContent: "center",

    alignItems: "center",

    marginLeft: 14,
  },
});