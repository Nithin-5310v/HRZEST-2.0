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
