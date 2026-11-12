import * as Sentry from '@sentry/react-native';

// Inert until EXPO_PUBLIC_SENTRY_DSN is set (see .env.example / eas.json).
// We deliberately don't ship a placeholder DSN -- that would either silently
// point crash reports at nobody's project or, worse, at someone else's.
const DSN = process.env.EXPO_PUBLIC_SENTRY_DSN;

export const initCrashReporting = () => {
  if (!DSN) return;
  Sentry.init({
    dsn: DSN,
    tracesSampleRate: 0.2,
    enabled: !__DEV__,
  });
};

// For errors the app handles but shouldn't hide: sent to Sentry once a DSN
// is set, printed in development, otherwise dropped. Never throws.
export const reportError = (error, context = {}) => {
  try {
    if (__DEV__) {
      console.warn('[reportError]', context?.where || '', error?.message || error);
    }
    if (DSN) Sentry.captureException(error, { extra: context });
  } catch (_) {
    // reporting must never break the caller
  }
};

export { Sentry };
