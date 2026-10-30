import React from "react";
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
  Image,
} from "react-native";
import { DrawerContentScrollView } from "@react-navigation/drawer";
import { getFocusedRouteNameFromRoute } from "@react-navigation/native";
import { Ionicons } from "@expo/vector-icons";
import { LinearGradient } from "expo-linear-gradient";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { useAuth } from "../../store/AuthContext";
import { useTheme } from "../../store/ThemeContext";

export default function AdminDrawerContent(props) {
  const { navigation, state } = props;
  const { user, signOut } = useAuth();
  const { colors } = useTheme();
  const styles = React.useMemo(() => makeStyles(colors), [colors]);
  const insets = useSafeAreaInsets();

  const drawerRoute = state.routes[state.index];
  const activeRoute =
    getFocusedRouteNameFromRoute(drawerRoute) ?? drawerRoute.name;

  const handleLogout = () => {
    signOut();
  };

  const menuItems = [
    // ANALYTICS & REPORTS
    {
      title: "Analytics & Reports",
      icon: "bar-chart-outline",
      iconFocused: "bar-chart",
      route: "Analytics",
      section: "ANALYTICS & REPORTS",
      badge: "STATS",
      badgeBg: "#E0F2FE",
      badgeColor: "#0369A1",
    },
    {
      title: "Notifications & Broadcast",
      icon: "notifications-outline",
      iconFocused: "notifications",
      route: "Notifications",
      section: "ANALYTICS & REPORTS",
    },

    // HR OPERATIONS & MANAGEMENT
    {
      title: "Mark Attendance",
      icon: "create-outline",
      iconFocused: "create",
      route: "MarkAttendance",
      section: "HR OPERATIONS",
    },
    {
      title: "OT & Comp-Off",
      icon: "time-outline",
      iconFocused: "time",
      route: "CompOff",
      section: "HR OPERATIONS",
    },
    {
      title: "Resignation Requests",
      icon: "exit-outline",
      iconFocused: "exit",
      route: "Resignations",
      section: "HR OPERATIONS",
    },
    {
      title: "Performance & KPIs",
      icon: "trending-up-outline",
      iconFocused: "trending-up",
      route: "Performance",
      section: "HR OPERATIONS",
    },
    {
      title: "Onboarding Tasks",
      icon: "briefcase-outline",
      iconFocused: "briefcase",
      route: "Onboarding",
      section: "HR OPERATIONS",
    },
    {
      title: "Departments & Units",
      icon: "business-outline",
      iconFocused: "business",
      route: "Departments",
      section: "HR OPERATIONS",
    },
    {
      title: "Leaves & Holidays",
      icon: "calendar-outline",
      iconFocused: "calendar",
      route: "LeavesHolidays",
      section: "HR OPERATIONS",
    },
    {
      title: "Organization Chart",
      icon: "git-network-outline",
      iconFocused: "git-network",
      route: "OrgChart",
      section: "HR OPERATIONS",
    },
    // @admin_required on web (blueprints/documents.py) -- both Admin and
    // HR can manage documents, unlike HR Accounts below.
    {
      title: "Employee Documents",
      icon: "document-text-outline",
      iconFocused: "document-text",
      route: "Documents",
      section: "HR OPERATIONS",
    },
    // PAYROLL & COMPENSATION
    {
      title: "Salary & Payslips",
      icon: "wallet-outline",
      iconFocused: "wallet",
      route: "Payroll",
      section: "PAYROLL & COMP",
    },
    {
      title: "Seats & Billing",
      icon: "pricetag-outline",
      iconFocused: "pricetag",
      route: "SeatsBilling",
      section: "PAYROLL & COMP",
    },

    // SUPPORT & HELPDESK
    {
      title: "Support Tickets",
      icon: "ticket-outline",
      iconFocused: "ticket",
      route: "Tickets",
      section: "SUPPORT & HELPDESK",
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
          navigation.navigate("AdminTabs", {
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
      {/* Admin Executive Hero Header */}
      <LinearGradient
        colors={["#0C3054", "#0B6E8E"]}
        start={{ x: 0, y: 0 }}
        end={{ x: 1, y: 1 }}
        style={styles.header}
      >
        <View style={styles.headerTopRow}>
          <View style={styles.avatarBorder}>
            {user?.logo ? (
              <Image
                source={{ uri: user.logo }}
                style={{ width: 48, height: 48, borderRadius: 24, backgroundColor: "#FFFFFF" }}
                resizeMode="cover"
              />
            ) : (
              <View style={[styles.avatar, { backgroundColor: "#FFFFFF" }]}>
                <Text style={{ fontSize: 22, fontWeight: "900", color: colors.primary }}>
                  {(user?.company || user?.name || "A").charAt(0).toUpperCase()}
                </Text>
              </View>
            )}
            <View style={styles.onlineDot} />
          </View>

          <View style={styles.userInfo}>
            <Text style={styles.name} numberOfLines={1}>
              {user?.company || user?.name || "Organisation Admin"}
            </Text>
            <Text style={styles.empId} numberOfLines={1}>
              {user?.name || "Administrator"}
            </Text>
            <View style={styles.roleBadgeRow}>
              <View style={styles.roleBadge}>
                <Ionicons name="shield-checkmark" size={12} color={colors.warning} style={{ marginRight: 4 }} />
                <Text style={styles.roleText}>Verified Organisation</Text>
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
        {renderSection("ANALYTICS & REPORTS", "ANALYTICS & REPORTS")}
        {renderSection("HR OPERATIONS", "HR OPERATIONS")}
        {renderSection("PAYROLL & COMP", "PAYROLL & COMP")}
        {renderSection("SUPPORT & HELPDESK", "SUPPORT & HELPDESK")}
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

        <Text style={styles.version}>Magzest HRMS Admin • v1.0.0</Text>
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
    backgroundColor: "rgba(255, 255, 255, 0.2)",
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
  onlineDot: {
    position: "absolute",
    bottom: 2,
    right: 2,
    width: 15,
    height: 15,
    borderRadius: 7.5,
    backgroundColor: colors.success,
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
    color: "#5A6A70",
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
    fontSize: 13,
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
    fontSize: 13,
  },
  version: {
    marginTop: 10,
    textAlign: "center",
    color: colors.textLight,
    fontSize: 11,
    fontWeight: "600",
  },
});
