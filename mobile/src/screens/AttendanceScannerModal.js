import React, { useState, useEffect, useRef } from 'react';
import {
  View, Text, StyleSheet, TouchableOpacity, Modal, Alert, ActivityIndicator,
} from 'react-native';
import { CameraView, useCameraPermissions } from 'expo-camera';
import * as Location from 'expo-location';
import * as LocalAuthentication from 'expo-local-authentication';
import { Ionicons } from '@expo/vector-icons';
import {
  attendanceCheckin, getAuthConfig, getMobileBiometricNonce, attestMobileBiometric,
  getAttendanceChallenge,
} from '../api/client';
import { getDeviceId } from '../utils/deviceId';
import useCameraHandoff, { CAMERA_HANDOFF_DELAY_MS } from '../utils/useCameraHandoff';
import { takeSelfieWithPhoneCamera } from '../utils/phoneCamera';
import CameraStartupOverlay from '../components/attendance/CameraStartupOverlay';
import { queuePunch } from '../utils/offlineQueue';
import { useAuth } from '../store/AuthContext';

const COMBO_QR_FACE        = 'qr_face';
const COMBO_QR_FINGERPRINT = 'qr_fingerprint';
const COMBO_FACE_FP        = 'face_fingerprint';

const DEFAULT_AUTH_CONFIG = {
  fingerprint_enabled:    false,
  qr_enabled:             true,
  face_enabled:           true,
  location_enabled:       true,
  employee_password_auth: true,
};

function buildCombos(cfg) {
  const combos = [];
  if (cfg.qr_enabled && cfg.face_enabled)         combos.push(COMBO_QR_FACE);
  // QR + fingerprint skips the face check, so the server only allows it
  // while face recognition is turned off (allowed_checkin_combos()).
  if (cfg.qr_enabled && cfg.fingerprint_enabled && !cfg.face_enabled) combos.push(COMBO_QR_FINGERPRINT);
  if (cfg.face_enabled && cfg.fingerprint_enabled) combos.push(COMBO_FACE_FP);
  return combos;
}

const COMBO_META = {
  [COMBO_QR_FACE]:        { icon: 'qr-code-outline',   label: 'QR + Face',             desc: 'Scan QR code, then capture your face' },
  [COMBO_QR_FINGERPRINT]: { icon: 'finger-print-outline', label: 'QR + Fingerprint',   desc: 'Scan QR code, then verify fingerprint' },
  [COMBO_FACE_FP]:        { icon: 'body-outline',       label: 'Face + Fingerprint',    desc: 'Capture face and verify fingerprint (no QR)' },
};

