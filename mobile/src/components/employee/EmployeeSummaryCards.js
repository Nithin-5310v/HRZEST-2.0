import React from "react";
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
} from "react-native";

import { Ionicons } from "@expo/vector-icons";

const CARDS = [

  {
    key: "hours",
    title: "Hours",
    subtitle: "Worked Today",
    icon: "time-outline",
    color: "#0B5CAD",
    bg: "#E6EFFA",
    screen: "Attendance",
  },

  {
    key: "attendance",
    title: "Attendance",
    subtitle: "This Month",
    icon: "calendar-outline",
    color: "#16A34A",
    bg: "#ECFDF5",
    screen: "Attendance",
  },

  {
    key: "leave",
    title: "Leave",
    subtitle: "Remaining",
    icon: "leaf-outline",
    color: "#EA580C",
    bg: "#FFF7ED",
    screen: "Leave",
  },

  {
    key: "attendanceGrade",
    title: "Attendance Grade",
    subtitle: "This Month",
    icon: "trending-up-outline",
    color: "#7C3AED",
    bg: "#F5F3FF",
    screen: "Attendance",
  },

];

export default function EmployeeSummaryCards({
  hours = "0h 00m",
  attendance = "0%",
  leaveBalance = "0",
  attendanceGrade = "N/A",
  navigation,
}) {

  const values = {

    hours,

    attendance,

    leave: leaveBalance,

    attendanceGrade,

  };

  return (

    <View style={styles.container}>

      <Text style={styles.heading}>
        Today's Summary
      </Text>

      <View style={styles.grid}>

        {

          CARDS.map((item) => (

            <TouchableOpacity
              key={item.key}
              activeOpacity={0.8}
              style={styles.card}
              onPress={() => {
                if (navigation && item.screen) {
                  navigation.navigate(item.screen);
                }
              }}
            >

              <View style={styles.topRow}>

                <View
                  style={[
                    styles.iconBox,
                    {
                      backgroundColor: item.bg,
                    },
                  ]}
                >

                  <Ionicons
                    name={item.icon}
                    size={18}
                    color={item.color}
                  />

                </View>

                <Ionicons
                  name="chevron-forward"
                  size={16}
                  color="#C3CFDD"
                />

              </View>

              <Text style={styles.value}>
                {values[item.key]}
              </Text>

              <Text style={styles.title}>
                {item.title}
              </Text>

              <Text style={styles.subtitle}>
                {item.subtitle}
              </Text>

            </TouchableOpacity>

          ))

        }

      </View>

    </View>

  );

}

const styles = StyleSheet.create({

  container: {

    marginBottom: 24,

  },

  heading: {

    fontSize: 18,

    fontWeight: "700",

    color: "#131C2A",

    marginBottom: 16,

  },

  grid: {

    flexDirection: "row",

    flexWrap: "wrap",

    justifyContent: "space-between",

  },

  card: {

    width: "48%",

    backgroundColor: "#FFFFFF",

    borderRadius: 20,

    padding: 16,

    marginBottom: 14,

    borderWidth: 1,

    borderColor: "#E3EAF3",

    shadowColor: "#131C2A",

    shadowOpacity: 0.05,

    shadowRadius: 10,

    shadowOffset: {
      width: 0,
      height: 4,
    },

    elevation: 3,

  },

  topRow: {

    flexDirection: "row",

    justifyContent: "space-between",

    alignItems: "center",

    marginBottom: 14,

  },

  iconBox: {

    width: 42,

    height: 42,

    borderRadius: 12,

    justifyContent: "center",

    alignItems: "center",

  },

  value: {

    fontSize: 24,

    fontWeight: "800",

    color: "#131C2A",

    letterSpacing: -0.5,

  },

  title: {

    marginTop: 6,

    fontSize: 14,

    fontWeight: "700",

    color: "#2E3A4A",

  },

  subtitle: {

    marginTop: 4,

    fontSize: 12,

    color: "#8794A5",

  },

});