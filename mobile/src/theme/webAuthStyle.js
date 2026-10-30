import React from "react";
import { View, Text, Image, TouchableOpacity, ActivityIndicator, StyleSheet } from "react-native";
import { LinearGradient } from "expo-linear-gradient";
import { Ionicons } from "@expo/vector-icons";

// The HRzest Material You look for the app's logged-out screens, shared
// with the website (static/landing_v2.css tokens): the logo's teal-blue
// as primary, navy for headings, soft teal surfaces, pill buttons.
// Light theme only, like those pages.

export const WEB = {
  brand: "#0B6E8E",          // --accent-cyan: the logo's teal-blue
  brandDark: "#0C3054",      // --accent-blue: the wordmark's navy
  brandGreen: "#3CB484",     // the logo's green leaf
  text: "#132026",           // --text-main
  textMuted: "#44555B",      // --text-muted
  textSubtle: "#8A9AA0",     // --text-subtle
  card: "#FFFFFF",           // --bg-card
  field: "#ECF3F5",          // --bg-tertiary: Material filled field
  fieldAlt: "#E3ECEF",
  border: "rgba(15,23,42,0.08)",      // --border-color
  borderGlow: "rgba(11,110,142,0.35)", // --border-color-glow
  brandTint: "rgba(11,110,142,0.06)",
  success: "#059669",        // --accent-emerald
  error: "#E11D48",          // --accent-rose
  radiusSm: 12,
  radiusMd: 16,
  radiusLg: 28,
  radiusPill: 999,
};

// --gradient-brand: the logo leaf, linear-gradient(135deg, #0B8AA0, #0A4E96)
export const WEB_BRAND_GRADIENT = {
  colors: ["#0B8AA0", "#0A4E96"],
  start: { x: 0, y: 0 },
  end: { x: 1, y: 1 },
};

// The page background: linear-gradient(160deg, #EAF6F8 0%, #EEF7F2 28%, #F2F8FA 56%, #F5FAFB 100%)
export const WEB_PAGE_GRADIENT = {
  colors: ["#EAF6F8", "#EEF7F2", "#F2F8FA", "#F5FAFB"],
  locations: [0, 0.28, 0.56, 1],
  start: { x: 0.33, y: 0 },
  end: { x: 0.67, y: 1 },
};

/** Full-screen page background. */
export function WebPage({ children, style }) {
  return (
    <LinearGradient {...WEB_PAGE_GRADIENT} style={[webAuthStyles.page, style]}>
      {children}
    </LinearGradient>
  );
}

/** The website's top bar: the HRzest logo and wordmark, with an optional
 *  button on the right (e.g. "Register a Company" on the login page). */
export function WebTopBar({ right, style }) {
  return (
    <View style={[webAuthStyles.topBar, style]}>
      <View style={webAuthStyles.brandRow} accessibilityRole="header" accessibilityLabel="HRzest">
        <Image source={require("../../assets/favicon.png")} style={webAuthStyles.brandLogo} />
        <Text style={webAuthStyles.brandName}>HRZEST</Text>
      </View>
      {right}
    </View>
  );
}

/** The white card with the 4px brand-gradient bar along its top. */
export function WebCard({ children, style }) {
  return (
    <View style={[webAuthStyles.card, style]}>
      <LinearGradient {...WEB_BRAND_GRADIENT} start={{ x: 0, y: 0 }} end={{ x: 1, y: 0 }} style={webAuthStyles.cardBar} />
      {children}
    </View>
  );
}

/** The card's stacked header: gradient icon tile, title, subtitle. Pass
 *  `image` (a rendered <Image>) to show a company logo in the tile. */
export function WebCardHeader({ icon, image, title, subtitle }) {
  return (
    <View style={webAuthStyles.cardHeader}>
      <LinearGradient {...WEB_BRAND_GRADIENT} style={webAuthStyles.iconTile}>
        {image || <Ionicons name={icon} size={28} color="#FFFFFF" />}
      </LinearGradient>
      <Text style={webAuthStyles.title}>{title}</Text>
      {subtitle ? <Text style={webAuthStyles.subtitle}>{subtitle}</Text> : null}
    </View>
  );
}

/** The full-width filled (Material) submit button. */
export function WebButton({ title, onPress, loading, disabled, icon = "arrow-forward", style }) {
  return (
    <TouchableOpacity onPress={onPress} disabled={disabled || loading} activeOpacity={0.85} style={style}>
      <View style={webAuthStyles.button}>
        {loading ? (
          <ActivityIndicator color="#FFFFFF" />
        ) : (
          <>
            <Text style={webAuthStyles.buttonText}>{title}</Text>
            {icon ? <Ionicons name={icon} size={16} color="#FFFFFF" style={{ marginLeft: 8 }} /> : null}
          </>
        )}
      </View>
    </TouchableOpacity>
  );
}

