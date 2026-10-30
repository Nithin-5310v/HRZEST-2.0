import React from "react";
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
  Image,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";

export default function ProfileImageCard({
  image = null,
  employeeName = "Staff Member",
  employeeId = "EMP-1001",
  designation = "Staff Member",
  department = "General",
  onChangePhoto = () => {},
  onEditProfile = () => {},
  onViewIdCard,
}) {
  return (
    <View style={styles.card}>
      {/* Avatar */}

      <View style={styles.avatarWrapper}>
        {image ? (
          <Image
            source={{ uri: image }}
            style={styles.avatar}
          />
        ) : (
          <View style={styles.avatar}>
            <Ionicons
              name="person"
              size={38}
              color="#0B5CAD"
            />
          </View>
        )}

        <TouchableOpacity
          activeOpacity={0.85}
          style={styles.cameraButton}
          onPress={onChangePhoto}
        >
          <Ionicons
            name="camera"
            size={14}
            color="#FFFFFF"
          />
        </TouchableOpacity>
      </View>

      {/* Details */}

      <Text style={styles.name}>
        {employeeName}
      </Text>

      <Text style={styles.designation}>
        {designation}
      </Text>

      <View style={styles.badge}>
        <Ionicons
          name="business-outline"
          size={13}
          color="#0B5CAD"
        />

        <Text style={styles.badgeText}>
          {department}
        </Text>
      </View>

      <Text style={styles.employeeId}>
        Employee ID • {employeeId}
      </Text>

      {/* Actions */}
      <View style={{ flexDirection: "row", gap: 10, marginTop: 16 }}>
        <TouchableOpacity
          activeOpacity={0.85}
          style={[styles.editButton, { flex: 1 }]}
          onPress={onEditProfile}
        >
          <Ionicons name="create-outline" size={16} color="#FFFFFF" />
          <Text style={styles.editText}>Edit Profile</Text>
        </TouchableOpacity>

        {onViewIdCard && (
          <TouchableOpacity
            activeOpacity={0.85}
            style={[styles.editButton, { flex: 1, backgroundColor: "#131C2A" }]}
            onPress={onViewIdCard}
          >
            <Ionicons name="qr-code-outline" size={16} color="#4C9BE8" />
            <Text style={[styles.editText, { color: "#4C9BE8" }]}>Digital ID Card</Text>
          </TouchableOpacity>
        )}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  // Card
card: {
  backgroundColor: "#FFFFFF",
  borderRadius: 20,
  paddingHorizontal: 18,
  paddingVertical: 18,
  marginBottom: 16,

  borderWidth: 1,
  borderColor: "#E3EAF3",

  shadowColor: "#131C2A",
  shadowOpacity: 0.04,
  shadowRadius: 8,
  shadowOffset: {
    width: 0,
    height: 3,
  },
  elevation: 2,
},

  avatarWrapper: {
    position: "relative",
  },

  // Avatar
avatar: {
  width: 78,
  height: 78,
  borderRadius: 39,
  backgroundColor: "#E6EFFA",

  justifyContent: "center",
  alignItems: "center",
},

  cameraButton: {
  position: "absolute",
  bottom: 0,
  right: 0,

  width: 28,
  height: 28,
  borderRadius: 14,

  backgroundColor: "#0B5CAD",

  justifyContent: "center",
  alignItems: "center",

  borderWidth: 2,
  borderColor: "#FFFFFF",
},

  name: {
  marginTop: 12,
  fontSize: 21,
  fontWeight: "700",
  color: "#131C2A",
},

  designation: {
  marginTop: 3,
  fontSize: 13,
  fontWeight: "500",
  color: "#5A6678",
},

  badge: {
  marginTop: 10,

  flexDirection: "row",
  alignItems: "center",

  backgroundColor: "#F5F8FC",

  paddingHorizontal: 10,
  paddingVertical: 5,

  borderRadius: 20,
},

  badgeText: {
    marginLeft: 6,

    color: "#0B5CAD",

    fontWeight: "700",

    fontSize: 13,
  },

  employeeId: {
  marginTop: 10,
  fontSize: 12,
  fontWeight: "500",
  color: "#8794A5",
},

  editButton: {
  marginTop: 16,

  width: 170,
  height: 42,

  borderRadius: 12,

  backgroundColor: "#0B5CAD",

  flexDirection: "row",
  justifyContent: "center",
  alignItems: "center",
},

  editText: {
  marginLeft: 6,
  fontSize: 14,
  fontWeight: "600",
  color: "#FFFFFF",
},
});