import React from "react";
import { useNavigation } from "@react-navigation/native";

import AppTopBar from "../AppTopBar";
import { useInnerPageBack } from "../../navigation/materialTabs";
import { useAuth } from "../../store/AuthContext";

// The employee screens' header: the app's one top bar
// (components/AppTopBar.js). `showBack` pages are the profile's sub-pages
// (back goes to the profile); other inner pages go back to where they were
// opened from. `rightAction` replaces the company logo.
export default function ProfileHeader({
  title = "My Profile",
  subtitle,
  showBack = false,
  onBack,
  rightAction,
}) {
  const navigation = useNavigation();
  const { user } = useAuth();
  const innerBack = useInnerPageBack();

  const toProfile = () => {
    try {
      navigation.navigate("Profile");
    } catch (_) {
      if (navigation.canGoBack()) navigation.goBack();
    }
  };

  return (
    <AppTopBar
      title={title}
      subtitle={user?.company || (subtitle && !/portal/i.test(subtitle) ? subtitle : undefined)}
      onBack={showBack ? onBack || toProfile : innerBack}
      right={rightAction || undefined}
      onCompanyPress={toProfile}
    />
  );
}
