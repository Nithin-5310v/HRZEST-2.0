/* Left sidebar (templates/admin_base.html): pinned items and drag-to-collapse.

   Pins: hover any menu item for a pin; pinned items move to a PINNED block at
   the top. Nothing is pinned until the person pins it; the list is saved per
   person (POST /api/sidebar_pins).
   Collapse: drag the sidebar's right edge left (or click the round arrow) and it
   shrinks to icons only; drag/click again to open it. Remembered in this browser. */
(function () {
  'use strict';
  var sb = document.querySelector('.sidebar');
  if (!sb) return;
  var root = document.documentElement;
  var KEY = 'hz_sidebar';
  var MIN_W = 68, FULL_W = 248, SNAP_W = 150;
  var wide = window.matchMedia('(min-width: 769px)');

  /* ---------- pins ---------- */
  var links = [].slice.call(sb.querySelectorAll('a.nav-item')).filter(function (a) {
    return a.pathname.indexOf('/super_admin') !== 0;
  });
  if (links.length) {
    var tail = links[links.length - 1].nextSibling;   // where unpinned items go back to the end
    var pinnedLbl = el('div', 'sb-lbl', 'Pinned');
    var pinnedBox = el('div', 'sb-pinned');
    var sep = el('div', 'sb-sep');
    var restLbl = el('div', 'sb-lbl', 'All modules');
    var header = [pinnedLbl, pinnedBox, sep, restLbl];
    var after = sb.firstChild;
    header.forEach(function (n) { sb.insertBefore(n, after); });

    var saved = null;
    try { saved = JSON.parse(sb.getAttribute('data-pins') || 'null'); } catch (e) { saved = null; }
    var pins = Array.isArray(saved) ? saved : [];

    links.forEach(function (a) {
      var name = [].slice.call(a.childNodes).filter(function (n) { return n.nodeType === 3; })
        .map(function (n) { return n.textContent; }).join(' ').replace(/\s+/g, ' ').trim();   // label only, not the count badge
      a.setAttribute('data-label', name);
      var b = el('span', 'nav-pin');
      b.setAttribute('role', 'button'); b.tabIndex = 0;
      b.innerHTML = '<i class="ti ti-pin"></i>';
      b.addEventListener('click', function (e) { e.preventDefault(); e.stopPropagation(); toggle(a); });
      b.addEventListener('keydown', function (e) {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); e.stopPropagation(); toggle(a); }
      });
      a.appendChild(b);
    });

    var toggle = function (a) {
      var i = pins.indexOf(a.pathname);
      if (i >= 0) pins.splice(i, 1); else pins.push(a.pathname);
      layout();
      try {
        fetch('/api/sidebar_pins', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ pins: pins.slice(0, 12) })
        });
      } catch (e) { /* the pin still works for this page view */ }
    };
    var layout = function () {
      var rest = [];
      links.forEach(function (a) {
        var on = pins.indexOf(a.pathname) >= 0;
        a.classList.toggle('is-pinned', on);
        var b = a.querySelector('.nav-pin');
        var nm = a.getAttribute('data-label');
        b.setAttribute('aria-label', (on ? 'Unpin ' : 'Pin ') + nm);
        b.title = on ? 'Unpin' : 'Pin to top';
        if (on) pinnedBox.appendChild(a); else rest.push(a);
      });
      rest.forEach(function (a) {
        var next = null;                              // next unpinned item after this one, in menu order
        for (var i = links.indexOf(a) + 1; i < links.length; i++) {
          if (links[i].parentNode !== pinnedBox) { next = links[i]; break; }
        }
        sb.insertBefore(a, next || tail);
      });
      var has = pinnedBox.children.length > 0;
      pinnedLbl.hidden = pinnedBox.hidden = sep.hidden = !has;
      restLbl.hidden = !has;
    };
    layout();
  }

  /* ---------- drag-to-collapse ---------- */
  var grip = el('div', 'sb-grip');
  grip.setAttribute('aria-hidden', 'true');
  var btn = el('button', 'sb-toggle');
  btn.type = 'button';
  btn.innerHTML = '<i class="ti ti-chevron-left"></i>';
  document.body.appendChild(grip);
  document.body.appendChild(btn);
  var tip = el('div', 'sb-tip');
  tip.hidden = true;
  document.body.appendChild(tip);

  function collapsed() { return root.classList.contains('sb-collapsed'); }
  function place() {
    var r = sb.getBoundingClientRect();
    var top = Math.max(r.top, 0);
    grip.style.left = (r.right - 4) + 'px';
    grip.style.top = top + 'px';
    grip.style.height = (window.innerHeight - top) + 'px';
    btn.style.left = (r.right - 13) + 'px';
    btn.style.top = (top + 20) + 'px';
  }
  function set(icons) {
    root.classList.toggle('sb-collapsed', icons);
    btn.firstChild.className = 'ti ti-chevron-' + (icons ? 'right' : 'left');
    btn.setAttribute('aria-expanded', String(!icons));
    btn.setAttribute('aria-label', icons ? 'Expand the sidebar' : 'Collapse the sidebar to icons');
    try { localStorage.setItem(KEY, icons ? 'icons' : 'full'); } catch (e) { /* private mode */ }
    place();
  }
  set(collapsed());
  btn.addEventListener('click', function () { set(!collapsed()); });
  sb.addEventListener('transitionend', place);
  window.addEventListener('resize', place);
  window.addEventListener('scroll', place, { passive: true });
  place();

  var dragging = false;
  grip.addEventListener('pointerdown', function (e) {
    if (!wide.matches) return;
    dragging = true;
    grip.setPointerCapture(e.pointerId);
    root.classList.add('sb-dragging');
    e.preventDefault();
  });
  grip.addEventListener('pointermove', function (e) {
    if (!dragging) return;
    var w = Math.max(MIN_W, Math.min(FULL_W, e.clientX - sb.getBoundingClientRect().left));
    sb.style.width = w + 'px';
    root.classList.toggle('sb-collapsed', w < SNAP_W);   // preview: labels vanish before you let go
    place();
  });
  function endDrag(e) {
    if (!dragging) return;
    dragging = false;
    var w = sb.getBoundingClientRect().width;
    root.classList.remove('sb-dragging');
    sb.style.width = '';
    set(w < SNAP_W);
    if (e && grip.hasPointerCapture && grip.hasPointerCapture(e.pointerId)) grip.releasePointerCapture(e.pointerId);
  }
  grip.addEventListener('pointerup', endDrag);
  grip.addEventListener('pointercancel', endDrag);

  /* names on hover while only icons show */
  sb.addEventListener('mouseover', function (e) {
    var a = e.target.closest && e.target.closest('.nav-item');
    if (!a || !collapsed() || !wide.matches) { tip.hidden = true; return; }
    var name = a.getAttribute('data-label');
    if (!name) { tip.hidden = true; return; }
    var r = a.getBoundingClientRect();
    tip.textContent = name;
    tip.style.left = (r.right + 10) + 'px';
    tip.style.top = (r.top + r.height / 2) + 'px';
    tip.hidden = false;
  });
  sb.addEventListener('mouseleave', function () { tip.hidden = true; });
  sb.addEventListener('scroll', function () { tip.hidden = true; });

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text) n.textContent = text;
    return n;
  }
})();
