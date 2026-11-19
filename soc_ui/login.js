/* SOC sign-in: the passkey step after the emailed code (utils/soc_passkeys.py).
 *
 * Public on purpose (served without a session): it only knows the two passkey
 * endpoints of the sign-in page. Server messages are shown as text only. */
(function () {
  'use strict';
  var btn = document.getElementById('pk');
  if (!btn) return;
  var status = document.getElementById('pkStatus');
  var meta = document.querySelector('meta[name="csrf-token"]');
  var csrf = meta ? meta.content : '';

  function say(msg, bad) {
    status.textContent = msg;
    status.className = bad ? 'alert' : 'notice';
    status.hidden = false;
  }
  function toB64u(buf) {
    var bytes = new Uint8Array(buf), s = '';
    for (var i = 0; i < bytes.length; i++) s += String.fromCharCode(bytes[i]);
    return btoa(s).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  }
  function fromB64u(str) {
    var s = str.replace(/-/g, '+').replace(/_/g, '/');
    while (s.length % 4) s += '=';
    var bin = atob(s), out = new Uint8Array(bin.length);
    for (var i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out.buffer;
  }
  function post(url, body) {
    return fetch(url, {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
      body: JSON.stringify(body || {})
    }).then(function (res) {
      return res.json().catch(function () { return { ok: false, msg: 'Unexpected response (' + res.status + ').' }; });
    });
  }
  function encode(cred, mode) {
    var r = cred.response;
    var out = { id: cred.id, rawId: toB64u(cred.rawId), type: cred.type,
      clientExtensionResults: cred.getClientExtensionResults ? cred.getClientExtensionResults() : {},
      response: { clientDataJSON: toB64u(r.clientDataJSON) } };
    if (mode === 'register') {
      out.response.attestationObject = toB64u(r.attestationObject);
      if (r.getTransports) out.response.transports = r.getTransports();
    } else {
      out.response.authenticatorData = toB64u(r.authenticatorData);
      out.response.signature = toB64u(r.signature);
      if (r.userHandle) out.response.userHandle = toB64u(r.userHandle);
    }
    return out;
  }

  btn.addEventListener('click', function () {
    if (!window.PublicKeyCredential || !navigator.credentials) {
      say('This browser cannot use passkeys. Use an up-to-date browser on a device with a fingerprint, face or PIN unlock.', true);
      return;
    }
    btn.disabled = true;
    say('Waiting for your passkey…', false);
    var mode;
    post('/socperson/login/passkey/options').then(function (d) {
      if (!d.ok) throw d;
      mode = d.mode;
      var o = d.options;
      o.challenge = fromB64u(o.challenge);
      if (mode === 'register') {
        o.user.id = fromB64u(o.user.id);
        (o.excludeCredentials || []).forEach(function (c) { c.id = fromB64u(c.id); });
        return navigator.credentials.create({ publicKey: o });
      }
      (o.allowCredentials || []).forEach(function (c) { c.id = fromB64u(c.id); });
      return navigator.credentials.get({ publicKey: o });
    }).then(function (cred) {
      return post('/socperson/login/passkey/verify', { credential: encode(cred, mode) });
    }).then(function (d) {
      if (!d.ok) throw d;
      window.location.href = '/socperson';
    }).catch(function (e) {
      btn.disabled = false;
      if (e && e.restart) { say(e.msg || 'Start again.', true); btn.hidden = true; return; }
      if (e && e.name === 'NotAllowedError') { say('The passkey request was cancelled or timed out. Try again.', true); return; }
      if (e && e.name === 'InvalidStateError') { say('This device already has a passkey for this account. Use it instead, or ask an admin to reset it.', true); return; }
      say((e && (e.msg || e.message)) || 'The passkey could not be checked. Try again.', true);
    });
  });
})();
