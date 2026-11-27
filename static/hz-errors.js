/* Every error a page shows says what went wrong and why -- "Network error:
   couldn't reach the server ..." -- never an HTML page, a stack trace, or
   browser jargon like "Unexpected token '<', "<!DOCTYPE "... is not valid
   JSON" (which is what an attendance check-in showed when the server, a
   proxy or an expired session answered with a web page instead of JSON).

   Added to every page by app_setup/csrf_csp.py (_inject_csrf_meta). It:
   - makes response.json() fail with a plain reason when the reply isn't
     JSON (an HTML error page, a proxy's 502/504 page, a login page after the
     session expired), instead of a SyntaxError quoting the HTML;
   - turns a dropped connection ("Failed to fetch", "Load failed") into a
     plain "Network error: ..." message;
   - marks same-origin fetch() calls with X-Requested-With, so the server
     answers errors as JSON ({"ok": false, "msg": ...}) rather than a page;
   - gives pages HZErrors.describe(err) for camera, location, fingerprint
     and other browser errors, HZErrors.clean(msg) to keep any message free
     of HTML or code, and HZErrors.readJson(resp), which never throws.
   The mobile app does the same in mobile/src/api/errors.js. */
(function () {
  'use strict';
  if (window.HZErrors) return;

  var NETWORK = "Network error: couldn't reach the server. Check your internet connection and try again.";
  var SESSION = 'Session expired: you were signed out. Please sign in again and retry.';
  var BAD_REPLY = "Unexpected reply: the server sent a response this page couldn't read. " +
                  'Please try again in a minute.';
  var GENERIC = 'Something went wrong: the action could not be completed. Please try again.';

  var REASONS = {
    400: 'Invalid request: some details were missing or invalid. Check them and try again.',
    401: 'Not signed in: your session has ended. Please sign in again and retry.',
    402: 'Plan expired: your company\'s plan needs renewing before this can be used.',
    403: "Access denied: you don't have permission to do this, or the request was blocked for security.",
    404: 'Not found: the page or item no longer exists. It may have been moved or deleted.',
    405: "Not allowed: this action isn't available here. Reload the page and try again.",
    408: 'Timed out: the request took too long. Check your connection and try again.',
    409: 'Conflict: this was changed meanwhile. Reload the page and try again.',
    413: 'File too large: the upload is bigger than the server accepts. Please choose a smaller file.',
    415: "Unsupported format: this file or data type isn't accepted here.",
    422: "Couldn't process: some values weren't accepted. Check them and try again.",
    429: 'Too many requests: please wait a moment and try again.',
    500: 'Server error: something went wrong on our side. It has been logged; please try again.',
    502: 'Server unreachable: the server is restarting or not responding. Please try again in a minute.',
    503: 'Service unavailable: the service is busy or under maintenance. Please try again in a minute.',
    504: 'Server timeout: the server took too long to answer. Please try again in a minute.'
  };

  function reasonForStatus(status) {
    if (REASONS[status]) return REASONS[status];
    if (status >= 500) return REASONS[500];
    if (status >= 400) return REASONS[400];
    return GENERIC;
  }

  // Markup, tracebacks, stack frames, SQL driver names: never shown as-is.
  var HTML_RE = /<!doctype|<\s*\/?\s*(html|head|body|script|style|div|span|pre|p|h[1-6]|title|table|br)\b/i;
  var CODE_RE = /Traceback \(most recent call last\)|File "[^"]+", line \d+|\b(psycopg2|sqlalchemy|werkzeug|jinja2)\.|^\s*at \S+ \(?\S+:\d+:\d+\)?$|Unexpected token|is not valid JSON|JSON\.parse|undefined is not|Cannot read propert/im;

  function clean(msg, status) {
    if (msg === null || msg === undefined || msg === '') return reasonForStatus(status);
    var text = typeof msg === 'string' ? msg : (msg.msg || msg.message || '');
    text = String(text);
    if (HTML_RE.test(text) || CODE_RE.test(text)) return reasonForStatus(status);
    text = text.replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').trim();
    if (!text) return reasonForStatus(status);
    return text.length > 500 ? text.slice(0, 497) + '...' : text;
  }

  function friendly(message, name) {
    var e = new Error(message);
    e.name = name || 'HZError';
    e.hzFriendly = true;
    return e;
  }

  var DOM_REASONS = {
    NotAllowedError: 'Permission denied: the browser blocked access (camera, location or fingerprint), or ' +
                     'the request was cancelled. Allow access for this site and try again.',
    PermissionDeniedError: 'Permission denied: the browser blocked access to the camera. Allow it for this site ' +
                           'and try again.',
    NotFoundError: 'Device not found: no camera (or other required device) was found. Connect one or use ' +
                   'another device.',
    DevicesNotFoundError: 'Device not found: no camera was found. Connect one or use another device.',
    NotReadableError: 'Camera busy: another app or browser tab is using the camera. Close it and try again.',
    TrackStartError: 'Camera busy: another app or browser tab is using the camera. Close it and try again.',
    OverconstrainedError: "Camera not suitable: this camera doesn't support the required settings. Try another " +
                          'camera or device.',
    SecurityError: 'Blocked for security: this feature needs a secure connection. Open the site using its ' +
                   'https:// address.',
    AbortError: 'Cancelled: the action was cancelled or took too long. Please try again.',
    TimeoutError: 'Timed out: the action took too long. Please try again.',
    InvalidStateError: 'Already set up: this device is already registered, or is busy with another request. ' +
                       'Try again, or use another device.',
    NotSupportedError: "Not supported: this browser or device can't do this. Try another browser or device.",
    QuotaExceededError: 'Storage full: the browser has no room left for this site. Free up space and try again.'
  };

  var GEO_REASONS = {
    1: 'Location blocked: allow location access for this site in your browser settings, then try again.',
    2: "Location unavailable: your device couldn't work out where you are. Turn on location services and " +
       'try again.',
    3: 'Location timed out: finding your location took too long. Check location services or move to an open ' +
       'area, then try again.'
  };

  /* A thrown error (from fetch, the camera, geolocation, WebAuthn, ...) as
     "Type: reason". fallback is used when nothing better is known. */
  function describe(err, fallback) {
    if (!err) return fallback || GENERIC;
    if (err.hzFriendly) return err.message;
    if (typeof err === 'string') return clean(err);
    // GeolocationPositionError: numeric code 1-3, has PERMISSION_DENIED.
    if (typeof err.code === 'number' && 'PERMISSION_DENIED' in err && GEO_REASONS[err.code]) {
      return GEO_REASONS[err.code];
    }
    if (err.name && DOM_REASONS[err.name]) return DOM_REASONS[err.name];
    var message = String(err.message || '');
    if (err.name === 'TypeError' && /fetch|network|load failed/i.test(message)) return NETWORK;
    if (err.name === 'SyntaxError') return BAD_REPLY;
    // A page bug (undefined variable, null property ...): nothing the user
    // can fix from the wording, and the client-log reporter records it.
    if (err.name === 'TypeError' || err.name === 'ReferenceError' || err.name === 'RangeError') {
      return fallback || 'Page error: something went wrong on this page. Please reload it and try again.';
    }
    return message ? clean(message) : (fallback || GENERIC);
  }

  function looksLikeLoginPage(resp) {
    return resp.redirected && /\/(login|signin|admin_login|employee_login)\b/i.test(resp.url || '');
  }

  /* The reply's JSON, or {ok: false, msg: reason} when it isn't JSON.
     Never throws. */
  function readJson(resp) {
    return resp.text().then(function (txt) {
      try {
        var data = JSON.parse(txt);
        if (data && typeof data === 'object' && !Array.isArray(data) && data.ok === false) {
          data.msg = clean(data.msg || data.error, resp.status);
        }
        return data;
      } catch (e) {
        if (looksLikeLoginPage(resp)) return { ok: false, msg: SESSION };
        return { ok: false, msg: resp.ok ? BAD_REPLY : reasonForStatus(resp.status) };
      }
    }, function () {
      return { ok: false, msg: NETWORK };
    });
  }

  // ── response.json(): a reply that isn't JSON fails with a plain reason ──
  if (window.Response && Response.prototype && Response.prototype.json) {
    Response.prototype.json = function () {
      var resp = this;
      return resp.text().then(function (txt) {
        try {
          return JSON.parse(txt);
        } catch (e) {
          if (looksLikeLoginPage(resp)) throw friendly(SESSION, 'SessionExpired');
          throw friendly(resp.ok ? BAD_REPLY : reasonForStatus(resp.status), 'BadReply');
        }
      });
    };
  }

  // ── fetch(): network failures in words; same-origin calls ask for JSON ──
  function sameOrigin(url) {
    if (!url || /^(data|blob):/i.test(url)) return false;
    try { return new URL(url, window.location.href).origin === window.location.origin; }
    catch (e) { return false; }
  }

  function withAjaxHeader(init) {
    init = Object.assign({}, init || {});
    var h = init.headers;
    if (h instanceof Headers || Array.isArray(h)) {
      h = new Headers(h);
      if (!h.has('X-Requested-With')) h.set('X-Requested-With', 'XMLHttpRequest');
    } else {
      h = Object.assign({}, h || {});
      var has = Object.keys(h).some(function (k) { return k.toLowerCase() === 'x-requested-with'; });
      if (!has) h['X-Requested-With'] = 'XMLHttpRequest';
    }
    init.headers = h;
    return init;
  }

  if (typeof window.fetch === 'function') {
    var _fetch = window.fetch;
    window.fetch = function (input, init) {
      try {
        if (typeof input === 'string' || (window.URL && input instanceof URL)) {
          if (sameOrigin(String(input))) init = withAjaxHeader(init);
        } else if (input && input.url && input.headers && sameOrigin(input.url) && !init) {
          if (!input.headers.has('X-Requested-With')) input.headers.set('X-Requested-With', 'XMLHttpRequest');
        }
      } catch (e) { /* never block a request over the header */ }
      return _fetch.call(this, input, init).catch(function (err) {
        if (err && err.name === 'AbortError') throw err;
        throw friendly(NETWORK, 'NetworkError');
      });
    };
  }

  // ── Error pop-ups: never show markup or code ──
  if (typeof window.showToast === 'function') {
    var _toast = window.showToast;
    window.showToast = function (message, type) {
      var args = Array.prototype.slice.call(arguments);
      if (type === 'error' || type === 'danger') args[0] = clean(message);
      return _toast.apply(this, args);
    };
  }

  window.HZErrors = {
    NETWORK: NETWORK,
    reasonForStatus: reasonForStatus,
    clean: clean,
    describe: describe,
    readJson: readJson
  };
})();
