// Stand-in for expo-notifications' build/TopicSubscriptionModule.android.js,
// swapped in by metro.config.js. The original loads the native module with
// requireNativeModule() as soon as anything imports expo-notifications, and
// Expo Go (SDK 57) doesn't ship ExpoTopicSubscriptionModule -- so the app
// crashed at startup there ("Cannot find native module
// 'ExpoTopicSubscriptionModule'", expo/expo#52175). This is the fix from
// expo/expo#52182: in a real build the module exists and this returns it
// unchanged; without it, subscribeToTopicAsync() rejects with
// UnavailabilityError instead of the whole app failing to start. The app
// doesn't use topic subscriptions. Delete this file and the metro.config.js
// hook once an expo-notifications release includes #52182.
import { requireOptionalNativeModule } from 'expo-modules-core';

export default requireOptionalNativeModule('ExpoTopicSubscriptionModule') ?? {};
