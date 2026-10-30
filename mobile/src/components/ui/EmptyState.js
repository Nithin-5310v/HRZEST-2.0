import React from 'react';
import {
  View,
  Text,
  StyleSheet,
} from 'react-native';

import { Ionicons } from '@expo/vector-icons';
import AppButton from './AppButton';

export default function EmptyState({

  icon = 'folder-open-outline',

  title = 'Nothing here yet',

  description = 'There is currently no data to display.',

  buttonText,

  onPress,

}) {

  return (

    <View style={styles.container}>

      <View style={styles.iconBox}>

        <Ionicons
          name={icon}
          size={54}
          color="#0B5CAD"
        />

      </View>

      <Text style={styles.title}>
        {title}
      </Text>

      <Text style={styles.description}>
        {description}
      </Text>

      {buttonText && (

        <View style={styles.buttonContainer}>

          <AppButton
            title={buttonText}
            icon="add-outline"
            onPress={onPress}
            fullWidth={false}
          />

        </View>

      )}

    </View>

  );

}

const styles = StyleSheet.create({

  container: {

    backgroundColor: '#FFFFFF',

    borderRadius: 24,

    paddingVertical: 48,

    paddingHorizontal: 24,

    justifyContent: 'center',

    alignItems: 'center',

    borderWidth: 1,

    borderColor: '#E7EDF6',

    shadowColor: '#000',

    shadowOpacity: 0.05,

    shadowRadius: 14,

    shadowOffset: {
      width: 0,
      height: 6,
    },

    elevation: 4,

  },

  iconBox: {

    width: 92,

    height: 92,

    borderRadius: 46,

    backgroundColor: '#E6EFFA',

    justifyContent: 'center',

    alignItems: 'center',

    marginBottom: 20,

  },

  title: {

    fontSize: 20,

    fontWeight: '700',

    color: '#111827',

    textAlign: 'center',

  },

  description: {

    marginTop: 10,

    fontSize: 14,

    color: '#5A6678',

    lineHeight: 22,

    textAlign: 'center',

    paddingHorizontal: 18,

  },

  buttonContainer: {

    marginTop: 28,

  },

});