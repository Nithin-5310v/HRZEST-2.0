import React from "react";
import {
  Modal,
  View,
  Text,
  TouchableOpacity,
  StyleSheet,
  ScrollView,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";
import THEME from "../../constants/theme";

export default function SaasFilterSheet({
  visible = false,
  title = "Filter & Refine",
  statusOptions = [],
  selectedStatus = "All",
  onSelectStatus,
  deptOptions = [],
  selectedDept = "All",
  onSelectDept,
  sortOptions = [],
  selectedSort = "Default",
  onSelectSort,
  dateOptions = [],
  selectedDate = "All Time",
  onSelectDate,
  onApply,
  onReset,
  onClose,
}) {
  return (
    <Modal
      visible={visible}
      transparent
      animationType="slide"
      onRequestClose={onClose}
    >
      <View style={styles.overlay}>
        <View style={styles.sheet}>
          {/* Top Grab Handle */}
          <View style={styles.handle} />

          {/* Header */}
          <View style={styles.header}>
            <View style={{ flexDirection: "row", alignItems: "center" }}>
              <Ionicons name="options" size={22} color={THEME.colors.primary} />
              <Text style={styles.title}>{title}</Text>
            </View>
            <TouchableOpacity onPress={onClose} style={styles.closeBtn}>
              <Ionicons name="close" size={20} color={THEME.colors.textPrimary || "#132026"} />
            </TouchableOpacity>
          </View>

          <ScrollView showsVerticalScrollIndicator={false}>
            {/* Status Section */}
            {statusOptions.length > 0 && (
              <View style={styles.section}>
                <Text style={styles.sectionTitle}>Status</Text>
                <View style={styles.chipsContainer}>
                  {statusOptions.map((status) => {
                    const active = selectedStatus === status;
                    return (
                      <TouchableOpacity
                        key={status}
                        activeOpacity={0.8}
                        style={[styles.chip, active && styles.chipActive]}
                        onPress={() => onSelectStatus && onSelectStatus(status)}
                      >
                        <Text style={[styles.chipText, active && styles.chipTextActive]}>
                          {status}
                        </Text>
                      </TouchableOpacity>
                    );
                  })}
                </View>
              </View>
            )}

            {/* Department / Category Section */}
            {deptOptions.length > 0 && (
              <View style={styles.section}>
                <Text style={styles.sectionTitle}>Department / Category</Text>
                <View style={styles.chipsContainer}>
                  {deptOptions.map((dept) => {
                    const active = selectedDept === dept;
                    return (
                      <TouchableOpacity
                        key={dept}
                        activeOpacity={0.8}
                        style={[styles.chip, active && styles.chipActive]}
                        onPress={() => onSelectDept && onSelectDept(dept)}
                      >
                        <Text style={[styles.chipText, active && styles.chipTextActive]}>
                          {dept}
                        </Text>
                      </TouchableOpacity>
                    );
                  })}
                </View>
              </View>
            )}

            {/* Sort Options */}
            {sortOptions.length > 0 && (
              <View style={styles.section}>
                <Text style={styles.sectionTitle}>Sort By</Text>
                <View style={styles.chipsContainer}>
                  {sortOptions.map((sort) => {
                    const active = selectedSort === sort;
                    return (
                      <TouchableOpacity
                        key={sort}
                        activeOpacity={0.8}
                        style={[styles.chip, active && styles.chipActive]}
                        onPress={() => onSelectSort && onSelectSort(sort)}
                      >
                        <Text style={[styles.chipText, active && styles.chipTextActive]}>
                          {sort}
                        </Text>
                      </TouchableOpacity>
                    );
                  })}
                </View>
              </View>
            )}

            {/* Date Range Section */}
            {dateOptions.length > 0 && (
              <View style={styles.section}>
                <Text style={styles.sectionTitle}>Time Frame</Text>
                <View style={styles.chipsContainer}>
                  {dateOptions.map((dateOpt) => {
                    const active = selectedDate === dateOpt;
                    return (
                      <TouchableOpacity
                        key={dateOpt}
                        activeOpacity={0.8}
                        style={[styles.chip, active && styles.chipActive]}
                        onPress={() => onSelectDate && onSelectDate(dateOpt)}
                      >
                        <Text style={[styles.chipText, active && styles.chipTextActive]}>
                          {dateOpt}
                        </Text>
                      </TouchableOpacity>
                    );
                  })}
                </View>
              </View>
            )}
          </ScrollView>

          {/* Action Footer */}
          <View style={styles.footer}>
            <TouchableOpacity style={styles.resetBtn} onPress={onReset}>
              <Text style={styles.resetText}>Reset All</Text>
            </TouchableOpacity>

            <TouchableOpacity style={styles.applyBtn} onPress={onApply || onClose}>
              <Text style={styles.applyText}>Apply Filters</Text>
            </TouchableOpacity>
          </View>
        </View>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  overlay: {
    flex: 1,
    backgroundColor: "rgba(15, 23, 42, 0.45)",
    justifyContent: "flex-end",
  },
  sheet: {
    backgroundColor: "#FFFFFF",
    borderTopLeftRadius: 28,
    borderTopRightRadius: 28,
    paddingHorizontal: 22,
    paddingTop: 14,
    paddingBottom: 28,
    maxHeight: "82%",
  },
  handle: {
    width: 50,
    height: 5,
    borderRadius: 3,
    backgroundColor: "#C4D2D6",
    alignSelf: "center",
    marginBottom: 16,
  },
  header: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    marginBottom: 18,
    paddingBottom: 12,
    borderBottomWidth: 1,
    borderBottomColor: "#DCE6E9",
  },
  title: {
    fontSize: 18,
    fontWeight: "800",
    color: "#132026",
    marginLeft: 10,
  },
  closeBtn: {
    width: 34,
    height: 34,
    borderRadius: 17,
    backgroundColor: "#ECF3F5",
    justifyContent: "center",
    alignItems: "center",
  },
  section: {
    marginBottom: 18,
  },
  sectionTitle: {
    fontSize: 14,
    fontWeight: "700",
    color: "#44555B",
    marginBottom: 10,
  },
  chipsContainer: {
    flexDirection: "row",
    flexWrap: "wrap",
  },
  chip: {
    paddingHorizontal: 16,
    paddingVertical: 9,
    borderRadius: 24,
    backgroundColor: "#F5FAFB",
    borderWidth: 1,
    borderColor: "#DCE6E9",
    marginRight: 8,
    marginBottom: 8,
  },
  chipActive: {
    backgroundColor: THEME.colors.primary,
    borderColor: THEME.colors.primary,
  },
  chipText: {
    fontSize: 13,
    fontWeight: "600",
    color: "#2E3D43",
  },
  chipTextActive: {
    color: "#FFFFFF",
    fontWeight: "700",
  },
  footer: {
    flexDirection: "row",
    justifyContent: "space-between",
    marginTop: 14,
    paddingTop: 12,
    borderTopWidth: 1,
    borderTopColor: "#DCE6E9",
  },
  resetBtn: {
    width: "32%",
    height: 50,
    borderRadius: 14,
    backgroundColor: "#ECF3F5",
    justifyContent: "center",
    alignItems: "center",
  },
  resetText: {
    fontSize: 14,
    fontWeight: "700",
    color: "#2E3D43",
  },
  applyBtn: {
    width: "64%",
    height: 50,
    borderRadius: 14,
    backgroundColor: THEME.colors.primary,
    justifyContent: "center",
    alignItems: "center",
  },
  applyText: {
    fontSize: 15,
    fontWeight: "800",
    color: "#FFFFFF",
  },
});
