// Every error the app shows says what went wrong and why -- never an HTML
// page, a stack trace or code (src/api/errors.js).
import {
  NETWORK_MSG,
  TIMEOUT_MSG,
  cleanMessage,
  errorMessage,
  normalizeApiError,
  reasonForStatus,
} from '../errors';

const HTML_502 = '<html>\r\n<head><title>502 Bad Gateway</title></head>\r\n<body><center><h1>502 Bad Gateway</h1>' +
  '</center><hr><center>nginx</center></body></html>';

const httpError = (status, data) => ({ isAxiosError: true, message: `Request failed with status code ${status}`,
  response: { status, data } });

describe('normalizeApiError', () => {
  test('an HTML error page becomes the status reason, never the HTML', () => {
    const err = normalizeApiError(httpError(502, HTML_502));
    expect(err.response.data.ok).toBe(false);
    expect(err.response.data.msg).toMatch(/^Server unreachable: /);
    expect(err.response.data.msg).not.toMatch(/<|nginx/);
  });

  test("the server's own reason is kept", () => {
    const err = normalizeApiError(httpError(400, { ok: false, msg: 'Face not recognised: please try again.' }));
    expect(err.response.data.msg).toBe('Face not recognised: please try again.');
  });

  test('a JSON error without a msg gets the status reason', () => {
    const err = normalizeApiError(httpError(403, { ok: false }));
    expect(err.response.data.msg).toMatch(/^Access denied: /);
  });

  test('a traceback in a msg is replaced', () => {
    const err = normalizeApiError(httpError(500, {
      ok: false, msg: 'Traceback (most recent call last):\n  File "app.py", line 3, in x\nKeyError: 1' }));
    expect(err.response.data.msg).toMatch(/^Server error: /);
  });

  test('no reply at all: a network reason, and still no response (offline punches rely on it)', () => {
    const err = normalizeApiError({ isAxiosError: true, code: 'ERR_NETWORK', message: 'Network Error' });
    expect(err.message).toBe(NETWORK_MSG);
    expect(err.response).toBeUndefined();
  });

  test('a timeout says so', () => {
    const err = normalizeApiError({ isAxiosError: true, code: 'ECONNABORTED', message: 'timeout of 10000ms exceeded' });
    expect(err.message).toBe(TIMEOUT_MSG);
  });
});

describe('errorMessage', () => {
  test("prefers the server's reason", () => {
    expect(errorMessage(httpError(409, { ok: false, msg: 'Already checked in: you checked in at 09:02.' }), 'x'))
      .toBe('Already checked in: you checked in at 09:02.');
  });

  test('network failure: the network reason, not the fallback', () => {
    const err = normalizeApiError({ isAxiosError: true, code: 'ERR_NETWORK', message: 'Network Error' });
    expect(errorMessage(err, 'Could not save.')).toBe(NETWORK_MSG);
  });

  test('a non-network, non-HTTP error uses the fallback', () => {
    expect(errorMessage(new Error('boom'), 'Could not save.')).toBe('Could not save.');
  });
});

describe('cleanMessage / reasonForStatus', () => {
  test('tags are removed from an otherwise fine message', () => {
    expect(cleanMessage('Leave <b>not</b> saved: dates overlap.', 400)).toBe('Leave not saved: dates overlap.');
  });

  test('every reason names a type before the colon', () => {
    [400, 401, 403, 404, 408, 413, 429, 500, 502, 503, 504, 418, 599].forEach((s) => {
      expect(reasonForStatus(s)).toMatch(/^[A-Z][^:]{2,40}: \S/);
    });
  });
});
