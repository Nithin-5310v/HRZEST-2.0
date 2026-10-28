import React, { useState } from "react";
import {
  ScrollView,
  StyleSheet,
} from "react-native";
import SafeAreaView from "../../components/SafeAreaView";

import ProfileHeader from "../../components/profile/ProfileHeader";

import PoliciesHeaderCard from "../../components/policies/PoliciesHeaderCard";
import PolicyTabs from "../../components/policies/PolicyTabs";
import PolicyBanner from "../../components/policies/PolicyBanner";
import PolicySection from "../../components/policies/PolicySection";
import HighlightCard from "../../components/policies/HighlightCard";
import ContactInfoCard from "../../components/policies/ContactInfoCard";
import PolicyFooter from "../../components/policies/PolicyFooter";
import EmptyPolicy from "../../components/policies/EmptyPolicy";
import SectionTitle from "../../components/policies/SectionTitle";

import {
  policyTabs,
  policies,
} from "../../data/policiesData";
import { useTheme } from "../../store/ThemeContext";

export default function PoliciesScreen() {
  const { colors } = useTheme();
  const styles = React.useMemo(() => makeStyles(colors), [colors]);
  const [selectedTab, setSelectedTab] = useState("terms");

  const selectedPolicy = policies[selectedTab];

  return (
    <SafeAreaView style={styles.container}>
      <ProfileHeader
        title="Policies & Guidelines"
        showBack={false}
      />

      <ScrollView
        showsVerticalScrollIndicator={false}
        contentContainerStyle={styles.content}
      >
        <PoliciesHeaderCard />

        <PolicyTabs
          tabs={policyTabs.map((item) => item.title)}
          selectedTab={
            policyTabs.find(
              (item) => item.id === selectedTab
            )?.title
          }
          onSelectTab={(title) => {
            const tab = policyTabs.find(
              (item) => item.title === title
            );

            if (tab) {
              setSelectedTab(tab.id);
            }
          }}
        />

        {selectedPolicy?.banner && (
          <PolicyBanner
            type={selectedPolicy.banner.type}
            title={selectedPolicy.banner.title}
            message={selectedPolicy.banner.message}
          />
        )}

        <SectionTitle
          icon="book-outline"
          title="Company Policies"
          subtitle="Read and understand the official company policies."
        />

        {selectedPolicy?.sections?.length ? (
          selectedPolicy.sections.map((section, index) => (
            <PolicySection
              key={index}
              title={section.title}
              bullets={section.bullets}
            />
          ))
        ) : (
          <EmptyPolicy />
        )}

        <SectionTitle
          icon="shield-checkmark-outline"
          title="Important Notice"
          subtitle="Please review these reminders carefully."
        />

        <HighlightCard
          type="warning"
          title="Policy Compliance"
          description="Every employee is expected to comply with all company policies. Failure to comply may result in disciplinary action as per organizational guidelines."
        />

        <SectionTitle
          icon="call-outline"
          title="Need Assistance?"
          subtitle="Contact the relevant department for any clarification."
        />

        <ContactInfoCard />

        <PolicyFooter
          version="v2.1"
          updated="01 July 2026"
          owner="Human Resources"
          status="Active"
        />

        <SafeAreaView style={{ height: 110 }} />
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
});