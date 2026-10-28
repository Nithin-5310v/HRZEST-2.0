import { useEffect, useState } from "react";

// The attendance scanners use one CameraView for the QR step (back camera)
// and another for the face step (front camera). On Android the second one
// can open before the first has released the camera hardware, and its
// preview then stays black. Mount each preview a moment after the step or
// facing changes, report a failed start, and allow a retry -- which remounts
// the preview through `cameraKey`.
const HANDOFF_DELAY_MS = 400;

export default function useCameraHandoff(...deps) {
  const [ready, setReady] = useState(false);
  const [error, setError] = useState(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    setReady(false);
    setError(null);
    const timer = setTimeout(() => setReady(true), HANDOFF_DELAY_MS);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, attempt]);

  return {
    ready,
    error,
    cameraKey: `${deps.join("-")}-${attempt}`,
    onMountError: (e) => setError(e?.message || "The camera could not start."),
    retry: () => setAttempt((n) => n + 1),
  };
}
