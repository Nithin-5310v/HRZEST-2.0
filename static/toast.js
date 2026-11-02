// Toast Notification Engine -- single shared implementation for the whole
// app (admin_base.html's live-dashboard alerts, employee_portal.html,
// salary_report.html, and any window.alert() call on a page that loads
// this script -- see the override at the bottom of this file).
//
// The message is ALWAYS treated as plain text. It used to be interpolated
// into innerHTML, and because window.alert is overridden below to call this,
// every alert() and every server-supplied `data.msg` on every page that loads
// this file (admin, HR, employee, analytics) became an HTML/DOM-XSS sink --
// e.g. a flash message echoing an employee name. Nothing passes markup on
// purpose, so text is the only mode.
const TOAST_TYPES = ['success', 'error', 'alert', 'info'];
const TOAST_DEFAULT_ICONS = { success: 'circle-check', error: 'alert-triangle', alert: 'alert-triangle', info: 'info-circle' };

function showToast(message, type = 'info', icon = null) {
  // Right after login the HRZest loader (static/login-loader.js) owns the
  // screen: hold the toast until it has finished, then show it.
  if (document.getElementById('hrzLoginLoader')) {
    window.addEventListener('hrz-loader-done', () => showToast(message, type, icon), { once: true });
    return;
  }
  let container = document.getElementById('toast-container');
  if (!container) {
    container = document.createElement('div');
    container.id = 'toast-container';
    // Toasts are injected after the page has already loaded/been read --
    // without this, a screen-reader user gets no announcement at all when
    // one appears. "polite" (not "assertive") queues the announcement
    // behind whatever the user is currently doing, rather than
    // interrupting it -- appropriate for a passive notification, not an
    // urgent one.
    container.setAttribute('role', 'status');
    container.setAttribute('aria-live', 'polite');
    document.body.appendChild(container);
  }

  // type and icon end up in class names: only known / slug-shaped values pass.
  const safeType = TOAST_TYPES.includes(type) ? type : 'info';
  const safeIcon = (typeof icon === 'string' && /^[a-z0-9-]+$/.test(icon)) ? icon : TOAST_DEFAULT_ICONS[safeType];

  const toast = document.createElement('div');
  toast.className = `toast ${safeType}`;

  const iconWrap = document.createElement('div');
  iconWrap.className = 'toast-icon';
  const iconEl = document.createElement('i');
  iconEl.className = `ti ti-${safeIcon}`;
  iconWrap.appendChild(iconEl);

  const content = document.createElement('div');
  content.className = 'toast-content';
  content.textContent = message === null || message === undefined ? '' : String(message);

  toast.appendChild(iconWrap);
  toast.appendChild(content);
  container.appendChild(toast);

  // Trigger animation
  requestAnimationFrame(() => {
    toast.classList.add('show');
  });

  // Auto-remove
  setTimeout(() => {
    toast.classList.remove('show');
    toast.classList.add('hide');
    setTimeout(() => {
      toast.remove();
    }, 300); // Wait for transition
  }, 4000);
}

// Override native alert to use Toast for a better UI experience
window.alert = function(msg) {
  showToast(msg, 'info');
};

