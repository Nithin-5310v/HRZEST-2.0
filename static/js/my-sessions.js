/* "Where you're signed in": lists the signed-in person's own sessions
 * (/api/my/sessions, blueprints/my_sessions.py) and ends the ones they pick.
 * Mounted on every [data-my-sessions] element; its data-endpoint is the
 * company-prefixed URL of the API. Builds text nodes only. The page's CSRF
 * script adds the token to these fetch() calls. */
(function () {
  'use strict';

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }
  function ago(seconds) {
    var s = Math.max(0, Math.floor(seconds || 0));
    if (s < 90) return 'just now';
    if (s < 3600) return Math.round(s / 60) + ' min ago';
    var h = Math.floor(s / 3600), m = Math.round((s % 3600) / 60);
    return h + ' h' + (m ? ' ' + m + ' min' : '') + ' ago';
  }

  function mount(box) {
    var base = box.getAttribute('data-endpoint');
    var list = el('div', 'my-sessions-list');
    var status = el('p', 'u-fs-13 u-c-64748b u-m-0');
    box.appendChild(list);
    box.appendChild(status);

    function request(method, url, body) {
      var init = { method: method, credentials: 'same-origin', headers: { 'Accept': 'application/json' } };
      if (body) { init.headers['Content-Type'] = 'application/json'; init.body = JSON.stringify(body); }
      return fetch(url, init).then(function (r) { // audit-ok: both callers (load, end) end the chain in .catch()
        return r.json().catch(function () { return {}; }).then(function (d) {
          if (!r.ok || d.ok === false) throw new Error(d.msg || 'Something went wrong (' + r.status + ').');
          return d;
        });
      });
    }

    function end(body, question) {
      if (!window.confirm(question)) return;
      status.textContent = 'Signing out…';
      request('POST', base + '/end', body).then(function (d) {
        status.textContent = d.ended === 1 ? 'Signed out of 1 session.' : 'Signed out of ' + d.ended + ' sessions.';
        load();
      }).catch(function (e) { status.textContent = e.message; });
    }

    function row(s) {
      var item = el('div', 'my-session u-d-flex u-ai-center u-gap-12 u-fxw-wrap u-p-10-12 u-bd-1-solid-dbeafe u-br-9 u-mb-8');
      var text = el('div', 'u-fx-1 u-minw-200');
      var title = el('div', 'u-fs-14 u-fw-600 u-c-1e293b', s.browser);
      if (s.current) title.appendChild(el('span', 'u-fs-12 u-fw-700 u-c-15803d', '  · This browser'));
      text.appendChild(title);
      text.appendChild(el('div', 'u-fs-12 u-c-64748b',
        'Network ' + s.network + ' · signed in ' + ago(s.started_seconds_ago) + ' · last active ' + ago(s.active_seconds_ago)));
      item.appendChild(text);
      if (!s.current) {
        var btn = el('button', 'btn-test', 'Sign out');
        btn.type = 'button';
        btn.addEventListener('click', function () {
          end({ ref: s.ref }, 'Sign out the session on ' + s.browser + '? It will be signed out on its next click.');
        });
        item.appendChild(btn);
      }
      return item;
    }

    function load() {
      request('GET', base).then(function (d) {
        list.textContent = '';
        d.sessions.forEach(function (s) { list.appendChild(row(s)); });
        var others = d.sessions.filter(function (s) { return !s.current; }).length;
        if (others > 1) {
          var all = el('button', 'btn-test', 'Sign out of all other sessions (' + others + ')');
          all.type = 'button';
          all.addEventListener('click', function () {
            end({ all_others: true }, 'Sign out every session except this one?');
          });
          list.appendChild(all);
        }
        if (!d.sessions.length) list.appendChild(el('p', 'u-fs-13 u-c-64748b u-m-0', 'No other signed-in sessions.'));
      }).catch(function (e) { status.textContent = e.message; });
    }
    load();
  }

  function start() {
    Array.prototype.forEach.call(document.querySelectorAll('[data-my-sessions]'), mount);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
}());
