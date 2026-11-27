// A picked photo that's too large or too small is refused at once, with its
// actual size in the message (src/utils/photoRules.js).
import { checkPickedPhoto, PHOTO_MAX_MB, PHOTO_MIN_PX } from '../photoRules';

describe('checkPickedPhoto', () => {
  test('a normal phone photo is fine', () => {
    expect(checkPickedPhoto({ uri: 'file:///a.jpg', width: 1080, height: 1080, fileSize: 450 * 1024 })).toBeNull();
  });

  test('too large: says the size and the limit', () => {
    const problem = checkPickedPhoto({ uri: 'file:///a.jpg', width: 4000, height: 4000, fileSize: 7.4 * 1024 * 1024 });
    expect(problem.title).toBe('Photo Too Large');
    expect(problem.message).toContain('7.4 MB');
    expect(problem.message).toContain(`maximum is ${PHOTO_MAX_MB} MB`);
  });

  test('too small: says the pixels, file size and the minimum', () => {
    const problem = checkPickedPhoto({ uri: 'file:///a.jpg', width: 120, height: 90, fileSize: 6 * 1024 });
    expect(problem.title).toBe('Photo Too Small');
    expect(problem.message).toContain('120 × 90 pixels (6 KB)');
    expect(problem.message).toContain(`at least ${PHOTO_MIN_PX} × ${PHOTO_MIN_PX} pixels`);
  });

  test('no photo', () => {
    expect(checkPickedPhoto(null).title).toBe('No Photo Selected');
  });

  test('unknown size (some pickers omit it) is left to the server', () => {
    expect(checkPickedPhoto({ uri: 'file:///a.jpg' })).toBeNull();
  });
});
