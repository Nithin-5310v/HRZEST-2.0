import React from "react";
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
} from "react-native";

import { Ionicons } from "@expo/vector-icons";

export default function EmployeeRecentAttendance({
  records = [],
  navigation,
}) {
  return (
    <View style={styles.container}>
      <View style={styles.header}>
        <View>
          <Text style={styles.title}>
            Recent Attendance
          </Text>
          <Text style={styles.subtitle}>
            Last attendance activity
          </Text>
        </View>
        {navigation && (
          <TouchableOpacity
            style={{ flexDirection: "row", alignItems: "center" }}
            onPress={() => navigation.navigate("Attendance")}
          >
            <Text style={{ color: "#0B6E8E", fontWeight: "700", fontSize: 13, marginRight: 2 }}>
              View All
            </Text>
            <Ionicons name="chevron-forward" size={16} color="#0B6E8E" />
          </TouchableOpacity>
        )}
      </View>

      {records.length === 0 ? (
        <View style={styles.empty}>
          <Ionicons
            name="calendar-outline"
            size={40}
            color="#C4D2D6"
          />
          <Text style={styles.emptyTitle}>
            No Records Found
          </Text>
          <Text style={styles.emptySubtitle}>
            Attendance history will appear here.
          </Text>
        </View>
      ) : (
        records.map((item, index) => {
          const status =
            item.attendance_type ||
            (item.login_time ? "Present" : "Absent");

          const color =
            status === "Present"
              ? "#22C55E"
              : status === "Late"
              ? "#F59E0B"
              : "#EF4444";

          return (
            <TouchableOpacity
              key={index}
              activeOpacity={0.8}
              style={styles.item}
              onPress={() => navigation && navigation.navigate("Attendance")}
            >
              <View style={styles.left}>
                <View
                  style={[
                    styles.circle,
                    {
                      backgroundColor: color,
                    },
                  ]}
                >
                  <Ionicons
                    name="checkmark"
                    size={14}
                    color="#FFFFFF"
                  />
                </View>
                <View style={styles.info}>
                  <Text style={styles.date}>
                    {item.date}
                  </Text>

                  <Text style={[styles.status, { color }]}>
                    {status}
                  </Text>
                </View>

                <View style={styles.timeRow}>

                  <Ionicons
                    name="time-outline"
                    size={14}
                    color="#5A6A70"
                  />

                  <Text style={styles.time}>

                    {item.login_time
                      ? item.login_time.slice(0,5)
                      : "--:--"}

                    {"   "}

                    -

                    {"   "}

                    {item.logout_time
                      ? item.logout_time.slice(0,5)
                      : "--:--"}

                  </Text>

                </View>

              </View>
            </TouchableOpacity>
          );
        })
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
    marginBottom:20,
  },

  title:{
    fontSize:18,
    fontWeight:"700",
    color:"#132026",
  },

  subtitle:{
    marginTop:4,
    color:"#5A6A70",
    fontSize:13,
  },

  empty:{
    alignItems:"center",
    paddingVertical:35,
  },

  emptyTitle:{
    marginTop:12,
    fontWeight:"700",
    color:"#132026",
    fontSize:15,
  },

  emptySubtitle:{
    marginTop:6,
    color:"#8A9AA0",
    fontSize:12,
  },

  item:{
    flexDirection:"row",
    marginBottom:18,
  },

  left:{
    width:28,
    alignItems:"center",
  },

  circle:{
    width:24,
    height:24,
    borderRadius:12,
    justifyContent:"center",
    alignItems:"center",
  },

  line:{
    flex:1,
    width:2,
    backgroundColor:"#DCE6E9",
    marginTop:2,
  },

  content:{
    flex:1,
    marginLeft:14,
    paddingBottom:12,
    borderBottomWidth:1,
    borderBottomColor:"#ECF3F5",
  },

  topRow:{
    flexDirection:"row",
    justifyContent:"space-between",
    alignItems:"center",
  },

  date:{
    fontSize:15,
    fontWeight:"700",
    color:"#132026",
  },

  badge:{
    paddingHorizontal:10,
    paddingVertical:4,
    borderRadius:20,
  },

  badgeText:{
    color:"#FFFFFF",
    fontSize:11,
    fontWeight:"700",
  },

  timeRow:{
    flexDirection:"row",
    alignItems:"center",
    marginTop:10,
  },

  time:{
    marginLeft:6,
    fontSize:13,
    color:"#5A6A70",
    fontWeight:"600",
  },

});