import React from "react";
import {
  View,
  Text,
  StyleSheet,
} from "react-native";

import { Ionicons } from "@expo/vector-icons";

const ANNOUNCEMENTS = [

  {
    title: "Monthly All Hands Meeting",
    message: "Friday • 10:00 AM • Conference Hall",
    icon: "megaphone-outline",
    color: "#2563EB",
    bg: "#EEF4FF",
  },

  {
    title: "Health Insurance Renewal",
    message: "Submit documents before 25 June.",
    icon: "medical-outline",
    color: "#16A34A",
    bg: "#ECFDF5",
  },

  {
    title: "Independence Day Holiday",
    message: "Office will remain closed on 15 August.",
    icon: "flag-outline",
    color: "#EA580C",
    bg: "#FFF7ED",
  },

];

export default function EmployeeAnnouncementCard({
  announcements = [],
}) {
  return (
    <View style={styles.container}>
      <View style={styles.header}>
        <Text style={styles.title}>
          Company Announcements
        </Text>
        <Ionicons
          name="notifications-outline"
          size={20}
          color="#2563EB"
        />
      </View>

      {announcements.length === 0 ? (
        <View style={{ backgroundColor: "#FFFFFF", borderRadius: 16, padding: 24, alignItems: "center", justifyContent: "center", borderWidth: 1, borderColor: "#E4E7EC" }}>
          <Ionicons name="notifications-off-outline" size={36} color="#667085" />
          <Text style={{ fontSize: 14, fontWeight: "600", color: "#475467", marginTop: 8 }}>
            No Active Company Announcements
          </Text>
        </View>
      ) : (
        announcements.map((item, index) => (

          <View
            key={index}
            style={styles.card}
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

            <View style={styles.content}>

              <Text style={styles.cardTitle}>
                {item.title}
              </Text>

              <Text style={styles.cardMessage}>
                {item.message}
              </Text>

            </View>

          </View>

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

    borderColor:"#E6E9EF",

    shadowColor:"#101828",

    shadowOpacity:.05,

    shadowRadius:14,

    shadowOffset:{
      width:0,
      height:6,
    },

    elevation:4,

  },

  header:{

    flexDirection:"row",

    justifyContent:"space-between",

    alignItems:"center",

    marginBottom:18,

  },

  title:{

    fontSize:18,

    fontWeight:"700",

    color:"#101828",

  },

  card:{

    flexDirection:"row",

    alignItems:"flex-start",

    paddingVertical:14,

    borderBottomWidth:1,

    borderBottomColor:"#EEF1F5",

  },

  iconBox:{

    width:48,

    height:48,

    borderRadius:14,

    justifyContent:"center",

    alignItems:"center",

    marginRight:14,

  },

  content:{

    flex:1,

  },

  cardTitle:{

    fontSize:15,

    fontWeight:"700",

    color:"#101828",

  },

  cardMessage:{

    marginTop:5,

    color:"#475467",

    lineHeight:20,

    fontSize:13,

  },

});