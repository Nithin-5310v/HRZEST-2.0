/* SOC console -- core: DOM builder, API client, router, shell.
 *
 * Security rule for every file in this console: event data (paths, identifiers,
 * user agents, log lines) is attacker-controlled. It is only ever placed in the
 * page as text nodes (h() / textContent). There is no innerHTML and no inline
 * handler anywhere, so a hostile string cannot become markup or script. */
(function () {
  'use strict';

  var body = document.body;
  var store = {
    get: function (k, d) { try { var v = localStorage.getItem('soc.' + k); return v == null ? d : v; } catch (e) { return d; } },
    set: function (k, v) { try { localStorage.setItem('soc.' + k, v); } catch (e) { /* storage unavailable: fine */ } }
  };
  var RANK = { viewer: 1, analyst: 2, admin: 3 };

  var SOC = window.SOC = {
    user: body.dataset.user,
    role: body.dataset.role,
    idle: Number(body.dataset.idle) || 900,
    loki: body.dataset.loki === '1',
    hours: Number(store.get('hours', 24)) || 24,
    views: {},
    skew: 0,
    charts: [],
    timers: [],
    store: store,
    can: function (min) { return RANK[SOC.role] >= RANK[min]; }
  };

  /* ---------- DOM builder ---------- */
  function h(tag, attrs) {
    var el = document.createElement(tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (k) {
        var v = attrs[k];
        if (v == null || v === false) return;
        if (k === 'class') el.className = v;
        else if (k === 'text') el.textContent = v;
        else if (k.slice(0, 2) === 'on' && typeof v === 'function') el.addEventListener(k.slice(2), v);
        else el.setAttribute(k, v === true ? '' : String(v));
      });
    }
    for (var i = 2; i < arguments.length; i++) append(el, arguments[i]);
    return el;
  }
  function append(el, kid) {
    if (kid == null || kid === false) return;
    if (Array.isArray(kid)) { kid.forEach(function (k) { append(el, k); }); return; }
    el.appendChild(kid && kid.nodeType ? kid : document.createTextNode(String(kid)));
  }
  var SVG = 'http://www.w3.org/2000/svg';
  var ICONS = {
    overview: 'M3 12l9-8 9 8M5 10v10h5v-6h4v6h5V10',
    alerts: 'M12 3l9 16H3L12 3zM12 10v4M12 17h.01',
    events: 'M4 6h16M4 12h16M4 18h10',
    entity: 'M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14zM21 21l-5-5',
    analysis: 'M7 3h8l4 4v14H7zM15 3v5h5M10 13h6M10 17h6',
    cases: 'M4 7h16v12H4zM9 7V4h6v3',
    response: 'M12 3l8 3v6c0 4.5-3.2 8.2-8 9-4.8-.8-8-4.5-8-9V6l8-3z',
    tenants: 'M3 21V8l9-5 9 5v13M9 21v-7h6v7',
    fraud: 'M12 2a7 7 0 0 1 7 7c0 5-7 13-7 13S5 14 5 9a7 7 0 0 1 7-7zM12 8v3M12 14h.01',
    detections: 'M12 2v4M12 18v4M2 12h4M18 12h4M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8z',
    integrity: 'M9 12l2 2 4-4M12 3l8 3v6c0 4.5-3.2 8.2-8 9-4.8-.8-8-4.5-8-9V6l8-3z',
    posture: 'M4 20V10M10 20V4M16 20v-7M22 20H2',
    logs: 'M4 5h16v14H4zM8 10l3 2-3 2M13 14h4',
    admin: 'M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM4 21a8 8 0 0 1 16 0'
  };
  function icon(name) {
    var s = document.createElementNS(SVG, 'svg');
    s.setAttribute('width', '18'); s.setAttribute('height', '18'); s.setAttribute('viewBox', '0 0 24 24');
    s.setAttribute('fill', 'none'); s.setAttribute('stroke', 'currentColor'); s.setAttribute('stroke-width', '1.8');
    s.setAttribute('stroke-linecap', 'round'); s.setAttribute('stroke-linejoin', 'round'); s.setAttribute('aria-hidden', 'true');
    var p = document.createElementNS(SVG, 'path'); p.setAttribute('d', ICONS[name] || ICONS.events); s.appendChild(p);
    return s;
  }
  SOC.h = h; SOC.icon = icon;

  /* ---------- formatting ---------- */
  var fmt = SOC.fmt = {
    n: function (v) { return v == null ? '–' : Number(v).toLocaleString(); },
    bytes: function (b) {
      if (b == null) return '–';
      var u = ['B', 'KB', 'MB', 'GB'], i = 0, x = Number(b);
      while (x >= 1024 && i < u.length - 1) { x /= 1024; i++; }
      return (i ? x.toFixed(1) : x) + ' ' + u[i];
    },
    time: function (s) { return s ? String(s).replace('T', ' ').slice(0, 19) : '–'; },
    ago: function (s) {
      if (!s) return '';
      var t = Date.parse(String(s).replace(' ', 'T').slice(0, 19) + 'Z');
      if (isNaN(t)) return '';
      var d = Math.max(0, (Date.now() + SOC.skew - t) / 1000);
      if (d < 60) return Math.floor(d) + 's ago';
      if (d < 3600) return Math.floor(d / 60) + 'm ago';
      if (d < 86400) return Math.floor(d / 3600) + 'h ago';
      return Math.floor(d / 86400) + 'd ago';
    },
    pct: function (a, b) { return b ? Math.round(100 * a / b) + '%' : '0%'; }
  };

  /* ---------- API client ---------- */
  function qs(q) {
    if (!q) return '';
    var parts = [];
    Object.keys(q).forEach(function (k) {
      if (q[k] !== '' && q[k] != null) parts.push(encodeURIComponent(k) + '=' + encodeURIComponent(q[k]));
    });
    return parts.length ? '?' + parts.join('&') : '';
  }
  function csrf() { var m = document.querySelector('meta[name="csrf-token"]'); return m ? m.content : ''; }
  function request(method, url, payload, poll) {
    var init = { method: method, credentials: 'same-origin', headers: { 'Accept': 'application/json' } };
    if (payload instanceof FormData) init.body = payload;
    else if (payload !== undefined) { init.headers['Content-Type'] = 'application/json'; init.body = JSON.stringify(payload); }
    if (method !== 'GET') init.headers['X-CSRF-Token'] = csrf();
    if (poll) init.headers['X-SOC-Poll'] = '1';
    return fetch(url, init).then(function (res) {
      if (res.status === 401) { window.location.href = '/soc/login'; throw new Error('Signed out'); }
      var json = (res.headers.get('content-type') || '').indexOf('json') >= 0;
      return (json ? res.json() : Promise.resolve(null)).then(function (data) {
        if (!res.ok || (data && data.ok === false)) {
          var e = new Error((data && data.msg) || ('Request failed (' + res.status + ')')); e.status = res.status; throw e;
        }
        return data;
      });
    });
  }
  SOC.api = {
    get: function (u, q, poll) { return request('GET', u + qs(q), undefined, poll); },
    post: function (u, b) { return request('POST', u, b === undefined ? {} : b); },
    form: function (u, fd) { return request('POST', u, fd); },
    qs: qs
  };

  /* ---------- small components ---------- */
  var LEVEL_SEV = { CRITICAL: 'critical', ERROR: 'high', WARNING: 'medium', INFO: 'info' };
  SOC.chip = function (text, cls) { return h('span', { class: 'chip ' + (cls || 'plain') }, text); };
  SOC.sev = function (sev) { return SOC.chip(sev, 'sev-' + (sev || 'info')); };
  SOC.level = function (level) { return SOC.sev(LEVEL_SEV[level] || 'info'); };
  SOC.mitre = function (ids) {
    return (ids || []).filter(function (id) { return /^T\d{4}(\.\d{3})?$/.test(id); }).map(function (id) {
      return h('a', { class: 'chip mitre', target: '_blank', rel: 'noopener noreferrer', title: 'MITRE ATT&CK ' + id,
        href: 'https://attack.mitre.org/techniques/' + id.replace('.', '/') + '/' }, id);
    });
  };
  SOC.ipLink = function (ip) {
    return ip ? h('a', { class: 'mono', href: '#/entity?ip=' + encodeURIComponent(ip), title: 'Look up ' + ip }, ip) : h('span', { class: 'muted' }, '–');
  };
  SOC.idLink = function (id) {
    return id && id !== 'None' ? h('a', { href: '#/entity?identifier=' + encodeURIComponent(id), title: 'Look up ' + id }, id) : h('span', { class: 'muted' }, '–');
  };
  SOC.kpi = function (label, value, sub, cls) {
    return h('div', { class: 'card kpi ' + (cls || '') },
      h('div', { class: 'lbl' }, label), h('div', { class: 'val' }, value), sub ? h('div', { class: 'sub' }, sub) : null);
  };
  SOC.card = function (title, content, actions) {
    return h('section', { class: 'card' },
      h('header', null, h('h3', null, title), h('span', { class: 'spacer' }), actions || null), content);
  };
  SOC.empty = function (msg) { return h('div', { class: 'empty' }, msg || 'Nothing to show.'); };
  SOC.skeleton = function () { return h('div', null, h('div', { class: 'skel' }), h('div', { class: 'skel' }), h('div', { class: 'skel' })); };
  SOC.callout = function (msg, kind) { return h('div', { class: 'callout ' + (kind || '') }, msg); };
  SOC.bars = function (items, opts) {
    opts = opts || {};
    var max = Math.max.apply(null, items.map(function (i) { return i.value; }).concat([1]));
    return h('div', { class: 'hbars' }, items.map(function (i) {
      var fill = h('i'); fill.style.width = Math.max(2, Math.round(100 * i.value / max)) + '%';
      return h('div', null, h('span', { class: 'lbl', title: i.label }, i.href ? h('a', { href: i.href }, i.label) : i.label),
        h('span', { class: 'bar ' + (i.cls || opts.cls || '') }, fill), h('span', { class: 'mono' }, fmt.n(i.value)));
    }));
  };
  SOC.table = function (o) {
    if (!o.rows || !o.rows.length) return SOC.empty(o.empty);
    var head = h('tr', null, o.columns.map(function (c) { return h('th', { class: c.num ? 'num' : '' }, c.label); }));
    var rows = o.rows.map(function (r) {
      var tr = h('tr', { class: o.onRow ? 'click' : '', tabindex: o.onRow ? '0' : null });
      o.columns.forEach(function (c) {
        var v = c.render ? c.render(r) : r[c.key];
        tr.appendChild(h('td', { class: (c.num ? 'num ' : '') + (c.cls || '') }, v == null || v === '' ? h('span', { class: 'muted' }, '–') : v));
      });
      if (o.onRow) {
        tr.addEventListener('click', function (e) { if (!e.target.closest('a,button,input,select')) o.onRow(r); });
        tr.addEventListener('keydown', function (e) { if (e.key === 'Enter' && e.target === tr) o.onRow(r); });
      }
      return tr;
    });
    return h('div', { class: 'tbl-wrap' }, h('table', { class: 'tbl' }, h('thead', null, head), h('tbody', null, rows)));
  };
  SOC.seg = function (options, current, onChange) {
    var wrap = h('div', { class: 'seg', role: 'group' });
    options.forEach(function (o) {
      var b = h('button', { type: 'button', 'aria-pressed': String(o.value === current) }, o.label);
      b.addEventListener('click', function () {
        Array.prototype.forEach.call(wrap.children, function (x) { x.setAttribute('aria-pressed', 'false'); });
        b.setAttribute('aria-pressed', 'true'); onChange(o.value);
      });
      wrap.appendChild(b);
    });
    return wrap;
  };
  SOC.kv = function (pairs) {
    var dl = h('dl', { class: 'kv' });
    pairs.forEach(function (p) { if (p) { dl.appendChild(h('dt', null, p[0])); dl.appendChild(h('dd', null, p[1] == null || p[1] === '' ? h('span', { class: 'muted' }, '–') : p[1])); } });
    return dl;
  };
  SOC.pre = function (text) { return h('pre', { class: 'pre' }, text); };
  SOC.copy = function (text) {
    var done = function () { SOC.toast('Copied', 'good'); };
    if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text).then(done, function () { SOC.toast('Copy failed', 'bad'); });
  };
  SOC.download = function (name, text, type) {
    var a = h('a', { href: URL.createObjectURL(new Blob([text], { type: type || 'text/plain' })), download: name });
    document.body.appendChild(a); a.click(); a.remove();
  };

  /* ---------- toast, drawer ---------- */
  SOC.toast = function (msg, kind) {
    var t = h('div', { class: 'toast ' + (kind || ''), role: kind === 'bad' ? 'alert' : 'status' }, msg);
    document.getElementById('toasts').appendChild(t);
    setTimeout(function () { t.remove(); }, kind === 'bad' ? 7000 : 3500);
  };
  var drawerEl = document.getElementById('drawer'), scrim = document.getElementById('scrim'), lastFocus = null;
  SOC.drawer = {
    open: function (title, node) {
      lastFocus = document.activeElement;
      document.getElementById('drawerTitle').textContent = title;
      var b = document.getElementById('drawerBody'); b.textContent = ''; b.appendChild(node);
      drawerEl.hidden = false; scrim.hidden = false;
      document.getElementById('drawerClose').focus();
    },
    close: function () {
      drawerEl.hidden = true; scrim.hidden = true;
      if (lastFocus && lastFocus.focus) lastFocus.focus();
    },
    isOpen: function () { return !drawerEl.hidden; }
  };
  document.getElementById('drawerClose').addEventListener('click', SOC.drawer.close);
  scrim.addEventListener('click', SOC.drawer.close);
  document.addEventListener('keydown', function (e) {
    if (!SOC.drawer.isOpen()) return;
    if (e.key === 'Escape') { SOC.drawer.close(); return; }
    if (e.key === 'Tab') {   // keep focus inside the dialog
      var f = drawerEl.querySelectorAll('a[href],button:not([disabled]),input,select,textarea,[tabindex="0"]');
      if (!f.length) return;
      var first = f[0], last = f[f.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    }
  });

  /* ---------- charts ---------- */
  SOC.cssVar = function (n) { return getComputedStyle(document.documentElement).getPropertyValue(n).trim(); };
  SOC.chart = function (canvas, config) {
    if (!window.Chart) return null;
    window.Chart.defaults.color = SOC.cssVar('--muted');
    window.Chart.defaults.borderColor = SOC.cssVar('--grid');
    config.options = config.options || {};
    config.options.maintainAspectRatio = false;
    var c = new window.Chart(canvas, config);
    SOC.charts.push(c);
    return c;
  };
  function destroyCharts() { SOC.charts.forEach(function (c) { try { c.destroy(); } catch (e) { /* already gone */ } }); SOC.charts = []; }

  /* ---------- router ---------- */
  var current = null, viewRoot = document.getElementById('view');
  function parseHash() {
    var raw = (location.hash || '#/overview').replace(/^#\/?/, '');
    var parts = raw.split('?'), params = {};
    (parts[1] || '').split('&').forEach(function (kv) {
      if (!kv) return;
      var i = kv.indexOf('='), k = decodeURIComponent(kv.slice(0, i < 0 ? undefined : i));
      params[k] = i < 0 ? '' : decodeURIComponent(kv.slice(i + 1).replace(/\+/g, ' '));
    });
    return { name: parts[0] || 'overview', params: params };
  }
  SOC.go = function (name, params) { location.hash = '#/' + name + SOC.api.qs(params); };
  function clearTimers() { SOC.timers.forEach(clearInterval); SOC.timers = []; }
  function route() {
    var r = parseHash(), view = SOC.views[r.name];
    if (!view || !SOC.can(view.min || 'viewer')) { SOC.go('overview'); return; }
    clearTimers(); destroyCharts();
    if (SOC.drawer.isOpen()) SOC.drawer.close();
    current = { view: view, params: r.params };
    document.getElementById('title').textContent = view.title || view.label;
    document.title = (view.title || view.label) + ' · SOC';
    Array.prototype.forEach.call(document.querySelectorAll('#nav a'), function (a) {
      if (a.dataset.view === r.name) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
    });
    document.getElementById('side').classList.remove('open');
    document.getElementById('menuBtn').setAttribute('aria-expanded', 'false');
    viewRoot.textContent = ''; viewRoot.appendChild(SOC.skeleton());
    var ctl = {
      params: r.params,
      poll: function (fn, ms) {
        SOC.timers.push(setInterval(function () { if (!document.hidden) fn(); }, ms));
      }
    };
    Promise.resolve().then(function () { return view.render(viewRoot, ctl); }).then(function () {
      viewRoot.focus({ preventScroll: true });
    }).catch(function (err) {
      viewRoot.textContent = '';
      var retry = h('button', { class: 'btn small', type: 'button' }, 'Retry');
      retry.addEventListener('click', route);
      viewRoot.appendChild(SOC.callout([(err && err.message) || 'Something went wrong. ', ' ', retry], 'bad'));
    });
  }
  SOC.reload = route;
  SOC.register = function (def) { SOC.views[def.id] = def; };

  /* ---------- shell ---------- */
  var GROUPS = [
    ['Monitor', ['overview', 'alerts', 'events']],
    ['Investigate', ['entity', 'analysis', 'logs', 'cases']],
    ['Respond', ['response']],
    ['Assess', ['tenants', 'fraud', 'detections', 'integrity', 'posture']],
    ['Administer', ['admin']]
  ];
  function buildNav() {
    var nav = document.getElementById('nav'); nav.textContent = '';
    GROUPS.forEach(function (g) {
      var items = g[1].filter(function (id) { var v = SOC.views[id]; return v && SOC.can(v.min || 'viewer'); });
      if (!items.length) return;
      nav.appendChild(h('div', { class: 'sep' }, g[0]));
      items.forEach(function (id) {
        var v = SOC.views[id];
        nav.appendChild(h('a', { href: '#/' + id, 'data-view': id }, icon(v.icon || id), h('span', null, v.label), h('span', { class: 'count', id: 'badge-' + id, hidden: true })));
      });
    });
  }
  function setBadge(id, n) {
    var b = document.getElementById('badge-' + id);
    if (!b) return;
    b.hidden = !n; b.textContent = n > 99 ? '99+' : String(n);
  }
  function applyPulse(p) {
    var t = document.getElementById('threat');
    t.className = 'threat ' + p.threat.level;
    t.textContent = p.threat.level;
    t.title = p.threat.reasons.join('. ');
    var ts = Date.parse(String(p.now).replace(' ', 'T').slice(0, 19) + 'Z');
    if (!isNaN(ts)) SOC.skew = ts - Date.now();
    setBadge('alerts', p.alerts_24h); setBadge('cases', p.open_cases);
    SOC.pulse = p;
  }
  SOC.refreshPulse = function (poll) {
    return SOC.api.get('/soc/api/pulse', null, poll).then(applyPulse).catch(function () { /* transient */ });
  };

  function idleWatch() {
    var last = Date.now(), warned = false, bar = document.getElementById('idleWarn');
    ['mousemove', 'keydown', 'click', 'scroll', 'touchstart'].forEach(function (ev) {
      document.addEventListener(ev, function () { if (!warned) last = Date.now(); }, { passive: true });
    });
    document.getElementById('idleStay').addEventListener('click', function () {
      SOC.api.get('/soc/api/me').then(function () { warned = false; last = Date.now(); bar.hidden = true; });
    });
    setInterval(function () {
      var idle = (Date.now() - last) / 1000;
      if (idle >= SOC.idle) { window.location.href = '/soc/login'; }
      else if (idle >= SOC.idle - 60) { warned = true; bar.hidden = false; }
    }, 5000);
  }

  SOC.start = function () {
    buildNav();
    var av = document.getElementById('avatar'); av.textContent = (SOC.user || '?').charAt(0).toUpperCase();
    document.getElementById('whoName').textContent = SOC.user; document.getElementById('whoRole').textContent = SOC.role;
    var sel = document.getElementById('windowSel'); sel.value = String(SOC.hours);
    if (sel.value !== String(SOC.hours)) { SOC.hours = 24; sel.value = '24'; }
    sel.addEventListener('change', function () { SOC.hours = Number(sel.value); store.set('hours', sel.value); route(); });
    document.getElementById('refreshBtn').addEventListener('click', function () { SOC.refreshPulse(); route(); });
    document.getElementById('menuBtn').addEventListener('click', function () {
      var s = document.getElementById('side'); var open = s.classList.toggle('open');
      document.getElementById('menuBtn').setAttribute('aria-expanded', String(open));
    });
    document.getElementById('themeBtn').addEventListener('click', function () {
      var r = document.documentElement, next = r.dataset.theme === 'dark' ? 'light' : 'dark';
      r.dataset.theme = next; store.set('theme', next); route();
    });
    var saved = store.get('theme', null);
    if (saved === 'light' || saved === 'dark') document.documentElement.dataset.theme = saved;
    else if (window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches) document.documentElement.dataset.theme = 'light';
    window.addEventListener('hashchange', route);
    SOC.refreshPulse().then(route);
    setInterval(function () { if (!document.hidden) SOC.refreshPulse(true); }, 60000);
    idleWatch();
  };

  /* views register themselves in the files loaded after this one; start once all are in */
  window.addEventListener('load', function () { SOC.start(); });
})();
