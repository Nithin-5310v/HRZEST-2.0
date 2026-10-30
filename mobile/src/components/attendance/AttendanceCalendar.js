import React, { useMemo } from "react";
import {
  View,
  Text,
  StyleSheet,
} from "react-native";
import { Calendar } from "react-native-calendars";

export default function AttendanceCalendar({
  records = [],
  month,
  year,
}) {
  const markedDates = useMemo(() => {
    const marks = {};

    records.forEach((item) => {
      if (!item.date) return;

      const statusStr = (item.status || item.attendance_type || item.login_status || "").toLowerCase();
      let color = "#C4D2D6";

      if (statusStr.includes("present") || statusStr.includes("full day")) {
        color = "#22C55E";
      } else if (statusStr.includes("late")) {
        color = "#F59E0B";
      } else if (statusStr.includes("half")) {
        color = "#FB923C";
      } else if (statusStr.includes("holiday") || statusStr.includes("week off")) {
        color = "#8B5CF6";
      } else if (statusStr.includes("absent")) {
        color = "#EF4444";
      } else if (statusStr.includes("leave")) {
        color = "#A855F7";
      } else {
        color = "#C4D2D6";
      }

      marks[item.date] = {
        selected: true,
        selectedColor: color,
        selectedTextColor: "#FFFFFF",
      };
    });

    return marks;
  }, [records]);

  return (
    <View style={styles.card}>
      <View style={styles.header}>
        <Text style={styles.title}>
          Attendance Calendar
        </Text>

        <Text style={styles.subtitle}>
          Monthly Attendance Overview
        </Text>
      </View>

      <Calendar
        current={`${year}-${String(month).padStart(2, "0")}-01`}
        markedDates={markedDates}
        enableSwipeMonths
        firstDay={1}
        hideExtraDays={false}
        theme={{
          calendarBackground: "#FFFFFF",

          monthTextColor: "#0B6E8E",
          textMonthFontWeight: "800",
          textMonthFontSize: 18,

          textDayFontSize: 15,
          textDayFontWeight: "700",

          textDayHeaderFontWeight: "700",
          textDayHeaderFontSize: 13,

          textSectionTitleColor: "#8A9AA0",

          dayTextColor: "#132026",

          todayTextColor: "#0B6E8E",

          selectedDayTextColor: "#FFFFFF",

          arrowColor: "#0B6E8E",

          textDisabledColor: "#D1D5DB",
        }}
        style={styles.calendar}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: "#FFFFFF",

    borderRadius: 26,

    marginTop: 22,

    overflow: "hidden",

    shadowColor: "#000",
    shadowOpacity: 0.06,
    shadowRadius: 14,
    shadowOffset: {
      width: 0,
      height: 6,
    },

    elevation: 5,
  },

  header: {
    paddingHorizontal: 22,
    paddingTop: 22,
    paddingBottom: 12,
  },

  title: {
    fontSize: 20,
    fontWeight: "800",
    color: "#132026",
  },

  subtitle: {
    marginTop: 4,
    color: "#5A6A70",
    fontSize: 13,
    fontWeight: "600",
  },

  calendar: {
    paddingBottom: 18,
  },
});