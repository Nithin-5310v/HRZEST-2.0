import React, { useState } from "react";
import {
  View,
 Text,
  StyleSheet,
  TouchableOpacity,
} from "react-native";
import { Picker } from "@react-native-picker/picker";
import { Ionicons } from "@expo/vector-icons";

export default function PayslipCard({
  onViewPayslip = () => {},
}) {
  const [month, setMonth] = useState("June");
  const [year, setYear] = useState("2026");

  const months = [
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
  ];

  const years = [
    "2024",
    "2025",
    "2026",
    "2027",
  ];

  return (
    <View style={styles.container}>
      <View style={styles.header}>
        <Ionicons
          name="document-text-outline"
          size={22}
          color="#2563EB"
        />

        <Text style={styles.headerTitle}>
          Payslips
        </Text>
      </View>

      <Text style={styles.subtitle}>
        Select a month and year to view your salary
        breakdown.
      </Text>

      <Text style={styles.label}>
        Month
      </Text>

      <View style={styles.pickerContainer}>
        <Picker
          selectedValue={month}
          onValueChange={(value) =>
            setMonth(value)
          }
        >
          {months.map((item) => (
            <Picker.Item
              key={item}
              label={item}
              value={item}
            />
          ))}
        </Picker>
      </View>

      <Text style={styles.label}>
        Year
      </Text>

      <View style={styles.pickerContainer}>
        <Picker
          selectedValue={year}
          onValueChange={(value) =>
            setYear(value)
          }
        >
          {years.map((item) => (
            <Picker.Item
              key={item}
              label={item}
              value={item}
            />
          ))}
        </Picker>
      </View>

      <TouchableOpacity accessibilityRole="button"
        activeOpacity={0.9}
        style={styles.button}
        onPress={() =>
          onViewPayslip(month, year)
        }
      >
        <Ionicons
          name="eye-outline"
          size={20}
          color="#FFFFFF"
        />

        <Text style={styles.buttonText}>
          View Payslip
        </Text>
      </TouchableOpacity>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    marginTop: 22,

    backgroundColor: "#FFFFFF",

    borderRadius: 22,

    padding: 18,

    borderWidth: 1,

    borderColor: "#E6E9EF",

    shadowColor: "#101828",
    shadowOpacity: 0.04,
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
    marginBottom: 6,
  },

  headerTitle: {
    marginLeft: 10,

    fontSize: 20,

    fontWeight: "800",

    color: "#101828",
  },

  subtitle: {
    marginTop: 6,
    marginBottom: 20,

    color: "#475467",

    fontSize: 14,

    lineHeight: 20,
  },

  label: {
    marginBottom: 8,

    fontSize: 14,

    fontWeight: "700",

    color: "#344054",
  },

  pickerContainer: {
    borderWidth: 1,

    borderColor: "#E4E7EC",

    borderRadius: 14,

    marginBottom: 18,

    overflow: "hidden",

    backgroundColor: "#F4F6F9",
  },

  button: {
    height: 56,

    borderRadius: 16,

    backgroundColor: "#2563EB",

    flexDirection: "row",

    justifyContent: "center",

    alignItems: "center",

    marginTop: 8,

    shadowColor: "#2563EB",
    shadowOpacity: 0.25,
    shadowRadius: 10,
    shadowOffset: {
      width: 0,
      height: 5,
    },

    elevation: 4,
  },

  buttonText: {
    marginLeft: 10,

    color: "#FFFFFF",

    fontWeight: "700",

    fontSize: 16,
  },
});