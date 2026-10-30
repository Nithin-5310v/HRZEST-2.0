import React from "react";
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
  Image,
  StatusBar,
  Platform,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { useNavigation, DrawerActions } from "@react-navigation/native";
import { useAuth } from "../../store/AuthContext";

export default function AdminHeader({
  title = "Dashboard",
  subtitle = "ADMIN PORTAL",
  profileImage,
  // No caller ever passes a real count -- this used to default to a literal
  // 3, a permanently fake "unread" badge. Defaulting to 0 until a real
  // unread count is threaded through (api_dashboard() already returns
  // unread_notifications -- a future pass could lift that into a shared
  // context this header reads) is honest: no badge, not a fabricated one.
  notificationCount = 0,
  onMenu,
  onNotification,
  onProfile,
}) {
  const navigation = useNavigation();
  const { user } = useAuth();
  const logoUri = profileImage || user?.logo;

  const handleMenuPress = () => {
    if (typeof onMenu === "function") {
      try {
        onMenu();
      } catch (e) {
        navigation.dispatch(DrawerActions.openDrawer());
      }
    } else {
      navigation.dispatch(DrawerActions.openDrawer());
    }
  };

  const handleProfilePress = () => {
    if (typeof onProfile === "function") {
      onProfile();
    } else {
      try {
        navigation.navigate("Settings", { tab: "profile" });
      } catch (e) {
        // Fallback if settings route not found
      }
    }
  };

  const handleNotificationPress = () => {
    if (typeof onNotification === "function") {
      onNotification();
    } else {
      try {
        navigation.navigate("Notifications");
      } catch (e) {
        // Fallback if the Notifications route isn't registered on this navigator
      }
    }
  };

  return (
    <>
      <StatusBar
        translucent
        backgroundColor="transparent"
        barStyle="dark-content"
      />
      <View style={styles.container}>
        {/* Hamburger Menu Button */}
        <TouchableOpacity
          activeOpacity={0.7}
          style={styles.iconButton}
          onPress={handleMenuPress}
        >
          <Ionicons
            name="menu-sharp"
            size={22}
            color="#132026"
          />
        </TouchableOpacity>

        {/* Header Title Section (Aligned next to Hamburger) */}
        <View style={styles.titleSection}>
          {!!subtitle && (
            <View style={styles.tagBadge}>
              <Text style={styles.tagText}>{(user?.company || subtitle).toUpperCase()}</Text>
            </View>
          )}
          <Text numberOfLines={1} style={styles.title}>
            {title}
          </Text>
        </View>

        {/* Right Action Icons */}
        <View style={styles.rightGroup}>
          <TouchableOpacity
            activeOpacity={0.7}
            style={styles.iconButton}
            onPress={handleNotificationPress}
          >
            <Ionicons
              name="notifications-outline"
              size={20}
              color="#132026"
            />
            {notificationCount > 0 && (
              <View style={styles.badge}>
                <Text style={styles.badgeText}>
                  {notificationCount > 9 ? "9+" : notificationCount}
                </Text>
              </View>
            )}
          </TouchableOpacity>

          <TouchableOpacity
            activeOpacity={0.8}
            style={styles.avatarButton}
            onPress={handleProfilePress}
          >
            {logoUri ? (
              <Image
                source={{ uri: logoUri }}
                style={{ width: 34, height: 34, borderRadius: 17, backgroundColor: "#FFFFFF" }}
                resizeMode="cover"
              />
            ) : (
              <View style={[styles.avatarFallback, { backgroundColor: "#0B6E8E" }]}>
                <Text style={{ color: "#FFFFFF", fontWeight: "900", fontSize: 14 }}>
                  {(user?.company || user?.name || "A").charAt(0).toUpperCase()}
                </Text>
              </View>
            )}
          </TouchableOpacity>
        </View>
      </View>
    </>
  );
}

const styles = StyleSheet.create({
  container: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingHorizontal: 16,
    paddingTop: Platform.OS === "ios" ? 54 : (StatusBar.currentHeight || 24) + 12,
    paddingBottom: 14,
    backgroundColor: "#FFFFFF",
    borderBottomWidth: 1,
    borderBottomColor: "#ECF3F5",
    shadowColor: "#132026",
    shadowOpacity: 0.03,
    shadowRadius: 8,
    shadowOffset: { width: 0, height: 3 },
    elevation: 2,
  },
  iconButton: {
    width: 40,
    height: 40,
    borderRadius: 12,
    backgroundColor: "#F5FAFB",
    justifyContent: "center",
    alignItems: "center",
    borderWidth: 1,
    borderColor: "#DCE6E9",
  },
  titleSection: {
    flex: 1,
    marginLeft: 12,
    marginRight: 8,
    justifyContent: "center",
  },
  tagBadge: {
    alignSelf: "flex-start",
    backgroundColor: "#E8F5F7",
    paddingHorizontal: 6,
    paddingVertical: 2,
    borderRadius: 6,
    marginBottom: 2,
  },
  tagText: {
    fontSize: 10,
    fontWeight: "800",
    color: "#0B6E8E",
    letterSpacing: 0.8,
  },
  title: {
    fontSize: 18,
    fontWeight: "800",
    color: "#132026",
    letterSpacing: -0.3,
  },
  rightGroup: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
  },
  badge: {
    position: "absolute",
    top: 5,
    right: 5,
    minWidth: 16,
    height: 16,
    borderRadius: 8,
    backgroundColor: "#EF4444",
    justifyContent: "center",
    alignItems: "center",
    paddingHorizontal: 4,
    borderWidth: 1.5,
    borderColor: "#FFFFFF",
  },
  badgeText: {
    color: "#FFFFFF",
    fontSize: 9,
    fontWeight: "800",
  },
  avatarButton: {
    borderRadius: 12,
    overflow: "hidden",
  },
  avatarImage: {
    width: 40,
    height: 40,
    borderRadius: 12,
    borderWidth: 1.5,
    borderColor: "#DCE6E9",
  },
  avatarFallback: {
    width: 40,
    height: 40,
    borderRadius: 12,
    backgroundColor: "#E8F5F7",
    justifyContent: "center",
    alignItems: "center",
    borderWidth: 1.5,
    borderColor: "#CDEBF2",
  },
});