/* Checks a chosen photo in the browser before it is uploaded, so a photo
   that's too big, too small or not a usable image gets a clear message
   ("Photo too small: this photo is 120 x 90 pixels ...") straight away,
   instead of a failed upload or an error page.

   HZPhotoCheck.check(file, function (error, info) { ... })
     error is null when the photo is fine, else the message to show.
     info (when the image could be read) is { size, width, height }:
     size is the file size as text ("1.2 MB").

   Any <input type="file" data-photo-check> is checked automatically when a
   photo is chosen. A rejected photo is cleared from the input, the reason
   pops up, and it is written into the element named by
   data-photo-check-hint (the picker's hint line) -- which otherwise shows
   the chosen photo's size.

   The rules match the server's (utils/helpers/uploads.py _MAX_PHOTO_SIZE_MB
   and PHOTO_MIN_PX; tests/test_clear_error_messages.py keeps them equal). */
(function () {
  var MAX_MB = 5;
  var MIN_PX = 200;
  var TIMES = ' ' + String.fromCharCode(215) + ' ';  // the multiplication sign
  var TYPES = ['image/jpeg', 'image/png', 'image/webp', 'image/bmp'];
  var EXT = /\.(jpe?g|png|webp|bmp)$/i;

  function sizeText(bytes) {
    return bytes >= 1024 * 1024 ? (bytes / (1024 * 1024)).toFixed(1) + ' MB'
                                : Math.max(1, Math.round(bytes / 1024)) + ' KB';
  }

  function check(file, done) {
    if (!file) { done(null); return; }
    if (!EXT.test(file.name || '') || (file.type && TYPES.indexOf(file.type) < 0)) {
      done('File type not allowed: this file isn\'t a JPG, PNG, WEBP or BMP photo. Please choose one of those.');
      return;
    }
    if (file.size === 0) {
      done('Empty file: the photo has no content. Please choose a different photo.');
      return;
    }
    if (file.size > MAX_MB * 1024 * 1024) {
      done('File too large: this photo is ' + sizeText(file.size) + '; the maximum is ' + MAX_MB +
           ' MB. Please choose a smaller photo or reduce its size.');
      return;
    }
    var url = URL.createObjectURL(file);
    var img = new Image();
    img.onload = function () {
      URL.revokeObjectURL(url);
      var info = { size: sizeText(file.size), width: img.naturalWidth, height: img.naturalHeight };
      if (img.naturalWidth < MIN_PX || img.naturalHeight < MIN_PX) {
        done('Photo too small: this photo is ' + img.naturalWidth + TIMES + img.naturalHeight + ' pixels (' +
             info.size + '); it must be at least ' + MIN_PX + TIMES + MIN_PX +
             ' pixels so the face is clear. Please choose a larger photo.', info);
      } else {
        done(null, info);
      }
    };
    img.onerror = function () {
      URL.revokeObjectURL(url);
      done('Unreadable photo: the file is damaged or isn\'t a complete image. Please choose a different photo.');
    };
    img.src = url;
  }

  /* Shows a rejected photo's message: the site's red error pop-up
     (static/toast.js) where the page has it, else the browser's alert. */
  function warn(message) {
    if (typeof window.showToast === 'function') window.showToast(message, 'error');
    else window.alert(message);
  }

  /* The automatic check for <input type="file" data-photo-check>. */
  function bind(input) {
    var hint = input.getAttribute('data-photo-check-hint');
    var hintEl = hint ? document.getElementById(hint) : null;
    var original = hintEl ? hintEl.textContent : '';
    var note = input.getAttribute('data-photo-check-note') || '';  // e.g. "Save changes to apply."
    input.addEventListener('change', function () {
      var file = input.files && input.files[0];
      if (hintEl) { hintEl.textContent = original; hintEl.style.color = ''; }
      if (!file) return;
      check(file, function (error, info) {
        if (error) {
          input.value = '';
          if (hintEl) { hintEl.textContent = error; hintEl.style.color = '#dc2626'; }
          warn(error);
          input.dispatchEvent(new CustomEvent('hz-photo-rejected', { detail: { message: error } }));
          return;
        }
        if (hintEl && info) {
          hintEl.textContent = 'Selected: ' + file.name + ' · ' + info.size + ' · ' +
                               info.width + TIMES + info.height + ' px' + (note ? '. ' + note : '');
          hintEl.style.color = '#15803d';
        }
      });
    });
  }

  function bindAll() {
    var inputs = document.querySelectorAll('input[type="file"][data-photo-check]');
    for (var i = 0; i < inputs.length; i++) bind(inputs[i]);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', bindAll);
  else bindAll();

  window.HZPhotoCheck = {
    warn: warn,
    MAX_MB: MAX_MB,
    MIN_PX: MIN_PX,
    sizeText: sizeText,
    check: check
  };
})();
