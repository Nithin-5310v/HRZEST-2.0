import React from "react";
import { View, Text, StyleSheet } from "react-native";
import { Ionicons } from "@expo/vector-icons";

// The company-signup step indicator -- the website's steps
// (templates/_org_wizard_steps.html) up to Documents; the app leaves
// payment to the website. Drawn for the signup screens' dark gradient.
export const SIGNUP_STEPS = ["Account", "Verify Email", "Company", "Documents"];

export default function SignupSteps({ current }) {
  return (
    <View style={styles.row}>
      {SIGNUP_STEPS.map((label, index) => {
        const num = index + 1;
        const done = num < current;
        const active = num === current;
        return (
          <React.Fragment key={label}>
            <View style={styles.step}>
              <View style={[styles.circle, done && styles.circleDone, active && styles.circleActive]}>
                {done ? (
                  <Ionicons name="checkmark" size={14} color="#34D399" />
                ) : (
                  <Text style={[styles.num, active && styles.numActive]}>{num}</Text>
                )}
              </View>
              <Text style={[styles.label, (done || active) && styles.labelOn]} numberOfLines={1}>
                {label}
              </Text>
            </View>
            {num < SIGNUP_STEPS.length && <View style={[styles.line, done && styles.lineDone]} />}
          </React.Fragment>
        );
      })}
    </View>
  );
}

const styles = StyleSheet.create({
  row: { flexDirection: "row", alignItems: "flex-start", justifyContent: "center", marginBottom: 22 },
  step: { alignItems: "center", width: 70 },
  circle: {
    width: 28, height: 28, borderRadius: 14, justifyContent: "center", alignItems: "center",
    backgroundColor: "rgba(255,255,255,0.10)", borderWidth: 1.5, borderColor: "rgba(255,255,255,0.28)",
  },
  circleActive: { backgroundColor: "#FFFFFF", borderColor: "#FFFFFF" },
  circleDone: { backgroundColor: "rgba(16,185,129,0.18)", borderColor: "rgba(52,211,153,0.6)" },
  num: { fontSize: 12, fontWeight: "800", color: "rgba(255,255,255,0.7)" },
  numActive: { color: "#173B8C" },
  label: { fontSize: 10, fontWeight: "700", color: "rgba(255,255,255,0.55)", marginTop: 6 },
  labelOn: { color: "#FFFFFF" },
  line: { flex: 1, maxWidth: 28, height: 2, marginTop: 13, backgroundColor: "rgba(255,255,255,0.22)" },
  lineDone: { backgroundColor: "#34D399" },
});
