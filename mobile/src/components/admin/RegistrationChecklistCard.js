import React, { useCallback, useState } from "react";
import { View, Text, TouchableOpacity, StyleSheet } from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { useFocusEffect } from "@react-navigation/native";

import { getRegistrationChecklist } from "../../api/client";
import { useTheme } from "../../store/ThemeContext";

// "Complete your registration": signup only asks for the company name and
// the admin's account, so the rest is finished from the dashboard -- the
// same checklist as the website's (templates/_registration_checklist.html,
// from blueprints/org.py's registration_checklist()). Payment opens Seats &
// Billing, which hands over to the website's Razorpay checkout. Shows
// nothing once every step is done, or for HR/manager accounts (the server
// answers 403: only the company's admin can act on these).
const STEPS = {
  company: { title: "Company details", detail: "Add your GSTIN and company logo.", icon: "business-outline",
             screen: "RegistrationCompany", action: "Add details" },
  documents: { title: "Business documents", detail: "Registration certificate, address proof, visiting card and a name-board photo.",
               icon: "document-attach-outline", screen: "RegistrationDocuments", action: "Upload documents" },
  payment: { title: "Payment", detail: "Set up monthly auto-debit so your account carries on after the free trial.",
             icon: "card-outline", screen: "SeatsBilling", action: "Set up payment" },
};

export default function RegistrationChecklistCard({ navigation }) {
  const { colors } = useTheme();
  const styles = React.useMemo(() => makeStyles(colors), [colors]);
  const [steps, setSteps] = useState([]);

  useFocusEffect(
    useCallback(() => {
      let active = true;
      getRegistrationChecklist()
        .then((res) => active && setSteps(res?.data?.ok ? res.data.steps || [] : []))
        .catch(() => active && setSteps([]));
      return () => {
        active = false;
      };
    }, [])
  );

  const known = steps.filter((step) => STEPS[step.key]);
  if (!known.length) return null;
  const finished = known.filter((step) => step.status === "done" || step.status === "review").length;

  return (
    <View style={styles.card} accessibilityLabel="Complete your registration">
      <View style={styles.head}>
        <Ionicons name="list-circle-outline" size={20} color={colors.primary} />
        <Text style={styles.title}>Complete your registration</Text>
        <Text style={styles.count}>{finished} of {known.length} done</Text>
      </View>
      <View style={styles.barTrack}>
        <View style={[styles.barFill, { width: `${Math.round((finished * 100) / known.length)}%` }]} />
      </View>

      {known.map((step, index) => {
        const info = STEPS[step.key];
        const done = step.status === "done";
        const rejected = step.status === "rejected";
        let detail = info.detail;
        if (rejected) detail = `Not approved${step.reason ? `: ${step.reason}` : ""} — please upload corrected documents.`;
        else if (step.status === "review") detail = "Submitted — we're reviewing them, usually within a minute.";
        return (
          <View key={step.key} style={[styles.step, done && styles.stepDone, rejected && styles.stepRejected]}>
            <View style={[styles.num, done && styles.numDone, rejected && styles.numRejected]}>
              {done ? <Ionicons name="checkmark" size={14} color="#FFFFFF" />
                : <Text style={[styles.numText, rejected && styles.numTextOn]}>{rejected ? "!" : index + 1}</Text>}
            </View>
            <View style={styles.stepBody}>
              <Text style={styles.stepTitle}>{info.title}</Text>
              <Text style={styles.stepDetail}>{detail}</Text>
              {done ? (
                <Text style={styles.doneText}>Done</Text>
              ) : step.status === "review" ? (
                <Text style={styles.reviewText}>Under review</Text>
              ) : (
                <TouchableOpacity style={styles.action} onPress={() => navigation.navigate(info.screen)} activeOpacity={0.85}>
                  <Text style={styles.actionText}>{rejected ? "Re-upload documents" : info.action}</Text>
                  <Ionicons name="arrow-forward" size={13} color="#FFFFFF" />
                </TouchableOpacity>
              )}
            </View>
          </View>
        );
      })}
    </View>
  );
}

const makeStyles = (colors) => StyleSheet.create({
  card: {
    backgroundColor: colors.surface, borderRadius: 18, padding: 16, marginBottom: 16,
    borderWidth: 1, borderColor: "rgba(79,70,229,0.25)",
    shadowColor: "#4F46E5", shadowOpacity: 0.1, shadowRadius: 14, shadowOffset: { width: 0, height: 6 }, elevation: 3,
  },
  head: { flexDirection: "row", alignItems: "center", marginBottom: 10 },
  title: { flex: 1, marginLeft: 8, fontSize: 15, fontWeight: "800", color: colors.text },
  count: { fontSize: 12, fontWeight: "700", color: "#4F46E5" },
  barTrack: { height: 6, borderRadius: 3, backgroundColor: "rgba(79,70,229,0.12)", overflow: "hidden", marginBottom: 12 },
  barFill: { height: "100%", borderRadius: 3, backgroundColor: "#4F46E5" },
  step: {
    flexDirection: "row", padding: 12, borderRadius: 12, borderWidth: 1, borderColor: colors.border, marginTop: 8,
  },
  stepDone: { backgroundColor: "rgba(22,163,74,0.07)", borderColor: "rgba(22,163,74,0.3)" },
  stepRejected: { backgroundColor: "rgba(220,38,38,0.06)", borderColor: "rgba(220,38,38,0.3)" },
  num: {
    width: 24, height: 24, borderRadius: 12, justifyContent: "center", alignItems: "center",
    backgroundColor: "rgba(79,70,229,0.12)", marginRight: 10, marginTop: 1,
  },
  numDone: { backgroundColor: "#16A34A" },
  numRejected: { backgroundColor: "#DC2626" },
  numText: { fontSize: 12, fontWeight: "800", color: "#4338CA" },
  numTextOn: { color: "#FFFFFF" },
  stepBody: { flex: 1 },
  stepTitle: { fontSize: 13.5, fontWeight: "700", color: colors.text },
  stepDetail: { fontSize: 12, color: colors.textSecondary, lineHeight: 17, marginTop: 2 },
  doneText: { fontSize: 12, fontWeight: "700", color: "#15803D", marginTop: 6 },
  reviewText: { fontSize: 12, fontWeight: "700", color: "#A16207", marginTop: 6 },
  action: {
    alignSelf: "flex-start", flexDirection: "row", alignItems: "center", marginTop: 8,
    paddingHorizontal: 12, paddingVertical: 7, borderRadius: 8, backgroundColor: "#4F46E5",
  },
  actionText: { fontSize: 12, fontWeight: "700", color: "#FFFFFF", marginRight: 5 },
});
