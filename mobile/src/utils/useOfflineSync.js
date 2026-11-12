import { useEffect } from "react";
import { Alert, AppState } from "react-native";

import { syncOfflinePunches } from "../api/client";
import { getDeviceId } from "./deviceId";
import { syncPendingPunches } from "./offlineQueue";

export const OFFLINE_RETRY_MS = 60000;

// Sends punches saved offline, from wherever the employee is in the app:
// on opening it, whenever it comes back to the foreground, and every minute
// while it's open. Before, they only went when the Dashboard was opened.
export const sendPendingPunches = async () => {
  const deviceId = await getDeviceId().catch(() => null);
  return syncPendingPunches((punches) => syncOfflinePunches(punches, deviceId));
};

export const describeSync = (result) => {
  if (!result) return null;
  return result.failed > 0
    ? `${result.synced} offline punch(es) synced. ${result.failed} rejected (too old or duplicate).`
    : `${result.synced} offline punch(es) synced.`;
};

export default function useOfflineSync(onSynced) {
  useEffect(() => {
    let alive = true;
    const run = async () => {
      const result = await sendPendingPunches();
      if (!alive || !result) return;
      Alert.alert("Sync Complete", describeSync(result));
      if (onSynced) onSynced(result);
    };
    run();
    const timer = setInterval(() => {
      if (AppState.currentState === "active") run();
    }, OFFLINE_RETRY_MS);
    const sub = AppState.addEventListener("change", (state) => {
      if (state === "active") run();
    });
    return () => {
      alive = false;
      clearInterval(timer);
      sub.remove();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
}
