jest.mock('expo-notifications', () => ({
  __esModule: true,
  setNotificationHandler: jest.fn(),
  getPermissionsAsync: jest.fn(),
  requestPermissionsAsync: jest.fn(),
  setNotificationChannelAsync: jest.fn(),
  getExpoPushTokenAsync: jest.fn(),
  AndroidImportance: { DEFAULT: 3 },
}));

jest.mock('expo-constants', () => ({
  __esModule: true,
  default: { expoConfig: { extra: { eas: { projectId: 'test-project-id' } } } },
}));

jest.mock('../../api/client', () => ({
  registerEmployeePushToken: jest.fn().mockResolvedValue({}),
  registerAdminPushToken: jest.fn().mockResolvedValue({}),
}));

const Notifications = require('expo-notifications');
const Constants = require('expo-constants').default;
const { registerEmployeePushToken, registerAdminPushToken } = require('../../api/client');
const { Platform } = require('react-native');
const { registerForPushNotifications } = require('../pushNotifications');

describe('registerForPushNotifications', () => {
  const originalPlatformOS = Platform.OS;

  beforeEach(() => {
    Platform.OS = 'ios';
    Notifications.getPermissionsAsync.mockResolvedValue({ status: 'granted' });
    Notifications.getExpoPushTokenAsync.mockResolvedValue({ data: 'ExponentPushToken[abc]' });
  });

  afterEach(() => {
    jest.clearAllMocks();
    Platform.OS = originalPlatformOS;
    Constants.expoConfig = { extra: { eas: { projectId: 'test-project-id' } } };
  });

  it('does nothing for a null user', async () => {
    await registerForPushNotifications(null);
    expect(Notifications.getPermissionsAsync).not.toHaveBeenCalled();
  });

  it('does nothing for an unrecognized role', async () => {
    await registerForPushNotifications({ role: 'superadmin' });
    expect(Notifications.getPermissionsAsync).not.toHaveBeenCalled();
  });

  it('no-ops when no EAS project is configured (no projectId)', async () => {
    Constants.expoConfig = { extra: {} };
    await registerForPushNotifications({ role: 'employee', employeeId: 'E1' });
    expect(Notifications.getExpoPushTokenAsync).not.toHaveBeenCalled();
    expect(registerEmployeePushToken).not.toHaveBeenCalled();
  });

  it('no-ops when expoConfig itself is missing entirely', async () => {
    Constants.expoConfig = null;
    await registerForPushNotifications({ role: 'employee', employeeId: 'E1' });
    expect(Notifications.getExpoPushTokenAsync).not.toHaveBeenCalled();
  });

  it('requests permission when not already granted', async () => {
    Notifications.getPermissionsAsync.mockResolvedValue({ status: 'undetermined' });
    Notifications.requestPermissionsAsync.mockResolvedValue({ status: 'granted' });

    await registerForPushNotifications({ role: 'employee', employeeId: 'E1' });

    expect(Notifications.requestPermissionsAsync).toHaveBeenCalled();
    expect(registerEmployeePushToken).toHaveBeenCalledWith('ExponentPushToken[abc]', 'ios');
  });

  it('stops without registering a token if permission is denied', async () => {
    Notifications.getPermissionsAsync.mockResolvedValue({ status: 'undetermined' });
    Notifications.requestPermissionsAsync.mockResolvedValue({ status: 'denied' });

    await registerForPushNotifications({ role: 'employee', employeeId: 'E1' });

    expect(Notifications.getExpoPushTokenAsync).not.toHaveBeenCalled();
    expect(registerEmployeePushToken).not.toHaveBeenCalled();
  });

  it('sets up the android notification channel only on android', async () => {
    Platform.OS = 'android';
    await registerForPushNotifications({ role: 'employee', employeeId: 'E1' });
    expect(Notifications.setNotificationChannelAsync).toHaveBeenCalledWith(
      'default',
      expect.objectContaining({ name: 'default' })
    );
  });

  it('does not set up an android channel on ios', async () => {
    await registerForPushNotifications({ role: 'employee', employeeId: 'E1' });
    expect(Notifications.setNotificationChannelAsync).not.toHaveBeenCalled();
  });

  it('registers an employee token via registerEmployeePushToken for role=employee', async () => {
    await registerForPushNotifications({ role: 'employee', employeeId: 'E1' });
    expect(registerEmployeePushToken).toHaveBeenCalledWith('ExponentPushToken[abc]', 'ios');
    expect(registerAdminPushToken).not.toHaveBeenCalled();
  });

  it('registers an admin token via registerAdminPushToken for role=admin', async () => {
    await registerForPushNotifications({ role: 'admin', adminRole: 'hr' });
    expect(registerAdminPushToken).toHaveBeenCalledWith('ExponentPushToken[abc]', 'ios');
    expect(registerEmployeePushToken).not.toHaveBeenCalled();
  });

  it('does not register when getExpoPushTokenAsync returns no token', async () => {
    Notifications.getExpoPushTokenAsync.mockResolvedValue({ data: null });
    await registerForPushNotifications({ role: 'employee', employeeId: 'E1' });
    expect(registerEmployeePushToken).not.toHaveBeenCalled();
  });

  it('swallows any error instead of throwing (must never crash or block login)', async () => {
    Notifications.getExpoPushTokenAsync.mockRejectedValue(new Error('no EAS project'));
    await expect(registerForPushNotifications({ role: 'employee', employeeId: 'E1' })).resolves.toBeUndefined();
  });
});
