import React, { useState } from "react";
import { Text, TouchableOpacity, View } from "react-native";
import DateTimePicker from "@react-native-community/datetimepicker";
import { Ionicons } from "@expo/vector-icons";

import { localISODate, parseISODate } from "../../utils/dates";

// Optional expiry date for a document upload (passport, visa, licence...).
// `value` is "" or "YYYY-MM-DD" in the phone's own time zone (localISODate:
// toISOString() would give yesterday's date before 05:30 in India), which is
// what blueprints/documents.py's upload routes accept as expiry_date.
export default function ExpiryDateField({ value, onChange, colors }) {
  const [open, setOpen] = useState(false);
  const picked = value ? parseISODate(value) : null;
  return (
    <View style={{ marginBottom: 16 }}>
      <Text style={{ fontSize: 11, fontWeight: "700", color: colors.textSecondary, marginBottom: 6 }}>
        EXPIRY DATE (OPTIONAL)
      </Text>
      <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
        <TouchableOpacity
          accessibilityRole="button"
          accessibilityLabel={value ? `Expiry date ${value}. Change` : "Set an expiry date"}
          testID="documentExpiryButton"
          onPress={() => setOpen(true)}
          style={{
            flex: 1, flexDirection: "row", alignItems: "center", gap: 8,
            backgroundColor: colors.background, borderWidth: 1, borderColor: colors.border,
            borderRadius: 10, padding: 10,
          }}
        >
          <Ionicons name="calendar-outline" size={18} color={colors.textSecondary} />
          <Text style={{ fontSize: 13, color: value ? colors.text : colors.textSecondary }}>
            {value || "No expiry date"}
          </Text>
        </TouchableOpacity>
        {value ? (
          <TouchableOpacity
            accessibilityRole="button"
            accessibilityLabel="Clear expiry date"
            testID="documentExpiryClear"
            onPress={() => onChange("")}
            hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}
          >
            <Ionicons name="close-circle" size={22} color={colors.textSecondary} />
          </TouchableOpacity>
        ) : null}
      </View>
      {open && (
        <DateTimePicker
          testID="documentExpiryPicker"
          value={picked || new Date()}
          mode="date"
          onChange={(event, d) => {
            setOpen(false);
            if (event?.type === "set" && d) onChange(localISODate(d));
          }}
        />
      )}
    </View>
  );
}
