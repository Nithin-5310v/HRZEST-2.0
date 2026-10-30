import React from "react";
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
} from "react-native";

import { Ionicons } from "@expo/vector-icons";

const EVENTS = [

  {
    title: "Company Holiday",
    subtitle: "Independence Day",
    date: "15 Aug",
    icon: "flag-outline",
    color: "#0B6E8E",
    bg: "#E3F2F5",
  },

  {
    title: "Birthday",
    subtitle: "Sarah Johnson",
    date: "22 Jun",
    icon: "gift-outline",
    color: "#F59E0B",
    bg: "#FFF7ED",
  },

  {
    title: "Team Meeting",
    subtitle: "Engineering Sync",
    date: "Tomorrow",
    icon: "people-outline",
    color: "#16A34A",
    bg: "#ECFDF5",
  },

];

export default function EmployeeUpcomingEvents({
  events = [],
  navigation,
}) {
  return (
    <View style={styles.container}>
      <View style={styles.header}>
        <View>
          <Text style={styles.title}>
            Upcoming Events
          </Text>
          <Text style={styles.subtitle}>
            Holidays, events & announcements
          </Text>
        </View>
        {navigation ? (
          <TouchableOpacity
            style={{ flexDirection: "row", alignItems: "center" }}
            onPress={() => navigation.navigate("Holidays")}
          >
            <Text style={{ color: "#0B6E8E", fontWeight: "700", fontSize: 13, marginRight: 2 }}>
              View All
            </Text>
            <Ionicons name="chevron-forward" size={16} color="#0B6E8E" />
          </TouchableOpacity>
        ) : (
          <Ionicons name="calendar-outline" size={20} color="#0B6E8E" />
        )}
      </View>

      {events.length === 0 ? (
        <View style={{ backgroundColor: "#FFFFFF", borderRadius: 16, padding: 24, alignItems: "center", justifyContent: "center", borderWidth: 1, borderColor: "#DCE6E9" }}>
          <Ionicons name="calendar-outline" size={36} color="#8A9AA0" />
          <Text style={{ fontSize: 14, fontWeight: "600", color: "#5A6A70", marginTop: 8 }}>
            No Upcoming Events Scheduled
          </Text>
        </View>
      ) : (
        events.map((item, index) => (
          <TouchableOpacity
            key={index}
            activeOpacity={0.8}
            style={styles.card}
            onPress={() => navigation && navigation.navigate("Holidays")}
          >

            <View
              style={[
                styles.iconBox,
                {
                  backgroundColor:item.bg,
                },
              ]}
            >

              <Ionicons
                name={item.icon}
                size={22}
                color={item.color}
              />

            </View>

            <View style={styles.info}>

              <Text style={styles.eventTitle}>
                {item.title}
              </Text>

              <Text style={styles.eventSubtitle}>
                {item.subtitle}
              </Text>

            </View>

            <View style={styles.dateBox}>

              <Text style={styles.date}>
                {item.date}
              </Text>

            </View>

          </TouchableOpacity>

        ))
      )}

    </View>

  );

}

const styles = StyleSheet.create({

  container:{

    backgroundColor:"#FFFFFF",

    borderRadius:22,

    padding:20,

    marginBottom:22,

    borderWidth:1,

    borderColor:"#E3ECEF",

    shadowColor:"#132026",

    shadowOpacity:.05,

    shadowRadius:14,

    shadowOffset:{
      width:0,
      height:6,
    },

    elevation:4,

  },

  header:{

    marginBottom:18,

  },

  title:{

    fontSize:18,

    fontWeight:"700",

    color:"#132026",

  },

  subtitle:{

    marginTop:4,

    fontSize:13,

    color:"#5A6A70",

  },

  card:{

    flexDirection:"row",

    alignItems:"center",

    paddingVertical:14,

    borderBottomWidth:1,

    borderBottomColor:"#E9F1F3",

  },

  iconBox:{

    width:48,

    height:48,

    borderRadius:14,

    justifyContent:"center",

    alignItems:"center",

    marginRight:14,

  },

  info:{

    flex:1,

  },

  eventTitle:{

    fontSize:15,

    fontWeight:"700",

    color:"#132026",

  },

  eventSubtitle:{

    marginTop:4,

    color:"#5A6A70",

    fontSize:12,

  },

  dateBox:{

    backgroundColor:"#F5FAFB",

    paddingHorizontal:12,

    paddingVertical:8,

    borderRadius:14,

  },

  date:{

    color:"#0B6E8E",

    fontWeight:"700",

    fontSize:12,

  },

});