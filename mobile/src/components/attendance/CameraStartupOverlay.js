import React from "react";
import { View, Text, TouchableOpacity, StyleSheet } from "react-native";
import { Ionicons } from "@expo/vector-icons";

// Shown over an attendance scanner's camera when it failed to start (see
// useCameraHandoff): a reason instead of a black preview, a restart, on the
// face step the phone's own camera as a way through, and a way out -- it
// covers the screen's own back/close button.
export default function CameraStartupOverlay({ camera, onUsePhoneCamera, onCancel }) {
  if (!camera.error) return null;
  return (
    <View style={styles.overlay}>
      <Ionicons name="videocam-off-outline" size={44} color="rgba(255,255,255,0.6)" />
      <Text style={styles.title}>Camera not available</Text>
      <Text style={styles.text}>{camera.error}</Text>
      <TouchableOpacity style={styles.primaryBtn} onPress={camera.retry} activeOpacity={0.85}>
        <Text style={styles.primaryTxt}>Restart camera</Text>
      </TouchableOpacity>
      {onUsePhoneCamera ? (
        <TouchableOpacity style={styles.secondaryBtn} onPress={onUsePhoneCamera} activeOpacity={0.85}>
          <Ionicons name="camera-outline" size={16} color="#fff" />
          <Text style={styles.secondaryTxt}>Use phone camera</Text>
        </TouchableOpacity>
      ) : null}
      {onCancel ? (
        <TouchableOpacity style={styles.cancelBtn} onPress={onCancel}>
          <Text style={styles.cancelTxt}>Cancel</Text>
        </TouchableOpacity>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  overlay: {
    ...StyleSheet.absoluteFillObject, zIndex: 5, alignItems: "center", justifyContent: "center",
    paddingHorizontal: 32, backgroundColor: "#000",
  },
  title: { color: "#fff", fontSize: 18, fontWeight: "700", marginTop: 14, marginBottom: 8 },
  text: { color: "rgba(255,255,255,0.65)", fontSize: 14, textAlign: "center", lineHeight: 21, marginBottom: 24 },
  primaryBtn: { backgroundColor: "#0B5CAD", borderRadius: 10, paddingVertical: 13, paddingHorizontal: 30 },
  primaryTxt: { color: "#fff", fontWeight: "700", fontSize: 15 },
  secondaryBtn: {
    flexDirection: "row", alignItems: "center", marginTop: 12, paddingVertical: 11, paddingHorizontal: 22,
    borderRadius: 10, borderWidth: 1, borderColor: "rgba(255,255,255,0.35)",
  },
  secondaryTxt: { color: "#fff", fontWeight: "600", fontSize: 14, marginLeft: 8 },
  cancelBtn: { marginTop: 18, padding: 8 },
  cancelTxt: { color: "rgba(255,255,255,0.6)", fontSize: 14 },
});
