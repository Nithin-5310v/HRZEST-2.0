import { useEffect, useState } from "react";

// The attendance scanners use one CameraView for the QR step (back camera)
// and another for the face step (front camera). On Android -- seen on a
// Xiaomi in Expo Go -- the face step's preview could stay black: the second
// camera opened while the first was still being released, and a camera that
// fails to start says nothing unless onMountError/onCameraReady are watched.
//
// So each camera "session" (a step + facing, or a retry) mounts its preview
// only after HANDOFF_DELAY_MS, derived from the session key so the very first
// render of a new step already has no preview and the old camera unmounts
// first. A session whose camera hasn't reported ready after START_TIMEOUT_MS
// is restarted once, then reported as an error (with a retry).
export const CAMERA_HANDOFF_DELAY_MS = 700;
const START_TIMEOUT_MS = 5000;

export default function useCameraHandoff(active, ...deps) {
  const [attempt, setAttempt] = useState(0);
  const [readyFor, setReadyFor] = useState(null);
  const [openFor, setOpenFor] = useState(null);
  const [failure, setFailure] = useState(null); // { key, message }
  const [autoRetriedFor, setAutoRetriedFor] = useState(null);
  const depsKey = deps.join("-");
  const key = `${depsKey}-${attempt}`;

  useEffect(() => {
    if (!active) return undefined;
    const timer = setTimeout(() => setReadyFor(key), CAMERA_HANDOFF_DELAY_MS);
    return () => clearTimeout(timer);
  }, [active, key]);

  const ready = active && readyFor === key;
  const open = ready && openFor === key;
  const failed = !!failure && failure.key === key;

  useEffect(() => {
    if (!ready || open || failed) return undefined;
    const timer = setTimeout(() => {
      if (autoRetriedFor !== depsKey) {
        setAutoRetriedFor(depsKey);
        setAttempt((n) => n + 1);
      } else {
        setFailure({ key, message: "The camera didn't start on this phone." });
      }
    }, START_TIMEOUT_MS);
    return () => clearTimeout(timer);
  }, [ready, open, failed, key, depsKey, autoRetriedFor]);

  return {
    ready,
    open,
    cameraKey: key,
    error: failed ? failure.message : null,
    onCameraReady: () => {
      setOpenFor(key);
      setFailure((f) => (f && f.key === key ? null : f)); // a slow start that made it after all
    },
    onMountError: (event) =>
      setFailure({ key, message: event?.message || event?.nativeEvent?.message || "The camera could not start." }),
    retry: () => setAttempt((n) => n + 1),
  };
}
