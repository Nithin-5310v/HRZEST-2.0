import React from "react";
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
} from "react-native";

import { Ionicons } from "@expo/vector-icons";

export default function NotificationCard({
  notification,
  onPress,
}) {
  return (
    <TouchableOpacity
      activeOpacity={0.9}
      style={[
        styles.card,
        notification.unread &&
          styles.unreadCard,
      ]}
      onPress={() => onPress?.(notification)}
    >
      {/* Left Icon */}

      <View
        style={[
          styles.iconContainer,
          {
            backgroundColor:
              notification.color + "15",
          },
        ]}
      >
        <Ionicons
          name={notification.icon}
          size={24}
          color={notification.color}
        />
      </View>

      {/* Content */}

      <View style={styles.content}>
        <View style={styles.topRow}>
          <Text
            numberOfLines={1}
            style={styles.title}
          >
            {notification.title}
          </Text>

          {notification.unread && (
            <View style={styles.newBadge}>
              <Text
                style={styles.newText}
              >
                NEW
              </Text>
            </View>
          )}
        </View>

        <Text
          numberOfLines={2}
          style={styles.message}
        >
          {notification.message}
        </Text>

        <View style={styles.bottomRow}>
          <View
            style={styles.timeRow}
          >
            <Ionicons
              name="time-outline"
              size={14}
              color="#8A9AA0"
            />

            <Text style={styles.time}>
              {notification.time}
            </Text>
          </View>

          <Ionicons
            name="chevron-forward"
            size={18}
            color="#8A9AA0"
          />
        </View>
      </View>

      {notification.unread && (
        <View style={styles.dot} />
      )}
    </TouchableOpacity>
  );
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: "#FFFFFF",

    borderRadius: 22,

    padding: 18,

    marginBottom: 16,

    flexDirection: "row",

    borderWidth: 1,
    borderColor: "#E3ECEF",

    shadowColor: "#000",
    shadowOpacity: 0.05,
    shadowRadius: 10,
    shadowOffset: {
      width: 0,
      height: 5,
    },

    elevation: 2,
  },

  unreadCard: {
    borderLeftWidth: 5,
    borderLeftColor: "#0B6E8E",
  },

  iconContainer: {
    width: 58,
    height: 58,

    borderRadius: 18,

    justifyContent: "center",
    alignItems: "center",

    marginRight: 16,
  },

  content: {
    flex: 1,
  },

  topRow: {
    flexDirection: "row",
    alignItems: "center",
  },

  title: {
    flex: 1,

    fontSize: 17,

    fontWeight: "800",

    color: "#132026",
  },

  newBadge: {
    backgroundColor: "#E3F2F5",

    paddingHorizontal: 10,
    paddingVertical: 4,

    borderRadius: 20,

    marginLeft: 10,
  },

  newText: {
    color: "#0B6E8E",

    fontWeight: "800",

    fontSize: 11,
  },

  message: {
    marginTop: 8,

    fontSize: 14,

    lineHeight: 22,

    color: "#5A6A70",
  },

  bottomRow: {
    marginTop: 14,

    flexDirection: "row",

    justifyContent: "space-between",

    alignItems: "center",
  },

  timeRow: {
    flexDirection: "row",
    alignItems: "center",
  },

  time: {
    marginLeft: 6,

    fontSize: 13,

    color: "#8A9AA0",

    fontWeight: "600",
  },

  dot: {
    position: "absolute",

    top: 18,
    right: 18,

    width: 10,
    height: 10,

    borderRadius: 5,

    backgroundColor: "#0B6E8E",
  },
});