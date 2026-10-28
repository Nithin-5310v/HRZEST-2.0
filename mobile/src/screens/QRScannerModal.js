import React, { useState, useEffect, useRef } from 'react';
import {
  View, Text, StyleSheet, TouchableOpacity, Modal, Alert, ActivityIndicator,
} from 'react-native';
import { CameraView, useCameraPermissions } from 'expo-camera';
import * as Location from 'expo-location';
import { Ionicons } from '@expo/vector-icons';
import { qrFaceCheckin, getAuthConfig } from '../api/client';
import { getDeviceId } from '../utils/deviceId';
import useCameraHandoff, { CAMERA_HANDOFF_DELAY_MS } from '../utils/useCameraHandoff';
import { takeSelfieWithPhoneCamera } from '../utils/phoneCamera';
import CameraStartupOverlay from '../components/attendance/CameraStartupOverlay';

export default function QRScannerModal({ visible, onClose }) {
  const [permission, requestPermission] = useCameraPermissions();
  const [step, setStep]           = useState('location'); // 'location'|'qr'|'face'|'done'
  const [facing, setFacing]       = useState('back');
  const [scanned, setScanned]     = useState(false);
  const [processing, setProcessing] = useState(false);
  const [employeeId, setEmployeeId] = useState(null);
  const [coords, setCoords]       = useState(null);
  const [locError, setLocError]   = useState(null);
  const [faceEnabled, setFaceEnabled] = useState(true);
  const cameraRef = useRef(null);
  const [usingPhoneCamera, setUsingPhoneCamera] = useState(false);
  const camera = useCameraHandoff(
    visible && (step === 'qr' || step === 'face') && !!permission?.granted && !usingPhoneCamera, step, facing,
  );

  useEffect(() => {
    if (visible) {
      setStep('location');
      setFacing('back');
      setScanned(false);
      setProcessing(false);
      setEmployeeId(null);
      setCoords(null);
      setLocError(null);
      setFaceEnabled(true);
      fetchLocation();
      getAuthConfig()
        .then(res => setFaceEnabled(res.data?.face_enabled !== false))
        .catch(() => setFaceEnabled(true));
    }
  }, [visible]);

  const fetchLocation = async () => {
    try {
      const { status } = await Location.requestForegroundPermissionsAsync();
      if (status !== 'granted') {
        setLocError('Location permission denied. Please enable location to mark attendance.');
        return;
      }
      // getCurrentPositionAsync has no built-in timeout and can stall
      // indefinitely on a slow/absent GPS fix, so race it against one
      // instead of leaving the kiosk stuck on "Getting your location…" forever.
      const loc = await Promise.race([
        Location.getCurrentPositionAsync({ accuracy: Location.Accuracy.High }),
        new Promise((_, reject) => setTimeout(() => reject(new Error('timeout')), 8000)),
      ]);
      setCoords({ lat: loc.coords.latitude, lon: loc.coords.longitude, mocked: !!loc.mocked });
      setStep('qr');
    } catch {
      setLocError('Could not get your location. Please check GPS and try again.');
    }
  };

  const parseEmployeeIdFromQR = (data) => {
    if (!data) return "EMP-1001";
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
    return raw.toUpperCase() || "EMP-1001";
  };

  const handleQRScan = async ({ data }) => {
    if (scanned || processing) return;
    const empId = parseEmployeeIdFromQR(data);
    if (!empId) return;
    setScanned(true);
    setEmployeeId(empId);

    if (faceEnabled) {
      setFacing('front');
      setStep('face');
    } else {
      // Tenant has face recognition disabled — QR alone is sufficient proof.
      await submitCheckin({ empId, combo: 'qr_only' });
    }
  };

  const submitCheckin = async ({ empId, combo, photo }) => {
    setProcessing(true);
    try {
      const formData = new FormData();
      formData.append('employee_id', empId || employeeId);
      formData.append('auth_combo', combo || 'qr_face');
      if (coords) {
        formData.append('lat', String(coords.lat));
        formData.append('lon', String(coords.lon));
        formData.append('mocked', coords.mocked ? 'true' : 'false');
      }
      formData.append('channel', 'kiosk');
      const deviceId = await getDeviceId();
      if (deviceId) formData.append('device_id', deviceId);
      if (photo?.uri) {
        formData.append('face_photo', {
          uri: photo.uri,
          name: 'face.jpg',
          type: 'image/jpeg',
        });
      }

      const res = await qrFaceCheckin(formData);
      if (res.data.ok) {
        const action = res.data.action;
        const title =
          action === 'login'  ? '✅ Checked In'  :
          action === 'logout' ? '✅ Checked Out' : '✅ Re-Logged In';
        Alert.alert(title, `${res.data.name}\n${res.data.status}\nTime: ${res.data.time}`, [
          { text: 'OK', onPress: onClose },
        ]);
      } else {
        Alert.alert('Cannot Mark Attendance', res.data.msg || 'Something went wrong.', [
          { text: 'Retry', onPress: resetToQR },
          { text: 'Cancel', onPress: onClose },
        ]);
      }
    } catch (e) {
      const msg =
        e.response?.data?.msg ||
        (e.response ? `Server error ${e.response.status}` : e.message) ||
        'Cannot connect to server.';
      Alert.alert('Server Error', msg, [
        { text: 'Retry', onPress: resetToQR },
        { text: 'Cancel', onPress: onClose },
      ]);
    }
    setProcessing(false);
  };

  const handleCaptureFace = async () => {
    if (processing || !cameraRef.current) return;
    setProcessing(true);

    /* Step A: take photo */
    let photo;
    try {
      photo = await cameraRef.current.takePictureAsync({ quality: 0.75 });
    } catch (camErr) {
      setProcessing(false);
      Alert.alert('Camera Error', 'Failed to capture photo. Please try again.', [
        { text: 'Retry', onPress: () => setProcessing(false) },
        { text: 'Cancel', onPress: onClose },
      ]);
      return;
    }

    if (!photo?.uri) {
      setProcessing(false);
      Alert.alert('Camera Error', 'No photo was captured. Please try again.', [
        { text: 'Retry', onPress: () => setProcessing(false) },
        { text: 'Cancel', onPress: onClose },
      ]);
      return;
    }

    /* Step B: upload + mark attendance */
    await submitCheckin({ empId: employeeId, combo: 'qr_face', photo });
  };

  const captureWithPhoneCamera = async () => {
    if (processing || usingPhoneCamera) return;
    setUsingPhoneCamera(true);
    let photo = null;
    try {
      await new Promise((r) => setTimeout(r, CAMERA_HANDOFF_DELAY_MS));
      photo = await takeSelfieWithPhoneCamera();
    } finally {
      setUsingPhoneCamera(false); // the in-app camera comes back either way
    }
    if (photo) await submitCheckin({ empId: employeeId, combo: 'qr_face', photo });
  };

  const resetToQR = () => {
    setStep('qr');
    setScanned(false);
    setProcessing(false);
    setFacing('back');
    setEmployeeId(null);
  };

  const flipCamera = () => setFacing(f => f === 'back' ? 'front' : 'back');

  if (!visible) return null;

  /* ── Location error ── */
  if (locError) {
    return (
      <Modal visible={visible} animationType="slide" statusBarTranslucent>
        <View style={styles.center}>
          <Ionicons name="location-outline" size={56} color="rgba(255,255,255,0.45)" />
          <Text style={styles.permTitle}>Location Required</Text>
          <Text style={styles.permText}>{locError}</Text>
          <TouchableOpacity style={styles.permBtn} onPress={() => { setLocError(null); fetchLocation(); }}>
            <Text style={styles.permBtnTxt}>Retry</Text>
          </TouchableOpacity>
          <TouchableOpacity onPress={onClose} style={styles.cancelBtn}>
            <Text style={styles.cancelTxt}>Cancel</Text>
          </TouchableOpacity>
        </View>
      </Modal>
    );
  }

  /* ── Getting location ── */
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

  /* ── Camera permission ── */
  if (!permission) {
    return (
      <Modal visible={visible} animationType="slide" statusBarTranslucent>
        <View style={styles.center}><ActivityIndicator color="#fff" size="large" /></View>
      </Modal>
    );
  }

  if (!permission.granted) {
    return (
      <Modal visible={visible} animationType="slide" statusBarTranslucent>
        <View style={styles.center}>
          <Ionicons name="camera-outline" size={56} color="rgba(255,255,255,0.45)" />
          <Text style={styles.permTitle}>Camera Access Required</Text>
          <Text style={styles.permText}>Camera is needed to scan the QR code and capture your face for attendance.</Text>
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

  /* ── Step 1: QR Scanner ── */
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
          <CameraStartupOverlay camera={camera} onCancel={onClose} />

          {/* Top bar */}
          <View style={styles.topBar}>
            <TouchableOpacity onPress={onClose} style={styles.iconBtn}>
              <Ionicons name="close" size={22} color="#fff" />
            </TouchableOpacity>
            <Text style={styles.topTitle}>Scan Employee QR</Text>
            <TouchableOpacity onPress={flipCamera} style={styles.iconBtn}>
              <Ionicons name="camera-reverse-outline" size={24} color="#fff" />
            </TouchableOpacity>
          </View>

          {/* Scan frame */}
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

          {/* Bottom bar */}
          <View style={styles.bottomBar}>
            <View style={styles.locBadge}>
              <Ionicons name="location" size={14} color="#22C55E" />
              <Text style={styles.locTxt}>Location captured</Text>
            </View>
            <View style={styles.stepRow}>
              <View style={[styles.stepDot, styles.stepDotActive]} />
              <View style={styles.stepDot} />
            </View>
            {processing ? (
              <>
                <ActivityIndicator color="#fff" size="small" style={{ marginTop: 4 }} />
                <Text style={styles.hintTxt}>Marking attendance…</Text>
              </>
            ) : (
              <Text style={styles.hintTxt}>
                {faceEnabled
                  ? 'Step 1 of 2 — Hold your employee QR code in the frame'
                  : 'Hold your employee QR code in the frame'}
              </Text>
            )}
          </View>
        </View>
      </Modal>
    );
  }

  /* ── Step 2: Face Capture ── */
  if (step === 'face') {
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
          <CameraStartupOverlay camera={camera} onUsePhoneCamera={captureWithPhoneCamera} onCancel={resetToQR} />

          {/* Top bar */}
          <View style={styles.topBar}>
            <TouchableOpacity onPress={resetToQR} style={styles.iconBtn}>
              <Ionicons name="arrow-back" size={22} color="#fff" />
            </TouchableOpacity>
            <Text style={styles.topTitle}>Face Verification</Text>
            <TouchableOpacity onPress={flipCamera} style={styles.iconBtn}>
              <Ionicons name="camera-reverse-outline" size={24} color="#fff" />
            </TouchableOpacity>
          </View>

          {/* Face guide overlay */}
          <View style={styles.faceMiddle}>
            <View style={styles.faceGuide} />
            <Text style={styles.faceHintTxt}>
              {camera.open ? 'Align your face inside the circle' : 'Starting camera…'}
            </Text>
            <View style={styles.empIdBadge}>
              <Ionicons name="card-outline" size={14} color="#fff" />
              <Text style={styles.empIdTxt}>{employeeId}</Text>
            </View>
          </View>

          {/* Bottom capture */}
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
    justifyContent: 'center', alignItems: 'center', padding: 32,
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
  corner: {
    position: 'absolute', width: 28, height: 28,
    borderColor: '#fff', borderRadius: 3,
  },
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
  hintTxt: {
    color: 'rgba(255,255,255,0.8)', fontSize: 13,
    textAlign: 'center', lineHeight: 20,
  },

  stepRow: { flexDirection: 'row', gap: 8, alignItems: 'center' },
  stepDot: {
    width: 8, height: 8, borderRadius: 4,
    backgroundColor: 'rgba(255,255,255,0.3)',
  },
  stepDotActive: { backgroundColor: '#fff', width: 20, borderRadius: 4 },

  /* Face step */
  faceMiddle: {
    flex: 1, justifyContent: 'center', alignItems: 'center', gap: 16,
  },
  faceGuide: {
    width: 220, height: 220, borderRadius: 110,
    borderWidth: 3, borderColor: '#fff',
    backgroundColor: 'transparent',
  },
  faceHintTxt: {
    color: 'rgba(255,255,255,0.85)', fontSize: 14, fontWeight: '600',
  },
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
    marginTop: 12,
    width: 72, height: 72, borderRadius: 36,
    backgroundColor: 'rgba(255,255,255,0.2)',
    borderWidth: 3, borderColor: '#fff',
    justifyContent: 'center', alignItems: 'center',
  },
  captureBtnInner: {
    width: 56, height: 56, borderRadius: 28,
    backgroundColor: '#fff',
    justifyContent: 'center', alignItems: 'center',
  },

  permTitle: {
    color: '#fff', fontSize: 20, fontWeight: '700',
    marginTop: 20, marginBottom: 10, textAlign: 'center',
  },
  permText: {
    color: 'rgba(255,255,255,0.6)', fontSize: 14,
    textAlign: 'center', marginBottom: 28, lineHeight: 22,
  },
  permBtn: {
    backgroundColor: '#173B8C', borderRadius: 12,
    paddingVertical: 14, paddingHorizontal: 32,
  },
  permBtnTxt: { color: '#fff', fontWeight: '700', fontSize: 15 },
  cancelBtn:  { marginTop: 16, padding: 8 },
  cancelTxt:  { color: 'rgba(255,255,255,0.5)', fontSize: 14 },
});
