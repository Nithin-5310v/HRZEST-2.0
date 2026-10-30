import React, { useMemo } from "react";
import {
  View,
  Text,
  TouchableOpacity,
  StyleSheet,
} from "react-native";

const WEEK_DAYS = [
  "Sun",
  "Mon",
  "Tue",
  "Wed",
  "Thu",
  "Fri",
  "Sat",
];

export default function HolidayCalendar({
  month = 5, // June (0 = January)
  year = 2026,
  holidays = [],
  selectedDate = null,
  onDatePress = () => {},
}) {
  const calendar = useMemo(() => {
    const firstDay = new Date(year, month, 1).getDay();

    const totalDays = new Date(
      year,
      month + 1,
      0
    ).getDate();

    const days = [];

    for (let i = 0; i < firstDay; i++) {
      days.push(null);
    }

    for (let d = 1; d <= totalDays; d++) {
      days.push(d);
    }

    return days;
  }, [month, year]);

  const today = new Date();

  return (
    <View style={styles.container}>
      <Text style={styles.title}>
        Monthly Calendar
      </Text>

      <View style={styles.weekRow}>
        {WEEK_DAYS.map((day) => (
          <Text
            key={day}
            style={[
              styles.weekText,
              day === "Sun" &&
                styles.sundayText,
            ]}
          >
            {day}
          </Text>
        ))}
      </View>

      <View style={styles.grid}>
        {calendar.map((day, index) => {
          if (!day) {
            return (
              <View
                key={index}
                style={styles.emptyCell}
              />
            );
          }

          const holiday = holidays.find(
            (h) => h.day === day
          );

          const isToday =
            today.getDate() === day &&
            today.getMonth() === month &&
            today.getFullYear() === year;

          const isSelected =
            selectedDate === day;

          let background = "#FFFFFF";
          let textColor = "#132026";

          if (holiday) {
            background =
              holiday.type === "Public"
                ? "#FEE2E2"
                : holiday.type ===
                  "Company"
                ? "#CDEBF2"
                : "#F3E8FF";

            textColor =
              holiday.type === "Public"
                ? "#DC2626"
                : holiday.type ===
                  "Company"
                ? "#0B6E8E"
                : "#7C3AED";
          }

          if (isToday) {
            background = "#22C55E";
            textColor = "#FFFFFF";
          }

          if (isSelected) {
            background = "#0B6E8E";
            textColor = "#FFFFFF";
          }

          return (
            <TouchableOpacity
              key={index}
              activeOpacity={0.8}
              style={[
                styles.dayCell,
                {
                  backgroundColor:
                    background,
                },
              ]}
              onPress={() =>
                onDatePress(day)
              }
            >
              <Text
                style={[
                  styles.dayText,
                  {
                    color: textColor,
                  },
                ]}
              >
                {day}
              </Text>

              {holiday && (
                <View
                  style={[
                    styles.dot,
                    {
                      backgroundColor:
                        holiday.type ===
                        "Public"
                          ? "#EF4444"
                          : holiday.type ===
                            "Company"
                          ? "#0B6E8E"
                          : "#7C3AED",
                    },
                  ]}
                />
              )}
            </TouchableOpacity>
          );
        })}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    marginTop: 24,

    backgroundColor: "#FFFFFF",

    borderRadius: 22,

    padding: 18,

    borderWidth: 1,

    borderColor: "#E3ECEF",

    shadowColor: "#000",

    shadowOpacity: 0.04,

    shadowRadius: 10,

    shadowOffset: {
      width: 0,
      height: 5,
    },

    elevation: 2,
  },

  title: {
    fontSize: 20,

    fontWeight: "800",

    color: "#132026",

    marginBottom: 18,
  },

  weekRow: {
    flexDirection: "row",

    justifyContent: "space-between",

    marginBottom: 12,
  },

  weekText: {
    width: "14.2%",

    textAlign: "center",

    fontSize: 14,

    fontWeight: "700",

    color: "#44555B",
  },

  sundayText: {
    color: "#EF4444",
  },

  grid: {
    flexDirection: "row",

    flexWrap: "wrap",
  },

  emptyCell: {
    width: "14.2%",

    aspectRatio: 1,
  },

  dayCell: {
    width: "14.2%",

    aspectRatio: 1,

    justifyContent: "center",

    alignItems: "center",

    borderRadius: 14,

    marginBottom: 10,
  },

  dayText: {
    fontSize: 16,

    fontWeight: "700",
  },

  dot: {
    marginTop: 3,

    width: 6,

    height: 6,

    borderRadius: 3,
  },
});