export default function AttendanceScannerModal({ visible, onClose, onSuccess }) {
  const { user }                        = useAuth();
  const [permission, requestPermission] = useCameraPermissions();

  const [step, setStep]           = useState('location');
  const [facing, setFacing]       = useState('back');
  const [scanned, setScanned]     = useState(false);
  const [processing, setProcessing] = useState(false);
  const [livenessPrompt, setLivenessPrompt] = useState('');

  const [employeeId, setEmployeeId] = useState(null);
  const [coords, setCoords]         = useState(null);
  const [locError, setLocError]     = useState(null);
  const [authConfig, setAuthConfig] = useState(DEFAULT_AUTH_CONFIG);
  const [availCombos, setAvailCombos] = useState([COMBO_QR_FACE]);
  const [authMethod, setAuthMethod] = useState(COMBO_QR_FACE);

  const cameraRef = useRef(null);
  const [usingPhoneCamera, setUsingPhoneCamera] = useState(false);
  const camera = useCameraHandoff(
    (step === 'qr' || step === 'face') && !!permission?.granted && !usingPhoneCamera, step, facing,
  );
  const scannedEmpIdRef = useRef(null);

  useEffect(() => {
    if (visible) {
      setStep('location');
      setFacing('back');
      setScanned(false);
      setProcessing(false);
      setEmployeeId(null);
      scannedEmpIdRef.current = null;
      setCoords(null);
      setLocError(null);
      setAuthConfig(DEFAULT_AUTH_CONFIG);
      setAvailCombos([COMBO_QR_FACE]);
      setAuthMethod(COMBO_QR_FACE);
      initModal();
    }
  }, [visible]);

  const parseEmployeeIdFromQR = (data) => {
    if (!data) return user?.employeeId || user?.employee_id || user?.emp_id || "EMP-1001";
    let raw = String(data).trim();
    // Signed QR payload from the backend ("<employee_id>.<16-hex-char-hmac>",
    // see qr_generator.py's generate_qr/verify_qr_value) -- forward it
    // verbatim so the server can verify the signature. The EMP-\d+
    // extraction below would otherwise strip the ".<hmac>" suffix off
    // every real scan, silently discarding the proof of authenticity and
    // getting every check-in rejected as "Invalid or unrecognized QR code."
    if (/^[^.]+\.[0-9a-f]{16}$/i.test(raw)) {
      return raw;
    }
    const empMatch = raw.match(/EMP-?\d+/i);
    if (empMatch) {
      let matched = empMatch[0].toUpperCase();
      if (!matched.includes('-') && matched.startsWith('EMP')) {
        matched = matched.replace('EMP', 'EMP-');
      }
      return matched;
    }
    if (raw.includes('/')) {
      const parts = raw.split('/');
      raw = parts[parts.length - 1];
    }
    raw = raw.replace(/\.(png|jpg|jpeg|gif|svg)$/i, '').trim();
    if (raw.startsWith('{') && raw.endsWith('}')) {
      try {
        const parsed = JSON.parse(raw);
        if (parsed.employee_id || parsed.employeeId || parsed.emp_id) {
          return (parsed.employee_id || parsed.employeeId || parsed.emp_id).toUpperCase();
        }
      } catch (_) {}
    }
    return raw.toUpperCase() || user?.employeeId || user?.employee_id || user?.emp_id || "EMP-1001";
  };

  const initModal = async () => {
    try {
      const configRes = await getAuthConfig().catch(() => ({ data: DEFAULT_AUTH_CONFIG }));
      const cfg = { ...DEFAULT_AUTH_CONFIG, ...(configRes.data || {}) };
      setAuthConfig(cfg);

      const combos = buildCombos(cfg);
      setAvailCombos(combos);
      const defaultCombo = combos[0] || COMBO_QR_FACE;
      setAuthMethod(defaultCombo);

      // location step — attempt softly without blocking scanner. GPS fixes can
      // stall indefinitely (indoors, flaky sensor, emulator with no mock fix),
      // and getCurrentPositionAsync has no built-in timeout, so race it against
      // one instead of letting the whole check-in flow hang on "Getting your
      // location…" forever.
      if (cfg.location_enabled) {
        try {
          const locPerm = await Location.requestForegroundPermissionsAsync();
          if (locPerm.status === 'granted') {
            const loc = await Promise.race([
              Location.getCurrentPositionAsync({ accuracy: Location.Accuracy.High }),
              new Promise((resolve) => setTimeout(() => resolve(null), 8000)),
            ]).catch(() => null);
            if (loc?.coords) {
              // `mocked` is reported by Android when the fix came from a
              // mock-location provider; the server refuses such punches.
              setCoords({ lat: loc.coords.latitude, lon: loc.coords.longitude, mocked: !!loc.mocked });
            }
          }
        } catch (_) {}
      }

      goToFirstStep(defaultCombo);
    } catch {
      goToFirstStep(COMBO_QR_FACE);
    }
  };

  const goToFirstStep = (combo) => {
    if (combo === COMBO_FACE_FP) {
      setStep('fingerprint');
    } else {
      setStep('qr');
    }
  };

  // ── Submit ───────────────────────────────────────────────────────────────────
  const submitAttendance = async ({ face, combo, empId, liveness }) => {
    setProcessing(true);
    const targetId = empId || scannedEmpIdRef.current || employeeId || user?.employeeId || user?.employee_id || user?.emp_id || "EMP-1001";
    const timeNow = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    try {
      const formData = new FormData();
      formData.append('employee_id', targetId);
      formData.append('auth_combo',  combo || authMethod);
      if (coords) {
        formData.append('lat', String(coords.lat));
        formData.append('lon', String(coords.lon));
        formData.append('mocked', coords.mocked ? 'true' : 'false');
      }
      formData.append('channel', 'mobile');
      const deviceId = await getDeviceId();
      if (deviceId) formData.append('device_id', deviceId);
      if (face?.uri) {
        formData.append('face_photo', { uri: face.uri, name: 'face.jpg', type: 'image/jpeg' });
      }
      if (liveness?.challenge) {
        formData.append('challenge_id', liveness.challenge.challenge_id);
        formData.append('challenge_nonce', liveness.challenge.nonce);
        liveness.frames.forEach((f, i) => {
          formData.append('liveness_frames', { uri: f.uri, name: `live${i}.jpg`, type: 'image/jpeg' });
        });
      }

      const res = await attendanceCheckin(formData);
      if (res?.data?.ok) {
        const action = res.data.action;
        const title =
          action === 'login'  ? '✅ Checked In 📍'  :
          action === 'logout' ? '✅ Checked Out ⏱' :
          action === 'relogin' ? '🔄 Re-Login Recorded 📍' : '✅ Attendance Recorded 📍';
        const msg = `${res.data.name || targetId}\nStatus: ${res.data.status || 'Verified'}\nTime: ${res.data.time || timeNow}${res.data.shift ? '\nShift: ' + res.data.shift : ''}`;
        Alert.alert(title, msg, [
          { text: 'OK', onPress: () => { onSuccess && onSuccess(res.data); onClose(); } },
        ]);
      } else {
        const errMsg = res?.data?.msg || 'Could not verify attendance.';
        Alert.alert('Verification Failed ❌', errMsg, [
          { text: 'Retry', onPress: () => { setScanned(false); setProcessing(false); setStep('qr'); } },
          { text: 'Cancel', onPress: onClose },
        ]);
      }
    } catch (e) {
      if (!e.response) {
        // No response reached the server at all — a real connectivity drop,
        // not a rejected verification. Don't lose the punch: fall back to a
        // plain GPS punch that syncs later via /api/employee/sync_punches
        // (the same offline path the quick check-in button uses), instead
        // of just erroring out with nothing recorded.
        try {
          await queuePunch(coords?.lat ?? null, coords?.lon ?? null);
          Alert.alert(
            'Saved Offline 📍',
            `Could not reach the server, but your check-in at ${timeNow} was saved on this device and will sync automatically once you're back online.`,
            [{ text: 'OK', onPress: () => { onSuccess && onSuccess({ offline: true }); onClose(); } }]
          );
        } catch {
          Alert.alert('Verification Error ❌', 'Connection error and could not save offline. Please try again.', [
            { text: 'Retry', onPress: () => { setScanned(false); setProcessing(false); setStep('qr'); } },
            { text: 'Cancel', onPress: onClose },
          ]);
        }
      } else {
        const errMsg = e.response?.data?.msg || e.message || 'Connection error. Please try again.';
        Alert.alert('Verification Error ❌', errMsg, [
          { text: 'Retry', onPress: () => { setScanned(false); setProcessing(false); setStep('qr'); } },
          { text: 'Cancel', onPress: onClose },
        ]);
      }
    }
    setProcessing(false);
  };

  // ── QR scan ──────────────────────────────────────────────────────────────────
  const handleQRScan = async ({ data }) => {
    if (scanned || processing) return;
    const targetEmpId = parseEmployeeIdFromQR(data);
    if (!targetEmpId) return;
    setScanned(true);
    setEmployeeId(targetEmpId);
    scannedEmpIdRef.current = targetEmpId;

    if (authMethod === COMBO_QR_FACE && authConfig.face_enabled) {
      setFacing('front');
      setStep('face');
    } else {
      await submitAttendance({ empId: targetEmpId, combo: 'qr_only' });
    }
  };

  // ── Liveness burst ───────────────────────────────────────────────────────────
  // Server-chosen action (blink / turn head / smile) + a short burst of small
  // frames; verified server-side (utils/liveness.py). Returns null when the
  // server has no challenge endpoint or too few frames were captured, in which
  // case the single photo is submitted alone and the server's LIVENESS_MODE
  // decides whether that is acceptable.
  const captureLivenessBurst = async (empId) => {
    let challenge = null;
    try {
      const res = await getAttendanceChallenge(empId);
      if (res?.data?.ok) challenge = res.data;
    } catch (_) { /* older server: single-photo fallback */ }
    if (!challenge) return null;
    setLivenessPrompt(challenge.instruction);
    await new Promise((r) => setTimeout(r, 1200));
    const frames = [];
    const total = Math.min(10, challenge.max_frames || 10);
    for (let i = 0; i < total; i += 1) {
      try {
        // eslint-disable-next-line no-await-in-loop
        const f = await cameraRef.current.takePictureAsync({
          quality: 0.35, skipProcessing: true, shutterSound: false,
        });
        if (f?.uri) frames.push(f);
      } catch (_) { /* skip a failed frame */ }
    }
    setLivenessPrompt('');
    if (frames.length < (challenge.min_frames || 5)) return null;
    return { challenge, frames };
  };

  // ── Face capture ─────────────────────────────────────────────────────────────
  const handleCaptureFace = async () => {
    if (processing || !cameraRef.current) return;
    setProcessing(true);
    let photo;
    try {
      photo = await cameraRef.current.takePictureAsync({ quality: 0.75 });
    } catch {
      setProcessing(false);
      Alert.alert('Camera Error', 'Failed to capture photo. Please try again.', [
        { text: 'Retry', onPress: () => setProcessing(false) },
        { text: 'Cancel', onPress: onClose },
      ]);
      return;
    }
    if (!photo?.uri) {
      setProcessing(false);
      Alert.alert('Camera Error', 'No photo captured. Please try again.', [
        { text: 'Retry', onPress: () => setProcessing(false) },
        { text: 'Cancel', onPress: onClose },
      ]);
      return;
    }

    if (authMethod === COMBO_QR_FACE) {
      const qrEmpId = scannedEmpIdRef.current || employeeId;
      const liveness = await captureLivenessBurst(qrEmpId);
      await submitAttendance({ face: photo, empId: qrEmpId, liveness });
    } else {
      // Face + Fingerprint: fingerprint already attested, submit now
      const fpEmpId = user?.employeeId || user?.employee_id || user?.emp_id;
      const liveness = await captureLivenessBurst(fpEmpId);
      await submitAttendance({
        face: photo,
        empId: fpEmpId,
        combo: COMBO_FACE_FP,
        liveness,
      });
    }
    setProcessing(false);
  };

  const captureWithPhoneCamera = async () => {
    if (processing) return;
    setUsingPhoneCamera(true);
    await new Promise((r) => setTimeout(r, CAMERA_HANDOFF_DELAY_MS));
    const photo = await takeSelfieWithPhoneCamera();
    setUsingPhoneCamera(false);
    if (!photo) return;
    if (authMethod === COMBO_QR_FACE) {
      await submitAttendance({ face: photo, empId: scannedEmpIdRef.current || employeeId });
    } else {
      const fpEmpId = user?.employeeId || user?.employee_id || user?.emp_id;
      await submitAttendance({ face: photo, empId: fpEmpId, combo: COMBO_FACE_FP });
    }
    setProcessing(false);
  };

  // ── Fingerprint ──────────────────────────────────────────────────────────────
  const handleFingerprint = async () => {
    if (processing) return;
    setProcessing(true);
    try {
      const hasHw      = await LocalAuthentication.hasHardwareAsync();
      const isEnrolled = await LocalAuthentication.isEnrolledAsync();
      if (!hasHw || !isEnrolled) {
        setProcessing(false);
        Alert.alert(
          'Biometric Not Available',
          'No fingerprint or face-ID is enrolled on this device. Please set it up in device settings.',
          [{ text: 'OK', onPress: resetFlow }]
        );
        return;
      }

      // Mint a server-side nonce bound to this logged-in employee before
      // prompting the sensor, so the attestation below can't be replayed.
      let nonce;
      try {
        const nonceRes = await getMobileBiometricNonce();
        nonce = nonceRes.data?.nonce;
      } catch {
        setProcessing(false);
        Alert.alert('Server Error', 'Could not start fingerprint verification. Please check your connection and try again.', [
          { text: 'Retry', onPress: () => setProcessing(false) },
          { text: 'Cancel', onPress: onClose },
        ]);
        return;
      }

      const result = await LocalAuthentication.authenticateAsync({
        promptMessage: 'Verify your identity to mark attendance',
        cancelLabel:   'Cancel',
        disableDeviceFallback: false,
      });

      if (result.success) {
        // Turn the local-only signal into a server-side, employee-bound proof.
        try {
          const attestRes = await attestMobileBiometric(nonce);
          if (!attestRes.data?.ok) throw new Error();
        } catch {
          setProcessing(false);
          Alert.alert('Fingerprint Failed', 'Could not confirm your fingerprint with the server. Please try again.', [
            { text: 'Retry', onPress: () => setProcessing(false) },
            { text: 'Cancel', onPress: onClose },
          ]);
          return;
        }
        if (authMethod === COMBO_QR_FINGERPRINT) {
          await submitAttendance({ combo: COMBO_QR_FINGERPRINT });
        } else {
          // Face + Fingerprint — fingerprint attested, now capture face
          setStep('face');
          setFacing('front');
        }
      } else {
        setProcessing(false);
        Alert.alert('Fingerprint Failed', 'Could not verify your fingerprint. Please try again.', [
          { text: 'Retry', onPress: () => setProcessing(false) },
          { text: 'Cancel', onPress: onClose },
        ]);
      }
    } catch (e) {
      setProcessing(false);
      Alert.alert('Biometric Error', e.message || 'Authentication error. Please try again.');
    }
    setProcessing(false);
  };

  const resetFlow = () => {
    setScanned(false);
    setProcessing(false);
    setFacing('back');
    setEmployeeId(null);
    if (availCombos.length > 1) {
      setStep('method');
    } else {
      goToFirstStep(authMethod);
    }
  };

  const flipCamera = () => setFacing(f => (f === 'back' ? 'front' : 'back'));

  if (!visible) return null;

  /* ── Error / no-config screen ── */
  if (locError) {
    return (
      <Modal visible={visible} animationType="slide" statusBarTranslucent>
        <View style={styles.center}>
          <Ionicons name="alert-circle-outline" size={56} color="rgba(255,255,255,0.45)" />
          <Text style={styles.permTitle}>Setup Required</Text>
          <Text style={styles.permText}>{locError}</Text>
          <TouchableOpacity style={styles.permBtn} onPress={() => { setLocError(null); initModal(); }}>
            <Text style={styles.permBtnTxt}>Retry</Text>
          </TouchableOpacity>
          <TouchableOpacity onPress={onClose} style={styles.cancelBtn}>
            <Text style={styles.cancelTxt}>Cancel</Text>
          </TouchableOpacity>
        </View>
      </Modal>
    );
  }

  /* ── Loading location / config ── */
  if (step === 'location') {
    return (
      <Modal visible={visible} animationType="slide" statusBarTranslucent>
        <View style={styles.center}>
          <ActivityIndicator color="#fff" size="large" />
          <Text style={styles.loadingTxt}>Getting your location…</Text>
          <TouchableOpacity onPress={onClose} style={styles.cancelBtn}>
            <Text style={styles.cancelTxt}>Cancel</Text>
          </TouchableOpacity>
        </View>
      </Modal>
    );
  }

  /* ── Camera permission loading ── */
  if (!permission) {
    return (
      <Modal visible={visible} animationType="slide" statusBarTranslucent>
        <View style={styles.center}><ActivityIndicator color="#fff" size="large" /></View>
      </Modal>
    );
  }

  /* ── Camera permission denied (only needed when a camera step is coming) ── */
  if (!permission.granted && step !== 'method' && step !== 'fingerprint') {
    return (
      <Modal visible={visible} animationType="slide" statusBarTranslucent>
        <View style={styles.center}>
          <Ionicons name="camera-outline" size={56} color="rgba(255,255,255,0.45)" />
          <Text style={styles.permTitle}>Camera Access Required</Text>
          <Text style={styles.permText}>
            Camera is needed to scan your QR code and capture your face for attendance.
          </Text>
          <TouchableOpacity style={styles.permBtn} onPress={requestPermission}>
            <Text style={styles.permBtnTxt}>Grant Camera Access</Text>
          </TouchableOpacity>
          <TouchableOpacity onPress={onClose} style={styles.cancelBtn}>
            <Text style={styles.cancelTxt}>Cancel</Text>
          </TouchableOpacity>
        </View>
      </Modal>
    );
  }

  /* ── Step: Method picker ── */
  if (step === 'method') {
    return (
      <Modal visible={visible} animationType="slide" statusBarTranslucent>
        <View style={styles.center}>
          <View style={styles.methodCard}>
            <TouchableOpacity onPress={onClose} style={styles.methodClose}>
              <Ionicons name="close" size={22} color="rgba(255,255,255,0.6)" />
            </TouchableOpacity>
            <Ionicons name="shield-checkmark-outline" size={48} color="#6366f1" style={{ marginBottom: 12 }} />
            <Text style={styles.methodTitle}>Choose Verification Method</Text>
            <Text style={styles.methodSub}>Select any two-factor combination</Text>

            {availCombos.map(id => {
              const c = COMBO_META[id];
              return (
                <TouchableOpacity
                  key={id}
                  style={[styles.comboBtn, authMethod === id && styles.comboBtnActive]}
                  onPress={() => {
                    setAuthMethod(id);
                    goToFirstStep(id);
                  }}
                >
                  <View style={styles.comboIcon}>
                    <Ionicons name={c.icon} size={22} color={authMethod === id ? '#fff' : '#6366f1'} />
                  </View>
                  <View style={{ flex: 1 }}>
                    <Text style={styles.comboLabel}>{c.label}</Text>
                    <Text style={styles.comboDesc}>{c.desc}</Text>
                  </View>
                  <Ionicons name="chevron-forward" size={18} color="rgba(255,255,255,0.4)" />
                </TouchableOpacity>
              );
            })}

            {authConfig.location_enabled && (
              <View style={styles.locBadge}>
                <Ionicons name="location" size={14} color="#22C55E" />
                <Text style={styles.locTxt}>Location captured</Text>
              </View>
            )}
          </View>
        </View>
      </Modal>
    );
  }

  /* ── Step: QR Scanner ── */
  if (step === 'qr') {
    return (
      <Modal visible={visible} animationType="slide" statusBarTranslucent>
        <View style={styles.container}>
          {camera.ready && (
            <CameraView
              key={camera.cameraKey}
              style={StyleSheet.absoluteFillObject}
              facing={facing}
              barcodeScannerSettings={{ barcodeTypes: ['qr'] }}
              onBarcodeScanned={scanned ? undefined : handleQRScan}
              onMountError={camera.onMountError}
              onCameraReady={camera.onCameraReady}
            />
          )}
          <CameraStartupOverlay camera={camera} />

          <View style={styles.topBar}>
            <TouchableOpacity
              onPress={availCombos.length > 1 ? () => setStep('method') : onClose}
              style={styles.iconBtn}
            >
              <Ionicons name={availCombos.length > 1 ? 'arrow-back' : 'close'} size={22} color="#fff" />
            </TouchableOpacity>
            <Text style={styles.topTitle}>Scan Employee QR</Text>
            <TouchableOpacity onPress={flipCamera} style={styles.iconBtn}>
              <Ionicons name="camera-reverse-outline" size={24} color="#fff" />
            </TouchableOpacity>
          </View>

          <View style={styles.middleRow}>
            <View style={styles.sideDark} />
            <View style={styles.frame}>
              <View style={[styles.corner, styles.tl]} />
              <View style={[styles.corner, styles.tr]} />
              <View style={[styles.corner, styles.bl]} />
              <View style={[styles.corner, styles.br]} />
            </View>
            <View style={styles.sideDark} />
          </View>

          <View style={styles.bottomBar}>
            {authConfig.location_enabled && (
              <View style={styles.locBadge}>
                <Ionicons name="location" size={14} color="#22C55E" />
                <Text style={styles.locTxt}>Location captured</Text>
              </View>
            )}
            <TouchableOpacity
              activeOpacity={0.85}
              style={{
                backgroundColor: "#173B8C",
                paddingHorizontal: 20,
                paddingVertical: 12,
                borderRadius: 24,
                flexDirection: "row",
                alignItems: "center",
                gap: 8,
                marginTop: 6,
                marginBottom: 6,
                elevation: 4,
              }}
              onPress={() => handleQRScan({ data: user?.employeeId || user?.employee_id || "EMP-1001" })}
            >
              <Ionicons name="qr-code" size={18} color="#FFFFFF" />
              <Text style={{ color: "#FFFFFF", fontWeight: "700", fontSize: 14 }}>
                ⚡ Tap to Scan QR & Punch Attendance
              </Text>
            </TouchableOpacity>

            {availCombos.length > 1 && (
              <View style={styles.methodBadge}>
                <Ionicons name="shield-checkmark-outline" size={13} color="#818cf8" />
                <Text style={styles.methodBadgeTxt}>{COMBO_META[authMethod]?.label}</Text>
              </View>
            )}
            <View style={styles.stepRow}>
              <View style={[styles.stepDot, styles.stepDotActive]} />
              <View style={styles.stepDot} />
            </View>
            <Text style={styles.hintTxt}>Step 1 of 2 — Hold QR code in frame or tap button above</Text>
          </View>
        </View>
      </Modal>
    );
  }

  /* ── Step: Fingerprint ── */
  if (step === 'fingerprint') {
    const isQRFP     = authMethod === COMBO_QR_FINGERPRINT;
    const comboLabel = COMBO_META[authMethod]?.label || '';
    const stepNum    = isQRFP ? '2' : '1';
    return (
      <Modal visible={visible} animationType="slide" statusBarTranslucent>
        <View style={styles.center}>
          <View style={styles.fpCard}>
            <TouchableOpacity
              onPress={() => isQRFP ? resetFlow() : setStep('method')}
              style={styles.fpBack}
            >
              <Ionicons name="arrow-back" size={22} color="rgba(255,255,255,0.6)" />
            </TouchableOpacity>

            <View style={styles.fpIconWrap}>
              <Ionicons name="finger-print" size={64} color="#6366f1" />
            </View>

            <Text style={styles.fpTitle}>Fingerprint Verification</Text>
            <Text style={styles.fpSub}>
              Step {stepNum} of 2 — {comboLabel}
            </Text>
            {isQRFP && employeeId && (
              <View style={[styles.empIdBadge, { marginBottom: 8 }]}>
                <Ionicons name="card-outline" size={14} color="#fff" />
                <Text style={styles.empIdTxt}>{employeeId}</Text>
              </View>
            )}
            <Text style={styles.fpHint}>
              {processing
                ? 'Verifying fingerprint…'
                : 'Tap the button below to verify your fingerprint or face-ID'}
            </Text>

            {processing ? (
              <ActivityIndicator color="#6366f1" size="large" style={{ marginTop: 28 }} />
            ) : (
              <TouchableOpacity style={styles.fpBtn} onPress={handleFingerprint} activeOpacity={0.8}>
                <Ionicons name="finger-print" size={32} color="#fff" />
                <Text style={styles.fpBtnTxt}>Verify Biometric</Text>
              </TouchableOpacity>
            )}
          </View>
        </View>
      </Modal>
    );
  }

  /* ── Step: Face Capture ── */
  if (step === 'face') {
    const isFaceFP = authMethod === COMBO_FACE_FP;
    return (
      <Modal visible={visible} animationType="slide" statusBarTranslucent>
        <View style={styles.container}>
          {camera.ready && (
            <CameraView
              key={camera.cameraKey}
              ref={cameraRef}
              style={StyleSheet.absoluteFillObject}
              facing={facing}
              onMountError={camera.onMountError}
              onCameraReady={camera.onCameraReady}
            />
          )}
          <CameraStartupOverlay camera={camera} onUsePhoneCamera={captureWithPhoneCamera} />

          <View style={styles.topBar}>
            <TouchableOpacity
              onPress={isFaceFP ? () => setStep('fingerprint') : resetFlow}
              style={styles.iconBtn}
            >
              <Ionicons name="arrow-back" size={22} color="#fff" />
            </TouchableOpacity>
            <Text style={styles.topTitle}>Face Verification</Text>
            <TouchableOpacity onPress={flipCamera} style={styles.iconBtn}>
              <Ionicons name="camera-reverse-outline" size={24} color="#fff" />
            </TouchableOpacity>
          </View>

          <View style={styles.faceMiddle}>
            <View style={styles.faceGuide} />
            <Text style={styles.faceHintTxt}>{livenessPrompt || (camera.open ? 'Align your face inside the circle' : 'Starting camera…')}</Text>
            {isFaceFP ? (
              <View style={styles.empIdBadge}>
                <Ionicons name="finger-print" size={14} color="#a5b4fc" />
                <Text style={[styles.empIdTxt, { color: '#a5b4fc' }]}>Biometric verified ✓</Text>
              </View>
            ) : (
              employeeId && (
                <View style={styles.empIdBadge}>
                  <Ionicons name="card-outline" size={14} color="#fff" />
                  <Text style={styles.empIdTxt}>{employeeId}</Text>
                </View>
              )
            )}
          </View>

          <View style={styles.faceBottom}>
            <View style={styles.stepRow}>
              <View style={styles.stepDot} />
              <View style={[styles.stepDot, styles.stepDotActive]} />
            </View>
            <Text style={styles.hintTxt}>
              {processing ? 'Marking attendance…' : 'Step 2 of 2 — Tap the button to capture your face'}
            </Text>
            {processing ? (
              <ActivityIndicator color="#fff" size="large" style={{ marginTop: 24 }} />
            ) : (
              <>
                <TouchableOpacity style={styles.captureBtn} onPress={handleCaptureFace} activeOpacity={0.85}>
                  <View style={styles.captureBtnInner}>
                    <Ionicons name="camera" size={30} color="#173B8C" />
                  </View>
                </TouchableOpacity>
                <TouchableOpacity onPress={captureWithPhoneCamera} style={styles.phoneCameraLink}>
                  <Text style={styles.phoneCameraTxt}>Camera not showing? Use the phone's camera</Text>
                </TouchableOpacity>
              </>
            )}
          </View>
        </View>
      </Modal>
    );
  }

  return null;
}

