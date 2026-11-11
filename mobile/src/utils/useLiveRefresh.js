// Keeps a screen's data current, so a check-in shows up on every screen at
// once instead of only after the app is restarted:
//   - loads when the screen comes into view (opening it, or coming back to it),
//   - reloads every LIVE_REFRESH_MS while it stays in view -- the website's
//     admin dashboard refreshes on the same 30 seconds (templates/admin.html),
//   - reloads when the app returns to the foreground,
//   - stops while the screen is out of view or the app is in the background.
//
// `load` may change every render (it can close over the chosen month); the
// newest one is always used. A load that resolves to `false` counts as
// failed and leaves `lastUpdated` alone, so "Updated hh:mm" is never shown
// for data that didn't arrive. `deps` reload at once when they change.
import { useCallback, useRef, useState } from "react";
import { AppState } from "react-native";
import { useFocusEffect } from "@react-navigation/native";

export const LIVE_REFRESH_MS = 30000;

export function formatUpdated(date) {
  if (!date) return "";
  return `Updated ${date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`;
}

export default function useLiveRefresh(load, deps = [], intervalMs = LIVE_REFRESH_MS) {
  const loadRef = useRef(load);
  loadRef.current = load;
  const busy = useRef(false);
  const [lastUpdated, setLastUpdated] = useState(null);

  const refresh = useCallback(async () => {
    if (busy.current) return;
    busy.current = true;
    try {
      const result = await loadRef.current();
      if (result !== false) setLastUpdated(new Date());
    } catch {
      // The screen's own load shows its error state; keep the old stamp.
    } finally {
      busy.current = false;
    }
  }, []);

  useFocusEffect(
    useCallback(() => {
      refresh();
      const timer = setInterval(() => {
        if (AppState.currentState === "active") refresh();
      }, intervalMs);
      const sub = AppState.addEventListener("change", (state) => {
        if (state === "active") refresh();
      });
      return () => {
        clearInterval(timer);
        sub.remove();
      };
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [refresh, intervalMs, ...deps])
  );

  return { lastUpdated, refresh };
}
