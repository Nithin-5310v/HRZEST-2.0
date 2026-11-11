/* Checks a chosen photo in the browser before it is uploaded, so a photo
   that's too big, too small or not a usable image gets a clear message
   instead of a failed upload or an error page.

   HZPhotoCheck.check(file, function (error) { ... })
     error is null when the photo is fine, else a message to show.
   The size limit matches the server's (utils/helpers/uploads.py
   _MAX_PHOTO_SIZE_MB); the minimum resolution keeps faces recognisable. */
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
      done('This file type isn\'t supported. Please choose a JPG, PNG, WEBP or BMP photo.');
      return;
    }
    if (file.size > MAX_MB * 1024 * 1024) {
      done('This photo is too large (' + sizeText(file.size) + '). The maximum size is ' + MAX_MB +
           ' MB. Please choose a smaller photo or reduce its size.');
      return;
    }
    var url = URL.createObjectURL(file);
    var img = new Image();
    img.onload = function () {
      URL.revokeObjectURL(url);
      if (img.naturalWidth < MIN_PX || img.naturalHeight < MIN_PX) {
        done('This photo is too small (' + img.naturalWidth + TIMES + img.naturalHeight + ' pixels). ' +
             'Please choose a photo of at least ' + MIN_PX + TIMES + MIN_PX + ' pixels so the face is clear.');
      } else {
        done(null);
      }
    };
    img.onerror = function () {
      URL.revokeObjectURL(url);
      done('This photo couldn\'t be read. Please choose a different JPG or PNG photo.');
    };
    img.src = url;
  }

  window.HZPhotoCheck = {
    MAX_MB: MAX_MB,
    MIN_PX: MIN_PX,
    check: check
  };
})();
