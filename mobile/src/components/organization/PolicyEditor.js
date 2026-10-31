import React, { useState } from "react";
import { View, Text, TextInput, TouchableOpacity, Switch, ScrollView, StyleSheet, ActivityIndicator } from "react-native";

import { useTheme } from "../../store/ThemeContext";

const OTHER = "other";

// Add or edit one company policy, like the website's Policies tab: a
// built-in category or the company's own, a title and the text. A new
// policy can start from one of the ready-made templates and be saved as a
// draft. `policy` is null when adding.
export default function PolicyEditor({ policy, categories = [], templates = [], saving, onSave, onCancel }) {
  const { colors } = useTheme();
  const styles = React.useMemo(() => makeStyles(colors), [colors]);

  const builtIn = (key) => categories.some((c) => c.key === key);
  const initialCategory = policy ? (builtIn(policy.category) ? policy.category : OTHER) : (categories[0]?.key || OTHER);
  const [category, setCategory] = useState(initialCategory);
  const [customCategory, setCustomCategory] = useState(policy && !builtIn(policy.category) ? policy.category : "");
  const [title, setTitle] = useState(policy?.title || "");
  const [body, setBody] = useState(policy?.body || "");
  const [publish, setPublish] = useState(true);
  const [error, setError] = useState("");

  const applyTemplate = (tpl) => {
    setCategory(builtIn(tpl.category) ? tpl.category : OTHER);
    setCustomCategory(builtIn(tpl.category) ? "" : tpl.category);
    setTitle(tpl.title);
    setBody(tpl.body);
    setError("");
  };

  const submit = () => {
    const finalCategory = category === OTHER ? customCategory.trim() : category;
    if (!finalCategory || !title.trim() || !body.trim()) {
      setError("Category, title and policy text are all required.");
      return;
    }
    setError("");
    onSave({
      category: finalCategory, title: title.trim(), body: body.trim(),
      ...(policy ? {} : { is_published: publish }),
    });
  };

  return (
    <View style={styles.card}>
      <Text style={styles.heading}>{policy ? "Edit policy" : "Add a policy"}</Text>

      {!policy && templates.length ? (
        <>
          <Text style={styles.label}>START FROM A TEMPLATE</Text>
          <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.chips}>
            {templates.map((tpl) => (
              <TouchableOpacity key={tpl.title} style={styles.chip} onPress={() => applyTemplate(tpl)}>
                <Text style={styles.chipText}>{tpl.title}</Text>
              </TouchableOpacity>
            ))}
          </ScrollView>
        </>
      ) : null}

      <Text style={styles.label}>CATEGORY</Text>
      <View style={styles.chipWrap}>
        {[...categories, { key: OTHER, label: "Other…" }].map((c) => {
          const active = category === c.key;
          return (
            <TouchableOpacity
              key={c.key}
              style={[styles.chip, active && styles.chipActive]}
              onPress={() => setCategory(c.key)}
              accessibilityState={{ selected: active }}
            >
              <Text style={[styles.chipText, active && styles.chipTextActive]}>{c.label}</Text>
            </TouchableOpacity>
          );
        })}
      </View>
      {category === OTHER ? (
        <TextInput
          style={styles.input}
          placeholder="e.g. Travel Policy, IT & Security"
          placeholderTextColor={colors.textLight}
          value={customCategory}
          onChangeText={setCustomCategory}
          maxLength={50}
        />
      ) : null}

      <Text style={styles.label}>TITLE</Text>
      <TextInput
        style={styles.input}
        placeholder="e.g. Leave Policy 2026"
        placeholderTextColor={colors.textLight}
        value={title}
        onChangeText={setTitle}
        maxLength={200}
      />

      <Text style={styles.label}>POLICY TEXT</Text>
      <TextInput
        style={[styles.input, styles.body]}
        placeholder="Write the policy here…"
        placeholderTextColor={colors.textLight}
        value={body}
        onChangeText={setBody}
        multiline
        textAlignVertical="top"
      />

      {!policy ? (
        <View style={styles.switchRow}>
          <View style={{ flex: 1 }}>
            <Text style={styles.switchTitle}>Publish now</Text>
            <Text style={styles.switchHint}>Off saves it as a draft that employees can't see yet.</Text>
          </View>
          <Switch value={publish} onValueChange={setPublish} accessibilityLabel="Publish now" />
        </View>
      ) : null}

      {error ? <Text style={styles.error}>{error}</Text> : null}

      <View style={styles.actions}>
        <TouchableOpacity style={[styles.btn, styles.btnGhost]} onPress={onCancel} disabled={saving}>
          <Text style={styles.btnGhostText}>Cancel</Text>
        </TouchableOpacity>
        <TouchableOpacity style={[styles.btn, styles.btnPrimary]} onPress={submit} disabled={saving}>
          {saving ? <ActivityIndicator color="#FFFFFF" /> : <Text style={styles.btnPrimaryText}>Save policy</Text>}
        </TouchableOpacity>
      </View>
    </View>
  );
}

const makeStyles = (colors) => StyleSheet.create({
  card: {
    backgroundColor: colors.surface, borderRadius: 16, padding: 16, marginBottom: 14,
    shadowColor: "#131C2A", shadowOpacity: 0.06, shadowRadius: 12, shadowOffset: { width: 0, height: 4 }, elevation: 2,
  },
  heading: { fontSize: 16, fontWeight: "800", color: colors.text, marginBottom: 14 },
  label: { fontSize: 10.5, fontWeight: "800", color: colors.textSecondary, letterSpacing: 0.6, marginBottom: 6 },
  chips: { paddingBottom: 14 },
  chipWrap: { flexDirection: "row", flexWrap: "wrap", marginBottom: 8 },
  chip: {
    paddingHorizontal: 12, paddingVertical: 8, borderRadius: 999, marginRight: 8, marginBottom: 8,
    backgroundColor: colors.blueBg, borderWidth: 1, borderColor: "transparent",
  },
  chipActive: { backgroundColor: colors.primary },
  chipText: { fontSize: 12, fontWeight: "700", color: colors.primary },
  chipTextActive: { color: "#FFFFFF" },
  input: {
    minHeight: 46, borderRadius: 12, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.background,
    paddingHorizontal: 14, paddingVertical: 10, fontSize: 14, color: colors.text, marginBottom: 14,
  },
  body: { minHeight: 160 },
  switchRow: { flexDirection: "row", alignItems: "center", marginBottom: 14 },
  switchTitle: { fontSize: 13.5, fontWeight: "700", color: colors.text },
  switchHint: { fontSize: 12, color: colors.textSecondary, marginTop: 2 },
  error: { fontSize: 12.5, fontWeight: "600", color: colors.danger, marginBottom: 12 },
  actions: { flexDirection: "row", justifyContent: "flex-end" },
  btn: { height: 44, borderRadius: 12, paddingHorizontal: 18, justifyContent: "center", alignItems: "center", marginLeft: 10 },
  btnGhost: { borderWidth: 1, borderColor: colors.border },
  btnGhostText: { fontSize: 13, fontWeight: "700", color: colors.textSecondary },
  btnPrimary: { backgroundColor: colors.primary, minWidth: 120 },
  btnPrimaryText: { fontSize: 13, fontWeight: "800", color: "#FFFFFF" },
});