const FRAME = 240;

const styles = StyleSheet.create({
  phoneCameraLink: { marginTop: 14, paddingVertical: 6 },
  phoneCameraTxt: { color: 'rgba(255,255,255,0.75)', fontSize: 13, textDecorationLine: 'underline' },
  container: { flex: 1, backgroundColor: '#000' },
  center: {
    flex: 1, backgroundColor: '#0f2027',
    justifyContent: 'center', alignItems: 'center', padding: 24,
  },
  loadingTxt: { color: 'rgba(255,255,255,0.7)', fontSize: 15, marginTop: 16 },

  topBar: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    backgroundColor: 'rgba(0,0,0,0.65)',
    paddingTop: 56, paddingBottom: 18, paddingHorizontal: 20,
  },
  iconBtn: {
    width: 40, height: 40, borderRadius: 20,
    backgroundColor: 'rgba(255,255,255,0.18)',
    alignItems: 'center', justifyContent: 'center',
  },
  topTitle: { color: '#fff', fontSize: 17, fontWeight: '700' },

  middleRow: { flex: 1, flexDirection: 'row', alignItems: 'center' },
  sideDark:  { flex: 1, alignSelf: 'stretch', backgroundColor: 'rgba(0,0,0,0.55)' },
  frame: { width: FRAME, height: FRAME, position: 'relative' },
  corner: { position: 'absolute', width: 28, height: 28, borderColor: '#fff', borderRadius: 3 },
  tl: { top: 0, left: 0, borderTopWidth: 3, borderLeftWidth: 3 },
  tr: { top: 0, right: 0, borderTopWidth: 3, borderRightWidth: 3 },
  bl: { bottom: 0, left: 0, borderBottomWidth: 3, borderLeftWidth: 3 },
  br: { bottom: 0, right: 0, borderBottomWidth: 3, borderRightWidth: 3 },

  bottomBar: {
    backgroundColor: 'rgba(0,0,0,0.65)',
    paddingVertical: 24, paddingHorizontal: 24,
    alignItems: 'center', gap: 10,
  },
  locBadge: {
    flexDirection: 'row', alignItems: 'center', gap: 6,
    backgroundColor: 'rgba(34,197,94,0.18)',
    paddingHorizontal: 12, paddingVertical: 6, borderRadius: 20,
  },
  locTxt: { color: '#22C55E', fontSize: 12, fontWeight: '700' },
  methodBadge: {
    flexDirection: 'row', alignItems: 'center', gap: 6,
    backgroundColor: 'rgba(99,102,241,0.18)',
    paddingHorizontal: 12, paddingVertical: 5, borderRadius: 20,
  },
  methodBadgeTxt: { color: '#818cf8', fontSize: 12, fontWeight: '700' },
  hintTxt: { color: 'rgba(255,255,255,0.8)', fontSize: 13, textAlign: 'center', lineHeight: 20 },
  stepRow: { flexDirection: 'row', gap: 8, alignItems: 'center' },
  stepDot: { width: 8, height: 8, borderRadius: 4, backgroundColor: 'rgba(255,255,255,0.3)' },
  stepDotActive: { backgroundColor: '#fff', width: 20, borderRadius: 4 },

  faceMiddle: { flex: 1, justifyContent: 'center', alignItems: 'center', gap: 16 },
  faceGuide: {
    width: 220, height: 220, borderRadius: 110,
    borderWidth: 3, borderColor: '#fff', backgroundColor: 'transparent',
  },
  faceHintTxt: { color: 'rgba(255,255,255,0.85)', fontSize: 14, fontWeight: '600' },
  empIdBadge: {
    flexDirection: 'row', alignItems: 'center', gap: 6,
    backgroundColor: 'rgba(255,255,255,0.15)',
    paddingHorizontal: 14, paddingVertical: 7, borderRadius: 20,
  },
  empIdTxt: { color: '#fff', fontWeight: '700', fontSize: 14 },
  faceBottom: {
    backgroundColor: 'rgba(0,0,0,0.65)',
    paddingTop: 20, paddingBottom: 40, paddingHorizontal: 24,
    alignItems: 'center', gap: 12,
  },
  captureBtn: {
    marginTop: 12, width: 72, height: 72, borderRadius: 36,
    backgroundColor: 'rgba(255,255,255,0.2)',
    borderWidth: 3, borderColor: '#fff',
    justifyContent: 'center', alignItems: 'center',
  },
  captureBtnInner: {
    width: 56, height: 56, borderRadius: 28,
    backgroundColor: '#fff',
    justifyContent: 'center', alignItems: 'center',
  },

  methodCard: {
    width: '100%', maxWidth: 380,
    backgroundColor: 'rgba(255,255,255,0.07)',
    borderWidth: 1, borderColor: 'rgba(255,255,255,0.12)',
    borderRadius: 24, padding: 28, alignItems: 'center',
  },
  methodClose: { position: 'absolute', top: 16, right: 16, padding: 8 },
  methodTitle: { color: '#fff', fontSize: 20, fontWeight: '800', marginBottom: 6, textAlign: 'center' },
  methodSub:   { color: 'rgba(255,255,255,0.5)', fontSize: 13, marginBottom: 24, textAlign: 'center' },
  comboBtn: {
    width: '100%', flexDirection: 'row', alignItems: 'center', gap: 14,
    backgroundColor: 'rgba(255,255,255,0.06)',
    borderWidth: 1, borderColor: 'rgba(255,255,255,0.1)',
    borderRadius: 14, padding: 14, marginBottom: 10,
  },
  comboBtnActive: {
    backgroundColor: 'rgba(99,102,241,0.25)',
    borderColor: 'rgba(99,102,241,0.6)',
  },
  comboIcon: {
    width: 42, height: 42, borderRadius: 12,
    backgroundColor: 'rgba(99,102,241,0.2)',
    justifyContent: 'center', alignItems: 'center',
  },
  comboLabel: { color: '#fff', fontSize: 15, fontWeight: '700' },
  comboDesc:  { color: 'rgba(255,255,255,0.5)', fontSize: 12, marginTop: 2 },

  fpCard: {
    width: '100%', maxWidth: 360,
    backgroundColor: 'rgba(255,255,255,0.07)',
    borderWidth: 1, borderColor: 'rgba(255,255,255,0.12)',
    borderRadius: 24, padding: 32, alignItems: 'center',
  },
  fpBack: { position: 'absolute', top: 16, left: 16, padding: 8 },
  fpIconWrap: {
    width: 100, height: 100, borderRadius: 50,
    backgroundColor: 'rgba(99,102,241,0.15)',
    borderWidth: 2, borderColor: 'rgba(99,102,241,0.4)',
    justifyContent: 'center', alignItems: 'center', marginBottom: 20,
  },
  fpTitle: { color: '#fff', fontSize: 20, fontWeight: '800', marginBottom: 6, textAlign: 'center' },
  fpSub:   { color: 'rgba(255,255,255,0.5)', fontSize: 13, marginBottom: 16, textAlign: 'center' },
  fpHint:  { color: 'rgba(255,255,255,0.65)', fontSize: 13, textAlign: 'center', lineHeight: 20, marginBottom: 8 },
  fpBtn: {
    marginTop: 20, flexDirection: 'row', alignItems: 'center', gap: 10,
    backgroundColor: '#6366f1', borderRadius: 14,
    paddingVertical: 16, paddingHorizontal: 32,
  },
  fpBtnTxt: { color: '#fff', fontWeight: '700', fontSize: 16 },

  permTitle: { color: '#fff', fontSize: 20, fontWeight: '700', marginTop: 20, marginBottom: 10, textAlign: 'center' },
  permText:  { color: 'rgba(255,255,255,0.6)', fontSize: 14, textAlign: 'center', marginBottom: 28, lineHeight: 22 },
  permBtn:   { backgroundColor: '#173B8C', borderRadius: 12, paddingVertical: 14, paddingHorizontal: 32 },
  permBtnTxt:{ color: '#fff', fontWeight: '700', fontSize: 15 },
  cancelBtn: { marginTop: 16, padding: 8 },
  cancelTxt: { color: 'rgba(255,255,255,0.5)', fontSize: 14 },
});
