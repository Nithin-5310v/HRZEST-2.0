/* hz-actions.js -- CSP-safe replacement for inline event handlers.
 *
 * Inline handlers (onclick="...") only run under the site's nonce-based CSP
 * if their hashes are listed with 'unsafe-hashes', which security scanners
 * flag (ZAP 10055) because injected markup could reuse any hashed snippet.
 * Templates instead write
 *
 *     <button data-on-click="openEdit('{{ id }}', this)">
 *     <form data-on-submit="return hzConfirmSubmit(event, 'Delete?')">
 *
 * and this script binds a real listener that calls the named global function.
 * The attribute is NOT evaluated as JavaScript: it must be a single call,
 * optionally prefixed with `return`, of a global function or dotted path
 * (HZChat.send) or of a method on the element (this.remove(),
 * this.form.submit()), with arguments limited to:
 *   - string / number / true / false / null / undefined literals
 *   - this, event, and property chains on them (this.value, this.dataset.id,
 *     this.form, event.target), optionally prefixed with + or !
 * `return` + a false result calls preventDefault(), like an inline handler.
 *
 * Elements added later (innerHTML, cloneNode, ...) are bound automatically.
 * app.py injects this file into every HTML page (_inject_csrf_meta).
 */
(function () {
  'use strict';
  if (window.HZActions) return;

  var EVENTS = [
    'click', 'dblclick', 'contextmenu', 'change', 'input', 'submit', 'reset',
    'keydown', 'keyup', 'keypress', 'focus', 'blur', 'focusin', 'focusout',
    'load', 'error', 'mouseover', 'mouseout', 'mouseenter', 'mouseleave',
    'mousedown', 'mouseup', 'dragstart', 'dragover', 'dragenter', 'dragleave',
    'drop', 'paste', 'scroll', 'wheel', 'toggle'
  ];
  var SELECTOR = EVENTS.map(function (e) { return '[data-on-' + e + ']'; }).join(',');
  var BOUND = typeof WeakMap === 'function' ? new WeakMap() : null;
  var cache = Object.create(null);

  /* ---------- parser for the restricted call syntax ---------- */
  function Parser(src) { this.s = src; this.i = 0; }
  Parser.prototype.ws = function () { while (/\s/.test(this.s.charAt(this.i))) this.i++; };
  Parser.prototype.peek = function (str) { return this.s.substr(this.i, str.length) === str; };
  Parser.prototype.eat = function (str) {
    this.ws();
    if (this.peek(str)) { this.i += str.length; return true; }
    return false;
  };
  Parser.prototype.fail = function (msg) {
    throw new Error(msg + ' at ' + this.i + ' in: ' + this.s);
  };
  Parser.prototype.ident = function () {
    this.ws();
    var m = /^[A-Za-z_$][\w$]*/.exec(this.s.slice(this.i));
    if (!m) this.fail('expected name');
    this.i += m[0].length;
    return m[0];
  };
  Parser.prototype.string = function () {
    var q = this.s.charAt(this.i++), out = '';
    var esc = { n: '\n', t: '\t', r: '\r', b: '\b', f: '\f', v: '\v', '0': '\0' };
    while (this.i < this.s.length) {
      var c = this.s.charAt(this.i++);
      if (c === q) return out;
      if (c === '\\') {
        var n = this.s.charAt(this.i++);
        if (n === 'u') {
          out += String.fromCharCode(parseInt(this.s.substr(this.i, 4), 16));
          this.i += 4;
        } else if (n === 'x') {
          out += String.fromCharCode(parseInt(this.s.substr(this.i, 2), 16));
          this.i += 2;
        } else {
          out += Object.prototype.hasOwnProperty.call(esc, n) ? esc[n] : n;
        }
      } else {
        out += c;
      }
    }
    this.fail('unterminated string');
  };
  Parser.prototype.arg = function () {
    this.ws();
    var c = this.s.charAt(this.i);
    if (c === "'" || c === '"') return { lit: this.string() };
    var num = /^-?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?/.exec(this.s.slice(this.i));
    if (num) { this.i += num[0].length; return { lit: Number(num[0]) }; }
    var op = '';
    if (c === '+' || c === '!') { op = c; this.i++; }
    var name = this.ident();
    if (!op) {
      if (name === 'true') return { lit: true };
      if (name === 'false') return { lit: false };
      if (name === 'null') return { lit: null };
      if (name === 'undefined') return { lit: undefined };
    }
    if (name !== 'this' && name !== 'event') this.fail('unsupported argument "' + name + '"');
    var props = [];
    while (this.eat('.')) props.push(this.ident());
    return { ref: name, props: props, op: op };
  };
  Parser.prototype.call = function () {
    var ret = false;
    this.ws();
    if (/^return\s/.test(this.s.slice(this.i))) { this.i += 6; ret = true; }
    var path = [this.ident()];
    while (this.eat('.')) path.push(this.ident());
    if (!this.eat('(')) this.fail('expected (');
    var args = [];
    if (!this.eat(')')) {
      do { args.push(this.arg()); } while (this.eat(','));
      if (!this.eat(')')) this.fail('expected )');
    }
    this.eat(';');
    this.ws();
    if (this.i !== this.s.length) this.fail('unexpected trailing code');
    return { ret: ret, path: path, args: args };
  };

  function parse(src) {
    if (!(src in cache)) cache[src] = new Parser(src).call();
    return cache[src];
  }

  /* ---------- running a parsed call ---------- */
  var BLOCKED = { constructor: 1, __proto__: 1, prototype: 1 };

  function getProp(obj, name) {
    if (BLOCKED[name]) throw new Error('blocked property ' + name);
    return obj == null ? undefined : obj[name];
  }

  function argValue(a, el, ev) {
    if ('lit' in a) return a.lit;
    var v = a.ref === 'this' ? el : ev;
    for (var i = 0; i < a.props.length; i++) v = getProp(v, a.props[i]);
    if (a.op === '+') return +v;
    if (a.op === '!') return !v;
    return v;
  }

  function run(el, type, ev) {
    var src = el.getAttribute('data-on-' + type);
    if (!src) return;
    var call, fn, owner = window;
    try {
      call = parse(src);
      // A path starting with `this` calls a method on the element itself
      // (this.remove(), this.form.submit()); anything else is a global.
      var start = 0;
      fn = window;
      if (call.path[0] === 'this') { fn = owner = el; start = 1; }
      for (var i = start; i < call.path.length; i++) {
        owner = fn;
        fn = getProp(fn, call.path[i]);
      }
    } catch (err) {
      console.error('[hz-actions] ' + err.message);
      return;
    }
    if (typeof fn !== 'function') {
      console.error('[hz-actions] ' + call.path.join('.') + ' is not a function (data-on-' + type + ')');
      return;
    }
    var args = call.args.map(function (a) { return argValue(a, el, ev); });
    var result = fn.apply(owner, args);
    if (call.ret && result === false && ev && ev.preventDefault) ev.preventDefault();
    return result;
  }

  /* ---------- binding ---------- */
  function boundSet(el) {
    if (BOUND) {
      var s = BOUND.get(el);
      if (!s) { s = {}; BOUND.set(el, s); }
      return s;
    }
    return el.__hzBound || (el.__hzBound = {});
  }

  function bind(el) {
    var done = boundSet(el);
    EVENTS.forEach(function (type) {
      if (done[type] || !el.hasAttribute('data-on-' + type)) return;
      done[type] = true;
      var seen = false;
      el.addEventListener(type, function (ev) { seen = true; return run(el, type, ev); });
      // A load/error the element already finished before this script ran
      // (images earlier in the page) would otherwise never reach the handler.
      // `complete` can turn true while the browser's own event is still
      // queued, so wait a task and only stand in if it never arrived.
      if (el.tagName === 'IMG' && el.complete && el.getAttribute('src')) {
        var failed = el.naturalWidth === 0;
        if ((type === 'error' && failed) || (type === 'load' && !failed)) {
          setTimeout(function () { if (!seen) run(el, type, new Event(type)); }, 0);
        }
      }
    });
  }

  /* ---------- data-hover-style="prop:value; prop:value" ----------
   * Hover effects that used onmouseover/onmouseout to set inline styles
   * (often with per-row colors from the template, so not expressible as a
   * shared CSS class). Applied with style.setProperty -- CSSOM writes aren't
   * restricted by CSP -- and the element's previous values are restored on
   * leave. */
  function parseStyle(text) {
    return text.split(';').map(function (decl) {
      var at = decl.indexOf(':');
      if (at < 0) return null;
      var prop = decl.slice(0, at).trim(), val = decl.slice(at + 1).trim();
      var important = /!important$/.test(val);
      if (important) val = val.replace(/\s*!important$/, '');
      return prop ? [prop, val, important ? 'important' : ''] : null;
    }).filter(Boolean);
  }

  function bindHover(el) {
    var done = boundSet(el);
    if (done.hover || !el.hasAttribute('data-hover-style')) return;
    done.hover = true;
    var saved = null;
    el.addEventListener('mouseenter', function () {
      var decls = parseStyle(el.getAttribute('data-hover-style') || '');
      saved = decls.map(function (d) {
        return [d[0], el.style.getPropertyValue(d[0]), el.style.getPropertyPriority(d[0])];
      });
      decls.forEach(function (d) { el.style.setProperty(d[0], d[1], d[2]); });
    });
    el.addEventListener('mouseleave', function () {
      if (!saved) return;
      saved.forEach(function (d) {
        if (d[1]) el.style.setProperty(d[0], d[1], d[2]);
        else el.style.removeProperty(d[0]);
      });
      saved = null;
    });
  }

  function scan(root) {
    if (!root || root.nodeType !== 1 && root.nodeType !== 9 && root.nodeType !== 11) return;
    if (root.nodeType === 1 && root.matches && root.matches('[data-hover-style]')) bindHover(root);
    if (root.querySelectorAll) {
      var hov = root.querySelectorAll('[data-hover-style]');
      for (var h = 0; h < hov.length; h++) bindHover(hov[h]);
    }
    if (root.nodeType === 1 && root.matches && root.matches(SELECTOR)) bind(root);
    if (root.querySelectorAll) {
      var list = root.querySelectorAll(SELECTOR);
      for (var i = 0; i < list.length; i++) bind(list[i]);
    }
  }

  /* ---------- small helpers for one-line handlers ----------
   * Stand-ins for the most common inline snippets, callable from
   * data-on-* attributes (e.g. data-on-click="HZ.click('photoFileInput')"). */
  function byId(id) { return document.getElementById(id); }
  window.HZ = {
    // document.getElementById(id).click() -- open a hidden file input
    click: function (id) { var el = byId(id); if (el) el.click(); },
    // element.style.display = value (default 'none')
    display: function (id, value) {
      var el = byId(id);
      if (el) el.style.display = value === undefined ? 'none' : value;
    },
    // toggle element.style.display between `shown` (default 'block') and none
    toggleDisplay: function (id, shown) {
      var el = byId(id);
      if (el) el.style.display = el.style.display === 'none' ? (shown || 'block') : 'none';
    },
    toggleHidden: function (id) { var el = byId(id); if (el) el.hidden = !el.hidden; },
    addClass: function (id, cls) { var el = byId(id); if (el) el.classList.add(cls); },
    removeClass: function (id, cls) { var el = byId(id); if (el) el.classList.remove(cls); },
    setValue: function (id, value) { var el = byId(id); if (el) el.value = value; },
    // run window[fnName]() only when the click landed on `el` itself, not a
    // child -- closing a modal from its backdrop
    ifSelf: function (ev, el, fnName) {
      if (ev.target === el && typeof window[fnName] === 'function') return window[fnName]();
    },
    // run window[fnName](arg) when Enter is pressed
    onEnter: function (ev, fnName, arg) {
      if (ev.key === 'Enter' && typeof window[fnName] === 'function') return window[fnName](arg);
    },
    // broken avatar image: replace `levels` ancestors' content with text
    // (usually the person's initial)
    avatarText: function (img, text, levels) {
      var host = img;
      for (var i = 0; i < (levels || 1); i++) host = host.parentNode;
      if (host) host.textContent = text;
    },
    // broken image: hide it and show the next sibling (a placeholder)
    showNextOnError: function (img, display) {
      img.style.display = 'none';
      if (img.nextElementSibling) img.nextElementSibling.style.display = display || 'flex';
    },
    // broken avatar image: replace the parent's content with an icon
    // placeholder, <span class="wrapClass"><i class="iconClass"></i></span>
    avatarIcon: function (img, wrapClass, iconClass) {
      var host = img.parentNode;
      if (!host) return;
      var span = document.createElement('span');
      span.className = wrapClass;
      var icon = document.createElement('i');
      icon.className = iconClass;
      icon.setAttribute('aria-hidden', 'true');
      span.appendChild(icon);
      host.replaceChildren(span);
    },
    // disable input `id` while `flag` is true, clearing it if `clear`
    disableIf: function (id, flag, clear) {
      var el = byId(id);
      if (!el) return;
      el.disabled = !!flag;
      if (flag && clear) el.value = '';
    },
    removeParent: function (el) { if (el.parentNode) el.parentNode.remove(); },
    // disable a form's submit button(s) once it is sent (no double submit)
    disableSubmit: function (form) {
      form.querySelectorAll('button[type=submit]').forEach(function (b) { b.disabled = true; });
    }
  };

  scan(document);
  if (typeof MutationObserver === 'function') {
    new MutationObserver(function (records) {
      records.forEach(function (r) {
        if (r.type === 'attributes') { scan(r.target); return; }
        for (var i = 0; i < r.addedNodes.length; i++) scan(r.addedNodes[i]);
      });
    }).observe(document.documentElement, {
      childList: true, subtree: true, attributes: true,
      attributeFilter: EVENTS.map(function (e) { return 'data-on-' + e; }).concat('data-hover-style')
    });
  }

  window.HZActions = { scan: scan, parse: parse, run: run };
})();
