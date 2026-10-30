import React, { useEffect, useState } from "react";
import {
  View,
  ScrollView,
  StyleSheet,
  RefreshControl,
  TouchableOpacity,
  Text,
} from "react-native";
import { LinearGradient } from "expo-linear-gradient";
import { Ionicons } from "@expo/vector-icons";
import { useNavigation } from "@react-navigation/native";

import { Alert } from "react-native";
import * as Location from "expo-location";
import { fetchEmployeeAttendance, employeeCheckin } from "../../api/client";
import { queuePunch } from "../../utils/offlineQueue";
import { getDeviceId } from "../../utils/deviceId";

import { useAuth } from "../../store/AuthContext";
import { useTheme } from "../../store/ThemeContext";

import LoadingSkeleton from "../../components/ui/LoadingSkeleton";
import AttendanceScannerModal from "../AttendanceScannerModal";

import MonthYearPicker from "../../components/attendance/MonthYearPicker";
import AttendanceSummaryCard from "../../components/attendance/AttendanceSummaryCard";
import AttendanceStatusCard from "../../components/attendance/AttendanceStatusCard";
import AttendanceCalendar from "../../components/attendance/AttendanceCalendar";
import AttendanceLegend from "../../components/attendance/AttendanceLegend";
import AttendanceHistoryCard from "../../components/attendance/AttendanceHistoryCard";
import AttendanceEmptyState from "../../components/attendance/AttendanceEmptyState";

import ProfileHeader from "../../components/profile/ProfileHeader";

