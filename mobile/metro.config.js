const path = require('path');
const { getDefaultConfig } = require('expo/metro-config');

const config = getDefaultConfig(__dirname);

// expo-notifications' Android topic-subscription module crashes the app at
// startup in Expo Go -- see shims/expoTopicSubscriptionModule.js. (iOS/web
// already get a no-op stub from expo-notifications itself.)
const TOPIC_SHIM = path.resolve(__dirname, 'shims/expoTopicSubscriptionModule.js');
const NOTIFICATIONS_BUILD = `${path.sep}expo-notifications${path.sep}build${path.sep}`;
const upstreamResolveRequest = config.resolver.resolveRequest;

config.resolver.resolveRequest = (context, moduleName, platform) => {
  if (platform === 'android' && moduleName === './TopicSubscriptionModule' &&
      context.originModulePath.includes(NOTIFICATIONS_BUILD)) {
    return { type: 'sourceFile', filePath: TOPIC_SHIM };
  }
  return (upstreamResolveRequest || context.resolveRequest)(context, moduleName, platform);
};

module.exports = config;
