import React, { useEffect, useRef } from 'react';
import {
  View,
  StyleSheet,
  Animated,
} from 'react-native';

export default function LoadingSkeleton({

  height = 20,

  width = '100%',

  radius = 12,

  style,

}) {

  const opacity = useRef(new Animated.Value(0.35)).current;

  useEffect(() => {

    Animated.loop(

      Animated.sequence([

        Animated.timing(opacity, {
          toValue: 0.9,
          duration: 700,
          useNativeDriver: true,
        }),

        Animated.timing(opacity, {
          toValue: 0.35,
          duration: 700,
          useNativeDriver: true,
        }),

      ])

    ).start();

  }, []);

  return (

    <Animated.View

      style={[

        styles.skeleton,

        {

          width,

          height,

          borderRadius: radius,

          opacity,

        },

        style,

      ]}

    />

  );

}

const styles = StyleSheet.create({

  skeleton: {

    backgroundColor: '#E3ECEF',

  },

});