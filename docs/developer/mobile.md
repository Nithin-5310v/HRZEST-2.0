# Mobile app

`mobile/`: one Expo / React Native app for admins, HR, managers and employees.
Expo SDK 58, React Native 0.88, React 19. Package `com.magzest.attendance`, EAS
project owner `knithin47`. It talks only to the server's JSON API.

## Layout

| Part | Where |
| --- | --- |
| Start-up, signed-out screens | `App.js`, `src/screens/LaunchCountdownScreen.js`, `LoginScreen.js`, `CompanySignupScreen.js` |
| Navigation per role | `src/navigation/` (admin, employee and manager drawers and tabs) |
| Screens | `src/screens/admin/`, `employee/`, `manager/`, plus the attendance scanner and ID card |
| Reusable components | `src/components/` |
| Every server call | `src/api/client.js` |
| Session and company | `src/store/AuthContext.js` |
| Look | `src/theme/` (the website's sign-in look), `src/constants/` |
| Device features | `src/utils/`: secure storage, offline punch queue, notifications, camera, OTA updates |
| Tests | `__tests__/` folders next to the code |

## Configuration

- **Server address:** `EXPO_PUBLIC_API_BASE_URL`, built into the app at build
  time (`src/config.js`). Sources: `mobile/.env` for local work,
  `mobile/.env.production` (`https://hrzest.com`), and the `env` of each
  `eas.json` profile. Without it, development falls back to the Expo dev host on
  port 5000.
- **Release builds refuse a non-HTTPS server:** the API client throws before
  sending anything if the address isn't `https://` outside `__DEV__`.
- **Crash reporting:** `EXPO_PUBLIC_SENTRY_DSN`; off when unset.

## How the app talks to the server (`src/api/client.js`)

- axios with a 10-second timeout.
- **Token:** read from secure storage and sent as `Authorization: Bearer <token>`.
- **Company:** after sign-in the server answers which company (`company_slug`),
  and every later call is prefixed `/<company>/...` (`withCompany()`). A few
  calls must not be prefixed, because they work across companies:
  `COMPANYLESS_PATHS` = `/api/create_org`, `/api/company/resolve`,
  `/api/forgot_password`.
- **Errors:** read the server's `msg`. Only a 401 from the dashboard calls
  (`/api/dashboard`, `/api/employee/portal`) signs the user out; other 401s are
  shown as errors. A 402 with `billing_locked` shows a "Plan expired" notice.
- Add new endpoints here, not with `fetch()` in a screen.

## Sign-in and session (`src/store/AuthContext.js`)

- Sign-in goes through `POST /api/signin` (email + password; employee ID still
  accepted). "Forgot password?" calls `POST /api/forgot_password` with the email
  in the box.
- **Secure storage** (SecureStore; AsyncStorage on web): `token`, `user`,
  `user_role`, `user_id`, and `hr_return` (the HR session kept while an HR user
  views the app as an employee).
- **AsyncStorage:** preferences, the last company, and an owner marker. When a
  different account signs in on the same phone, cached data from the previous
  account is wiped.
- **App lock:** if the phone has biometrics enrolled and the user hasn't turned
  the lock off, a restored session is locked at start-up and again after 5
  minutes in the background. Face/fingerprint unlocks it, with the phone PIN as
  fallback. A fresh sign-in is never locked.
- **Sign-out** deletes the token on the server and clears secure storage.

## Check-in and the offline queue

- Check-in requires a signed-in employee; there's no check-in before sign-in.
  The scanner (`src/screens/AttendanceScannerModal.js`) does QR, face and
  location, and posts to `/api/employee/qr-face-checkin`.
- **Offline punches** (`src/utils/offlineQueue.js`): with no connection, a punch
  is queued in AsyncStorage (`offline_punch_queue`) with its time, location and
  owner, kept up to 7 days, and only sent while the same user is signed in.
  `useOfflineSync` sends the queue on start, on return to the foreground, and
  every 60 seconds, to `/api/employee/sync_punches`.

## Running it

```bash
cd mobile
npm ci
npx expo start --lan        # open in Expo Go on a phone on the same Wi-Fi
npm test                    # Jest
```

- Expo Go must match the SDK version (58).
- `metro.config.js` and `shims/` work around an expo-notifications crash in Expo
  Go (expo/expo#52175). Remove both once expo-notifications ships the fix.

## Releasing

There are two ways to get a change onto phones:

| Change | How it reaches phones |
| --- | --- |
| JavaScript only (screens, logic, text, styles) | **Over-the-air update.** Pushes to `main` that touch `mobile/` publish one to the `preview` channel (`.github/workflows/mobile-update.yml`); by hand: `npm run update:preview`. Apps check when they start or come back to the foreground (at most every 30 minutes) and offer "Restart now". |
| Native (new or upgraded packages, permissions, `app.json` plugins, Expo SDK) | **A new build** with EAS. The app's runtime version is a fingerprint of the native code, so phones on an older build never receive updates meant for the new one. |

Only the `preview` channel gets automatic updates. Builds from the `production`
profile get no updates from CI.

### Building

```bash
cd mobile
eas build -p android --profile preview      # APK for direct installs (internal distribution)
eas build -p android --profile production   # Play Store bundle (AAB), auto-incremented version
```

Profiles are in `eas.json`. Both set `EXPO_PUBLIC_API_BASE_URL=https://hrzest.com`.
The build runs on Expo's servers (about 10-20 minutes); the build page gives the
download link. Use `eas build --no-wait` to start one without waiting.

Before a build, run `npx expo-doctor` (all checks should pass; fix version
mismatches with `npx expo install --fix`) and the Jest suite.

Build from a clean checkout of the branch you're releasing, not from a working
folder with unfinished changes: EAS uploads the working tree as it is.

### Build size

The APK is about **35 MB**. It used to be 129 MB, because it carried native
libraries for four CPU types, uncompressed. `app.json` → `expo-build-properties`
keeps it small:

- `buildArchs: ["arm64-v8a", "armeabi-v7a"]`: phone CPUs only, no emulator (x86)
  libraries.
- `useLegacyPackaging: true`: native libraries are compressed in the APK.
- `enableMinifyInReleaseBuilds` / `enableShrinkResourcesInReleaseBuilds`: R8
  code and resource shrinking. If a release build crashes where development
  doesn't, suspect R8 stripping something a library needs. Turning these two
  off keeps most of the saving.

The Play Store bundle (AAB) is smaller still for users, because the store sends
each phone only its own CPU's libraries.

## Permissions

Android: fine and coarse location, camera, internet. Microphone and external
storage are blocked. Every permission request has a reason string in `app.json`
(camera for QR and face photos, location when marking attendance, Face ID to
unlock the app).

## Tests

Jest with `jest-expo` and React Native Testing Library 14 (async: `await
render(...)`, `await fireEvent.press(...)`). On a busy PC, run with
`--runInBand` before treating a timeout as a failure. More in
[testing.md](testing.md#mobile-tests).
