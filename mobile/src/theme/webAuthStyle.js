import React from "react";
import { View, Text, TouchableOpacity, ActivityIndicator, StyleSheet } from "react-native";
import { LinearGradient } from "expo-linear-gradient";
import { Ionicons } from "@expo/vector-icons";

// The website's sign-in / sign-up look (static/landing_v2.css tokens and
// templates/admin_login.html / create_org.html at phone width), for the
// app's logged-out screens -- so the app and the website read as one
// product. Light theme only, like those pages.

export const WEB = {
  brand: "#4F46E5",          // --accent-cyan
  brandDark: "#4338CA",      // --accent-blue
  text: "#0F172A",           // --text-main
  textMuted: "#475569",      // --text-muted
  textSubtle: "#94A3B8",     // --text-subtle
  card: "#FFFFFF",           // --bg-card
  field: "#F8FAFC",          // --bg-secondary
  fieldAlt: "#F1F5F9",       // --bg-tertiary
  border: "rgba(15,23,42,0.08)",      // --border-color
  borderGlow: "rgba(79,70,229,0.35)", // --border-color-glow
  brandTint: "rgba(79,70,229,0.06)",
  success: "#059669",        // --accent-emerald
  error: "#E11D48",          // --accent-rose
  radiusSm: 8,
  radiusMd: 14,
  radiusLg: 20,
};

// --gradient-brand: linear-gradient(135deg, #4F46E5, #4338CA)
export const WEB_BRAND_GRADIENT = {
  colors: [WEB.brand, WEB.brandDark],
  start: { x: 0, y: 0 },
  end: { x: 1, y: 1 },
};

// The page background: linear-gradient(160deg, #FDF3E3 0%, #F7ECF7 28%, #EAF0FC 56%, #F8FAFC 100%)
export const WEB_PAGE_GRADIENT = {
  colors: ["#FDF3E3", "#F7ECF7", "#EAF0FC", "#F8FAFC"],
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

/** The website's top bar: the HRzest badge and name, with an optional
 *  button on the right (e.g. "Register a Company" on the login page). */
export function WebTopBar({ right, style }) {
  return (
    <View style={[webAuthStyles.topBar, style]}>
      <View style={webAuthStyles.brandRow}>
        <LinearGradient {...WEB_BRAND_GRADIENT} style={webAuthStyles.brandBadge}>
          <Ionicons name="shield-checkmark" size={18} color="#FFFFFF" />
        </LinearGradient>
        <Text style={webAuthStyles.brandName}>HRzest</Text>
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

/** The full-width brand-gradient submit button. */
export function WebButton({ title, onPress, loading, disabled, icon = "arrow-forward", style }) {
  return (
    <TouchableOpacity onPress={onPress} disabled={disabled || loading} activeOpacity={0.85} style={style}>
      <LinearGradient {...WEB_BRAND_GRADIENT} style={webAuthStyles.button}>
        {loading ? (
          <ActivityIndicator color="#FFFFFF" />
        ) : (
          <>
            <Text style={webAuthStyles.buttonText}>{title}</Text>
            {icon ? <Ionicons name={icon} size={16} color="#FFFFFF" style={{ marginLeft: 8 }} /> : null}
          </>
        )}
      </LinearGradient>
    </TouchableOpacity>
  );
}

/** The small gradient button in the top bar (the website's .nav-link-btn). */
export function WebNavButton({ title, icon, onPress }) {
  return (
    <TouchableOpacity onPress={onPress} activeOpacity={0.85}>
      <LinearGradient {...WEB_BRAND_GRADIENT} style={webAuthStyles.navButton}>
        {icon ? <Ionicons name={icon} size={14} color="#FFFFFF" style={{ marginRight: 6 }} /> : null}
        <Text style={webAuthStyles.navButtonText}>{title}</Text>
      </LinearGradient>
    </TouchableOpacity>
  );
}

export const webAuthStyles = StyleSheet.create({
  page: { flex: 1 },
  scroll: { flexGrow: 1, paddingHorizontal: 20, paddingTop: 12, paddingBottom: 32 },

  topBar: {
    flexDirection: "row", alignItems: "center", justifyContent: "space-between",
    paddingHorizontal: 20, paddingVertical: 14,
    backgroundColor: "rgba(255,255,255,0.65)",
    shadowColor: "#0F172A", shadowOpacity: 0.05, shadowRadius: 16, shadowOffset: { width: 0, height: 4 }, elevation: 2,
  },
  brandRow: { flexDirection: "row", alignItems: "center" },
  brandBadge: { width: 36, height: 36, borderRadius: WEB.radiusSm, justifyContent: "center", alignItems: "center" },
  brandName: { marginLeft: 10, fontSize: 20, fontWeight: "800", color: WEB.text },
  navButton: {
    flexDirection: "row", alignItems: "center", paddingHorizontal: 14, paddingVertical: 9, borderRadius: WEB.radiusSm,
  },
  navButtonText: { fontSize: 12.5, fontWeight: "700", color: "#FFFFFF" },

  card: {
    backgroundColor: WEB.card, borderRadius: WEB.radiusLg, borderWidth: 1, borderColor: WEB.border,
    paddingHorizontal: 24, paddingTop: 34, paddingBottom: 26, overflow: "hidden",
    shadowColor: "#0F172A", shadowOpacity: 0.16, shadowRadius: 24, shadowOffset: { width: 0, height: 16 }, elevation: 6,
  },
  cardBar: { position: "absolute", top: 0, left: 0, right: 0, height: 4 },
  cardHeader: { alignItems: "center", marginBottom: 26 },
  iconTile: {
    width: 64, height: 64, borderRadius: WEB.radiusMd, justifyContent: "center", alignItems: "center",
    marginBottom: 16, overflow: "hidden",
  },
  title: { fontSize: 22, fontWeight: "800", letterSpacing: -0.4, color: WEB.text, textAlign: "center", marginBottom: 6 },
  subtitle: { fontSize: 13.5, fontWeight: "600", color: WEB.textMuted, textAlign: "center", lineHeight: 19 },

  label: {
    fontSize: 11, fontWeight: "700", letterSpacing: 0.6, textTransform: "uppercase",
    color: WEB.textMuted, marginBottom: 7,
  },
  field: { marginBottom: 18 },
  input: {
    backgroundColor: WEB.field, borderWidth: 1, borderColor: WEB.border, borderRadius: WEB.radiusSm,
    paddingHorizontal: 15, paddingVertical: 12, fontSize: 14, color: WEB.text,
  },
  inputFocused: { backgroundColor: WEB.card, borderColor: WEB.brand },
  hint: { fontSize: 12, color: WEB.textSubtle, marginTop: 5, lineHeight: 17 },

  button: {
    flexDirection: "row", alignItems: "center", justifyContent: "center",
    paddingVertical: 15, borderRadius: WEB.radiusSm, marginTop: 4,
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
