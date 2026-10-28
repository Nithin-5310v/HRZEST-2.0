import React from "react";
import { Platform } from "react-native";
import { SafeAreaView as ContextSafeAreaView } from "react-native-safe-area-context";

// Drop-in for react-native's own SafeAreaView, which React Native has
// deprecated (it warns on use) and will remove. Same layout as it gave:
// the device's safe-area insets on iOS, none on Android -- react-native's
// was a plain View there, and these screens' headers already allow for the
// status bar themselves.
const EDGES = Platform.OS === "ios" ? ["top", "right", "bottom", "left"] : [];

export default function SafeAreaView(props) {
  return <ContextSafeAreaView edges={EDGES} {...props} />;
}
