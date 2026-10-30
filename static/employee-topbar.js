/* ==========================================================================
   HRzest - Employee top banner (templates/_employee_topbar.html)

   - date + time (#empTopDate / #empTopTime), refreshed every 15 s
   - profile menu (#empUserBtn -> #empUserPanel), closes on outside click/Esc
   - moves the employee portal's own notifications bell (#notifBell, which
     keeps its panel and behaviour) into the banner slot #empTopBell
   ========================================================================== */

(function () {
  // ── Date + time ──────────────────────────────────────────────────────
  var dateEl = document.getElementById('empTopDate');
  var timeEl = document.getElementById('empTopTime');
  function tick() {
    var now = new Date();
    if (dateEl) dateEl.textContent = now.toLocaleDateString('en-IN', {weekday: 'short', day: '2-digit', month: 'short', year: 'numeric'});
    if (timeEl) timeEl.textContent = now.toLocaleTimeString('en-IN', {hour: '2-digit', minute: '2-digit', hour12: true});
  }
  if (dateEl || timeEl) { tick(); setInterval(tick, 15000); }

  // ── Profile menu ─────────────────────────────────────────────────────
  var menu = document.getElementById('empUserMenu');
  var btn = document.getElementById('empUserBtn');
  var panel = document.getElementById('empUserPanel');
  if (menu && btn && panel) {
    var close = function () { panel.hidden = true; btn.setAttribute('aria-expanded', 'false'); };
    btn.addEventListener('click', function (e) {
      e.stopPropagation();
      var opening = panel.hidden;
      panel.hidden = !opening;
      btn.setAttribute('aria-expanded', opening ? 'true' : 'false');
    });
    panel.addEventListener('click', function (e) {
      if (e.target.closest('button:not([type="submit"]), a')) close();  // "My Profile" etc.
    });
    document.addEventListener('click', function (e) { if (!panel.hidden && !menu.contains(e.target)) close(); });
    document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && !panel.hidden) { close(); btn.focus(); } });
  }

  // ── Move the portal's bell into the banner ───────────────────────────
  var slot = document.getElementById('empTopBell');
  var bell = document.getElementById('notifBell');
  if (slot && bell) {
    slot.appendChild(bell);
    slot.classList.add('has-bell');
  }
})();
