import React from "react";
import {
  View,
  Text,
  StyleSheet,
} from "react-native";
import { LinearGradient } from "expo-linear-gradient";
import { Ionicons } from "@expo/vector-icons";

export default function CompOffHeaderCard({
  employeeName = "John Doe",
  designation = "Software Engineer",
  department = "Engineering",
  availableDays = "4.5",
}) {
  return (
    <LinearGradient
      colors={["#0B5CAD", "#214AA8"]}
      start={{ x: 0, y: 0 }}
      end={{ x: 1, y: 1 }}
      style={styles.card}
    >
      {/* Background Shape */}

      <View style={styles.bgCircleLarge} />
      <View style={styles.bgCircleSmall} />

      {/* Top */}

      <View style={styles.topRow}>
        <View style={styles.profileSection}>
          <View style={styles.avatar}>
            <Ionicons
              name="person"
              size={26}
              color="#0B5CAD"
            />
          </View>

          <View style={styles.userInfo}>
            <Text style={styles.name}>
              {employeeName}
            </Text>

            <Text style={styles.designation}>
              {designation}
            </Text>

            <View style={styles.departmentRow}>
              <Ionicons
                name="business-outline"
                size={12}
                color="rgba(255,255,255,.75)"
              />

              <Text style={styles.department}>
                {department}
              </Text>
            </View>
          </View>
        </View>

        <View style={styles.liveBadge}>
          <View style={styles.liveDot} />

          <Text style={styles.liveText}>
            ACTIVE
          </Text>
        </View>
      </View>

      {/* Divider */}

      <View style={styles.divider} />

      {/* Bottom */}

      <View style={styles.bottomRow}>
        <View style={styles.balanceSection}>
          <Text style={styles.balanceLabel}>
            Available Comp-Off Balance
          </Text>

          <View style={styles.balanceRow}>
            <Text style={styles.balanceValue}>
              {availableDays}
            </Text>

            <Text style={styles.balanceUnit}>
              Days
            </Text>
          </View>

          <Text style={styles.updatedText}>
            Updated today
          </Text>
        </View>

        <View style={styles.iconCard}>
          <Ionicons
            name="calendar-clear"
            size={32}
            color="#FFFFFF"
          />
        </View>
      </View>
    </LinearGradient>
  );
}

const styles = StyleSheet.create({
  card: {
    borderRadius: 24,

    paddingHorizontal: 22,
    paddingVertical: 20,

    overflow: "hidden",

    marginBottom: 20,

    shadowColor: "#0B5CAD",
    shadowOpacity: 0.18,
    shadowRadius: 18,
    shadowOffset: {
      width: 0,
      height: 10,
    },

    elevation: 8,
  },

  bgCircleLarge: {
    position: "absolute",

    width: 190,
    height: 190,

    borderRadius: 95,

    backgroundColor:
      "rgba(255,255,255,0.05)",

    right: -80,
    top: -70,
  },

  bgCircleSmall: {
    position: "absolute",

    width: 110,
    height: 110,

    borderRadius: 55,

    backgroundColor:
      "rgba(255,255,255,0.08)",

    left: -30,
    bottom: -30,
  },

  topRow: {
    flexDirection: "row",

    justifyContent: "space-between",

    alignItems: "flex-start",
  },

  profileSection: {
    flexDirection: "row",

    flex: 1,

    alignItems: "center",
  },

  avatar: {
    width: 54,
    height: 54,

    borderRadius: 27,

    backgroundColor: "#FFFFFF",

    justifyContent: "center",

    alignItems: "center",
  },

  userInfo: {
    marginLeft: 14,

    flex: 1,
  },

  name: {
    fontSize: 20,

    fontWeight: "800",

    color: "#FFFFFF",
  },

  designation: {
    marginTop: 3,

    fontSize: 13,

    color: "rgba(255,255,255,.90)",

    fontWeight: "600",
  },

  departmentRow: {
    flexDirection: "row",

    alignItems: "center",

    marginTop: 6,
  },

  department: {
    marginLeft: 5,

    color: "rgba(255,255,255,.72)",

    fontSize: 12,

    fontWeight: "500",
  },

  liveBadge: {
    flexDirection: "row",

    alignItems: "center",

    backgroundColor:
      "rgba(255,255,255,.14)",

    paddingHorizontal: 12,

    paddingVertical: 7,

    borderRadius: 20,
  },

  liveDot: {
    width: 7,
    height: 7,

    borderRadius: 4,

    backgroundColor: "#22C55E",

    marginRight: 6,
  },

  liveText: {
    color: "#FFFFFF",

    fontSize: 11,

    fontWeight: "700",
  },

  divider: {
    height: 1,

    backgroundColor:
      "rgba(255,255,255,.12)",

    marginVertical: 18,
  },

  bottomRow: {
    flexDirection: "row",

    justifyContent: "space-between",

    alignItems: "center",
  },

  balanceSection: {
    flex: 1,
  },

  balanceLabel: {
    fontSize: 13,

    color: "rgba(255,255,255,.72)",

    fontWeight: "600",
  },

  balanceRow: {
    flexDirection: "row",

    alignItems: "flex-end",

    marginTop: 6,
  },

  balanceValue: {
    fontSize: 26,

    fontWeight: "800",

    color: "#FFFFFF",

    letterSpacing: -0.5,
  },

  balanceUnit: {
    marginLeft: 6,

    marginBottom: 8,

    fontSize: 16,

    color: "#FFFFFF",

    fontWeight: "700",
  },

  updatedText: {
    marginTop: 6,

    fontSize: 12,

    color: "rgba(255,255,255,.70)",

    fontWeight: "500",
  },

  iconCard: {
    width: 62,

    height: 62,

    borderRadius: 18,

    backgroundColor:
      "rgba(255,255,255,.14)",

    justifyContent: "center",

    alignItems: "center",

    borderWidth: 1,

    borderColor:
      "rgba(255,255,255,.10)",
          shadowColor: "#FFFFFF",
    shadowOpacity: 0.08,
    shadowRadius: 10,
    shadowOffset: {
      width: 0,
      height: 4,
    },

    elevation: 2,
  },
});