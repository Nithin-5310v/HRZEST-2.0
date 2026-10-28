import { Alert } from "react-native";
import * as ImagePicker from "expo-image-picker";

// The phone's own camera app, for a face photo when the in-app preview won't
// work on a device. The photo is taken there and then (launchCameraAsync
// never offers the gallery). Release the in-app CameraView first -- see
// useCameraHandoff's CAMERA_HANDOFF_DELAY_MS -- or the camera app may find
// the camera busy. Returns { uri }, or null if cancelled or not allowed.
export async function takeSelfieWithPhoneCamera() {
  try {
    const permission = await ImagePicker.requestCameraPermissionsAsync();
    if (!permission.granted) {
      Alert.alert("Camera Access Required", "Allow camera access to take your photo.");
      return null;
    }
    const result = await ImagePicker.launchCameraAsync({
      cameraType: ImagePicker.CameraType.front,
      quality: 0.75,
    });
    const asset = !result.canceled && result.assets?.[0];
    return asset?.uri ? { uri: asset.uri } : null;
  } catch (_err) {
    Alert.alert("Camera Error", "The phone's camera could not be opened. Please try again.");
    return null;
  }
}
