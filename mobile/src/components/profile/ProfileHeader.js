import React from "react";
import {
  View,
  Text,
  TouchableOpacity,
  StyleSheet,
  StatusBar,
  Platform,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { useNavigation, DrawerActions } from "@react-navigation/native";

export default function ProfileHeader({
  title = "My Profile",
  subtitle = "EMPLOYEE PORTAL",
  showBack = false,
  onBack,
  rightAction,
}) {
  const navigation = useNavigation();

  const handleMenuOrBack = () => {
    if (showBack) {
      if (onBack) {
        onBack();
      } else {
        try {
          navigation.navigate("Profile");
        } catch (_) {
          if (navigation.canGoBack()) {
            navigation.goBack();
          }
        }
      }
    } else {
      navigation.dispatch(DrawerActions.openDrawer());
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
        {/* Left Action (Hamburger or Back) */}
        <TouchableOpacity
          activeOpacity={0.7}
          style={styles.iconButton}
          onPress={handleMenuOrBack}
        >
          <Ionicons
            name={showBack ? "arrow-back-sharp" : "menu-sharp"}
            size={22}
            color="#131C2A"
          />
        </TouchableOpacity>

        {/* Title Section (Aligned next to Hamburger) */}
        <View style={styles.titleSection}>
          {!!subtitle && (
            <View style={styles.tagBadge}>
              <Text style={styles.tagText}>{subtitle.toUpperCase()}</Text>
            </View>
          )}
          <Text numberOfLines={1} style={styles.title}>
            {title}
          </Text>
        </View>

        {/* Right Action */}
        <View style={styles.rightGroup}>
          {rightAction ? (
            rightAction
          ) : (
            <TouchableOpacity
              activeOpacity={0.8}
              style={styles.avatarButton}
              onPress={() => navigation.navigate("Profile")}
            >
              <View style={styles.avatarFallback}>
                <Ionicons
                  name="person-sharp"
                  size={18}
                  color="#0B5CAD"
                />
              </View>
            </TouchableOpacity>
          )}
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
    borderBottomColor: "#EDF2F8",
    shadowColor: "#131C2A",
    shadowOpacity: 0.03,
    shadowRadius: 8,
    shadowOffset: { width: 0, height: 3 },
    elevation: 2,
  },
  iconButton: {
    width: 40,
    height: 40,
    borderRadius: 12,
    backgroundColor: "#F5F8FC",
    justifyContent: "center",
    alignItems: "center",
    borderWidth: 1,
    borderColor: "#DBE3EE",
  },
  titleSection: {
    flex: 1,
    marginLeft: 12,
    marginRight: 8,
    justifyContent: "center",
  },
  tagBadge: {
    alignSelf: "flex-start",
    backgroundColor: "#E6EFFA",
    paddingHorizontal: 6,
    paddingVertical: 2,
    borderRadius: 6,
    marginBottom: 2,
  },
  tagText: {
    fontSize: 10,
    fontWeight: "800",
    color: "#0B5CAD",
    letterSpacing: 0.8,
  },
  title: {
    fontSize: 18,
    fontWeight: "800",
    color: "#131C2A",
    letterSpacing: -0.3,
  },
  rightGroup: {
    flexDirection: "row",
    alignItems: "center",
  },
  avatarButton: {
    borderRadius: 12,
    overflow: "hidden",
  },
  avatarFallback: {
    width: 40,
    height: 40,
    borderRadius: 12,
    backgroundColor: "#E6EFFA",
    justifyContent: "center",
    alignItems: "center",
    borderWidth: 1.5,
    borderColor: "#D3E3F7",
  },
});