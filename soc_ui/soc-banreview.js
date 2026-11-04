/* SOC console -- "Ban review" view (utils/soc_ban_review.py).
 *
 * The WAF, the rate limiter and the honeypots no longer ban by themselves:
 * they queue the address here and an analyst decides -- ban it for a chosen
 * time, dismiss it as a false positive, or trust it (a customer office, a
 * partner's authorised scanner). Same rule as the rest of the console:
 * untrusted data (IPs, reasons, notes) is only ever rendered as text nodes. */
(function () {
  'use strict';
  const { h, fmt, api } = SOC;

  const BAN_DURATIONS = [['15', '15 minutes'], ['60', '1 hour'], ['1440', '24 hours'], ['10080', '7 days'], ['43200', '30 days'], ['0', 'Until lifted']];
  const TRUST_DAYS = [['7', '7 days'], ['30', '30 days'], ['90', '90 days'], ['365', '1 year'], ['0', 'Until revoked']];
  const TABS = [['pending', 'Pending'], ['trusted', 'Trusted'], ['banned', 'Banned'], ['dismissed', 'Dismissed']];
  const SOURCE_LABEL = { waf: 'WAF blocks', ratelimit: 'Rate limit', honeypot: 'Honeypot path', 'honeypot-tcp': 'Decoy port' };
  const SOURCE_CLS = { waf: 'sev-high', ratelimit: 'sev-low', honeypot: 'sev-critical', 'honeypot-tcp': 'sev-critical' };

  const select = (options, value, attrs) => {
    const s = h('select', Object.assign({ class: 'select' }, attrs || {}));
    options.forEach(([v, l]) => s.appendChild(h('option', { value: v, selected: String(v) === String(value) }, l)));
    return s;
  };
  const field = (label, control, id) => h('div', { class: 'field' }, h('label', { for: id }, label), control);
  const when = (ts) => ts ? h('span', { title: ts }, fmt.time(ts), h('div', { class: 'note' }, fmt.ago(ts))) : null;
  const minutesLabel = (m) => {
    if (m == null) return 'Until lifted';
    const hit = BAN_DURATIONS.find(([v]) => Number(v) === Number(m));
    if (hit) return hit[1];
    return m >= 1440 ? Math.round(m / 1440) + ' days' : m >= 60 ? Math.round(m / 60) + ' hours' : m + ' minutes';
  };
  // Closest offered duration at or above the detector's suggestion.
  const suggestedOption = (m) => {
    if (m == null) return '0';
    const opt = BAN_DURATIONS.find(([v]) => v !== '0' && Number(v) >= Number(m));
    return opt ? opt[0] : '0';
  };

  /* pending count on the nav item; the shared pulse doesn't carry it */
  function updateBadge() {
    return api.get('/soc/api/ban-reviews', { status: 'pending' }, true).then((d) => {
      const b = document.getElementById('badge-banreview');
      if (!b) return;
      const n = (d.counts && d.counts.pending) || 0;
      b.hidden = !n; b.textContent = n > 99 ? '99+' : String(n);
    }).catch(() => { /* transient */ });
  }

  function decisionForm(r, done) {
    let action = 'ban';
    const dur = select(BAN_DURATIONS, suggestedOption(r.suggested_minutes), { id: 'brDur' });
    const days = select(TRUST_DAYS, '30', { id: 'brDays' });
    const note = h('input', { class: 'input', id: 'brNote', maxlength: '240', placeholder: 'Optional note for the audit trail (e.g. "customer office, confirmed by phone")' });
    const durRow = field('Ban for', dur, 'brDur');
    const daysRow = field('Trust for', days, 'brDays');
    daysRow.hidden = true;
    const help = h('p', { class: 'note' });
    const submit = h('button', { class: 'btn danger', type: 'submit' }, 'Ban address');
    const HELP = {
      ban: 'Every visitor from this address is refused until the ban ends. If this is a shared office or mobile-network address, real users behind it are blocked too.',
      dismiss: 'False positive: nothing is blocked. A later detection from this address opens a new review.',
      trust: 'Known-good address (customer office, authorised scanner). Later detections only add to its hit count until the trust ends. Attack requests themselves are still rejected by the WAF.'
    };
    const LABEL = { ban: 'Ban address', dismiss: 'Dismiss', trust: 'Trust address' };
    const setAction = (a) => {
      action = a;
      durRow.hidden = a !== 'ban'; daysRow.hidden = a !== 'trust';
      help.textContent = HELP[a];
      submit.textContent = LABEL[a];
      submit.className = a === 'ban' ? 'btn danger' : 'btn';
    };
    setAction('ban');
    return h('form', { class: 'stack', onsubmit: async (e) => {
      e.preventDefault();
      const q = action === 'ban' ? 'Ban ' + r.ip + ' for ' + minutesLabel(dur.value === '0' ? null : dur.value) + '?'
        : action === 'trust' ? 'Trust ' + r.ip + '? Detections from it will stop asking for a ban.'
          : 'Dismiss the review for ' + r.ip + '?';
      if (!(await SOC.confirm(q, { okText: LABEL[action], danger: action === 'ban' }))) return;
      api.post('/soc/api/ban-reviews/decide', { ip: r.ip, action, minutes: Number(dur.value), days: Number(days.value), note: note.value })
        .then(() => { SOC.toast(r.ip + ': ' + LABEL[action].toLowerCase() + ' done', 'good'); SOC.drawer.close(); updateBadge(); SOC.refreshPulse(); if (done) done(); })
        .catch((err) => SOC.toast(err.message, 'bad'));
    } },
      SOC.kv([
        ['Address', h('span', { class: 'mono' }, r.ip)],
        ['Detected by', SOURCE_LABEL[r.source] || r.source],
        ['Latest reason', r.reason],
        ['Hits', String(r.hits)],
        ['First / last seen', fmt.time(r.first_seen) + ' / ' + fmt.time(r.last_seen)],
        ['Suggested ban', minutesLabel(r.suggested_minutes)]
      ]),
      h('p', { class: 'note' }, 'Look up ', SOC.ipLink(r.ip),
        ' first: recent logins from it mean real customers are behind it.'),
      SOC.seg([{ value: 'ban', label: 'Ban' }, { value: 'dismiss', label: 'Dismiss' }, { value: 'trust', label: 'Trust' }], 'ban', setAction),
      help, durRow, daysRow, field('Note', note, 'brNote'), submit);
  }

  SOC.register({
    id: 'banreview', label: 'Ban review', title: 'Ban review', icon: 'alerts',
    async render(root, ctl) {
      const status = TABS.some(([k]) => k === ctl.params.status) ? ctl.params.status : 'pending';
      const d = await api.get('/soc/api/ban-reviews', { status });
      root.textContent = '';
      root.appendChild(h('div', { class: 'tabs', role: 'tablist' }, TABS.map(([k, l]) =>
        h('button', { role: 'tab', 'aria-selected': String(status === k), onclick: () => SOC.go('banreview', { status: k }) },
          l + (d.counts && d.counts[k] ? ' (' + d.counts[k] + ')' : '')))));
      root.appendChild(d.auto_ban
        ? SOC.callout('SOC_AUTO_BAN is on: detections are banned immediately with the suggested duration and listed under Banned. Lift any wrong ones from Response → IP bans.', 'warn')
        : SOC.callout('Repeated WAF blocks, rate-limit breaches and honeypot hits no longer ban an address by themselves. They wait here for your decision. The offending requests themselves are still rejected.'));

      const columns = [
        { label: 'IP', render: (r) => SOC.ipLink(r.ip) },
        { label: 'Source', render: (r) => SOC.chip(SOURCE_LABEL[r.source] || r.source, SOURCE_CLS[r.source] || 'plain') },
        { label: 'Reason', cls: 'clip', key: 'reason' },
        { label: 'Hits', num: true, key: 'hits' },
        { label: 'Last seen', render: (r) => when(r.last_seen) }
      ];
      if (status === 'pending') {
        columns.push({ label: 'Suggested', render: (r) => minutesLabel(r.suggested_minutes) });
      } else {
        columns.push({ label: 'Decided by', key: 'decided_by' }, { label: 'When', render: (r) => when(r.decided_at) },
          { label: 'Note', cls: 'clip', key: 'decision_note' });
        if (status === 'trusted') columns.push({ label: 'Trusted until', render: (r) => r.trusted_until ? fmt.time(r.trusted_until) : 'revoked manually' });
      }
      if (SOC.can('analyst')) {
        columns.push({ label: '', render: (r) => h('button', { class: 'btn small' + (status === 'pending' ? ' danger' : ' ghost'), type: 'button',
          onclick: () => SOC.drawer.open('Decide: ' + r.ip, decisionForm(r, () => SOC.reload())) }, status === 'pending' ? 'Decide' : 'Change') });
      }
      root.appendChild(SOC.table({
        empty: status === 'pending' ? 'Nothing waiting for a decision.' : 'None.',
        rows: d.reviews, columns
      }));
      updateBadge();
    }
  });

  window.addEventListener('load', function () {
    updateBadge();
    setInterval(updateBadge, 60000);
  });
})();
