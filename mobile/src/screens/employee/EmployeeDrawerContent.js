import React from "react";
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
  Image,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { DrawerContentScrollView } from "@react-navigation/drawer";
import { getFocusedRouteNameFromRoute } from "@react-navigation/native";
import { LinearGradient } from "expo-linear-gradient";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { employeeLogout } from "../../api/client";
import { useAuth } from "../../store/AuthContext";
import { useTheme } from "../../store/ThemeContext";

export default function EmployeeDrawerContent(props) {
  const { navigation, state } = props;
  const { signOut, user } = useAuth();
  const { colors } = useTheme();
  const styles = React.useMemo(() => makeStyles(colors), [colors]);
  const insets = useSafeAreaInsets();

  const drawerRoute = state.routes[state.index];
  const activeRoute =
    getFocusedRouteNameFromRoute(drawerRoute) ?? drawerRoute.name;

  const handleLogout = async () => {
    try {
      await employeeLogout();
    } catch (_) {}
    signOut();
  };

  const menuItems = [
    // ATTENDANCE & TIME
    {
      title: "My Attendance Log",
      icon: "calendar-outline",
      iconFocused: "calendar",
      route: "Attendance",
      section: "ATTENDANCE & TIME",
      badge: "LIVE",
      badgeBg: "#DCFCE7",
      badgeColor: "#15803D",
    },
    {
      title: "Comp-Off & Overtime",
      icon: "time-outline",
      iconFocused: "time",
      route: "CompOff",
      section: "ATTENDANCE & TIME",
    },
    {
      title: "Holidays Calendar",
      icon: "calendar-clear-outline",
      iconFocused: "calendar-clear",
      route: "Holidays",
      section: "ATTENDANCE & TIME",
    },

    // PAYROLL & EARNINGS
    {
      title: "Payslips & Monthly Salary",
      icon: "document-outline",
      iconFocused: "document",
      route: "Payslips",
      section: "PAYROLL & EARNINGS",
    },
    {
      title: "Earnings Breakdown",
      icon: "wallet-outline",
      iconFocused: "wallet",
      route: "Earnings",
      section: "PAYROLL & EARNINGS",
    },

    // CAREER & PERFORMANCE
    {
      title: "My Performance & Goals",
      icon: "trending-up-outline",
      iconFocused: "trending-up",
      route: "Performance",
      section: "CAREER & PERFORMANCE",
    },
    {
      title: "My Onboarding Checklist",
      icon: "briefcase-outline",
      iconFocused: "briefcase",
      route: "Onboarding",
      section: "CAREER & PERFORMANCE",
    },

    // SELF-SERVICE & POLICIES
    {
      title: "My Profile & Personal Info",
      icon: "person-circle-outline",
      iconFocused: "person-circle",
      route: "Profile",
      section: "SELF SERVICE",
    },
    {
      title: "Support Tickets & Helpdesk",
      icon: "chatbubbles-outline",
      iconFocused: "chatbubbles",
      route: "Tickets",
      section: "SELF SERVICE",
    },
    {
      title: "Company Policies & Guidelines",
      icon: "document-text-outline",
      iconFocused: "document-text",
      route: "Policies",
      section: "SELF SERVICE",
    },
    {
      title: "Resignation Request",
      icon: "exit-outline",
      iconFocused: "exit",
      route: "Resignation",
      section: "SELF SERVICE",
    },
  ];

  const renderMenuItem = (item) => {
    const active = activeRoute === item.route;

    return (
      <TouchableOpacity
        key={item.title}
        activeOpacity={0.88}
        style={[styles.menuItem, active && styles.activeMenuItem]}
        onPress={() => {
          navigation.navigate("EmployeeTabs", {
            screen: item.route,
          });
          navigation.closeDrawer();
        }}
      >
        <View style={[styles.iconBg, active && styles.activeIconBg]}>
          <Ionicons
            name={active ? item.iconFocused : item.icon}
            size={18}
            color={active ? "#FFFFFF" : colors.primary}
          />
        </View>

        <Text style={[styles.menuText, active && styles.activeMenuText]} numberOfLines={1}>
          {item.title}
        </Text>

        {item.badge && !active && (
          <View style={[styles.pillBadge, { backgroundColor: item.badgeBg }]}>
            <Text style={[styles.pillBadgeText, { color: item.badgeColor }]}>
              {item.badge}
            </Text>
          </View>
        )}

        <View style={[styles.chevronContainer, active && styles.activeChevronContainer]}>
          <Ionicons
            name={active ? "checkmark-circle" : "chevron-forward"}
            size={16}
            color={active ? colors.success : colors.textLight}
          />
        </View>
      </TouchableOpacity>
    );
  };

  const renderSection = (title, section) => {
    const items = menuItems.filter((item) => item.section === section);
    if (items.length === 0) return null;

    return (
      <View style={styles.section} key={section}>
        <Text style={styles.sectionTitle}>{title}</Text>
        {items.map(renderMenuItem)}
      </View>
    );
  };

  return (
    <View style={[styles.container, { paddingTop: insets.top }]}>
      {/* Executive Hero Header */}
      <LinearGradient
        colors={["#0C3054", "#0B5CAD"]}
        start={{ x: 0, y: 0 }}
        end={{ x: 1, y: 1 }}
        style={styles.header}
      >
        {/* Company Branding Pill */}
        <View style={{ flexDirection: "row", alignItems: "center", marginBottom: 14, backgroundColor: "rgba(255,255,255,0.15)", paddingHorizontal: 12, paddingVertical: 6, borderRadius: 14, alignSelf: "flex-start" }}>
          {user?.logo ? (
            <Image source={{ uri: user.logo }} style={{ width: 20, height: 20, borderRadius: 10, marginRight: 8, backgroundColor: "#FFFFFF" }} />
          ) : (
            <Ionicons name="business" size={16} color="#FFFFFF" style={{ marginRight: 6 }} />
          )}
          <Text style={{ color: "#FFFFFF", fontWeight: "800", fontSize: 13 }} numberOfLines={1}>
            {user?.company || "Organisation Portal"}
          </Text>
        </View>

        <View style={styles.headerTopRow}>
          <View style={styles.avatarBorder}>
            <View style={styles.avatar}>
              <Text style={styles.avatarText}>
                {user && user.name ? user.name.charAt(0).toUpperCase() : "E"}
              </Text>
            </View>
            <View style={styles.onlineDot} />
          </View>

          <View style={styles.userInfo}>
            <Text style={styles.name} numberOfLines={1}>
              {user?.name || "Employee Portal"}
            </Text>
            <Text style={styles.empId}>
              ID: {user?.employeeId || "EMP-1001"}
            </Text>
            <View style={styles.roleBadgeRow}>
              <View style={styles.roleBadge}>
                <Ionicons name="shield-checkmark" size={12} color="#4C9BE8" style={{ marginRight: 4 }} />
                <Text style={styles.roleText}>Verified Staff</Text>
              </View>
            </View>
          </View>
        </View>
      </LinearGradient>

      {/* Menu Items Scroll View */}
      <DrawerContentScrollView
        {...props}
        showsVerticalScrollIndicator={false}
        contentContainerStyle={styles.scrollContent}
      >
        {renderSection("ATTENDANCE & TIME", "ATTENDANCE & TIME")}
        {renderSection("PAYROLL & EARNINGS", "PAYROLL & EARNINGS")}
        {renderSection("CAREER & PERFORMANCE", "CAREER & PERFORMANCE")}
        {renderSection("SELF SERVICE", "SELF SERVICE")}
      </DrawerContentScrollView>

      {/* Bottom Logout Button */}
      <View style={[styles.bottomContainer, { paddingBottom: Math.max(insets.bottom, 16) }]}>
        <TouchableOpacity
          activeOpacity={0.88}
          style={styles.logoutButton}
          onPress={handleLogout}
        >
          <Ionicons name="log-out-outline" size={20} color={colors.danger} />
          <Text style={styles.logoutText}>Sign Out Account</Text>
        </TouchableOpacity>

        <Text style={styles.version}>Magzest HRMS Mobile • v1.0.0</Text>
      </View>
    </View>
  );
}

