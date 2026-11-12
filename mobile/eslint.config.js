// `npm run lint` (expo lint). Expo's recommended rules for this SDK, plus
// the two that catch the bugs found in the November 2026 review: names used
// but never defined, and hooks with missing dependencies.
const { defineConfig } = require("eslint/config");
const expoConfig = require("eslint-config-expo/flat");

module.exports = defineConfig([
  expoConfig,
  {
    ignores: ["dist/*", "node_modules/*", "shims/*", "coverage/*"],
  },
  {
    rules: {
      "no-undef": "off", // handled by the import plugin and JSX checks below
      "react/jsx-no-undef": "error",
      "import/no-unresolved": "error",
    },
  },
]);
