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

// Trimmed twin of screens/admin/AdminDrawerContent.js -- same visual
// pattern (hero header, sectioned menu, logout footer) but scoped to only
// the screens a manager account actually has server-side access to
// (leave/resignation/overtime approval -- see blueprints/leave.py's
// _LEAVE_APPROVER_ROLES). Deliberately omits Employees, Payroll, Seats &
// Billing, Analytics, Settings toggles, etc. -- those are admin/HR-only
// routes a manager token would just get 403s from.
export default function ManagerDrawerContent(props) {
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
    { title: "Dashboard", icon: "home-outline", iconFocused: "home", route: "Dashboard" },
    { title: "Leave Requests", icon: "document-text-outline", iconFocused: "document-text", route: "LeaveRequests" },
    { title: "Resignation Requests", icon: "exit-outline", iconFocused: "exit", route: "Resignations" },
    { title: "Overtime & Comp-Off", icon: "time-outline", iconFocused: "time", route: "CompOff" },
    { title: "Settings", icon: "settings-outline", iconFocused: "settings", route: "Settings" },
  ];

  const renderMenuItem = (item) => {
    const active = activeRoute === item.route;

    return (
      <TouchableOpacity
        key={item.title}
        activeOpacity={0.88}
        style={[styles.menuItem, active && styles.activeMenuItem]}
        onPress={() => {
          navigation.navigate("ManagerTabs", { screen: item.route });
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

        <View style={styles.chevronContainer}>
          <Ionicons
            name={active ? "checkmark-circle" : "chevron-forward"}
            size={16}
            color={active ? colors.success : colors.textLight}
          />
        </View>
      </TouchableOpacity>
    );
  };

  return (
    <View style={[styles.container, { paddingTop: insets.top }]}>
      <LinearGradient
        colors={["#0C3054", "#0B5CAD"]}
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
                  {(user?.company || user?.name || "M").charAt(0).toUpperCase()}
                </Text>
              </View>
            )}
            <View style={styles.onlineDot} />
          </View>

          <View style={styles.userInfo}>
            <Text style={styles.name} numberOfLines={1}>
              {user?.company || user?.name || "Organisation"}
            </Text>
            <Text style={styles.empId} numberOfLines={1}>
              {user?.name || "Manager"}
            </Text>
            <View style={styles.roleBadgeRow}>
              <View style={styles.roleBadge}>
                <Ionicons name="people" size={12} color={colors.warning} style={{ marginRight: 4 }} />
                <Text style={styles.roleText}>Manager / Team Lead</Text>
              </View>
            </View>
          </View>
        </View>
      </LinearGradient>

      <DrawerContentScrollView
        {...props}
        showsVerticalScrollIndicator={false}
        contentContainerStyle={styles.scrollContent}
      >
        <View style={styles.section}>{menuItems.map(renderMenuItem)}</View>
      </DrawerContentScrollView>

      <View style={[styles.bottomContainer, { paddingBottom: Math.max(insets.bottom, 16) }]}>
        <TouchableOpacity
          activeOpacity={0.88}
          style={styles.logoutButton}
          onPress={handleLogout}
        >
          <Ionicons name="log-out-outline" size={20} color={colors.danger} />
          <Text style={styles.logoutText}>Sign Out Account</Text>
        </TouchableOpacity>

        <Text style={styles.version}>Magzest HRMS Manager • v1.0.0</Text>
      </View>
    </View>
  );
}

const makeStyles = (colors) => StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.background },
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
  headerTopRow: { flexDirection: "row", alignItems: "center" },
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
  userInfo: { marginLeft: 14, flex: 1 },
  name: { fontSize: 15, fontWeight: "800", color: "#FFFFFF", letterSpacing: 0.2 },
  empId: { color: "rgba(255, 255, 255, 0.8)", fontSize: 12, fontWeight: "600", marginTop: 2 },
  roleBadgeRow: { flexDirection: "row", alignItems: "center", marginTop: 6 },
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
  roleText: { color: "#FFFFFF", fontWeight: "700", fontSize: 11 },
  scrollContent: { paddingTop: 16, paddingBottom: 16 },
  section: { marginBottom: 14, paddingHorizontal: 16 },
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
  activeIconBg: { backgroundColor: "rgba(255, 255, 255, 0.2)" },
  menuText: { flex: 1, marginLeft: 12, color: colors.text, fontWeight: "700", fontSize: 13 },
  activeMenuText: { color: "#FFFFFF" },
  chevronContainer: { width: 24, height: 24, justifyContent: "center", alignItems: "center" },
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
  logoutText: { marginLeft: 8, color: colors.danger, fontWeight: "700", fontSize: 13 },
  version: { marginTop: 10, textAlign: "center", color: colors.textLight, fontSize: 11, fontWeight: "600" },
});
