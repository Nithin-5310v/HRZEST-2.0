import React from "react";
import { useNavigation } from "@react-navigation/native";

import AppTopBar from "../AppTopBar";
import { useInnerPageBack } from "../../navigation/materialTabs";
import { useAuth } from "../../store/AuthContext";

// The admin and manager screens' header: the app's one top bar
// (components/AppTopBar.js), with the company under the title, the
// notifications bell, and the company logo opening Settings. Inner pages
// (anything that isn't a bottom-bar tab) get a back arrow.
export default function AdminHeader({
  title = "Dashboard",
  subtitle = "ADMIN PORTAL",
  // No caller ever passes a real count -- this used to default to a literal
  // 3, a permanently fake "unread" badge. Defaulting to 0 until a real
  // unread count is threaded through (api_dashboard() already returns
  // unread_notifications -- a future pass could lift that into a shared
  // context this header reads) is honest: no badge, not a fabricated one.
  notificationCount = 0,
  onNotification,
  onProfile,
}) {
  const navigation = useNavigation();
  const { user } = useAuth();
  const onBack = useInnerPageBack();

  const go = (route, params) => {
    try {
      navigation.navigate(route, params);
    } catch (e) {
      // The route isn't registered on this navigator
    }
  };

  return (
    <AppTopBar
      title={title}
      subtitle={user?.company || sentenceCase(subtitle)}
      onBack={onBack}
      actions={[{
        icon: "notifications-outline",
        label: "Notifications",
        badge: notificationCount,
        onPress: typeof onNotification === "function" ? onNotification : () => go("Notifications"),
      }]}
      onCompanyPress={typeof onProfile === "function" ? onProfile : () => go("Settings", { tab: "profile" })}
    />
  );
}

function sentenceCase(text) {
  if (!text) return "";
  const lower = String(text).toLowerCase();
  return lower.charAt(0).toUpperCase() + lower.slice(1);
}
