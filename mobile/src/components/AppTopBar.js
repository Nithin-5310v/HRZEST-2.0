import React from "react";
import { View, Text, Image, TouchableOpacity, StyleSheet, StatusBar, Platform } from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { useAuth } from "../store/AuthContext";

// The one top bar every screen wears (design: "Final app" board "Shared ·
// Top bar"): room for the phone's status bar, a back arrow on inner pages,
// the HRZEST logo, the page title, optional round actions, and -- once
// signed in -- the company's logo on the right.
export const BAR = {
  bg: "#FFFFFF",
  line: "#E3E9F1",
  text: "#131C2A",
  muted: "#455163",
  tile: "#E7EEF7",
  navy: "#0C3054",
  badge: "#D93025",
};

/** Height the phone's status bar takes: Android draws the app under it
 *  (edge to edge), so the bar starts below it. */
export function useStatusBarHeight() {
  const insets = useSafeAreaInsets();
  if (insets.top) return insets.top;
  return Platform.OS === "android" ? StatusBar.currentHeight || 24 : 0;
}

/** The company's logo, or its initials on navy when it has none. */
export function CompanyLogo({ size = 36, onPress }) {
  const { user } = useAuth();
  // Employee sign-ins don't carry the company's name; its address does.
  const name = user?.company || user?.companySlug || "HRZEST";
  const initials = name
    .split(/[\s_-]+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w[0].toUpperCase())
    .join("") || "H";
  const ring = { width: size, height: size, borderRadius: size / 2 };
  const logo = user?.logo ? (
    <Image source={{ uri: user.logo }} style={[styles.companyImage, ring]} resizeMode="cover" />
  ) : (
    <View style={[styles.companyFallback, ring]}>
      <Text style={[styles.companyInitials, { fontSize: Math.round(size * 0.36) }]}>{initials}</Text>
    </View>
  );
  if (!onPress) return <View accessibilityLabel={`${name || "Company"} logo`}>{logo}</View>;
  return (
    <TouchableOpacity onPress={onPress} activeOpacity={0.8} accessibilityRole="button" accessibilityLabel={`${name || "Company"} logo`}>
      {logo}
    </TouchableOpacity>
  );
}

/**
 * title / subtitle: the page's name and a short line under it.
 * onBack: shows the back arrow (inner pages).
 * actions: [{ icon, label, onPress, badge }] round buttons before the logo.
 * right: replaces the company logo (e.g. a "Help" link before sign-in).
 * showCompany: false on the signed-out screens.
 */
export default function AppTopBar({
  title,
  subtitle,
  onBack,
  actions = [],
  right,
  showCompany = true,
  onCompanyPress,
  titleNode,
}) {
  const top = useStatusBarHeight();
  return (
    <View style={[styles.bar, { paddingTop: top }]}>
      <StatusBar translucent backgroundColor="transparent" barStyle="dark-content" />
      <View style={styles.row}>
        {onBack ? (
          <TouchableOpacity onPress={onBack} style={styles.back} accessibilityRole="button" accessibilityLabel="Back">
            <Ionicons name="arrow-back" size={24} color={BAR.text} />
          </TouchableOpacity>
        ) : null}
        <Image source={require("../../assets/favicon.png")} style={styles.mark} accessibilityLabel="HRZEST" />
        <View style={styles.titles}>
          {titleNode || (
            <Text style={styles.title} numberOfLines={1} accessibilityRole="header">
              {title}
            </Text>
          )}
          {subtitle ? (
            <Text style={styles.subtitle} numberOfLines={1}>
              {subtitle}
            </Text>
          ) : null}
        </View>
        {actions.map((a) => (
          <TouchableOpacity
            key={a.label}
            onPress={a.onPress}
            style={styles.action}
            accessibilityRole="button"
            accessibilityLabel={a.label}
          >
            <Ionicons name={a.icon} size={20} color={BAR.navy} />
            {a.badge ? (
              <View style={styles.badge}>
                <Text style={styles.badgeText}>{a.badge > 9 ? "9+" : a.badge}</Text>
              </View>
            ) : null}
          </TouchableOpacity>
        ))}
        {right !== undefined ? right : showCompany ? <CompanyLogo onPress={onCompanyPress} /> : null}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  bar: { backgroundColor: BAR.bg, borderBottomWidth: 1, borderBottomColor: BAR.line },
  row: { height: 60, flexDirection: "row", alignItems: "center", paddingHorizontal: 16, gap: 10 },
  back: { width: 40, height: 40, marginLeft: -6, borderRadius: 20, alignItems: "center", justifyContent: "center" },
  mark: { width: 32, height: 32, resizeMode: "contain" },
  titles: { flex: 1, minWidth: 0 },
  title: { fontSize: 18, fontWeight: "700", color: BAR.text },
  subtitle: { fontSize: 12, color: BAR.muted, marginTop: 1 },
  action: {
    width: 40, height: 40, borderRadius: 20, backgroundColor: BAR.tile,
    alignItems: "center", justifyContent: "center",
  },
  badge: {
    position: "absolute", top: 2, right: 2, minWidth: 16, height: 16, borderRadius: 8, paddingHorizontal: 3,
    backgroundColor: BAR.badge, alignItems: "center", justifyContent: "center",
  },
  badgeText: { color: "#FFFFFF", fontSize: 10, fontWeight: "700" },
  companyImage: { backgroundColor: "#FFFFFF", borderWidth: 1, borderColor: BAR.line },
  companyFallback: { backgroundColor: BAR.navy, alignItems: "center", justifyContent: "center", borderWidth: 2, borderColor: "#FFFFFF" },
  companyInitials: { color: "#FFFFFF", fontWeight: "900", letterSpacing: 0.3 },
});
