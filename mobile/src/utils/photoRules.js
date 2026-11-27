// The rules the server enforces for photos of people (utils/helpers/uploads.py
// _MAX_PHOTO_SIZE_MB and PHOTO_MIN_PX; the website checks the same in
// static/js/photo-check.js). Checked as soon as a photo is picked, so a photo
// that's too big or too small gets an alert that says so -- with its actual
// size -- instead of a failed upload. tests/test_clear_error_messages.py keeps
// the numbers equal to the server's.

export const PHOTO_MAX_MB = 5;
export const PHOTO_MIN_PX = 200;
export const PHOTO_RULES_HINT =
  `JPG, PNG, WEBP or BMP · up to ${PHOTO_MAX_MB} MB · at least ${PHOTO_MIN_PX} × ${PHOTO_MIN_PX} px`;

export const sizeText = (bytes) =>
  bytes >= 1024 * 1024 ? `${(bytes / (1024 * 1024)).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`;

// asset: an expo-image-picker result asset ({ width, height, fileSize }).
// Returns null when the photo is fine, else { title, message } for an alert.
export const checkPickedPhoto = (asset) => {
  if (!asset || !asset.uri) {
    return { title: 'No Photo Selected', message: 'No photo selected: please choose a photo.' };
  }
  const { width, height, fileSize } = asset;
  if (fileSize && fileSize > PHOTO_MAX_MB * 1024 * 1024) {
    return {
      title: 'Photo Too Large',
      message: `File too large: this photo is ${sizeText(fileSize)}; the maximum is ${PHOTO_MAX_MB} MB. ` +
        'Please choose a smaller photo or reduce its size.',
    };
  }
  if (width && height && (width < PHOTO_MIN_PX || height < PHOTO_MIN_PX)) {
    const size = fileSize ? ` (${sizeText(fileSize)})` : '';
    return {
      title: 'Photo Too Small',
      message: `Photo too small: this photo is ${width} × ${height} pixels${size}; it must be at least ` +
        `${PHOTO_MIN_PX} × ${PHOTO_MIN_PX} pixels so the face is clear. Please choose a larger photo.`,
    };
  }
  return null;
};
