import React, { useState, useEffect } from "react";
import {
  ScrollView,
  View,
  Text,
  ActivityIndicator,
  RefreshControl,
  StyleSheet,
  TouchableOpacity,
} from "react-native";
import SafeAreaView from "../../components/SafeAreaView";
import { Ionicons } from "@expo/vector-icons";

import ProfileHeader from "../../components/profile/ProfileHeader";
import HolidayHeaderCard from "../../components/holidays/HolidayHeaderCard";
import HolidaySummaryCard from "../../components/holidays/HolidaySummaryCard";
import YearSelector from "../../components/holidays/YearSelector";
import HolidayLegend from "../../components/holidays/HolidayLegend";
import HolidayCalendar from "../../components/holidays/HolidayCalendar";
import HolidayList from "../../components/holidays/HolidayList";
import EmptyHolidayCard from "../../components/holidays/EmptyHolidayCard";
import { fetchEmployeeHolidays } from "../../api/client";
import { useTheme } from "../../store/ThemeContext";

const MONTH_NOW = new Date().getMonth() + 1;
const YEAR_NOW = new Date().getFullYear();

export default function HolidaysScreen() {
  const { colors } = useTheme();
  const styles = React.useMemo(() => makeStyles(colors), [colors]);
  const [year, setYear] = useState(YEAR_NOW);
  const [month, setMonth] = useState(MONTH_NOW);
  const [selectedDate, setSelectedDate] = useState(null);
  // allHolidays is the full, unfiltered fetch -- /api/employee/holidays
  // (blueprints/leave.py's api_employee_holidays) has no year parameter
  // and returns every holiday ever entered, so the Year Selector filters
  // client-side against this rather than re-fetching (there's nothing
  // server-side to filter by).
  const [allHolidays, setAllHolidays] = useState([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  const loadHolidays = async () => {
    try {
      const res = await fetchEmployeeHolidays();
      if (res?.data?.holidays && Array.isArray(res.data.holidays)) {
        setAllHolidays(res.data.holidays);
      } else if (Array.isArray(res?.data)) {
        setAllHolidays(res.data);
      } else {
        setAllHolidays([]);
      }
    } catch (_) {
      setAllHolidays([]);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  };

  useEffect(() => {
    loadHolidays();
  }, []);

  const holidays = allHolidays.filter((h) => h.date && new Date(h.date).getFullYear() === year);

  const upcoming = holidays
    .filter((h) => !h.passed)
    .sort((a, b) => new Date(a.date) - new Date(b.date));
  const nextHoliday = upcoming[0];
  const remainingDays = nextHoliday
    ? Math.max(0, Math.round((new Date(nextHoliday.date) - new Date()) / (1000 * 60 * 60 * 24)))
    : 0;
  return (
    <SafeAreaView style={styles.container}>
      <ProfileHeader
        title="Holiday Calendar"
        showBack={false}
      />

      <ScrollView
        showsVerticalScrollIndicator={false}
        contentContainerStyle={styles.content}
      >
        {/* Header */}

        <HolidayHeaderCard
          year={year}
          totalHolidays={holidays.length}
          upcomingCount={upcoming.length}
        />

        {/* Upcoming */}

        <HolidaySummaryCard
          upcomingHoliday={nextHoliday?.name || "No upcoming holiday"}
          holidayDate={nextHoliday ? new Date(nextHoliday.date).toLocaleDateString("en-IN", { day: "numeric", month: "long", year: "numeric" }) : ""}
          remainingDays={remainingDays}
          hasUpcoming={!!nextHoliday}
        />

        {/* Year */}

        <YearSelector
          year={year}
          onPrevious={() =>
            setYear(year - 1)
          }
          onNext={() =>
            setYear(year + 1)
          }
        />

        {/* Legend */}

        <HolidayLegend />

        {/* Calendar -- was permanently stuck showing June regardless of
            year/actual date; now defaults to the real current month and
            has its own prev/next since HolidayCalendar has no built-in
            month navigation. */}

        <View style={styles.monthNavRow}>
          <TouchableOpacity
            onPress={() => {
              if (month === 1) { setMonth(12); setYear(year - 1); } else { setMonth(month - 1); }
            }}
            style={styles.monthNavBtn}
          >
            <Ionicons name="chevron-back" size={18} color={colors.primary} />
          </TouchableOpacity>
          <Text style={styles.monthNavLabel}>
            {new Date(year, month - 1).toLocaleString("en-IN", { month: "long", year: "numeric" })}
          </Text>
          <TouchableOpacity
            onPress={() => {
              if (month === 12) { setMonth(1); setYear(year + 1); } else { setMonth(month + 1); }
            }}
            style={styles.monthNavBtn}
          >
            <Ionicons name="chevron-forward" size={18} color={colors.primary} />
          </TouchableOpacity>
        </View>

        <HolidayCalendar
          month={month - 1}
          year={year}
          holidays={holidays}
          selectedDate={selectedDate}
          onDatePress={(day) =>
            setSelectedDate(day)
          }
        />

        {/* Holiday List */}

        <Text style={styles.sectionTitle}>
          Holidays
        </Text>

        {holidays.length > 0 ? (
          <HolidayList
            holidays={holidays}
          />
        ) : (
          <EmptyHolidayCard />
        )}

        <View
          style={{
            height: 40,
          }}
        />
      </ScrollView>
    </SafeAreaView>
  );
}
const makeStyles = (colors) => StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: colors.background,
  },

  content: {
    paddingHorizontal: 18,
    paddingBottom: 120,
  },

  monthNavRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    marginTop: 20,
    marginBottom: 8,
  },
  monthNavBtn: {
    width: 34,
    height: 34,
    borderRadius: 10,
    backgroundColor: colors.primaryLight,
    justifyContent: "center",
    alignItems: "center",
  },
  monthNavLabel: {
    fontSize: 14,
    fontWeight: "800",
    color: colors.text,
  },
  sectionTitle: {
    marginTop: 26,
    marginBottom: 16,

    fontSize: 16,

    fontWeight: "800",

    color: colors.text,

    letterSpacing: -0.4,
  },

  card: {
    backgroundColor: colors.card,

    borderRadius: 22,

    padding: 20,

    marginBottom: 18,

    borderWidth: 1,

    borderColor: colors.border,

    shadowColor: "#132026",
    shadowOpacity: 0.05,
    shadowRadius: 10,
    shadowOffset: {
      width: 0,
      height: 5,
    },

    elevation: 3,
  },

  cardHeader: {
    flexDirection: "row",

    alignItems: "center",
  },

  iconBox: {
    width: 44,
    height: 44,

    borderRadius: 14,

    backgroundColor: colors.primaryLight,

    justifyContent: "center",
    alignItems: "center",

    marginRight: 14,
  },

  row: {
    flexDirection: "row",

    justifyContent: "space-between",

    alignItems: "center",

    paddingVertical: 12,

    borderBottomWidth: 1,

    borderBottomColor: colors.border,
  },

  rowLeft: {
    flexDirection: "row",

    alignItems: "center",

    flex: 1,
  },

  rowRight: {
    alignItems: "flex-end",
  },

  iconContainer: {
    width: 42,
    height: 42,

    borderRadius: 14,

    backgroundColor: colors.primaryLight,

    justifyContent: "center",
    alignItems: "center",

    marginRight: 14,
  },

  title: {
    fontSize: 13,

    fontWeight: "700",

    color: colors.text,
  },

  subtitle: {
    marginTop: 3,

    fontSize: 12,

    color: colors.textSecondary,
  },

  value: {
    fontSize: 14,

    fontWeight: "800",

    color: colors.primary,
  },

  badge: {
    marginTop: 6,

    paddingHorizontal: 12,

    paddingVertical: 5,

    borderRadius: 16,

    backgroundColor: colors.primaryLight,
  },

  badgeText: {
    color: colors.primary,

    fontWeight: "700",

    fontSize: 11,
  },

  infoCard: {
    marginTop: 20,

    backgroundColor: colors.primaryLight,

    borderLeftWidth: 4,

    borderLeftColor: colors.primary,

    borderRadius: 18,

    padding: 18,
  },

  infoTitle: {
    fontSize: 14,

    fontWeight: "800",

    color: colors.primary,

    marginBottom: 8,
  },

  infoText: {
    color: colors.textSecondary,

    fontSize: 12,

    lineHeight: 18,

    fontWeight: "500",
  },

  divider: {
    height: 1,

    backgroundColor: colors.border,

    marginVertical: 20,
  },

  footerCard: {
    marginTop: 20,

    backgroundColor: colors.card,

    borderRadius: 22,

    padding: 20,

    borderWidth: 1,

    borderColor: colors.border,

    shadowColor: "#132026",
    shadowOpacity: 0.05,
    shadowRadius: 10,
    shadowOffset: {
      width: 0,
      height: 5,
    },

    elevation: 3,
  },

  footerTitle: {
    fontSize: 14,

    fontWeight: "800",

    color: colors.text,

    marginBottom: 10,
  },

  footerText: {
    fontSize: 12,

    lineHeight: 18,

    color: colors.textSecondary,
  },

  statsRow: {
    flexDirection: "row",

    justifyContent: "space-between",

    marginTop: 18,
  },

  statBox: {
    flex: 1,

    backgroundColor: colors.card,

    borderRadius: 18,

    paddingVertical: 16,

    alignItems: "center",

    marginHorizontal: 4,

    borderWidth: 1,

    borderColor: colors.border,

    shadowColor: "#132026",
    shadowOpacity: 0.03,
    shadowRadius: 8,
    shadowOffset: {
      width: 0,
      height: 3,
    },

    elevation: 2,
  },

  statNumber: {
    marginTop: 6,

    fontSize: 20,

    fontWeight: "800",

    color: colors.primary,
  },

  statLabel: {
    marginTop: 4,

    fontSize: 12,

    color: colors.textSecondary,

    fontWeight: "600",

    textAlign: "center",
  },
});