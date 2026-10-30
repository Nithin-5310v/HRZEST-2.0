import React from "react";
import {
  View,
  Text,
  StyleSheet,
  Image,
} from "react-native";

import { Ionicons } from "@expo/vector-icons";
import { LinearGradient } from "expo-linear-gradient";

import { getCompanySlug } from "../../api/client";
import { PORTAL_HOST } from "../../config";

// verification: the registration checklist's documents step, as
// RegistrationChecklistCard reports it -- "verified" (approved, or nothing
// left to finish), "review" (submitted, awaiting review) or "pending"
// (not submitted yet, or rejected). No badge until it's known.
const BADGES = {
  verified: { label: "VERIFIED", icon: "shield-checkmark", color: "#34D399", bg: "rgba(16, 185, 129, 0.2)", border: "rgba(16, 185, 129, 0.4)" },
  review: { label: "UNDER REVIEW", icon: "time-outline", color: "#FBBF24", bg: "rgba(245, 158, 11, 0.2)", border: "rgba(245, 158, 11, 0.4)" },
  pending: { label: "NOT VERIFIED", icon: "alert-circle-outline", color: "#FCA5A5", bg: "rgba(239, 68, 68, 0.2)", border: "rgba(239, 68, 68, 0.4)" },
};

export default function DashboardHeroCard({
  adminName = "Administrator",
  company = "Workforce Portal",
  totalEmployees = 0,
  present = 0,
  attendance = "0%",
  payroll = "₹0",
  profileImage,
  verification,
}) {
  const slug = getCompanySlug();
  const badge = BADGES[verification];

  return (

    <LinearGradient
      colors={["#0C3054", "#0A4E96"]}
      start={{ x: 0, y: 0 }}
      end={{ x: 1, y: 1 }}
      style={styles.container}
    >
      {/* Executive Company Banner Header */}
      <View style={styles.companyBannerContainer}>
        <View style={styles.companyBannerLeft}>
          {profileImage ? (
            <Image
              source={{ uri: profileImage }}
              style={styles.companyLogoImg}
              resizeMode="cover"
            />
          ) : (
            <Image
              source={require("../../../assets/company_logo.png")}
              style={styles.companyLogoImg}
              resizeMode="cover"
            />
          )}
          <View style={{ flex: 1, marginLeft: 10 }}>
            <Text style={styles.companyBannerName} numberOfLines={1}>
              {(company && company !== "Workforce Portal"
                ? company
                : "HRZEST TECHNOLOGIES"
              ).toUpperCase()}
            </Text>
            {slug ? (
              <View style={styles.tenantMetaRow}>
                <Ionicons name="globe-outline" size={10} color="#4C9BE8" style={{ marginRight: 3 }} />
                <Text style={styles.companyBannerTenant} numberOfLines={1}>
                  {PORTAL_HOST}/{slug}
                </Text>
              </View>
            ) : null}
          </View>
        </View>
        {badge ? (
          <View style={[styles.verifiedBadge, { backgroundColor: badge.bg, borderColor: badge.border }]}>
            <Ionicons name={badge.icon} size={12} color={badge.color} style={{ marginRight: 4 }} />
            <Text style={[styles.verifiedText, { color: badge.color }]}>{badge.label}</Text>
          </View>
        ) : null}
      </View>

      <View style={styles.topRow}>

        <View style={{ flex: 1 }}>

          <Text style={styles.small}>
            Welcome Back 👋
          </Text>

          <Text
            numberOfLines={1}
            style={styles.name}
          >
            {adminName}
          </Text>

          <Text
            numberOfLines={1}
            style={styles.companySub}
          >
            Executive Administrator
          </Text>

          <View style={styles.dateRow}>

            <Ionicons
              name="calendar-outline"
              size={14}
              color="rgba(255,255,255,.9)"
            />

            <Text style={styles.date}>
              {new Date().toLocaleDateString(
                "en-US",
                {
                  weekday: "long",
                  month: "long",
                  day: "numeric",
                }
              )}
            </Text>

          </View>

        </View>

        {profileImage ? (

          <Image
            source={{
              uri: profileImage,
            }}
            style={styles.avatar}
          />

        ) : (
          <View style={[styles.avatarPlaceholder, { backgroundColor: "rgba(255,255,255,0.2)", borderWidth: 1, borderColor: "rgba(255,255,255,0.4)" }]}>
            <Text style={{ color: "#FFFFFF", fontWeight: "900", fontSize: 24 }}>
              {(company || adminName || "A").charAt(0).toUpperCase()}
            </Text>
          </View>
        )}

      </View>

      <View style={styles.divider} />

      <View style={styles.statsRow}>

        <View style={styles.stat}>

          <Text style={styles.value}>
            {totalEmployees}
          </Text>

          <Text style={styles.label}>
            Employees
          </Text>

        </View>

        <View style={styles.separator} />

        <View style={styles.stat}>

          <Text style={styles.value}>
            {present}
          </Text>

          <Text style={styles.label}>
            Present
          </Text>

        </View>

        <View style={styles.separator} />

        <View style={styles.stat}>

          <Text style={styles.value}>
            {attendance}
          </Text>

          <Text style={styles.label}>
            Attendance
          </Text>

        </View>

        <View style={styles.separator} />

        <View style={styles.stat}>

          <Text style={styles.value}>
            {payroll}
          </Text>

          <Text style={styles.label}>
            Payroll
          </Text>

        </View>

      </View>

    </LinearGradient>

  );

}

