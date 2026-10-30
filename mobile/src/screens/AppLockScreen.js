import React, { useEffect, useState } from "react";
import { View, Text, TouchableOpacity, StyleSheet } from "react-native";
import { LinearGradient } from "expo-linear-gradient";
import { Ionicons } from "@expo/vector-icons";

import { useAuth } from "../store/AuthContext";

export default function AppLockScreen() {
  const { unlockApp, signOut } = useAuth();
  const [failed, setFailed] = useState(false);

  const prompt = async () => {
    setFailed(false);
    const ok = await unlockApp();
    if (!ok) setFailed(true);
  };

  useEffect(() => {
    prompt();
  }, []);

  return (
    <LinearGradient colors={["#132026", "#0C3054", "#0B6E8E"]} style={styles.bg}>
      <View style={styles.center}>
        <View style={styles.iconCircle}>
          <Ionicons name="lock-closed" size={36} color="#0B6E8E" />
        </View>
        <Text style={styles.title}>App Locked</Text>
        <Text style={styles.subtitle}>
          {failed
            ? "Verification failed or was cancelled."
            : "Verify your identity to continue."}
        </Text>

        <TouchableOpacity style={styles.unlockBtn} onPress={prompt} activeOpacity={0.85}>
          <Ionicons name="finger-print" size={20} color="#FFFFFF" style={{ marginRight: 8 }} />
          <Text style={styles.unlockBtnText}>Unlock</Text>
        </TouchableOpacity>

        <TouchableOpacity style={styles.signOutBtn} onPress={signOut}>
          <Text style={styles.signOutText}>Sign out instead</Text>
        </TouchableOpacity>
      </View>
    </LinearGradient>
  );
}

const styles = StyleSheet.create({
  bg: { flex: 1 },
  center: { flex: 1, justifyContent: "center", alignItems: "center", padding: 24 },
  iconCircle: {
    width: 76, height: 76, borderRadius: 38,
    backgroundColor: "#FFFFFF",
    justifyContent: "center", alignItems: "center",
    marginBottom: 20,
  },
  title: { fontSize: 20, fontWeight: "800", color: "#FFFFFF" },
  subtitle: {
    fontSize: 13, color: "rgba(255,255,255,0.7)",
    marginTop: 8, marginBottom: 28, textAlign: "center", paddingHorizontal: 20,
  },
  unlockBtn: {
    flexDirection: "row", alignItems: "center",
    backgroundColor: "#0B6E8E",
    paddingHorizontal: 28, paddingVertical: 14,
    borderRadius: 14,
  },
  unlockBtnText: { color: "#FFFFFF", fontWeight: "800", fontSize: 15 },
  signOutBtn: { marginTop: 20, padding: 8 },
  signOutText: { color: "rgba(255,255,255,0.6)", fontSize: 13, fontWeight: "600" },
});
