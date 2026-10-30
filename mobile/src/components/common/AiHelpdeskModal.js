import React, { useState } from "react";
import {
  Modal,
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
  TextInput,
  ScrollView,
  ActivityIndicator,
  KeyboardAvoidingView,
  Platform,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { fetchAiHelpdeskResponse } from "../../api/client";

export default function AiHelpdeskModal({ visible, onClose }) {
  const [messages, setMessages] = useState([
    {
      id: "1",
      sender: "bot",
      text: "Hello! I am your AI HRMS Assistant 🤖. How can I help you today with leave balances, company policies, or payroll?",
    },
  ]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);

  const handleSend = async () => {
    if (!input.trim()) return;
    const userMsg = { id: Date.now().toString(), sender: "user", text: input.trim() };
    setMessages((prev) => [...prev, userMsg]);
    const queryText = input.trim();
    setInput("");
    setLoading(true);

    try {
      const res = await fetchAiHelpdeskResponse(queryText);
      // Backend shape is { ok, data: { answer, escalated, ticket_id, ... } }
      // (blueprints/ai_hrms.py's api_hr_helpdesk) -- none of the top-level
      // res.data.* keys below ever existed, so this always fell through to
      // the canned line regardless of whether the call actually succeeded.
      let botText =
        res?.data?.data?.answer ||
        "I have registered your query. You can view your leave balances under the Leave tab or request payslips in the Payslips section.";
      if (res?.data?.data?.escalated && res?.data?.data?.ticket_id) {
        botText += `\n\nThis has been escalated to HR Ticket #${res.data.data.ticket_id}.`;
      }
      setMessages((prev) => [
        ...prev,
        { id: (Date.now() + 1).toString(), sender: "bot", text: botText },
      ]);
    } catch (e) {
      setMessages((prev) => [
        ...prev,
        {
          id: (Date.now() + 1).toString(),
          sender: "bot",
          text: "I'm here to assist you! For leave applications, go to the Leave section. For salary breakdown, check Payslips.",
        },
      ]);
    } finally {
      setLoading(false);
    }
  };

  return (
    <Modal visible={visible} transparent animationType="slide" onRequestClose={onClose}>
      <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : undefined} style={styles.overlay}>
        <View style={styles.modalCard}>
          {/* Modal Header */}
          <View style={styles.header}>
            <View style={{ flexDirection: "row", alignItems: "center" }}>
              <View style={styles.botIconCircle}>
                <Ionicons name="sparkles" size={20} color="#FFFFFF" />
              </View>
              <View style={{ marginLeft: 10 }}>
                <Text style={styles.headerTitle}>AI HR Helpdesk</Text>
                <Text style={styles.headerSub}>Always active • Virtual Assistant</Text>
              </View>
            </View>
            <TouchableOpacity onPress={onClose} style={styles.closeBtn}>
              <Ionicons name="close-circle" size={26} color="#5A6A70" />
            </TouchableOpacity>
          </View>

          {/* Chat Messages */}
          <ScrollView style={styles.chatArea} contentContainerStyle={{ paddingVertical: 12 }}>
            {messages.map((msg) => (
              <View
                key={msg.id}
                style={[
                  styles.msgBubble,
                  msg.sender === "user" ? styles.userBubble : styles.botBubble,
                ]}
              >
                <Text
                  style={[
                    styles.msgText,
                    msg.sender === "user" ? styles.userMsgText : styles.botMsgText,
                  ]}
                >
                  {msg.text}
                </Text>
              </View>
            ))}
            {loading && (
              <View style={[styles.msgBubble, styles.botBubble, { flexDirection: "row", alignItems: "center", gap: 8 }]}>
                <ActivityIndicator size="small" color="#0B6E8E" />
                <Text style={styles.botMsgText}>AI Assistant thinking...</Text>
              </View>
            )}
          </ScrollView>

          {/* Input Box */}
          <View style={styles.inputContainer}>
            <TextInput
              style={styles.textInput}
              placeholder="Ask about leave rules, payslips, or holidays..."
              placeholderTextColor="#8A9AA0"
              value={input}
              onChangeText={setInput}
              onSubmitEditing={handleSend}
            />
            <TouchableOpacity style={styles.sendBtn} onPress={handleSend}>
              <Ionicons name="send" size={18} color="#FFFFFF" />
            </TouchableOpacity>
          </View>
        </View>
      </KeyboardAvoidingView>
    </Modal>
  );
}

const styles = StyleSheet.create({
  overlay: { flex: 1, backgroundColor: "rgba(15, 23, 42, 0.75)", justifyContent: "flex-end" },
  modalCard: { height: "78%", backgroundColor: "#FFFFFF", borderTopLeftRadius: 26, borderTopRightRadius: 26, padding: 18 },
  header: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", borderBottomWidth: 1, borderBottomColor: "#ECF3F5", paddingBottom: 14 },
  botIconCircle: { width: 38, height: 38, borderRadius: 19, backgroundColor: "#0B6E8E", justifyContent: "center", alignItems: "center" },
  headerTitle: { fontSize: 16, fontWeight: "700", color: "#132026" },
  headerSub: { fontSize: 11, color: "#10B981", fontWeight: "600" },
  closeBtn: { padding: 2 },
  chatArea: { flex: 1 },
  msgBubble: { maxWidth: "82%", borderRadius: 18, padding: 12, marginBottom: 10 },
  userBubble: { alignSelf: "flex-end", backgroundColor: "#0B6E8E", borderBottomRightRadius: 4 },
  botBubble: { alignSelf: "flex-start", backgroundColor: "#ECF3F5", borderBottomLeftRadius: 4 },
  msgText: { fontSize: 14, lineHeight: 20 },
  userMsgText: { color: "#FFFFFF" },
  botMsgText: { color: "#132026" },
  inputContainer: { flexDirection: "row", alignItems: "center", borderTopWidth: 1, borderTopColor: "#ECF3F5", paddingTop: 10 },
  textInput: { flex: 1, backgroundColor: "#F5FAFB", borderWidth: 1, borderColor: "#DCE6E9", borderRadius: 20, paddingHorizontal: 16, paddingVertical: 10, fontSize: 14 },
  sendBtn: { width: 42, height: 42, borderRadius: 21, backgroundColor: "#0B6E8E", justifyContent: "center", alignItems: "center", marginLeft: 8 },
});