const styles = StyleSheet.create({

  container: {

    borderRadius: 28,

    padding: 22,

    marginBottom: 24,

    shadowColor: "#0B5CAD",

    shadowOpacity: 0.25,

    shadowRadius: 18,

    shadowOffset: {
      width: 0,
      height: 10,
    },

    elevation: 12,

  },

  companyBannerContainer: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    backgroundColor: "rgba(255, 255, 255, 0.12)",
    borderColor: "rgba(255, 255, 255, 0.22)",
    borderWidth: 1,
    borderRadius: 16,
    paddingHorizontal: 14,
    paddingVertical: 10,
    marginBottom: 16,
  },
  companyBannerLeft: {
    flexDirection: "row",
    alignItems: "center",
    flex: 1,
    marginRight: 8,
  },
  companyLogoImg: {
    width: 36,
    height: 36,
    borderRadius: 18,
    borderWidth: 1.5,
    borderColor: "#4C9BE8",
    backgroundColor: "#0C3054",
  },
  companyBannerName: {
    color: "#FFFFFF",
    fontSize: 14,
    fontWeight: "900",
    letterSpacing: 0.5,
  },
  tenantMetaRow: {
    flexDirection: "row",
    alignItems: "center",
    marginTop: 2,
  },
  companyBannerTenant: {
    color: "#A9CBF2",
    fontSize: 10,
    fontWeight: "800",
    letterSpacing: 0.6,
  },
  verifiedBadge: {
    flexDirection: "row",
    alignItems: "center",
    backgroundColor: "rgba(16, 185, 129, 0.2)",
    borderColor: "rgba(16, 185, 129, 0.4)",
    borderWidth: 1,
    paddingHorizontal: 8,
    paddingVertical: 4,
    borderRadius: 10,
  },
  verifiedText: {
    color: "#34D399",
    fontSize: 10,
    fontWeight: "900",
    letterSpacing: 0.5,
  },
  companySub: {
    marginTop: 2,
    color: "#A9CBF2",
    fontSize: 12,
    fontWeight: "700",
  },

  topRow: {

    flexDirection: "row",

    alignItems: "center",

  },

  small: {

    color: "rgba(255,255,255,.9)",

    fontSize: 13,

    fontWeight: "600",

  },

  name: {

    marginTop: 6,

    fontSize: 28,

    fontWeight: "800",

    color: "#FFFFFF",

  },

  company: {

    marginTop: 4,

    color: "rgba(255,255,255,.85)",

    fontSize: 14,

  },

  dateRow: {

    flexDirection: "row",

    alignItems: "center",

    marginTop: 14,

  },

  date: {

    color: "#FFFFFF",

    marginLeft: 6,

    fontSize: 13,

    fontWeight: "600",

  },

  avatar: {

    width: 72,

    height: 72,

    borderRadius: 36,

    borderWidth: 3,

    borderColor: "#FFFFFF",

  },

  avatarPlaceholder: {

    width: 72,

    height: 72,

    borderRadius: 36,

    backgroundColor: "rgba(255,255,255,.2)",

    justifyContent: "center",

    alignItems: "center",

  },

  divider: {

    height: 1,

    backgroundColor: "rgba(255,255,255,.2)",

    marginVertical: 20,

  },

  statsRow: {

    flexDirection: "row",

    justifyContent: "space-between",

    alignItems: "center",

  },

  stat: {

    flex: 1,

    alignItems: "center",

  },

  value: {

    color: "#FFFFFF",

    fontSize: 22,

    fontWeight: "800",

  },

  label: {

    marginTop: 5,

    color: "rgba(255,255,255,.9)",

    fontSize: 12,

    fontWeight: "600",

  },

  separator: {

    width: 1,

    height: 36,

    backgroundColor: "rgba(255,255,255,.25)",

  },

});