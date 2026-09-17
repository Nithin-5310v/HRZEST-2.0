#!/usr/bin/env node
// -*- coding: utf-8 -*-
// Patches the native android/app/build.gradle that `expo prebuild` just
// regenerated from scratch, so the release build type signs with the real
// upload keystore instead of Expo's default debug key.
//
// Why this exists instead of committing a customized android/ directory:
// this project stays a "managed" Expo workflow (android/ is gitignored,
// regenerated fresh by `expo prebuild` on every CI build) -- see
// buildspec-android.yml. `expo prebuild` has no built-in option to
// configure release signing from environment variables, so this script
// fills that one gap. Idempotent: running it twice is a no-op the second
// time (checks for its own marker comment first).
//
// Reads these env vars (set by buildspec-android.yml from the
// mobile-android-signing Secrets Manager secret -- see MOBILE_AWS_BUILD.md):
//   MYAPP_UPLOAD_STORE_FILE      -- absolute path to the .keystore file
//   MYAPP_UPLOAD_STORE_PASSWORD
//   MYAPP_UPLOAD_KEY_ALIAS
//   MYAPP_UPLOAD_KEY_PASSWORD
const fs = require('fs');
const path = require('path');

const GRADLE_PATH = path.join(__dirname, '..', 'android', 'app', 'build.gradle');
const MARKER = '// BEGIN hrzest release signing (patch-android-release-signing.js)';

const required = [
  'MYAPP_UPLOAD_STORE_FILE',
  'MYAPP_UPLOAD_STORE_PASSWORD',
  'MYAPP_UPLOAD_KEY_ALIAS',
  'MYAPP_UPLOAD_KEY_PASSWORD',
];
const missing = required.filter((k) => !process.env[k]);
if (missing.length) {
  console.error(`patch-android-release-signing: missing env var(s): ${missing.join(', ')}`);
  process.exit(1);
}

let gradle = fs.readFileSync(GRADLE_PATH, 'utf8');

if (gradle.includes(MARKER)) {
  console.log('patch-android-release-signing: already patched, skipping.');
  process.exit(0);
}

// Insert a real signingConfigs.release block right after `signingConfigs {`
// (expo prebuild's template always has this block with just `debug` in it).
const signingConfigsRe = /(signingConfigs\s*\{)/;
if (!signingConfigsRe.test(gradle)) {
  console.error('patch-android-release-signing: could not find `signingConfigs {` in build.gradle -- expo prebuild template may have changed.');
  process.exit(1);
}
const releaseSigningBlock = `$1
        ${MARKER}
        release {
            storeFile file(System.getenv("MYAPP_UPLOAD_STORE_FILE"))
            storePassword System.getenv("MYAPP_UPLOAD_STORE_PASSWORD")
            keyAlias System.getenv("MYAPP_UPLOAD_KEY_ALIAS")
            keyPassword System.getenv("MYAPP_UPLOAD_KEY_PASSWORD")
        }
        // END hrzest release signing`;
gradle = gradle.replace(signingConfigsRe, releaseSigningBlock);

// Rewire buildTypes.release.signingConfig from signingConfigs.debug to
// signingConfigs.release. Scoped to the `release {` block inside
// `buildTypes {}` specifically, not any other `release {` occurrence.
const buildTypesReleaseRe = /(buildTypes\s*\{[\s\S]*?release\s*\{[\s\S]*?signingConfig\s+signingConfigs\.)debug/;
if (!buildTypesReleaseRe.test(gradle)) {
  console.error('patch-android-release-signing: could not find buildTypes.release.signingConfig referencing signingConfigs.debug -- expo prebuild template may have changed.');
  process.exit(1);
}
gradle = gradle.replace(buildTypesReleaseRe, '$1release');

fs.writeFileSync(GRADLE_PATH, gradle);
console.log('patch-android-release-signing: patched android/app/build.gradle for release signing.');
