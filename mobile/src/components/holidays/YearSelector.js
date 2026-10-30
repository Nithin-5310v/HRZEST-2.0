import React from "react";
import {
  View,
  Text,
  TouchableOpacity,
  StyleSheet,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";

export default function YearSelector({
  year = 2026,
  onPrevious = () => {},
  onNext = () => {},
}) {
  return (
    <View style={styles.container}>
      <TouchableOpacity
        activeOpacity={0.85}
        style={styles.button}
        onPress={onPrevious}
      >
        <Ionicons
          name="chevron-back"
          size={18}
          color="#0B6E8E"
        />

        <Text style={styles.buttonText}>
          {year - 1}
        </Text>
      </TouchableOpacity>

      <View style={styles.activeYear}>
        <Ionicons
          name="calendar-outline"
          size={20}
          color="#FFFFFF"
        />

        <Text style={styles.activeYearText}>
          {year}
        </Text>
      </View>

      <TouchableOpacity
        activeOpacity={0.85}
        style={styles.button}
        onPress={onNext}
      >
        <Text style={styles.buttonText}>
          {year + 1}
        </Text>

        <Ionicons
          name="chevron-forward"
          size={18}
          color="#0B6E8E"
        />
      </TouchableOpacity>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    marginTop: 22,

    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
  },

  button: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",

    width: "30%",
    height: 52,

    backgroundColor: "#FFFFFF",

    borderRadius: 16,

    borderWidth: 1,
    borderColor: "#D9E4F2",

    shadowColor: "#000",
    shadowOpacity: 0.04,
    shadowRadius: 8,
    shadowOffset: {
      width: 0,
      height: 3,
    },

    elevation: 2,
  },

  buttonText: {
    fontSize: 16,
    fontWeight: "700",
    color: "#0B6E8E",
  },

  activeYear: {
    width: "32%",
    height: 58,

    backgroundColor: "#0B6E8E",

    borderRadius: 18,

    justifyContent: "center",
    alignItems: "center",

    shadowColor: "#0B6E8E",
    shadowOpacity: 0.25,
    shadowRadius: 10,
    shadowOffset: {
      width: 0,
      height: 5,
    },

    elevation: 4,
  },

  activeYearText: {
    marginTop: 2,

    color: "#FFFFFF",

    fontSize: 22,

    fontWeight: "800",
  },
});