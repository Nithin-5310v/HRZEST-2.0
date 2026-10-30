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
              color="#0B6E8E"
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
          color="#0B6E8E"
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
            style={[styles.editButton, { flex: 1, backgroundColor: "#132026" }]}
            onPress={onViewIdCard}
          >
            <Ionicons name="qr-code-outline" size={16} color="#3FB8C4" />
            <Text style={[styles.editText, { color: "#3FB8C4" }]}>Digital ID Card</Text>
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
  borderColor: "#E3ECEF",

  shadowColor: "#132026",
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
  backgroundColor: "#E3F2F5",

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

  backgroundColor: "#0B6E8E",

  justifyContent: "center",
  alignItems: "center",

  borderWidth: 2,
  borderColor: "#FFFFFF",
},

  name: {
  marginTop: 12,
  fontSize: 21,
  fontWeight: "700",
  color: "#132026",
},

  designation: {
  marginTop: 3,
  fontSize: 13,
  fontWeight: "500",
  color: "#5A6A70",
},

  badge: {
  marginTop: 10,

  flexDirection: "row",
  alignItems: "center",

  backgroundColor: "#F5FAFB",

  paddingHorizontal: 10,
  paddingVertical: 5,

  borderRadius: 20,
},

  badgeText: {
    marginLeft: 6,

    color: "#0B6E8E",

    fontWeight: "700",

    fontSize: 13,
  },

  employeeId: {
  marginTop: 10,
  fontSize: 12,
  fontWeight: "500",
  color: "#8A9AA0",
},

  editButton: {
  marginTop: 16,

  width: 170,
  height: 42,

  borderRadius: 12,

  backgroundColor: "#0B6E8E",

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