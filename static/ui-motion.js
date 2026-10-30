/* ==========================================================================
   HRzest - Motion & depth (3D) -- pairs with static/ui-motion.css

   Loaded in <head> (no defer) so html.m-js is set before first paint; the
   rest waits for DOMContentLoaded.

     1. 3D entrance   cards / sections rise into place, staggered, when they
                      first scroll into view (also when a hidden employee-
                      portal view is shown -- IntersectionObserver fires then)
     2. 3D tilt       stat tiles, module tiles, login card, landing preview
                      tilt toward the pointer with a soft glare (mouse/pen only)
     3. Count-up      numeric stat values count up from 0 once visible
     4. Scenes        landing hero 3D backdrop + layered preview cards;
                      login / sign-up floating glass backdrop

   Only classes, decorative empty elements and CSS variables are added --
   no content changes, no inline style="" attributes (CSP-safe). Everything
   is skipped for prefers-reduced-motion.
   ========================================================================== */

(function () {
  var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (reduce) {
    // no motion, but the sidebar collapse button must still work
    ready(function () { try { setupSidebarCollapse(); } catch (e) {} });
    return;
  }
  document.documentElement.classList.add('m-js');

  var RISE = [
    '.stat-card', '.main .section', '.main .card', '.module-card', '.chapter', '.pricing-card',
    '.faq-item', '.contact-info-card', '.contact-form-box', '.feedback-card', '.cta-banner',
    '.sandbox-window', '.section-header', '.al-card', '.demo-card', '.payslip-card',
    '.app-content .card', '.hero', '.cards-row > *', '.container > .card',
    '.auth-split > .card', '.auth-split > *:not(.auth-promo) .card', '.auth-promo'
  ].join(',');

  var TILT = [
    '.stat-card', '.module-card', '.hero-widget', '.auth-split .card', '.pricing-card',
    '.chapter-panel', '.main .section a > [class*="u-bd-1d5-solid-"]', '.payslip-card'
  ].join(',');

  var COUNT = '.stat-val, .stat-num, .stat-card .num, .metric-value, .stat-value';

  function ready(fn) {
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', fn);
    else fn();
  }

  // ── 1. 3D entrance ───────────────────────────────────────────────────
  function setupRise() {
    var all = Array.prototype.slice.call(document.querySelectorAll(RISE));
    // skip elements nested inside another animated element (no double motion)
    var targets = all.filter(function (el) {
      for (var p = el.parentElement; p; p = p.parentElement) if (all.indexOf(p) !== -1) return false;
      return !el.closest('.modal-backdrop, .admin-search-results, [role="dialog"]');
    });
    if (!targets.length) return;
    targets.forEach(function (el) { el.classList.add('m-rise'); });
    if (!('IntersectionObserver' in window)) { targets.forEach(function (el) { el.classList.add('m-in'); }); return; }

    var batch = 0, batchTimer = null;
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (!e.isIntersecting) return;
        e.target.style.setProperty('--m-delay', Math.min(batch, 10) * 70 + 'ms');
        batch++;
        e.target.classList.add('m-in');
        io.unobserve(e.target);
      });
      clearTimeout(batchTimer);
      batchTimer = setTimeout(function () { batch = 0; }, 120);
    }, {threshold: 0.06, rootMargin: '0px 0px -4% 0px'});
    targets.forEach(function (el) { io.observe(el); });
  }

  // ── 2. 3D tilt ───────────────────────────────────────────────────────
  function setupTilt() {
    if (!window.matchMedia('(hover: hover) and (pointer: fine)').matches) return;
    document.querySelectorAll(TILT).forEach(function (el) {
      if (el.classList.contains('m-tilt')) return;
      var big = el.offsetWidth > 520;           // big panels tilt less
      var max = big ? 3 : 7;
      el.classList.add('m-tilt');
      if (getComputedStyle(el).position === 'static') el.classList.add('m-rel');
      var glare = document.createElement('span');
      glare.className = 'm-glare';
      glare.setAttribute('aria-hidden', 'true');
      el.appendChild(glare);
      var raf = 0;
      el.addEventListener('pointermove', function (ev) {
        if (ev.pointerType !== 'mouse' && ev.pointerType !== 'pen') return;
        var r = el.getBoundingClientRect();
        var x = (ev.clientX - r.left) / r.width, y = (ev.clientY - r.top) / r.height;
        cancelAnimationFrame(raf);
        raf = requestAnimationFrame(function () {
          el.classList.add('m-tilting');
          el.style.transform = 'perspective(900px) rotateX(' + ((0.5 - y) * max).toFixed(2) + 'deg) rotateY(' + ((x - 0.5) * max).toFixed(2) + 'deg) translateZ(0)';
          el.style.setProperty('--gx', (x * 100).toFixed(1) + '%');
          el.style.setProperty('--gy', (y * 100).toFixed(1) + '%');
        });
      });
      el.addEventListener('pointerleave', function () {
        cancelAnimationFrame(raf);
        el.classList.remove('m-tilting');
        el.style.transform = '';
      });
    });
  }

  // ── 3. Count-up numbers ──────────────────────────────────────────────
  var NUM = /^(\D*?)(\d[\d,]*)(\.\d+)?(\D*)$/;
  function countUp(el) {
    var text = (el.textContent || '').trim();
    var m = text.match(NUM);
    if (!m || el.children.length) return;
    var prefix = m[1], intPart = m[2], dec = m[3] || '', suffix = m[4];
    var target = parseFloat(intPart.replace(/,/g, '') + dec);
    if (!isFinite(target) || target === 0) return;
    var decimals = dec ? dec.length - 1 : 0, grouped = intPart.indexOf(',') !== -1;
    var start = performance.now(), dur = 900;
    function fmt(v) {
      var s = v.toFixed(decimals);
      if (grouped) { var parts = s.split('.'); parts[0] = Number(parts[0]).toLocaleString('en-IN'); s = parts.join('.'); }
      return prefix + s + suffix;
    }
    (function step(now) {
      var t = Math.min(1, (now - start) / dur), eased = 1 - Math.pow(1 - t, 3);
      el.textContent = fmt(target * eased);
      if (t < 1) requestAnimationFrame(step);
      else el.textContent = text; // end on the exact original text
    })(start);
  }
  function setupCount() {
    var els = document.querySelectorAll(COUNT);
    if (!els.length || !('IntersectionObserver' in window)) return;
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) { if (e.isIntersecting) { countUp(e.target); io.unobserve(e.target); } });
    }, {threshold: 0.4});
    els.forEach(function (el) { io.observe(el); });
  }

  // ── 4. Scenes ────────────────────────────────────────────────────────
  function deco(cls, children) {
    var d = document.createElement('div');
    d.className = cls;
    d.setAttribute('aria-hidden', 'true');
    children.forEach(function (c) { var s = document.createElement('span'); s.className = c; d.appendChild(s); });
    return d;
  }
  function setupScenes() {
    var hero = document.querySelector('.hero-section');
    if (hero && !hero.querySelector('.m-hero3d')) {
      hero.insertBefore(deco('m-hero3d', ['m-mesh a', 'm-mesh b', 'm-mesh c', 'm-floor', 'm-glass g1', 'm-glass g2', 'm-glass g3']), hero.firstChild);
      var widget = hero.querySelector('.hero-widget');
      if (widget) {
        ['m-layer l1', 'm-layer l2'].forEach(function (c) {
          var l = document.createElement('span'); l.className = c; l.setAttribute('aria-hidden', 'true'); widget.appendChild(l);
        });
      }
    }
    if (document.querySelector('.auth-split') && !document.querySelector('.m-auth-bg')) {
      document.body.insertBefore(deco('m-auth-bg', ['m-mesh a', 'm-mesh b', 'm-glass g1', 'm-glass g2', 'm-glass g3', 'm-glass g4']), document.body.firstChild);
    }
  }

  // ── 5. Ripple on buttons ─────────────────────────────────────────────
  var RIPPLE = '.btn, .hbtn, .btn-primary, .scan-btn, .al-btn, .new-co-btn, .mini-btn, .btn-submit, button[type="submit"]';
  function setupRipple() {
    document.addEventListener('pointerdown', function (ev) {
      var host = ev.target.closest && ev.target.closest(RIPPLE);
      if (!host || host.disabled || host.closest('.sidebar')) return;
      host.classList.add('m-ripple-host');
      var r = host.getBoundingClientRect(), size = Math.max(r.width, r.height);
      var dot = document.createElement('span');
      dot.className = 'm-ripple';
      dot.style.width = dot.style.height = size + 'px';
      dot.style.left = (ev.clientX - r.left - size / 2) + 'px';
      dot.style.top = (ev.clientY - r.top - size / 2) + 'px';
      host.appendChild(dot);
      setTimeout(function () { dot.remove(); }, 650);
    }, {passive: true});
  }

  // ── 6. Collapsible admin sidebar ─────────────────────────────────────
  // The collapsed state is restored before first paint by the inline
  // script in admin_base.html's <head> (localStorage key "sbc").
  function setupSidebarCollapse() {
    var tip = null;
    document.querySelectorAll('.sidebar .nav-item').forEach(function (a) {
      var label = (a.textContent || '').replace(/\s+/g, ' ').trim();
      if (!label) return;
      a.setAttribute('aria-label', a.getAttribute('aria-label') || label);
      // icon-rail tooltip: one fixed element, placed beside the hovered icon
      a.addEventListener('mouseenter', function () {
        if (!document.documentElement.classList.contains('sb-collapsed')) return;
        if (!tip) { tip = document.createElement('div'); tip.className = 'm-sbtip'; tip.setAttribute('aria-hidden', 'true'); document.body.appendChild(tip); }
        var r = a.getBoundingClientRect();
        tip.textContent = label;
        tip.style.left = (r.right + 12) + 'px';
        tip.style.top = (r.top + r.height / 2) + 'px';
        tip.classList.add('show');
      });
      a.addEventListener('mouseleave', function () { if (tip) tip.classList.remove('show'); });
    });
    var btn = document.getElementById('sbCollapseBtn');
    if (!btn) return;
    btn.addEventListener('click', function () {
      var on = document.documentElement.classList.toggle('sb-collapsed');
      btn.setAttribute('aria-pressed', on ? 'true' : 'false');
      try { localStorage.setItem('sbc', on ? '1' : '0'); } catch (e) {}
    });
    btn.setAttribute('aria-pressed', document.documentElement.classList.contains('sb-collapsed') ? 'true' : 'false');
  }

  ready(function () {
    try { setupRipple(); } catch (e) {}
    try { setupSidebarCollapse(); } catch (e) {}
    try { setupScenes(); } catch (e) {}
    try { setupRise(); } catch (e) { document.documentElement.classList.remove('m-js'); }
    try { setupTilt(); } catch (e) {}
    try { setupCount(); } catch (e) {}
  });
})();
