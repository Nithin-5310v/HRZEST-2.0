import React, { createContext, useContext, useState, useEffect } from "react";
import { getDarkModeEnabled, setDarkModeEnabled } from "../utils/preferences";

const ThemeContext = createContext(null);

// Same semantic keys as constants/colors.js (background/surface/card/text/
// textSecondary/textLight/border) so a screen migrating to useTheme() just
// swaps COLORS.x for colors.x -- brand/status/dashboard accent colors are
// deliberately NOT overridden per-theme (an accent blue reads fine on both
// a light and a dark surface), only what actually needs to invert does.
const LIGHT_COLORS = {
  primary: "#0B5CAD",
  primaryDark: "#0C3054",
  primaryLight: "#E6EFFA",
  background: "#F5F8FC",
  surface: "#FFFFFF",
  card: "#FFFFFF",
  text: "#131C2A",
  textSecondary: "#455163",
  textLight: "#8794A5",
  border: "#DBE3EE",
  success: "#22C55E",
  warning: "#F59E0B",
  danger: "#EF4444",
  info: "#06B6D4",
  employee: "#2E7BD1",
  attendance: "#22C55E",
  absent: "#EF4444",
  payroll: "#8B5CF6",
  analytics: "#F97316",
  blueBg: "#EBF2FB",
  greenBg: "#ECFDF5",
  redBg: "#FEF2F2",
  yellowBg: "#FFFBEB",
  purpleBg: "#F5F3FF",
  white: "#FFFFFF",
  black: "#000000",
  // Most screens wrap their whole body in
  // <LinearGradient colors={THEME's screenGradient}> as the page background.
  screenGradient: ["#F5F8FC", "#EDF2F8", "#DBE3EE"],
};

const DARK_COLORS = {
  ...LIGHT_COLORS,
  primaryLight: "#1E2A44",
  background: "#0B1220",
  surface: "#111A2E",
  card: "#151F35",
  text: "#EDF2F8",
  textSecondary: "#C3CFDD",
  textLight: "#8B96A8",
  border: "#25324A",
  blueBg: "#16233F",
  greenBg: "#132A22",
  redBg: "#2E1A1E",
  yellowBg: "#2E2712",
  purpleBg: "#231B3D",
  white: "#151F35",
  black: "#000000",
  screenGradient: ["#0B1220", "#0E1A2E", "#111A2E"],
};

export function ThemeProvider({ children }) {
  const [isDark, setIsDark] = useState(false);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    (async () => {
      setIsDark(await getDarkModeEnabled());
      setLoaded(true);
    })();
  }, []);

  const toggleTheme = async (value) => {
    setIsDark(value);
    await setDarkModeEnabled(value);
  };

  const colors = isDark ? DARK_COLORS : LIGHT_COLORS;

  // Gate rendering until the persisted preference loads -- otherwise every
  // screen flashes light-mode colors for one frame on a cold start in dark
  // mode.
  if (!loaded) return null;

  return (
    <ThemeContext.Provider value={{ colors, isDark, toggleTheme }}>
      {children}
    </ThemeContext.Provider>
  );
}

export function useTheme() {
  const ctx = useContext(ThemeContext);
  if (!ctx) {
    throw new Error("useTheme() must be called within a ThemeProvider");
  }
  return ctx;
}