/** The small tonal button in the top bar (the website's .nav-link-btn). */
export function WebNavButton({ title, icon, onPress }) {
  return (
    <TouchableOpacity onPress={onPress} activeOpacity={0.85}>
      <View style={webAuthStyles.navButton}>
        {icon ? <Ionicons name={icon} size={14} color={WEB.brandDark} style={{ marginRight: 6 }} /> : null}
        <Text style={webAuthStyles.navButtonText}>{title}</Text>
      </View>
    </TouchableOpacity>
  );
}

export const webAuthStyles = StyleSheet.create({
  page: { flex: 1 },
  scroll: { flexGrow: 1, paddingHorizontal: 20, paddingTop: 12, paddingBottom: 32 },

  topBar: {
    flexDirection: "row", alignItems: "center", justifyContent: "space-between",
    paddingHorizontal: 20, paddingVertical: 14,
    backgroundColor: "rgba(245,250,251,0.9)",
  },
  brandRow: { flexDirection: "row", alignItems: "center" },
  brandLogo: { width: 38, height: 38, resizeMode: "contain" },
  brandName: { marginLeft: 8, fontSize: 21, fontWeight: "800", letterSpacing: 1.2, color: WEB.brandDark },
  navButton: {
    flexDirection: "row", alignItems: "center", paddingHorizontal: 16, paddingVertical: 10, borderRadius: WEB.radiusPill,
    backgroundColor: "#CDEBF2",
  },
  navButtonText: { fontSize: 13, fontWeight: "700", color: WEB.brandDark },

  card: {
    backgroundColor: WEB.card, borderRadius: WEB.radiusLg,
    paddingHorizontal: 24, paddingTop: 34, paddingBottom: 26, overflow: "hidden",
    shadowColor: "#0C3054", shadowOpacity: 0.08, shadowRadius: 18, shadowOffset: { width: 0, height: 8 }, elevation: 2,
  },
  cardBar: { position: "absolute", top: 0, left: 0, right: 0, height: 4 },
  cardHeader: { alignItems: "center", marginBottom: 26 },
  iconTile: {
    width: 64, height: 64, borderRadius: WEB.radiusMd, justifyContent: "center", alignItems: "center",
    marginBottom: 16, overflow: "hidden",
  },
  title: { fontSize: 24, fontWeight: "700", letterSpacing: -0.2, color: WEB.brandDark, textAlign: "center", marginBottom: 6 },
  subtitle: { fontSize: 13.5, fontWeight: "600", color: WEB.textMuted, textAlign: "center", lineHeight: 19 },

  label: {
    fontSize: 11, fontWeight: "700", letterSpacing: 0.6, textTransform: "uppercase",
    color: WEB.textMuted, marginBottom: 7,
  },
  field: { marginBottom: 18 },
  input: {
    backgroundColor: WEB.field, borderWidth: 1, borderColor: "transparent", borderRadius: WEB.radiusSm,
    paddingHorizontal: 16, paddingVertical: 14, fontSize: 15, color: WEB.text,
  },
  inputFocused: { backgroundColor: WEB.card, borderColor: WEB.brand },
  hint: { fontSize: 12, color: WEB.textSubtle, marginTop: 5, lineHeight: 17 },

  button: {
    flexDirection: "row", alignItems: "center", justifyContent: "center",
    paddingVertical: 16, borderRadius: WEB.radiusPill, marginTop: 4, backgroundColor: WEB.brand,
  },
  buttonText: { fontSize: 14.5, fontWeight: "700", letterSpacing: 0.2, color: "#FFFFFF" },

  link: { fontSize: 12.5, fontWeight: "600", color: WEB.brand },
  linkMuted: { fontSize: 12.5, color: WEB.textSubtle },

  note: {
    flexDirection: "row", alignItems: "flex-start", backgroundColor: WEB.brandTint,
    borderWidth: 1, borderColor: WEB.borderGlow, borderRadius: WEB.radiusSm, padding: 12, marginBottom: 18,
  },
  noteText: { flex: 1, marginLeft: 8, fontSize: 12, lineHeight: 17, color: WEB.textMuted },
  helpBox: {
    marginTop: 22, padding: 13, backgroundColor: WEB.field, borderWidth: 1, borderColor: WEB.border,
    borderRadius: WEB.radiusSm,
  },
  helpText: { fontSize: 12, lineHeight: 19, color: WEB.textMuted },
  helpStrong: { fontWeight: "700", color: WEB.text },
  errorBox: {
    flexDirection: "row", alignItems: "center", padding: 13, borderRadius: WEB.radiusSm, marginBottom: 18,
    backgroundColor: "rgba(225,29,72,0.08)", borderWidth: 1, borderColor: "rgba(225,29,72,0.25)",
  },
  errorText: { flex: 1, marginLeft: 8, fontSize: 13, fontWeight: "600", color: WEB.error },
});