export default function AttendanceScreen() {
  const navigation = useNavigation();
  const { signOut } = useAuth();
  const { colors } = useTheme();
  const styles = React.useMemo(() => makeStyles(colors), [colors]);

  const today = new Date();

  const [month, setMonth] = useState(today.getMonth() + 1);
  const [year, setYear] = useState(today.getFullYear());

  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [checking, setChecking] = useState(false);
  const [showScanner, setShowScanner] = useState(false);

  const [attendance, setAttendance] = useState([]);

  const loadAttendance = async () => {
    try {
      const res = await fetchEmployeeAttendance(year, month);

      if (res.data?.ok) {
        setAttendance(res.data.records || []);
      } else {
        setAttendance([]);
      }
    } catch {
      setAttendance([]);
    }

    setLoading(false);
    setRefreshing(false);
  };

  const handleCheckIn = async () => {
    setChecking(true);
    let lat = null;
    let lon = null;
    let mocked = false;
    try {
      const { status } = await Location.requestForegroundPermissionsAsync();
      if (status === "granted") {
        const loc = await Promise.race([
          Location.getCurrentPositionAsync({ accuracy: Location.Accuracy.High }),
          new Promise((_, reject) => setTimeout(() => reject(new Error("timeout")), 8000)),
        ]);
        lat = loc.coords.latitude;
        lon = loc.coords.longitude;
        mocked = !!loc.mocked;
      }
    } catch (_) {}

    const timeNow = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    const todayIso = new Date().toISOString().split("T")[0];

    try {
      const res = await employeeCheckin(lat, lon, { mocked, device_id: await getDeviceId() });
      if (res.data?.ok) {
        const title = res.data.action === "logout" ? "✅ Shift Completed ⏱" : "✅ Attendance Marked 📍";
        Alert.alert(title, `${res.data.name || ""}\nStatus: ${res.data.status}\nTime: ${res.data.time || timeNow}`, [
          { text: "OK", onPress: () => loadAttendance() }
        ]);
        setAttendance((prev) => {
          const updated = [...prev];
          const idx = updated.findIndex((x) => x.date === todayIso);
          if (idx >= 0) {
            updated[idx] = { ...updated[idx], login_time: timeNow, status: "Checked In", attendance_type: "Full Day" };
          } else {
            updated.unshift({ date: todayIso, login_time: timeNow, logout_time: "", status: "Checked In", attendance_type: "Full Day" });
          }
          return updated;
        });
      } else {
        Alert.alert("Notice", res.data?.msg || "Could not mark attendance.");
      }
    } catch (e) {
      if (e.response?.status === 401) {
        Alert.alert(
          "Session Expired",
          "Your login session has expired. Please sign in again to continue.",
          [{ text: "Sign In", onPress: () => signOut() }]
        );
      } else if (e.response) {
        Alert.alert("Notice", e.response?.data?.msg || "Could not mark attendance.");
      } else {
        await queuePunch(lat, lon, { mocked });
        const existing = attendance.find((x) => x.date === todayIso);
        const wasCheckedIn = existing?.login_time && !existing?.logout_time;
        setAttendance((prev) => {
          const updated = [...prev];
          const idx = updated.findIndex((x) => x.date === todayIso);
          if (idx >= 0) {
            updated[idx] = wasCheckedIn
              ? { ...updated[idx], logout_time: timeNow, status: "Checked Out" }
              : { ...updated[idx], login_time: timeNow, status: "Checked In", attendance_type: "Full Day" };
          } else {
            updated.unshift({ date: todayIso, login_time: timeNow, logout_time: "", status: "Checked In", attendance_type: "Full Day" });
          }
          return updated;
        });
        Alert.alert(
          wasCheckedIn ? "Check-Out Recorded 📍" : "Check-In Recorded 📍",
          `Punch recorded at ${timeNow}. Saved locally and will sync to server.`
        );
      }
    }
    setChecking(false);
  };

  useEffect(() => {
    loadAttendance();
  }, [month, year]);

  const previousMonth = () => {
    if (month === 1) {
      setMonth(12);
      setYear((y) => y - 1);
    } else {
      setMonth((m) => m - 1);
    }
  };

  const nextMonth = () => {
    if (month === 12) {
      setMonth(1);
      setYear((y) => y + 1);
    } else {
      setMonth((m) => m + 1);
    }
  };

  const present = attendance.filter((x) => {
    const s = (x.attendance_type || x.login_status || x.status || "").toLowerCase();
    return s.includes("present") || s.includes("full day") || s.includes("half day");
  }).length;

  const absent = attendance.filter((x) => {
    const s = (x.attendance_type || x.login_status || x.status || "").toLowerCase();
    return s.includes("absent");
  }).length;

  const late = attendance.filter((x) => {
    const s = (x.attendance_type || x.login_status || x.status || "").toLowerCase();
    return s.includes("late");
  }).length;

  const totalMarked = attendance.length;
  const percentage = totalMarked === 0 ? 0 : Math.round((present / totalMarked) * 100);

  // Today's or latest attendance record
  const todayIso = new Date().toISOString().split("T")[0];
  const todayRecord = attendance.find((x) => x.date === todayIso) || attendance[0] || {};

  const checkInTime = todayRecord.login_time || todayRecord.check_in || "--:--";
  const checkOutTime = todayRecord.logout_time || todayRecord.check_out || "--:--";
  const statusVal = todayRecord.attendance_type || todayRecord.login_status || todayRecord.status || "Not Marked";

  let hoursVal = "--";
  if (todayRecord.worked_minutes) {
    const h = Math.floor(todayRecord.worked_minutes / 60);
    const m = todayRecord.worked_minutes % 60;
    hoursVal = `${h}h ${m}m`;
  } else if (todayRecord.hours) {
    hoursVal = `${todayRecord.hours} hrs`;
  } else if (todayRecord.login_time && !todayRecord.logout_time) {
    hoursVal = "In Progress";
  }

  if (loading) {
    return (
      <LinearGradient
        colors={colors.screenGradient}
        style={{ flex: 1 }}
      >
        <LoadingSkeleton />
      </LinearGradient>
    );
  }

  return (
    <LinearGradient
      colors={colors.screenGradient}
      style={styles.container}
    >
      <AttendanceScannerModal
        visible={showScanner}
        onClose={() => setShowScanner(false)}
        onSuccess={() => loadAttendance()}
      />

      <ProfileHeader title="Attendance Logs" subtitle="EMPLOYEE PORTAL" />

      <ScrollView
        showsVerticalScrollIndicator={false}
        contentContainerStyle={styles.content}
        refreshControl={
          <RefreshControl
            refreshing={refreshing}
            onRefresh={() => {
              setRefreshing(true);
              loadAttendance();
            }}
            colors={["#0B6E8E"]}
          />
        }
      >
        <MonthYearPicker
          month={month}
          year={year}
          onPrevious={previousMonth}
          onNext={nextMonth}
        />

        <AttendanceSummaryCard
          percentage={percentage}
          present={present}
          absent={absent}
          late={late}
        />

        <AttendanceStatusCard
          checkIn={checkInTime}
          checkOut={checkOutTime}
          workingHours={hoursVal}
          status={statusVal}
          onCheckIn={() => setShowScanner(true)}
          checking={checking}
        />

        {attendance.length > 0 ? (
          <>
            <AttendanceCalendar
              month={month}
              year={year}
              records={attendance}
            />

            <AttendanceLegend />

            <AttendanceHistoryCard
              records={attendance}
            />
          </>
        ) : (
          <AttendanceEmptyState />
        )}
      </ScrollView>
    </LinearGradient>
  );
}

const makeStyles = (colors) => StyleSheet.create({
  container: {
    flex: 1,
  },

  header: {
    paddingHorizontal: 20,
    paddingTop: 56,
    paddingBottom: 18,

    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
  },

  menuButton: {
    width: 46,
    height: 46,
    borderRadius: 14,

    backgroundColor: colors.card,

    justifyContent: "center",
    alignItems: "center",

    elevation: 4,

    shadowColor: "#000",
    shadowOpacity: 0.05,
    shadowRadius: 8,
    shadowOffset: {
      width: 0,
      height: 3,
    },
  },

  profile: {
    width: 46,
    height: 46,
    borderRadius: 14,

    backgroundColor: colors.card,

    justifyContent: "center",
    alignItems: "center",

    elevation: 4,

    shadowColor: "#000",
    shadowOpacity: 0.05,
    shadowRadius: 8,
    shadowOffset: {
      width: 0,
      height: 3,
    },
  },

  smallTitle: {
    fontSize: 13,
    color: colors.textSecondary,
    textAlign: "center",
    fontWeight: "600",
  },

  title: {
    marginTop: 3,
    fontSize: 28,
    color: colors.text,
    fontWeight: "800",
    textAlign: "center",
  },

  content: {
    paddingHorizontal: 18,
    paddingBottom: 120,
  },
});