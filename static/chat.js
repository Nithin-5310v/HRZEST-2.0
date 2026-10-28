// Company <-> platform-team chat renderer -- one shared implementation for
// templates/_admin_chat_widget.html (company admin/HR side) and
// super_admin_dashboard.html's per-tenant chat (platform side). Each page
// keeps its own fetch() calls; this file only draws the thread.
//
// Message text and sender names are typed by the other party, so every node
// is built with textContent -- nothing is ever parsed as HTML. Styling comes
// from static/chat.css classes only: the page CSP blocks style="" attributes
// created at runtime.
(function () {
  var GROUP_GAP_MS = 5 * 60 * 1000;    // a pause this long starts a new run of bubbles

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function icon(name, label) {
    var i = el('i', 'ti ti-' + name);
    i.setAttribute('aria-hidden', 'true');
    var wrap = el('span');
    wrap.appendChild(i);
    if (label) wrap.appendChild(el('span', 'hz-sr', label));
    return wrap;
  }

  function parseTime(iso) {
    var d = iso ? new Date(iso) : null;
    return d && !isNaN(d.getTime()) ? d : null;
  }

  function sameDay(a, b) {
    return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
  }

  function dayLabel(d) {
    var today = new Date();
    var yesterday = new Date(today.getFullYear(), today.getMonth(), today.getDate() - 1);
    if (sameDay(d, today)) return 'Today';
    if (sameDay(d, yesterday)) return 'Yesterday';
    return d.toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' });
  }

  function timeLabel(d) {
    return d.toLocaleTimeString('en-IN', { hour: 'numeric', minute: '2-digit' });
  }

  // status: 'sent' | 'seen' for delivered messages; 'pending' | 'failed' while sending.
  function messageNode(m, mine, first, status) {
    var row = el('div', 'hz-msg ' + (mine ? 'hz-mine' : 'hz-theirs') + (first ? ' hz-first' : ''));
    var bubble = el('div', 'hz-bubble');
    if (!mine && first && m.sender_name) bubble.appendChild(el('div', 'hz-sender', m.sender_name));
    bubble.appendChild(el('span', 'hz-text', m.message));
    var meta = el('span', 'hz-meta');
    var d = parseTime(m.created_at);
    if (status === 'failed') {
      meta.appendChild(icon('alert-circle'));
      meta.appendChild(el('span', '', 'Not sent'));
    } else {
      if (d) meta.appendChild(el('span', '', timeLabel(d)));
      if (status === 'pending') meta.appendChild(icon('clock', 'Sending'));
      else if (status === 'seen') { var s = icon('checks', 'Seen'); s.className = 'hz-seen'; meta.appendChild(s); }
      else if (status === 'sent') meta.appendChild(icon('check', 'Sent'));
    }
    bubble.appendChild(meta);
    row.appendChild(bubble);
    return row;
  }

  function nearBottom(box) {
    return box.scrollHeight - box.scrollTop - box.clientHeight < 80;
  }

  // opts: isMine(m) -> bool; seenByOther(m) -> bool; emptyText; notice {text, href};
  // force -> redraw and jump to the newest message even if nothing changed.
  function render(box, messages, opts) {
    var sig = JSON.stringify(messages.map(function (m) { return [m.id, !!opts.seenByOther(m)]; }));
    if (!opts.force && box._hzSig === sig) return;      // a poll with nothing new: leave the scroll alone
    var stick = opts.force || nearBottom(box);
    var seen = box._hzIds;                              // null on the first draw: nothing animates in
    var frag = document.createDocumentFragment();

    if (opts.notice) {
      var n = el(opts.notice.href ? 'a' : 'div', 'hz-system', opts.notice.text);
      if (opts.notice.href) n.href = opts.notice.href;
      frag.appendChild(n);
    }
    if (!messages.length && opts.emptyText) frag.appendChild(el('div', 'hz-system', opts.emptyText));

    var prev = null;
    var lastDay = null;
    messages.forEach(function (m) {
      var d = parseTime(m.created_at);
      if (d && (!lastDay || !sameDay(d, lastDay))) {
        frag.appendChild(el('div', 'hz-day', dayLabel(d)));
        lastDay = d;
        prev = null;
      }
      var mine = !!opts.isMine(m);
      var first = !prev || prev.mine !== mine || prev.sender !== m.sender_name ||
                  (d && prev.d && d - prev.d > GROUP_GAP_MS);
      var node = messageNode(m, mine, first, mine ? (opts.seenByOther(m) ? 'seen' : 'sent') : null);
      if (seen && !seen[m.id] && !mine) node.className += ' hz-new';   // own messages popped in as pending
      frag.appendChild(node);
      prev = { mine: mine, sender: m.sender_name, d: d };
    });
    (box._hzPending || []).forEach(function (p) { frag.appendChild(p); });

    box._hzSig = sig;
    box._hzIds = {};
    messages.forEach(function (m) { box._hzIds[m.id] = true; });
    box.textContent = '';
    box.appendChild(frag);
    if (stick) box.scrollTop = box.scrollHeight;
  }

  // Shows the message straight away with a clock, the way chat apps do,
  // while the page's own fetch() sends it. done(): the next render() draws
  // the stored copy in its place. fail(): stays in the thread as "Not sent".
  function addPending(box, text) {
    var pending = box._hzPending = (box._hzPending || []).filter(function (p) {
      return p.className.indexOf('hz-failed') < 0;     // a new send clears the old failures
    });
    var node = messageNode({ message: text, created_at: new Date().toISOString() }, true, true, 'pending');
    node.className += ' hz-new';
    pending.push(node);
    box.appendChild(node);
    box.scrollTop = box.scrollHeight;
    return {
      done: function () {
        box._hzPending = box._hzPending.filter(function (p) { return p !== node; });
      },
      fail: function () {
        var failed = messageNode({ message: text }, true, true, 'failed');
        failed.className += ' hz-failed';
        box._hzPending = box._hzPending.map(function (p) { return p === node ? failed : p; });
        if (node.parentNode === box) box.replaceChild(failed, node);
      },
    };
  }

  // Forget the previous thread (the platform side reuses one panel for every company).
  function reset(box) {
    box._hzSig = null;
    box._hzIds = null;
    box._hzPending = [];
    box.textContent = '';
  }

  function autosize(input) {
    input.style.height = 'auto';
    input.style.height = Math.min(input.scrollHeight, 120) + 'px';
  }

  // Enter sends, Shift+Enter starts a new line; the box grows with the text.
  function wireComposer(form, input) {
    input.addEventListener('input', function () { autosize(input); });
    input.addEventListener('keydown', function (e) {
      if (e.key !== 'Enter' || e.shiftKey || e.isComposing) return;
      e.preventDefault();
      if (form.requestSubmit) form.requestSubmit();
      else form.dispatchEvent(new Event('submit', { cancelable: true }));
    });
  }

  function clearComposer(input) {
    input.value = '';
    autosize(input);
  }

  function initials(name) {
    var words = String(name || '').trim().split(/\s+/).filter(Boolean);
    if (!words.length) return '?';
    return (words.length === 1 ? words[0].slice(0, 2) : words[0][0] + words[1][0]).toUpperCase();
  }

  window.HZChat = {
    render: render, addPending: addPending, reset: reset,
    wireComposer: wireComposer, clearComposer: clearComposer, initials: initials,
  };
})();
