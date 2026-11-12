import { useEffect, useRef } from "react";
import { Alert, AppState } from "react-native";
import * as Updates from "expo-updates";

export const UPDATE_CHECK_EVERY_MS = 30 * 60 * 1000;

// Over-the-air updates: expo-updates downloads a new version quietly on
// launch but only switches to it on the NEXT launch, so phones needed two
// restarts and nobody knew an update was waiting. This checks on launch and
// when the app comes back to the foreground (at most every 30 minutes),
// downloads it, and offers to restart into it straight away.
export async function checkForAppUpdate({ ask = Alert.alert, dev = __DEV__ } = {}) {
  if (dev || !Updates.isEnabled) return "disabled";
  const check = await Updates.checkForUpdateAsync();
  if (!check?.isAvailable) return "none";
  const fetched = await Updates.fetchUpdateAsync();
  if (!fetched?.isNew) return "none";
  ask(
    "Update ready",
    "A new version of HRzest has been downloaded. Restart now to use it?",
    [
      { text: "Later", style: "cancel" },
      { text: "Restart now", onPress: () => Updates.reloadAsync().catch(() => {}) },
    ],
  );
  return "ready";
}

export default function useAppUpdates() {
  const lastCheck = useRef(0);
  useEffect(() => {
    const run = () => {
      const now = Date.now();
      if (now - lastCheck.current < UPDATE_CHECK_EVERY_MS) return;
      lastCheck.current = now;
      checkForAppUpdate().catch(() => {}); // offline: try again later
    };
    run();
    const sub = AppState.addEventListener("change", (state) => {
      if (state === "active") run();
    });
    return () => sub.remove();
  }, []);
}
