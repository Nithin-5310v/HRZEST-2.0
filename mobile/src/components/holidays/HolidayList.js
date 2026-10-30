import React from "react";
import {
  View,
  Text,
  FlatList,
  StyleSheet,
} from "react-native";

import HolidayCard from "./HolidayCard";

export default function HolidayList({
  holidays = [],
}) {
  return (
    <View style={styles.container}>
      <Text style={styles.title}>
        Holiday List
      </Text>

      <FlatList
        data={holidays}
        keyExtractor={(item) =>
          item.id.toString()
        }
        scrollEnabled={false}
        showsVerticalScrollIndicator={false}
        renderItem={({ item }) => (
          <HolidayCard
            title={item.title}
            date={item.date}
            day={item.day}
            type={item.type}
            description={item.description}
          />
        )}
        ItemSeparatorComponent={() => (
          <View style={styles.separator} />
        )}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    marginTop: 24,
  },

  title: {
    fontSize: 20,
    fontWeight: "800",
    color: "#131C2A",
    marginBottom: 16,
    letterSpacing: -0.3,
  },

  separator: {
    height: 2,
  },
});