// ── Soft confirm / prompt dialogs ─────────────────────────────────────────
// In-page replacements for window.confirm()/prompt(), which show the
// browser's own "localhost:5000 says" box. Those block until answered;
// these can't, so they return a Promise -- `if (!(await hzConfirm(msg)))
// return;` in JS, or hzConfirmSubmit() below for inline form/button
// handlers. Message text is plain text only, same as showToast().
function _hzDialog(message, opts) {
  opts = opts || {};
  return new Promise((resolve) => {
    const prevFocus = document.activeElement;
    const backdrop = document.createElement('div');
    backdrop.className = 'hz-dialog-backdrop';

    const box = document.createElement('div');
    box.className = 'hz-dialog' + (opts.danger ? ' danger' : '');
    box.setAttribute('role', opts.input ? 'dialog' : 'alertdialog');
    box.setAttribute('aria-modal', 'true');

    const msg = document.createElement('div');
    msg.className = 'hz-dialog-msg';
    msg.id = 'hzDialogMsg' + Date.now();
    msg.textContent = message === null || message === undefined ? '' : String(message);
    box.setAttribute('aria-describedby', msg.id);
    box.appendChild(msg);

    let input = null;
    if (opts.input) {
      input = document.createElement('input');
      input.type = 'text';
      input.className = 'hz-dialog-input';
      input.value = opts.defaultValue || '';
      box.appendChild(input);
    }

    const actions = document.createElement('div');
    actions.className = 'hz-dialog-actions';
    const cancelBtn = document.createElement('button');
    cancelBtn.type = 'button';
    cancelBtn.className = 'hz-dialog-btn cancel';
    cancelBtn.textContent = opts.cancelText || 'Cancel';
    const okBtn = document.createElement('button');
    okBtn.type = 'button';
    okBtn.className = 'hz-dialog-btn ok';
    okBtn.textContent = opts.okText || 'OK';
    actions.appendChild(cancelBtn);
    actions.appendChild(okBtn);
    box.appendChild(actions);
    backdrop.appendChild(box);
    document.body.appendChild(backdrop);
    requestAnimationFrame(() => backdrop.classList.add('show'));

    function close(result) {
      document.removeEventListener('keydown', onKey, true);
      backdrop.classList.remove('show');
      setTimeout(() => backdrop.remove(), 200);
      if (prevFocus && prevFocus.focus) prevFocus.focus();
      resolve(result);
    }
    function onKey(e) {
      if (e.key === 'Escape') { e.preventDefault(); close(opts.input ? null : false); }
      else if (e.key === 'Enter' && (input ? document.activeElement === input : true)) {
        e.preventDefault(); close(input ? input.value : true);
      } else if (e.key === 'Tab') {
        // Keep focus inside the dialog
        const f = [input, cancelBtn, okBtn].filter(Boolean);
        const i = f.indexOf(document.activeElement);
        e.preventDefault();
        f[(i + (e.shiftKey ? f.length - 1 : 1)) % f.length].focus();
      }
    }
    okBtn.addEventListener('click', () => close(input ? input.value : true));
    cancelBtn.addEventListener('click', () => close(input ? null : false));
    backdrop.addEventListener('click', (e) => { if (e.target === backdrop) close(input ? null : false); });
    document.addEventListener('keydown', onKey, true);
    (input || okBtn).focus();
  });
}

const _HZ_DANGER_WORDS = /\b(delete|remove|reset|revoke|ban|deactivate|disable|cancel|reject|unlock|regenerate|replace)\b/i;

function hzConfirm(message, opts) {
  opts = Object.assign({ danger: _HZ_DANGER_WORDS.test(String(message || '')) }, opts || {});
  return _hzDialog(message, opts);
}

function hzPrompt(message, defaultValue, opts) {
  return _hzDialog(message, Object.assign({ input: true, defaultValue: defaultValue }, opts || {}));
}

// For form/button handlers that used `return confirm(...)` (bound through
// data-on-* attributes -- see static/hz-actions.js; inline on*="" attributes
// are blocked by the CSP):
//   <form data-on-submit="return hzConfirmSubmit(event, 'Delete this?')">
//   <button type="submit" data-on-click="return hzConfirmSubmit(event, '...')">
// Cancels the first event, asks, and on OK replays the same submit/click
// with a one-shot flag so this handler lets it through the second time.
function hzConfirmSubmit(event, message, opts) {
  const el = event.currentTarget || event.target;
  if (el.dataset.hzConfirmed === '1') {
    delete el.dataset.hzConfirmed;
    return true;
  }
  event.preventDefault();
  const submitter = event.submitter || null;
  hzConfirm(message, opts).then((ok) => {
    if (!ok) return;
    el.dataset.hzConfirmed = '1';
    if (el.tagName === 'FORM') {
      if (el.requestSubmit) el.requestSubmit(submitter && submitter.form === el ? submitter : undefined);
      else el.submit();
    } else {
      el.click();
    }
  });
  return false;
}
