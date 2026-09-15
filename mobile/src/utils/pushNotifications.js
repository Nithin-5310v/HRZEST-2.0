import { Platform } from 'react-native';
import * as Notifications from 'expo-notifications';
import Constants from 'expo-constants';
import { registerEmployeePushToken, registerAdminPushToken } from '../api/client';

// Remote push now has a real backend (utils/push_utils.py, /api/push_token,
// /api/employee/push_token) -- unlike localNotifications.js's on-device-only
// reminder, this needs a token from Expo's push service, which in turn
// needs an EAS project (mobile/app.json's extra.eas.projectId). That
// project doesn't exist yet in this app -- getExpoPushTokenAsync() would
// throw without it, so this bails out cleanly rather than surfacing an
// error to the user; once `eas init` is run and the projectId is filled
// in, this starts working with no other code changes needed.
//
// Calling this repeatedly (setNotificationHandler included) is harmless --
// it's idempotent, so re-registering on every login is the simplest
// correct behavior rather than tracking "have we already done this."
Notifications.setNotificationHandler({
  handleNotification: async () => ({
    shouldShowAlert: true,
    shouldPlaySound: false,
    shouldSetBadge: false,
    shouldShowBanner: true,
    shouldShowList: true,
  }),
});

export async function registerForPushNotifications(user) {
  if (!user || (user.role !== 'employee' && user.role !== 'admin')) return;

  const projectId = Constants.expoConfig?.extra?.eas?.projectId;
  if (!projectId) return;

  try {
    const { status: existing } = await Notifications.getPermissionsAsync();
    let finalStatus = existing;
    if (existing !== 'granted') {
      const { status } = await Notifications.requestPermissionsAsync();
      finalStatus = status;
    }
    if (finalStatus !== 'granted') return;

    if (Platform.OS === 'android') {
      await Notifications.setNotificationChannelAsync('default', {
        name: 'default',
        importance: Notifications.AndroidImportance.DEFAULT,
      });
    }

    const { data: expoPushToken } = await Notifications.getExpoPushTokenAsync({ projectId });
    if (!expoPushToken) return;

    if (user.role === 'employee') {
      await registerEmployeePushToken(expoPushToken, Platform.OS);
    } else {
      await registerAdminPushToken(expoPushToken, Platform.OS);
    }
  } catch (_) {
    // Best-effort, deliberately silent -- must never block or crash login.
  }
}
