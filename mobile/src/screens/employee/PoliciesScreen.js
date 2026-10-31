import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  View,
  Text,
  ScrollView,
  StyleSheet,
  ActivityIndicator,
  RefreshControl,
  TouchableOpacity,
  Linking,
} from "react-native";
import SafeAreaView from "../../components/SafeAreaView";
import { Ionicons } from "@expo/vector-icons";

import ProfileHeader from "../../components/profile/ProfileHeader";

import PoliciesHeaderCard from "../../components/policies/PoliciesHeaderCard";
import PolicyTabs from "../../components/policies/PolicyTabs";
import PolicySection from "../../components/policies/PolicySection";
import EmptyPolicy from "../../components/policies/EmptyPolicy";
import SectionTitle from "../../components/policies/SectionTitle";

import { fetchCompanyPolicies } from "../../api/client";
import { useTheme } from "../../store/ThemeContext";

// The terms and policies the employee's company has written and published
// (the admin's Organization > Terms & Policies, or the website's
// Organization page), grouped into one tab per category.
const CATEGORY_TABS = {
  terms: "Terms",
  rules: "Rules",
  limitations: "Limitations",
  instructions: "Instructions",
  posh: "POSH",
  resignation: "Resignation",
};

export const categoryTab = (category) => CATEGORY_TABS[category] || category;

// A policy's text as PolicySection bullets: one per line, without any
// "-", "*" or "•" the author typed in front.
export const policyBullets = (body) =>
  String(body || "")
    .split(/\r?\n/)
    .map((line) => line.trim().replace(/^[-*•]\s+/, ""))
    .filter(Boolean);

export default function PoliciesScreen() {
  const { colors } = useTheme();
  const styles = React.useMemo(() => makeStyles(colors), [colors]);

  const [policies, setPolicies] = useState([]);
  const [contact, setContact] = useState(null);
  const [selectedTab, setSelectedTab] = useState(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [failed, setFailed] = useState(false);

  const load = useCallback(async () => {
    try {
      const res = await fetchCompanyPolicies();
      setPolicies(res?.data?.policies || []);
      setContact(res?.data?.contact || null);
      setFailed(false);
    } catch {
      setFailed(true);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const tabs = useMemo(() => [...new Set(policies.map((p) => categoryTab(p.category)))], [policies]);
  const activeTab = tabs.includes(selectedTab) ? selectedTab : tabs[0];
  const shown = policies.filter((p) => categoryTab(p.category) === activeTab);

  const contactRows = contact ? [
    contact.contact_email && { icon: "mail-outline", text: contact.contact_email, url: `mailto:${contact.contact_email}` },
    contact.contact_phone && { icon: "call-outline", text: contact.contact_phone, url: `tel:${contact.contact_phone.replace(/[^\d+]/g, "")}` },
    contact.website && { icon: "globe-outline", text: contact.website, url: contact.website },
  ].filter(Boolean) : [];

  return (
    <SafeAreaView style={styles.container}>
      <ProfileHeader title="Policies & Guidelines" showBack={false} />

      <ScrollView
        showsVerticalScrollIndicator={false}
        contentContainerStyle={styles.content}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => { setRefreshing(true); load(); }} />}
      >
        <PoliciesHeaderCard />

        {loading ? (
          <ActivityIndicator style={{ marginTop: 30 }} color={colors.primary} />
        ) : !policies.length ? (
          <EmptyPolicy
            title={failed ? "Couldn't load policies" : "No policies yet"}
            message={failed
              ? "Check your connection and try again."
              : "Your company hasn't published any policies yet. They'll appear here once HR adds them."}
            buttonText="Refresh"
            onPress={() => { setLoading(true); load(); }}
          />
        ) : (
          <>
            <PolicyTabs tabs={tabs} selectedTab={activeTab} onSelectTab={setSelectedTab} />

            <SectionTitle
              icon="book-outline"
              title="Company Policies"
              subtitle="Read and understand your company's official policies."
            />

            {shown.map((p) => (
              <PolicySection key={p.id} title={p.title} bullets={policyBullets(p.body)} />
            ))}
          </>
        )}

        {contactRows.length ? (
          <>
            <SectionTitle
              icon="call-outline"
              title="Need Assistance?"
              subtitle={`Contact ${contact.company_name || "your company"} about any policy.`}
            />
            <View style={styles.contactCard}>
              {contactRows.map((row) => (
                <TouchableOpacity key={row.icon} style={styles.contactRow} onPress={() => Linking.openURL(row.url)}>
                  <Ionicons name={row.icon} size={18} color={colors.primary} />
                  <Text style={styles.contactText} numberOfLines={1}>{row.text}</Text>
                  <Ionicons name="open-outline" size={15} color={colors.textLight} />
                </TouchableOpacity>
              ))}
            </View>
          </>
        ) : null}

        <View style={{ height: 110 }} />
      </ScrollView>
    </SafeAreaView>
  );
}

const makeStyles = (colors) => StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: colors.background,
  },

  content: {
    paddingHorizontal: 20,
    paddingTop: 18,
    paddingBottom: 30,
  },

  contactCard: {
    backgroundColor: colors.surface,
    borderRadius: 18,
    paddingHorizontal: 16,
    paddingVertical: 6,
    borderWidth: 1,
    borderColor: colors.border,
  },

  contactRow: {
    flexDirection: "row",
    alignItems: "center",
    paddingVertical: 12,
  },

  contactText: {
    flex: 1,
    marginLeft: 12,
    fontSize: 14,
    fontWeight: "600",
    color: colors.text,
  },
});
