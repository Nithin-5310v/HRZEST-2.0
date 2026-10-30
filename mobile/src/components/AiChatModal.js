import React, { useState } from "react";
import {
  View,
  Text,
  Modal,
  TouchableOpacity,
  StyleSheet,
  TextInput,
  ScrollView,
  ActivityIndicator,
  KeyboardAvoidingView,
  Platform,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { LinearGradient } from "expo-linear-gradient";
import { fetchAiHelpdeskResponse } from "../api/client";

export default function AiChatModal({ visible, onClose }) {
  const [messages, setMessages] = useState([
    {
      id: "welcome",
      sender: "bot",
      text: "Hello! I am your AI HR Assistant. Ask me anything about company policies, leave rules, attendance, payslips, or performance reviews!",
    },
  ]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);

  const quickPrompts = [
    "How to apply for leave?",
    "Explain my salary breakdown",
    "What are company holiday rules?",
    "How to check-in using GPS?",
  ];

  const handleSend = async (textToSend) => {
    const text = textToSend || input.trim();
    if (!text) return;

    const userMsg = { id: Date.now().toString(), sender: "user", text };
    setMessages((prev) => [...prev, userMsg]);
    if (!textToSend) setInput("");
    setLoading(true);

    try {
      const res = await fetchAiHelpdeskResponse(text);
      // Backend shape is { ok, data: { answer, escalated, ticket_id, ... } }
      // (blueprints/ai_hrms.py's api_hr_helpdesk) -- res.data.response/reply
      // never existed, so this always fell through to the canned line below
      // regardless of whether the call actually succeeded.
      let botResponse =
        res?.data?.data?.answer ||
        "I'm here to assist you with HR policies, leave balances, and company guidelines. Please feel free to rephrase your query!";
      if (res?.data?.data?.escalated && res?.data?.data?.ticket_id) {
        botResponse += `\n\nThis has been escalated to HR Ticket #${res.data.data.ticket_id}.`;
      }
      setMessages((prev) => [
        ...prev,
        { id: (Date.now() + 1).toString(), sender: "bot", text: botResponse },
      ]);
    } catch (err) {
      setMessages((prev) => [
        ...prev,
        {
          id: (Date.now() + 1).toString(),
          sender: "bot",
          text: "I am having trouble connecting to the AI Helpdesk server. Please ensure your backend service is reachable.",
        },
      ]);
    } finally {
      setLoading(false);
    }
  };

  return (
    <Modal visible={visible} animationType="slide" transparent>
      <View style={styles.overlay}>
        <KeyboardAvoidingView
          behavior={Platform.OS === "ios" ? "padding" : undefined}
          style={styles.modalContainer}
        >
          {/* Header */}
          <LinearGradient
            colors={["#132026", "#1E293B"]}
            style={styles.header}
          >
            <View style={styles.headerLeft}>
              <View style={styles.botAvatar}>
                <Ionicons name="sparkles" size={18} color="#3FB8C4" />
              </View>
              <View style={{ marginLeft: 10 }}>
                <Text style={styles.headerTitle}>AI HR Assistant</Text>
                <Text style={styles.headerSubtitle}>24/7 Smart HR Helpdesk</Text>
              </View>
            </View>
            <TouchableOpacity style={styles.closeBtn} onPress={onClose}>
              <Ionicons name="close" size={24} color="#8A9AA0" />
            </TouchableOpacity>
          </LinearGradient>

          {/* Quick Prompts */}
          <View style={styles.promptsContainer}>
            <ScrollView horizontal showsHorizontalScrollIndicator={false}>
              {quickPrompts.map((prompt, idx) => (
                <TouchableOpacity
                  key={idx}
                  style={styles.promptChip}
                  onPress={() => handleSend(prompt)}
                >
                  <Ionicons name="flash-outline" size={12} color="#0284C7" style={{ marginRight: 4 }} />
                  <Text style={styles.promptText}>{prompt}</Text>
                </TouchableOpacity>
              ))}
            </ScrollView>
          </View>

          {/* Messages */}
          <ScrollView
            style={styles.messagesList}
            contentContainerStyle={{ paddingVertical: 12, paddingHorizontal: 16 }}
          >
            {messages.map((item) => (
              <View
                key={item.id}
                style={[
                  styles.msgBubble,
                  item.sender === "user" ? styles.userBubble : styles.botBubble,
                ]}
              >
                <Text
                  style={[
                    styles.msgText,
                    item.sender === "user" ? styles.userMsgText : styles.botMsgText,
                  ]}
                >
                  {item.text}
                </Text>
              </View>
            ))}
            {loading && (
              <View style={[styles.msgBubble, styles.botBubble, { flexDirection: "row", alignItems: "center" }]}>
                <ActivityIndicator size="small" color="#0284C7" style={{ marginRight: 8 }} />
                <Text style={styles.botMsgText}>AI is thinking...</Text>
              </View>
            )}
          </ScrollView>

          {/* Input Footer */}
          <View style={styles.inputRow}>
            <TextInput
              style={styles.textInput}
              placeholder="Ask AI HR anything..."
              placeholderTextColor="#8A9AA0"
              value={input}
              onChangeText={setInput}
              onSubmitEditing={() => handleSend()}
            />
            <TouchableOpacity
              style={[styles.sendBtn, !input.trim() && { opacity: 0.5 }]}
              disabled={!input.trim() || loading}
              onPress={() => handleSend()}
            >
              <Ionicons name="send" size={18} color="#FFFFFF" />
            </TouchableOpacity>
          </View>
        </KeyboardAvoidingView>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  overlay: {
    flex: 1,
    backgroundColor: "rgba(15, 23, 42, 0.6)",
    justifyContent: "flex-end",
  },
  modalContainer: {
    height: "82%",
    backgroundColor: "#F5FAFB",
    borderTopLeftRadius: 24,
    borderTopRightRadius: 24,
    overflow: "hidden",
  },
  header: {
    paddingHorizontal: 20,
    paddingVertical: 16,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
  },
  headerLeft: {
    flexDirection: "row",
    alignItems: "center",
  },
  botAvatar: {
    width: 38,
    height: 38,
    borderRadius: 19,
    backgroundColor: "rgba(56, 189, 248, 0.2)",
    justifyContent: "center",
    alignItems: "center",
  },
  headerTitle: {
    fontSize: 14,
    fontWeight: "800",
    color: "#FFFFFF",
  },
  headerSubtitle: {
    fontSize: 11,
    color: "#8A9AA0",
    marginTop: 1,
  },
  closeBtn: {
    padding: 6,
  },
  promptsContainer: {
    backgroundColor: "#FFFFFF",
    paddingVertical: 10,
    paddingHorizontal: 12,
    borderBottomWidth: 1,
    borderBottomColor: "#DCE6E9",
  },
  promptChip: {
    flexDirection: "row",
    alignItems: "center",
    backgroundColor: "#F0F9FF",
    borderWidth: 1,
    borderColor: "#BAE6FD",
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderRadius: 16,
    marginRight: 8,
  },
  promptText: {
    fontSize: 11,
    color: "#0369A1",
    fontWeight: "600",
  },
  messagesList: {
    flex: 1,
  },
  msgBubble: {
    maxWidth: "82%",
    padding: 12,
    borderRadius: 16,
    marginBottom: 10,
  },
  userBubble: {
    alignSelf: "flex-end",
    backgroundColor: "#0284C7",
    borderBottomRightRadius: 2,
  },
  botBubble: {
    alignSelf: "flex-start",
    backgroundColor: "#FFFFFF",
    borderBottomLeftRadius: 2,
    borderWidth: 1,
    borderColor: "#DCE6E9",
  },
  msgText: {
    fontSize: 13,
    lineHeight: 18,
  },
  userMsgText: {
    color: "#FFFFFF",
    fontWeight: "500",
  },
  botMsgText: {
    color: "#1E293B",
    fontWeight: "400",
  },
  inputRow: {
    flexDirection: "row",
    alignItems: "center",
    padding: 12,
    backgroundColor: "#FFFFFF",
    borderTopWidth: 1,
    borderTopColor: "#DCE6E9",
  },
  textInput: {
    flex: 1,
    height: 44,
    backgroundColor: "#ECF3F5",
    borderRadius: 22,
    paddingHorizontal: 16,
    fontSize: 13,
    color: "#132026",
    marginRight: 10,
  },
  sendBtn: {
    width: 44,
    height: 44,
    borderRadius: 22,
    backgroundColor: "#0284C7",
    justifyContent: "center",
    alignItems: "center",
  },
});
