import React, { useState, useCallback } from "react";
import {
  ScrollView,
  StyleSheet,
  Alert,
  RefreshControl,
  View,
} from "react-native";
import SafeAreaView from "../../components/SafeAreaView";
import { useFocusEffect } from "@react-navigation/native";
import ProfileHeader from "../../components/profile/ProfileHeader";
import TicketHeaderCard from "../../components/tickets/TicketHeaderCard";
import SectionTitle from "../../components/tickets/SectionTitle";
import TicketCategoryPicker from "../../components/tickets/TicketCategoryPicker";
import PrioritySelector from "../../components/tickets/PrioritySelector";
import SubjectInput from "../../components/tickets/SubjectInput";
import DescriptionInput from "../../components/tickets/DescriptionInput";
import RaiseTicketButton from "../../components/tickets/RaiseTicketButton";
import TicketStatsCard from "../../components/tickets/TicketStatsCard";
import TicketCard from "../../components/tickets/TicketCard";
import EmptyTickets from "../../components/tickets/EmptyTickets";
import { fetchEmployeeTickets, raiseTicket } from "../../api/client";
import { useTheme } from "../../store/ThemeContext";

export default function TicketsScreen() {
  const { colors } = useTheme();
  const styles = React.useMemo(() => makeStyles(colors), [colors]);
  const [category, setCategory] = useState("hr");
  const [priority, setPriority] = useState("Medium");
  const [subject, setSubject] = useState("");
  const [description, setDescription] = useState("");
  // No attachment field here anymore -- blueprints/tickets.py's
  // /api/employee/raise_ticket doesn't accept a file/attachment param at
  // all, so the old "upload" button just hardcoded a fake filename into
  // state and submitted a ticket with no real attachment ever sent.
  const [submitting, setSubmitting] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [ticketList, setTicketList] = useState([]);

  const loadTickets = async () => {
    try {
      const res = await fetchEmployeeTickets();
      if (res?.data?.tickets) {
        setTicketList(res.data.tickets);
      } else if (Array.isArray(res?.data)) {
        setTicketList(res.data);
      } else {
        setTicketList([]);
      }
    } catch (e) {
      setTicketList([]);
    } finally {
      setRefreshing(false);
    }
  };

  useFocusEffect(
    useCallback(() => {
      loadTickets();
    }, [])
  );

  const handleRaiseTicket = async () => {
    if (!subject.trim() || !description.trim()) {
      Alert.alert("Input Required", "Subject and Description are required to submit a ticket.");
      return;
    }
    setSubmitting(true);
    try {
      const res = await raiseTicket(category, subject.trim(), description.trim(), priority);
      if (res?.data?.ok) {
        Alert.alert("Ticket Submitted 🎉", "Your support request has been created.");
        setSubject("");
        setDescription("");
        // Reload from the server rather than fabricate a local entry --
        // the create endpoint doesn't return the new ticket's real id.
        await loadTickets();
      } else {
        Alert.alert("Submission Failed", res?.data?.msg || "Could not submit your ticket.");
      }
    } catch (e) {
      Alert.alert("Submission Failed", e?.response?.data?.msg || "Could not submit your ticket. Check your connection.");
    }
    setSubmitting(false);
  };

  const totalTickets = ticketList.length;
  const openTickets = ticketList.filter((t) => t.status === "Open" || t.status === "Pending").length;
  const inProgressTickets = ticketList.filter((t) => t.status === "In Progress" || t.status === "Assigned").length;
  const resolvedTickets = ticketList.filter((t) => t.status === "Resolved" || t.status === "Closed").length;

  return (
    <SafeAreaView style={styles.container}>
      <ProfileHeader title="Support Tickets" showBack={false} />

      <ScrollView
        showsVerticalScrollIndicator={false}
        contentContainerStyle={styles.content}
        refreshControl={
          <RefreshControl
            refreshing={refreshing}
            onRefresh={() => {
              setRefreshing(true);
              loadTickets();
            }}
            colors={[colors.primary]}
          />
        }
      >
        <TicketHeaderCard
          totalTickets={totalTickets}
          openTickets={openTickets}
          resolvedTickets={resolvedTickets}
        />

        <SectionTitle
          icon="create-outline"
          title="Raise New Ticket"
          subtitle="Submit your issue directly to HR & IT support."
        />

        <TicketCategoryPicker
          selectedCategory={category}
          onSelectCategory={setCategory}
        />

        <PrioritySelector
          selectedPriority={priority}
          onSelectPriority={setPriority}
        />

        <SubjectInput value={subject} onChangeText={setSubject} />

        <DescriptionInput value={description} onChangeText={setDescription} />

        <RaiseTicketButton loading={submitting} onPress={handleRaiseTicket} />

        <SectionTitle
          icon="stats-chart-outline"
          title="Ticket Overview"
          subtitle="Current support request breakdown"
        />

        <TicketStatsCard
          open={openTickets}
          inProgress={inProgressTickets}
          resolved={resolvedTickets}
        />

        <SectionTitle
          icon="document-text-outline"
          title="My Tickets"
          subtitle="Your submitted support history"
        />

        {ticketList.length === 0 ? (
          <EmptyTickets />
        ) : (
          ticketList.map((item, idx) => (
            <TicketCard
              key={item.id || idx.toString()}
              id={item.id || ""}
              category={item.category || ""}
              subject={item.subject}
              priority={item.priority || ""}
              status={item.status || ""}
              createdAt={item.created_at || item.createdAt || ""}
            />
          ))
        )}
      </ScrollView>
    </SafeAreaView>
  );
}

const makeStyles = (colors) => StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: colors.background,
  },
  content: {
    padding: 16,
    paddingBottom: 40,
  },
});