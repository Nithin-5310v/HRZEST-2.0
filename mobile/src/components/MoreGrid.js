import React from "react";
import { View, Text, ScrollView, TouchableOpacity, StyleSheet } from "react-native";
import { Ionicons } from "@expo/vector-icons";

import AppTopBar from "./AppTopBar";
import { useAuth } from "../store/AuthContext";

// The "More" tab (admin and employee): every screen that isn't a bottom
// tab, as a grouped grid of tiles -- the Tabs + More layout that replaces
// the long side menu. `groups` is [{ title, items: [{ label, icon, route }] }];
// each tile opens its route in the same tab navigator.
export default function MoreGrid({ navigation, title = "More", subtitle, groups, footer = [], onSignOut }) {
  const { user } = useAuth();

  return (
    <View style={styles.page}>
      <AppTopBar title={title} subtitle={subtitle || user?.company || "HRZEST"} />
      <ScrollView contentContainerStyle={styles.content} showsVerticalScrollIndicator={false}>
        {groups.map((group) => (
          <View key={group.title} style={styles.group}>
            <Text style={styles.groupTitle}>{group.title}</Text>
            <View style={styles.grid}>
              {group.items.map((item) => (
                <TouchableOpacity
                  key={item.route}
                  style={styles.tile}
                  activeOpacity={0.8}
                  accessibilityRole="button"
                  accessibilityLabel={item.label}
                  onPress={() => navigation.navigate(item.route)}
                >
                  <View style={styles.tileIcon}>
                    <Ionicons name={item.icon} size={20} color="#0B5CAD" />
                  </View>
                  <Text style={styles.tileLabel} numberOfLines={2}>{item.label}</Text>
                </TouchableOpacity>
              ))}
            </View>
          </View>
        ))}

        <View style={styles.list}>
          {footer.map((row) => (
            <TouchableOpacity key={row.route} style={styles.row} onPress={() => navigation.navigate(row.route)} accessibilityRole="button">
              <Text style={styles.rowLabel}>{row.label}</Text>
              {row.detail ? <Text style={styles.rowDetail}>{row.detail}</Text> : null}
              <Ionicons name="chevron-forward" size={18} color="#8794A5" />
            </TouchableOpacity>
          ))}
          <TouchableOpacity style={styles.row} onPress={onSignOut} accessibilityRole="button">
            <Text style={[styles.rowLabel, styles.signOut]}>Sign out</Text>
          </TouchableOpacity>
        </View>
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  page: { flex: 1, backgroundColor: "#F5F8FC" },
  content: { paddingHorizontal: 20, paddingTop: 16, paddingBottom: 120, gap: 18 },
  group: { gap: 10 },
  groupTitle: { fontSize: 12, fontWeight: "700", letterSpacing: 0.6, textTransform: "uppercase", color: "#455163" },
  grid: { flexDirection: "row", flexWrap: "wrap", gap: 10 },
  tile: {
    width: "22.8%",
    minHeight: 84,
    borderRadius: 18,
    backgroundColor: "#FFFFFF",
    borderWidth: 1,
    borderColor: "#E3E9F1",
    alignItems: "center",
    paddingVertical: 12,
    paddingHorizontal: 4,
    gap: 6,
  },
  tileIcon: { width: 40, height: 40, borderRadius: 12, backgroundColor: "#E7EEF7", alignItems: "center", justifyContent: "center" },
  tileLabel: { fontSize: 11.5, fontWeight: "600", color: "#131C2A", textAlign: "center", lineHeight: 15 },
  list: { backgroundColor: "#FFFFFF", borderWidth: 1, borderColor: "#E3E9F1", borderRadius: 20, overflow: "hidden" },
  row: { flexDirection: "row", alignItems: "center", minHeight: 52, paddingHorizontal: 16, gap: 8 },
  rowLabel: { flex: 1, fontSize: 15, fontWeight: "600", color: "#131C2A" },
  rowDetail: { fontSize: 13, color: "#455163" },
  signOut: { color: "#B42318" },
});
