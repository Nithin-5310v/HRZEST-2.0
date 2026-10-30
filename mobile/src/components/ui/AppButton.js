import React from 'react';
import {
  Pressable,
  Text,
  StyleSheet,
  ActivityIndicator,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';

export default function AppButton({
  title,
  onPress,
  loading = false,
  disabled = false,

  icon,
  iconPosition = 'left',

  variant = 'primary',

  fullWidth = true,

  style,

  textStyle,
}) {

  const buttonStyles = [
    styles.button,
    styles[variant],
    fullWidth && styles.fullWidth,
    disabled && styles.disabled,
    style,
  ];

  const textStyles = [
    styles.text,
    styles[`${variant}Text`],
    textStyle,
  ];

  return (

    <Pressable

      onPress={onPress}

      disabled={disabled || loading}

      android_ripple={{
        color: '#E8EEF9',
      }}

      style={({ pressed }) => [

        buttonStyles,

        pressed && styles.pressed,

      ]}

    >

      {loading ? (

        <ActivityIndicator

          color={
            variant === 'primary'
              ? '#FFFFFF'
              : '#0B6E8E'
          }

        />

      ) : (

        <>

          {icon && iconPosition === 'left' && (

            <Ionicons
              name={icon}
              size={18}
              color={
                variant === 'primary'
                  ? '#FFFFFF'
                  : '#0B6E8E'
              }
              style={styles.leftIcon}
            />

          )}

          <Text style={textStyles}>

            {title}

          </Text>

          {icon && iconPosition === 'right' && (

            <Ionicons
              name={icon}
              size={18}
              color={
                variant === 'primary'
                  ? '#FFFFFF'
                  : '#0B6E8E'
              }
              style={styles.rightIcon}
            />

          )}

        </>

      )}

    </Pressable>

  );

}

const styles = StyleSheet.create({

  button: {

    height: 54,

    borderRadius: 18,

    flexDirection: 'row',

    justifyContent: 'center',

    alignItems: 'center',

    paddingHorizontal: 24,

    borderWidth: 1,

  },

  fullWidth: {

    width: '100%',

  },

  primary: {

    backgroundColor: '#0B6E8E',

    borderColor: '#0B6E8E',

  },

  secondary: {

    backgroundColor: '#F4F7FC',

    borderColor: '#D9E2EF',

  },

  outline: {

    backgroundColor: '#FFFFFF',

    borderColor: '#D6E0EC',

  },

  danger: {

    backgroundColor: '#DC2626',

    borderColor: '#DC2626',

  },

  disabled: {

    opacity: 0.55,

  },

  pressed: {

    transform: [
      {
        scale: 0.985,
      },
    ],

    opacity: 0.95,

  },

  text: {

    fontSize: 15,

    fontWeight: '700',

  },

  primaryText: {

    color: '#FFFFFF',

  },

  secondaryText: {

    color: '#0B6E8E',

  },

  outlineText: {

    color: '#0B6E8E',

  },

  dangerText: {

    color: '#FFFFFF',

  },

  leftIcon: {

    marginRight: 10,

  },

  rightIcon: {

    marginLeft: 10,

  },

});