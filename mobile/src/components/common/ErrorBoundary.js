import React from "react";
import { View, Text, TouchableOpacity, StyleSheet } from "react-native";
import * as Updates from "expo-updates";

import { reportError } from "../../utils/crashReporting";

// A crash while drawing a screen used to close the app (or leave it blank)
// with nothing recorded. This shows a way back instead and reports the
// error (utils/crashReporting.js; sent once a Sentry key is configured).
export default class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    reportError(error, { componentStack: info?.componentStack });
  }

  retry = () => this.setState({ error: null });

  restart = () => {
    if (Updates.isEnabled) Updates.reloadAsync().catch(() => this.retry());
    else this.retry();
  };

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <View style={styles.wrap} testID="errorBoundary">
        <Text style={styles.title}>Something went wrong</Text>
        <Text style={styles.body}>
          This screen hit a problem. Your data is safe. Try again, or restart the app.
        </Text>
        <TouchableOpacity style={styles.primary} onPress={this.retry} accessibilityRole="button">
          <Text style={styles.primaryText}>Try again</Text>
        </TouchableOpacity>
        <TouchableOpacity style={styles.secondary} onPress={this.restart} accessibilityRole="button">
          <Text style={styles.secondaryText}>Restart app</Text>
        </TouchableOpacity>
      </View>
    );
  }
}

const styles = StyleSheet.create({
  wrap: { flex: 1, alignItems: "center", justifyContent: "center", padding: 28, backgroundColor: "#FFFFFF" },
  title: { fontSize: 20, fontWeight: "800", color: "#101828", marginBottom: 8 },
  body: { fontSize: 15, color: "#475467", textAlign: "center", lineHeight: 22, marginBottom: 24 },
  primary: { backgroundColor: "#2563EB", borderRadius: 12, paddingVertical: 12, paddingHorizontal: 28, marginBottom: 12 },
  primaryText: { color: "#FFFFFF", fontSize: 15, fontWeight: "700" },
  secondary: { paddingVertical: 10, paddingHorizontal: 20 },
  secondaryText: { color: "#2563EB", fontSize: 15, fontWeight: "600" },
});
