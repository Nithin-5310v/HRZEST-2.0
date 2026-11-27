// Every error the app shows says what went wrong and why -- "Network error:
// couldn't reach HRzest ..." -- never an HTML page, a stack trace or code.
//
// The server answers errors as JSON ({ ok: false, msg }), but a proxy's
// 502/504 page during a restart, a firewall page or a timeout arrives as
// HTML (or nothing at all). client.js runs every failed call through
// normalizeApiError(), so error.response.data.msg is always a readable
// reason and error.message is a readable reason when nothing reached the
// server. Screens show errorMessage(e, fallback).
//
// Same wording as the website's static/hz-errors.js.

export const NETWORK_MSG = "Network error: couldn't reach HRzest. Check your internet connection and try again.";
export const TIMEOUT_MSG = 'Timed out: the server took too long to answer. Check your connection and try again.';
const GENERIC = 'Something went wrong: the action could not be completed. Please try again.';

const REASONS = {
  400: 'Invalid request: some details were missing or invalid. Check them and try again.',
  401: 'Not signed in: your session has ended. Please sign in again and retry.',
  402: "Plan expired: your company's plan needs renewing before this can be used.",
  403: "Access denied: you don't have permission to do this, or the request was blocked for security.",
  404: 'Not found: the item no longer exists. It may have been moved or deleted.',
  405: "Not allowed: this action isn't available. Update the app and try again.",
  408: 'Timed out: the request took too long. Check your connection and try again.',
  409: 'Conflict: this was changed meanwhile. Refresh and try again.',
  413: 'File too large: the upload is bigger than the server accepts. Please choose a smaller file.',
  415: "Unsupported format: this file or data type isn't accepted.",
  422: "Couldn't process: some values weren't accepted. Check them and try again.",
  429: 'Too many requests: please wait a moment and try again.',
  500: 'Server error: something went wrong on our side. It has been logged; please try again.',
  502: 'Server unreachable: the server is restarting or not responding. Please try again in a minute.',
  503: 'Service unavailable: the service is busy or under maintenance. Please try again in a minute.',
  504: 'Server timeout: the server took too long to answer. Please try again in a minute.',
};

export const reasonForStatus = (status) => {
  if (REASONS[status]) return REASONS[status];
  if (status >= 500) return REASONS[500];
  if (status >= 400) return REASONS[400];
  return GENERIC;
};

// Markup, tracebacks, stack frames, SQL driver names: never shown as-is.
const HTML_RE = /<!doctype|<\s*\/?\s*(html|head|body|script|style|div|span|pre|p|h[1-6]|title|table|br)\b/i;
const CODE_RE = /Traceback \(most recent call last\)|File "[^"]+", line \d+|\b(psycopg2|sqlalchemy|werkzeug|jinja2)\.|^\s*at \S+ \(?\S+:\d+:\d+\)?$|Unexpected token|is not valid JSON|JSON Parse error|undefined is not|Cannot read propert/im;

// A message safe to show: the reason for `status` when it is empty or
// contains HTML or code; otherwise the message with any tags removed.
export const cleanMessage = (msg, status) => {
  if (msg === null || msg === undefined) return reasonForStatus(status);
  let text = String(msg);
  if (HTML_RE.test(text) || CODE_RE.test(text)) return reasonForStatus(status);
  text = text.replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').trim();
  if (!text) return reasonForStatus(status);
  return text.length > 500 ? `${text.slice(0, 497)}...` : text;
};

// Called by client.js for every failed call. Mutates and returns `error`:
// - a reply: response.data becomes an object whose msg is a clean reason
//   (an HTML page or empty body is replaced by the status's reason);
// - no reply: error.message becomes the network/timeout reason. The
//   error keeps having no `response`, which is how the attendance screens
//   tell "offline, save the punch for later" from "the server refused".
export const normalizeApiError = (error) => {
  if (!error || typeof error !== 'object') return error;
  const res = error.response;
  if (res) {
    const data = res.data;
    if (data && typeof data === 'object' && !Array.isArray(data)) {
      const raw = data.msg || data.message || (typeof data.error === 'string' ? data.error : '');
      data.msg = cleanMessage(raw || null, res.status);
    } else {
      res.data = { ok: false, error: `http_${res.status || 0}`, msg: reasonForStatus(res.status) };
    }
  } else if (error.isAxiosError || error.code || error.message === 'Network Error') {
    if (error.code === 'ECONNABORTED' || error.code === 'ETIMEDOUT' || /timeout/i.test(error.message || '')) {
      error.message = TIMEOUT_MSG;
      error.hzFriendly = true;
    } else if (error.code === 'ERR_NETWORK' || error.message === 'Network Error') {
      error.message = NETWORK_MSG;
      error.hzFriendly = true;
    }
  }
  return error;
};

// What to show for a failed call: the server's reason, else the network
// reason, else `fallback`.
export const errorMessage = (e, fallback = GENERIC) => {
  const res = e?.response;
  const msg = res?.data?.msg;
  if (typeof msg === 'string' && msg.trim()) return cleanMessage(msg, res.status);
  if (res) return reasonForStatus(res.status);
  if (e?.hzFriendly) return e.message;
  if (e?.isAxiosError) return NETWORK_MSG;
  return fallback;
};
