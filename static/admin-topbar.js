/* ==========================================================================
   HRzest - Admin top bar: notifications bell
   (templates/admin_base.html)

   Bell: unread count + dropdown from GET /web/admin/notifications/list,
         "Mark all as read" -> POST /web/admin/notifications/mark_read
         (blueprints/notifications.py). The page's fetch() wrapper adds the
         CSRF header automatically. Refreshes every 60 s.
   Notification text is user content -> set with textContent only.

   Also: today's date + time in the banner (#appTopDate / #appTopTime,
   refreshed every 15 s) and the profile menu
   (#appUserBtn -> #appUserPanel: Profile / Settings / Logout).
   ========================================================================== */

(function () {

  // ── Date ─────────────────────────────────────────────────────────────
  var dateEl = document.getElementById('appTopDate');
  var timeEl = document.getElementById('appTopTime');
  function tick() {
    var now = new Date();
    if (dateEl) dateEl.textContent = now.toLocaleDateString('en-IN', {weekday: 'short', day: '2-digit', month: 'short', year: 'numeric'});
    if (timeEl) timeEl.textContent = now.toLocaleTimeString('en-IN', {hour: '2-digit', minute: '2-digit', hour12: true});
  }
  if (dateEl || timeEl) { tick(); setInterval(tick, 15000); }

  // ── Profile menu ─────────────────────────────────────────────────────
  var userMenu = document.getElementById('appUserMenu');
  var userBtn = document.getElementById('appUserBtn');
  var userPanel = document.getElementById('appUserPanel');
  if (userMenu && userBtn && userPanel) {
    var closeUser = function () { userPanel.hidden = true; userBtn.setAttribute('aria-expanded', 'false'); };
    userBtn.addEventListener('click', function (e) {
      e.stopPropagation();
      var opening = userPanel.hidden;
      userPanel.hidden = !opening;
      userBtn.setAttribute('aria-expanded', opening ? 'true' : 'false');
    });
    document.addEventListener('click', function (e) { if (!userPanel.hidden && !userMenu.contains(e.target)) closeUser(); });
    document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && !userPanel.hidden) { closeUser(); userBtn.focus(); } });
  }

  // ── Notifications bell ───────────────────────────────────────────────
  var wrap = document.getElementById('appNotif');
  var btn = document.getElementById('appNotifBtn');
  var panel = document.getElementById('appNotifPanel');
  var list = document.getElementById('appNotifList');
  var count = document.getElementById('appNotifCount');
  var markAll = document.getElementById('appNotifMarkAll');
  if (!wrap || !btn || !panel || !list || !count) return;

  // tpath()-built URLs from the markup, so a company's /<slug>/ prefix is kept
  var LIST_URL = wrap.dataset.listUrl || '/web/admin/notifications/list';
  var READ_URL = wrap.dataset.readUrl || '/web/admin/notifications/mark_read';
  var lastUnread = -1;

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  function setCount(n) {
    if (n > 0) { count.textContent = n > 99 ? '99+' : String(n); count.hidden = false; }
    else { count.hidden = true; }
    if (n > lastUnread && lastUnread !== -1) {
      btn.classList.remove('has-unread'); void btn.offsetWidth; btn.classList.add('has-unread');
    }
    if (lastUnread === -1 && n > 0) btn.classList.add('has-unread');
    btn.setAttribute('aria-label', n > 0 ? 'Notifications, ' + n + ' unread' : 'Notifications');
    lastUnread = n;
  }

  function render(items) {
    list.textContent = '';
    if (!items.length) { list.appendChild(el('div', 'app-notif-empty', 'You’re all caught up — no notifications yet.')); return; }
    items.forEach(function (n) {
      var row = el('div', 'app-notif-item' + (n.is_read ? '' : ' unread'));
      row.appendChild(el('span', 'app-notif-dot'));
      var body = el('div');
      body.appendChild(el('div', 'app-notif-title', n.title || 'Notification'));
      if (n.message) body.appendChild(el('div', 'app-notif-msg', n.message));
      if (n.created_at) body.appendChild(el('div', 'app-notif-time', n.created_at));
      row.appendChild(body);
      list.appendChild(row);
    });
  }

  function load(withList) {
    return fetch(LIST_URL, {headers: {'Accept': 'application/json'}, credentials: 'same-origin'})
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (!d || !d.ok) { if (withList) render([]); return; }
        setCount(d.unread || 0);
        if (withList) render(d.notifications || []);
      })
      .catch(function () { if (withList) { list.textContent = ''; list.appendChild(el('div', 'app-notif-empty', 'Couldn’t load notifications.')); } });
  }

  function open() { panel.hidden = false; btn.setAttribute('aria-expanded', 'true'); load(true); }
  function close() { panel.hidden = true; btn.setAttribute('aria-expanded', 'false'); }

  btn.addEventListener('click', function (e) { e.stopPropagation(); if (panel.hidden) open(); else close(); });
  document.addEventListener('click', function (e) { if (!panel.hidden && !wrap.contains(e.target)) close(); });
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && !panel.hidden) { close(); btn.focus(); } });

  if (markAll) {
    markAll.addEventListener('click', function () {
      fetch(READ_URL, {method: 'POST', headers: {'Content-Type': 'application/json'}, credentials: 'same-origin', body: '{}'})
        .then(function () { return load(true); })
        .catch(function () { list.textContent = ''; list.appendChild(el('div', 'app-notif-empty', 'Couldn’t update notifications.')); });
    });
  }

  load(false);
  setInterval(function () { if (document.visibilityState === 'visible') load(!panel.hidden); }, 60000);
})();