const makeStyles = (colors) => StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: colors.background,
  },
  header: {
    paddingHorizontal: 20,
    paddingTop: 22,
    paddingBottom: 24,
    borderBottomLeftRadius: 26,
    borderBottomRightRadius: 26,
    elevation: 10,
    shadowColor: "#0C3054",
    shadowOpacity: 0.35,
    shadowRadius: 14,
    shadowOffset: { width: 0, height: 6 },
  },
  headerTopRow: {
    flexDirection: "row",
    alignItems: "center",
  },
  avatarBorder: {
    padding: 3,
    borderRadius: 36,
    backgroundColor: "rgba(255, 255, 255, 0.25)",
    position: "relative",
  },
  avatar: {
    width: 60,
    height: 60,
    borderRadius: 30,
    backgroundColor: "#FFFFFF",
    justifyContent: "center",
    alignItems: "center",
    elevation: 4,
    shadowColor: "#000",
    shadowOpacity: 0.1,
    shadowRadius: 6,
  },
  avatarText: {
    fontSize: 20,
    fontWeight: "800",
    color: "#0C3054",
  },
  onlineDot: {
    position: "absolute",
    bottom: 2,
    right: 2,
    width: 15,
    height: 15,
    borderRadius: 7.5,
    backgroundColor: "#22C55E",
    borderWidth: 2.5,
    borderColor: "#FFFFFF",
  },
  userInfo: {
    marginLeft: 14,
    flex: 1,
  },
  name: {
    fontSize: 15,
    fontWeight: "800",
    color: "#FFFFFF",
    letterSpacing: 0.2,
  },
  empId: {
    color: "rgba(255, 255, 255, 0.8)",
    fontSize: 12,
    fontWeight: "600",
    marginTop: 2,
  },
  roleBadgeRow: {
    flexDirection: "row",
    alignItems: "center",
    marginTop: 6,
  },
  roleBadge: {
    flexDirection: "row",
    alignItems: "center",
    backgroundColor: "rgba(255, 255, 255, 0.18)",
    paddingHorizontal: 10,
    paddingVertical: 3,
    borderRadius: 12,
    borderWidth: 1,
    borderColor: "rgba(255, 255, 255, 0.25)",
  },
  roleText: {
    color: "#FFFFFF",
    fontWeight: "700",
    fontSize: 11,
  },
  scrollContent: {
    paddingTop: 16,
    paddingBottom: 16,
  },
  section: {
    marginBottom: 14,
    paddingHorizontal: 16,
  },
  sectionTitle: {
    marginBottom: 8,
    marginLeft: 4,
    fontSize: 10,
    fontWeight: "800",
    color: colors.textSecondary,
    letterSpacing: 1.2,
  },
  menuItem: {
    height: 50,
    borderRadius: 14,
    paddingHorizontal: 14,
    marginBottom: 6,
    backgroundColor: colors.card,
    flexDirection: "row",
    alignItems: "center",
    borderWidth: 1,
    borderColor: colors.border,
    elevation: 2,
    shadowColor: "#000",
    shadowOpacity: 0.04,
    shadowRadius: 4,
    shadowOffset: { width: 0, height: 2 },
  },
  activeMenuItem: {
    backgroundColor: colors.primary,
    borderColor: colors.primary,
    elevation: 6,
    shadowColor: "#0C3054",
    shadowOpacity: 0.35,
    shadowRadius: 10,
    shadowOffset: { width: 0, height: 4 },
  },
  iconBg: {
    width: 34,
    height: 34,
    borderRadius: 10,
    backgroundColor: colors.blueBg,
    justifyContent: "center",
    alignItems: "center",
  },
  activeIconBg: {
    backgroundColor: "rgba(255, 255, 255, 0.2)",
  },
  menuText: {
    flex: 1,
    marginLeft: 12,
    color: colors.text,
    fontWeight: "700",
    fontSize: 14,
  },
  activeMenuText: {
    color: "#FFFFFF",
  },
  pillBadge: {
    paddingHorizontal: 8,
    paddingVertical: 2,
    borderRadius: 8,
    marginRight: 6,
  },
  pillBadgeText: {
    fontSize: 10,
    fontWeight: "800",
    letterSpacing: 0.5,
  },
  chevronContainer: {
    width: 24,
    height: 24,
    justifyContent: "center",
    alignItems: "center",
  },
  activeChevronContainer: {
    backgroundColor: "transparent",
  },
  bottomContainer: {
    paddingHorizontal: 16,
    paddingTop: 12,
    backgroundColor: colors.card,
    borderTopWidth: 1,
    borderTopColor: colors.border,
  },
  logoutButton: {
    height: 48,
    borderRadius: 14,
    backgroundColor: colors.redBg,
    flexDirection: "row",
    justifyContent: "center",
    alignItems: "center",
    borderWidth: 1,
    borderColor: "#FEE2E2",
  },
  logoutText: {
    marginLeft: 8,
    color: colors.danger,
    fontWeight: "700",
    fontSize: 14,
  },
  version: {
    marginTop: 10,
    textAlign: "center",
    color: colors.textLight,
    fontSize: 11,
    fontWeight: "600",
  },
});