/* SOC console -- views. See soc-core.js for the rule that applies to this whole
 * file: untrusted data is rendered only as text nodes. */
(function () {
  'use strict';
  const { h, fmt, api } = SOC;

  const FAMILIES = [
    ['auth', 'Authentication'], ['access', 'Access control'], ['waf', 'Web application firewall'], ['honeypot', 'Honeypot / deception'],
    ['ratelimit', 'Rate limiting'], ['session', 'Session security'], ['detection', 'Correlation detections'], ['audit', 'Audit integrity'],
    ['data', 'Sensitive data access'], ['privacy', 'Privacy & consent'], ['billing', 'Payments & webhooks'], ['attendance', 'Attendance integrity'],
    ['device', 'Device posture'], ['admin', 'Administrative activity'], ['validation', 'Input validation'], ['app', 'Application health'],
    ['soc', 'SOC console'], ['other', 'Other']
  ];
  const DURATIONS = [['60', '1 hour'], ['1440', '24 hours'], ['10080', '7 days'], ['43200', '30 days'], ['0', 'Until lifted']];
  const LEVELS = ['CRITICAL', 'ERROR', 'WARNING', 'INFO'];

  const select = (options, value, attrs) => {
    const s = h('select', Object.assign({ class: 'select' }, attrs || {}));
    options.forEach(([v, l]) => s.appendChild(h('option', { value: v, selected: String(v) === String(value) }, l)));
    return s;
  };
  const field = (label, control, id) => h('div', { class: 'field' }, h('label', { for: id }, label), control);
  const when = (ts) => ts ? h('span', { title: ts }, fmt.time(ts), h('div', { class: 'note' }, fmt.ago(ts))) : null;
  const act = (p, ok) => p.then((r) => { if (ok) SOC.toast(ok, 'good'); return r; }).catch((e) => { SOC.toast(e.message, 'bad'); throw e; });
  const needs = (role) => SOC.can(role);

  /* ============ shared: event detail drawer, ban form, case form ============ */
  SOC.banForm = function (ip, done) {
    const reason = h('input', { class: 'input', id: 'banReason', maxlength: '240', placeholder: 'Why? (shown in the ban list and audit trail)', required: true });
    const dur = select(DURATIONS, '1440', { id: 'banDur' });
    const form = h('form', { class: 'stack', onsubmit: (e) => {
      e.preventDefault();
      if (!window.confirm('Ban ' + ip + '? Every visitor from this address will be refused.')) return;
      act(api.post('/soc/api/bans', { ip, reason: reason.value, minutes: Number(dur.value) }), ip + ' banned')
        .then(() => { SOC.drawer.close(); SOC.refreshPulse(); if (done) done(); }).catch(() => {});
    } },
      h('p', null, 'Ban ', h('strong', { class: 'mono' }, ip), ' at the application layer. Private, shared and never-ban addresses are refused.'),
      field('Reason', reason, 'banReason'), field('Duration', dur, 'banDur'),
      h('button', { class: 'btn danger', type: 'submit' }, 'Ban address'));
    return form;
  };

  SOC.caseForm = function (seed, done) {
    const title = h('input', { class: 'input', id: 'cTitle', maxlength: '200', value: seed.title || '', required: true });
    const sev = select(['critical', 'high', 'medium', 'low', 'info'].map((s) => [s, s]), seed.severity || 'medium', { id: 'cSev' });
    const sum = h('textarea', { class: 'textarea', id: 'cSum', maxlength: '5000' }, seed.summary || '');
    return h('form', { class: 'stack', onsubmit: (e) => {
      e.preventDefault();
      act(api.post('/soc/api/cases', { title: title.value, severity: sev.value, summary: sum.value, source_type: seed.source_type || 'manual', source_event_id: seed.source_event_id || null }), 'Case saved')
        .then((r) => { SOC.drawer.close(); SOC.refreshPulse(); if (done) done(r.id); else SOC.go('cases'); }).catch(() => {});
    } },
      field('Title', title, 'cTitle'), field('Severity', sev, 'cSev'), field('Summary', sum, 'cSum'),
      h('button', { class: 'btn', type: 'submit' }, 'Create case'));
  };

  const eventRowLite = (e) => h('li', null,
    h('div', { class: 'row' }, SOC.level(e.level), h('a', { href: '#', onclick: (ev) => { ev.preventDefault(); SOC.openEvent(e.id); } }, e.label),
      h('span', { class: 'spacer' }), h('span', { class: 'note' }, fmt.time(e.created_at))),
    e.path ? h('div', { class: 'mono note' }, (e.method || '') + ' ' + e.path) : null);

  SOC.openEvent = function (id) {
    SOC.drawer.open('Event #' + id, SOC.skeleton());
    api.get('/soc/api/events/' + id).then(({ event: e }) => {
      const box = h('div', { class: 'stack' });
      box.appendChild(h('div', { class: 'row' }, SOC.level(e.level), SOC.chip(e.family_label || e.family, 'plain'), SOC.mitre(e.mitre)));
      box.appendChild(h('p', null, e.message));
      box.appendChild(SOC.kv([
        ['Time', fmt.time(e.created_at) + (fmt.ago(e.created_at) ? '  (' + fmt.ago(e.created_at) + ')' : '')],
        ['Event type', h('span', { class: 'mono' }, e.event_type)],
        ['Source IP', e.ip ? [SOC.ipLink(e.ip), e.banned ? SOC.chip(' banned', 'bad') : null, e.protected ? SOC.chip(' shared/private', 'plain') : null] : null],
        ['Account', SOC.idLink(e.identifier)],
        ['Request', e.path ? h('span', { class: 'mono' }, (e.method || '') + ' ' + e.path) : null],
        ['Tenant (from URL)', e.tenant],
        ['Chain hash', e.chain && e.chain.row_hash ? h('span', { class: 'mono', title: e.chain.row_hash }, e.chain.row_hash.slice(0, 16) + '…') : null]
      ]));
      if (e.mitre_detail && e.mitre_detail.length) {
        box.appendChild(SOC.card('ATT&CK', h('ul', { class: 'list' }, e.mitre_detail.map((m) =>
          h('li', null, SOC.mitre([m.id]), ' ', m.name)))));
      }
      if (e.extra && Object.keys(e.extra).length) box.appendChild(SOC.card('Fields', SOC.pre(JSON.stringify(e.extra, null, 2))));

      const actions = h('div', { class: 'row' });
      if (e.ip) {
        actions.appendChild(h('a', { class: 'btn ghost small', href: '#/events?ip=' + encodeURIComponent(e.ip) }, 'Events from this IP'));
        actions.appendChild(h('a', { class: 'btn ghost small', href: '#/entity?ip=' + encodeURIComponent(e.ip) }, 'Profile'));
        if (needs('analyst') && !e.banned && !e.protected) {
          actions.appendChild(h('button', { class: 'btn danger small', type: 'button', onclick: () => SOC.drawer.open('Ban ' + e.ip, SOC.banForm(e.ip, () => SOC.reload())) }, 'Ban IP'));
        }
      }
      if (e.identifier && e.identifier !== 'None') actions.appendChild(h('a', { class: 'btn ghost small', href: '#/events?identifier=' + encodeURIComponent(e.identifier) }, 'Events for account'));
      if (needs('analyst')) {
        const existing = (e.cases || [])[0];
        actions.appendChild(existing
          ? h('a', { class: 'btn ghost small', href: '#/cases?open=' + existing.id }, 'Case #' + existing.id + ' (' + existing.status + ')')
          : h('button', { class: 'btn small', type: 'button', onclick: () => SOC.drawer.open('New case', SOC.caseForm({ title: e.label + (e.ip ? ' from ' + e.ip : ''), severity: e.severity, summary: e.message, source_type: 'event', source_event_id: e.id }, (cid) => SOC.go('cases', { open: cid }))) }, 'Open case'));
      }
      box.appendChild(actions);
      [['Same source IP (±24h)', e.related.same_ip], ['Same account (±24h)', e.related.same_identifier]].forEach(([t, list]) => {
        if (list && list.length) box.appendChild(SOC.card(t, h('ul', { class: 'list' }, list.map(eventRowLite))));
      });
      SOC.drawer.open('Event #' + id, box);
    }).catch((err) => SOC.drawer.open('Event #' + id, SOC.callout(err.message, 'bad')));
  };

  /* ============ overview ============ */
  SOC.register({
    id: 'overview', label: 'Overview', title: 'Overview',
    async render(root, ctl) {
      const draw = async (initial) => {
        const d = await api.get('/soc/api/overview', { hours: SOC.hours }, !initial);
        const k = d.kpi, frag = h('div');
        frag.appendChild(h('div', { class: 'callout ' + (d.threat.level === 'normal' ? '' : d.threat.level === 'elevated' ? 'warn' : 'bad') },
          h('strong', null, 'Threat level: ' + d.threat.level + '. '), d.threat.reasons.join('. ') + '.'));
        frag.appendChild(h('div', { class: 'grid kpis' },
          SOC.kpi('Events', fmt.n(k.events), 'last ' + d.hours + 'h', 'info'),
          SOC.kpi('Critical', fmt.n(k.critical), '', k.critical ? 'critical' : 'ok'),
          SOC.kpi('Errors', fmt.n(k.error), 'page-level', k.error ? 'high' : 'ok'),
          SOC.kpi('Detections', fmt.n(k.detections), 'correlation rules', k.detections ? 'high' : 'ok'),
          SOC.kpi('Failed logins', fmt.n(k.failed_logins), fmt.n(k.lockouts) + ' lockouts', k.failed_logins > 50 ? 'medium' : 'info'),
          SOC.kpi('WAF blocks', fmt.n(k.waf_blocks), '', k.waf_blocks ? 'medium' : 'ok'),
          SOC.kpi('Honeypot hits', fmt.n(k.honeypot_hits), '', k.honeypot_hits ? 'medium' : 'ok'),
          SOC.kpi('Unique IPs', fmt.n(k.unique_ips), 'in events', 'info'),
          SOC.kpi('Active bans', fmt.n(d.active_bans), '', 'info'),
          SOC.kpi('Open cases', fmt.n(d.cases.open), '', d.cases.open ? 'medium' : 'ok')));

        const canvas = h('canvas', { role: 'img', 'aria-label': 'Events over time by severity' });
        frag.appendChild(h('div', { class: 'grid cols-21' },
          SOC.card('Events over time', h('div', { class: 'chart' }, canvas)),
          SOC.card('By area', d.families.length ? SOC.bars(d.families.map((f) => ({ label: f.label, value: f.count, href: '#/events?family=' + f.family }))) : SOC.empty('No events in this window.'))));

        const ipTable = SOC.table({
          empty: 'No source addresses in this window.', rows: d.top_ips, onRow: (r) => SOC.go('entity', { ip: r.ip }),
          columns: [
            { label: 'Source IP', render: (r) => SOC.ipLink(r.ip) },
            { label: 'Events', num: true, key: 'events' }, { label: 'High', num: true, key: 'high' },
            { label: 'Kinds', num: true, key: 'types' }, { label: 'Last', render: (r) => when(r.last) },
            { label: '', render: (r) => r.banned ? SOC.chip('banned', 'bad') : r.protected ? SOC.chip('shared', 'plain')
              : (needs('analyst') ? h('button', { class: 'btn danger small', type: 'button', onclick: () => SOC.drawer.open('Ban ' + r.ip, SOC.banForm(r.ip, () => SOC.reload())) }, 'Ban') : null) }
          ]
        });
        const idTable = SOC.table({
          empty: 'No accounts in this window.', rows: d.top_identifiers, onRow: (r) => SOC.go('entity', { identifier: r.identifier }),
          columns: [{ label: 'Account', render: (r) => SOC.idLink(r.identifier) }, { label: 'Events', num: true, key: 'events' },
            { label: 'IPs', num: true, key: 'ips' }, { label: 'Last', render: (r) => when(r.last) }]
        });
        frag.appendChild(h('div', { class: 'grid cols-2' }, SOC.card('Most active sources', ipTable), SOC.card('Most active accounts', idTable)));

        const alerts = d.recent_alerts.length
          ? h('ul', { class: 'list' }, d.recent_alerts.map(eventRowLite)) : SOC.empty('No alerts in this window.');
        const tech = d.techniques.length
          ? h('div', { class: 'row' }, d.techniques.map((t) => h('span', { class: 'row' }, SOC.mitre([t.id]), h('span', { class: 'note' }, t.name + ' ×' + fmt.n(t.count)))))
          : SOC.empty('No ATT&CK-mapped activity.');
        frag.appendChild(h('div', { class: 'grid cols-2' },
          SOC.card('Latest alerts', alerts, h('a', { href: '#/alerts' }, 'All alerts')),
          SOC.card('MITRE ATT&CK techniques observed', tech)));

        root.textContent = ''; root.appendChild(frag);
        const lv = (n) => d.timeline.map((p) => p[n]);
        const label = (t) => (d.bucket === '1 day' ? t.slice(5, 10) : t.slice(5, 16));
        SOC.chart(canvas, {
          type: 'bar',
          data: { labels: d.timeline.map((p) => label(p.t)), datasets: [
            { label: 'Critical', data: lv('CRITICAL'), backgroundColor: SOC.cssVar('--critical') },
            { label: 'Error', data: lv('ERROR'), backgroundColor: SOC.cssVar('--high') },
            { label: 'Warning', data: lv('WARNING'), backgroundColor: SOC.cssVar('--medium') },
            { label: 'Info', data: lv('INFO'), backgroundColor: SOC.cssVar('--info') }] },
          options: { scales: { x: { stacked: true, ticks: { maxTicksLimit: 12 } }, y: { stacked: true, beginAtZero: true, ticks: { precision: 0 } } },
            plugins: { legend: { position: 'bottom' } } }
        });
      };
      await draw(true);
      ctl.poll(() => { if (!SOC.drawer.isOpen()) draw(false).catch(() => {}); }, 30000);
    }
  });

  /* ============ events + alerts ============ */
  function eventsView(id, label, alertsOnly) {
    SOC.register({
      id, label, title: label,
      async render(root, ctl) {
        const p = ctl.params;
        const state = { q: p.q || '', level: p.level || '', family: p.family || '', ip: p.ip || '', identifier: p.identifier || '', tenant: p.tenant || '', event_type: p.event_type || '' };
        const out = h('div'), tableBox = h('div'), more = h('div', { class: 'row' });
        let cursor = null, shown = 0;

        const query = (extra) => Object.assign({}, state, { hours: SOC.hours, alerts: alertsOnly ? '1' : '' }, extra || {});
        const csvLink = needs('analyst') ? h('a', { class: 'btn ghost', href: '/soc/api/events.csv' + api.qs(query()), download: '' }, 'Export CSV') : null;
        const sync = () => {
          history.replaceState(null, '', '#/' + id + api.qs(state));
          if (csvLink) csvLink.href = '/soc/api/events.csv' + api.qs(query());
        };
        const load = async (reset) => {
          if (reset) { cursor = null; shown = 0; tableBox.textContent = ''; tableBox.appendChild(SOC.skeleton()); }
          const d = await api.get('/soc/api/events', query({ cursor }));
          if (reset) tableBox.textContent = '';
          if (reset && !d.events.length) { tableBox.appendChild(SOC.empty('No events match these filters in the selected window.')); more.textContent = ''; return; }
          if (reset || !tableBox.firstChild) tableBox.appendChild(table(d.events));
          else tableBox.querySelector('tbody').append(...rows(d.events));
          shown += d.events.length; cursor = d.next_cursor;
          more.textContent = '';
          more.appendChild(h('span', { class: 'note' }, 'Showing ' + fmt.n(shown) + (cursor ? '+' : '') + ' events'));
          if (cursor) more.appendChild(h('button', { class: 'btn ghost small', type: 'button', onclick: () => load(false).catch((e) => SOC.toast(e.message, 'bad')) }, 'Load more'));
        };
        const cols = [
          { label: 'Time', render: (e) => when(e.created_at) },
          { label: 'Severity', render: (e) => SOC.level(e.level) },
          { label: 'Event', render: (e) => [e.label, h('div', { class: 'mono note' }, e.event_type)] },
          { label: 'Source', render: (e) => SOC.ipLink(e.ip) },
          { label: 'Account', render: (e) => SOC.idLink(e.identifier) },
          { label: 'Request', cls: 'clip mono', render: (e) => e.path ? (e.method || '') + ' ' + e.path : null },
          { label: 'ATT&CK', render: (e) => SOC.mitre(e.mitre.slice(0, 2)) },
          { label: 'Case', render: (e) => e.case ? h('a', { href: '#/cases?open=' + e.case.id }, '#' + e.case.id) : null }
        ];
        const table = (events) => SOC.table({ columns: cols, rows: events, onRow: (e) => SOC.openEvent(e.id) });
        const rows = (events) => Array.from(table(events).querySelectorAll('tbody tr'));

        const qBox = h('input', { class: 'input', type: 'search', 'aria-label': 'Search events', placeholder: 'Search message, account, path, type, fields, or an exact IP…', value: state.q });
        const fam = select([['', 'All areas']].concat(FAMILIES), state.family, { 'aria-label': 'Area' });
        const lvl = select([['', 'All levels']].concat(LEVELS.map((l) => [l, l[0] + l.slice(1).toLowerCase()])), state.level, { 'aria-label': 'Level' });
        const ipBox = h('input', { class: 'input', 'aria-label': 'Source IP', placeholder: 'Source IP', value: state.ip });
        const idBox = h('input', { class: 'input', 'aria-label': 'Account', placeholder: 'Account', value: state.identifier });
        const tnBox = h('input', { class: 'input', 'aria-label': 'Tenant slug', placeholder: 'Tenant', value: state.tenant });
        const apply = () => {
          Object.assign(state, { q: qBox.value.trim(), family: fam.value, level: lvl.value, ip: ipBox.value.trim(), identifier: idBox.value.trim(), tenant: tnBox.value.trim().toLowerCase() });
          sync(); load(true).catch((e) => SOC.toast(e.message, 'bad'));
        };
        const filters = h('form', { class: 'filters', onsubmit: (e) => { e.preventDefault(); apply(); } },
          qBox, fam, lvl, ipBox, idBox, tnBox, h('button', { class: 'btn', type: 'submit' }, 'Search'),
          h('button', { class: 'btn ghost', type: 'button', onclick: () => { [qBox, ipBox, idBox, tnBox].forEach((i) => { i.value = ''; }); fam.value = ''; lvl.value = ''; apply(); } }, 'Clear'),
          csvLink);
        out.append(alertsOnly ? SOC.callout('Alerts are correlation detections plus anything logged at ERROR or CRITICAL (the same bar that pages on-call).') : '',
          filters, tableBox, more);
        root.textContent = ''; root.appendChild(out);
        await load(true);
      }
    });
  }
  eventsView('alerts', 'Alerts', true);
  eventsView('events', 'Events', false);

  /* ============ entity lookup ============ */
  SOC.register({
    id: 'entity', label: 'Lookup', title: 'IP & account lookup', icon: 'entity',
    async render(root, ctl) {
      const p = ctl.params;
      const kind = p.identifier ? 'identifier' : 'ip';
      const value = p.identifier || p.ip || '';
      const kindSel = select([['ip', 'IP address'], ['identifier', 'Account']], kind, { 'aria-label': 'Lookup type' });
      const box = h('input', { class: 'input', 'aria-label': 'Value', placeholder: 'e.g. 45.33.32.156 or alice@example.com', value });
      const days = select([['7', '7 days'], ['30', '30 days'], ['90', '90 days']], p.days || '30', { 'aria-label': 'Look-back' });
      const go = () => { if (box.value.trim()) SOC.go('entity', { [kindSel.value]: box.value.trim(), days: days.value }); };
      const bar = h('form', { class: 'filters', onsubmit: (e) => { e.preventDefault(); go(); } }, kindSel, box, days, h('button', { class: 'btn', type: 'submit' }, 'Look up'));
      root.textContent = ''; root.appendChild(bar);
      if (!value) { root.appendChild(SOC.empty('Enter an IP address or an account identifier to see everything the platform recorded about it.')); return; }
      const d = (await api.get(kind === 'ip' ? '/soc/api/entity/ip/' + encodeURIComponent(value) : '/soc/api/entity/identifier',
        kind === 'ip' ? { days: days.value } : { value, days: days.value })).entity;
      const out = h('div', { class: 'stack' });
      const title = h('div', { class: 'row' }, h('h2', { class: 'mono' }, d.value),
        d.ban ? SOC.chip('banned', 'bad') : null, d.protected ? SOC.chip('shared / private', 'plain') : null);
      const acts = h('div', { class: 'row' });
      if (kind === 'ip' && needs('analyst')) {
        if (d.ban) acts.appendChild(h('button', { class: 'btn ghost small', type: 'button', onclick: () => {
          if (window.confirm('Lift the ban on ' + d.value + '?')) act(api.post('/soc/api/bans/remove', { ip: d.value }), 'Ban lifted').then(() => SOC.reload()).catch(() => {});
        } }, 'Lift ban'));
        else if (!d.protected) acts.appendChild(h('button', { class: 'btn danger small', type: 'button', onclick: () => SOC.drawer.open('Ban ' + d.value, SOC.banForm(d.value, () => SOC.reload())) }, 'Ban IP'));
      }
      if (needs('analyst')) acts.appendChild(h('button', { class: 'btn small', type: 'button', onclick: () => SOC.drawer.open('New case', SOC.caseForm({ title: 'Investigate ' + d.value, severity: d.high_severity ? 'high' : 'medium', summary: d.events + ' events in ' + d.days + ' days', source_type: 'ip' })) }, 'Open case'));
      acts.appendChild(h('a', { class: 'btn ghost small', href: '#/events?' + (kind === 'ip' ? 'ip=' : 'identifier=') + encodeURIComponent(d.value) }, 'All events'));
      out.append(title, acts);
      if (d.ban) out.appendChild(SOC.callout('Banned by ' + d.ban.by + ' on ' + fmt.time(d.ban.at) + ' — ' + (d.ban.reason || 'no reason recorded') + (d.ban.expires ? ' (until ' + fmt.time(d.ban.expires) + ')' : ' (until lifted)'), 'bad'));
      out.appendChild(h('div', { class: 'grid kpis' },
        SOC.kpi('Events', fmt.n(d.events), 'last ' + d.days + ' days', 'info'), SOC.kpi('High severity', fmt.n(d.high_severity), '', d.high_severity ? 'high' : 'ok'),
        SOC.kpi('Kinds of event', fmt.n(d.event_types), '', 'info'), SOC.kpi('First seen', d.first_seen ? fmt.time(d.first_seen).slice(0, 10) : '–', fmt.ago(d.first_seen), 'info'),
        SOC.kpi('Last seen', d.last_seen ? fmt.time(d.last_seen).slice(0, 10) : '–', fmt.ago(d.last_seen), 'info')));
      if (!d.events) { out.appendChild(SOC.empty('The platform has recorded nothing for this value in the window.')); root.appendChild(out); return; }
      out.appendChild(h('div', { class: 'grid cols-2' },
        SOC.card('What it did', SOC.bars(d.types.map((t) => ({ label: t.label, value: t.count, href: '#/events?event_type=' + encodeURIComponent(t.event_type) + '&' + (kind === 'ip' ? 'ip=' : 'identifier=') + encodeURIComponent(d.value) })))),
        SOC.card(kind === 'ip' ? 'Accounts it touched' : 'Addresses it used', d.other.length
          ? SOC.table({ rows: d.other, columns: [{ label: kind === 'ip' ? 'Account' : 'IP', render: (r) => kind === 'ip' ? SOC.idLink(r.value) : SOC.ipLink(r.value) }, { label: 'Events', num: true, key: 'count' }] })
          : SOC.empty('None recorded.'))));
      out.appendChild(h('div', { class: 'grid cols-2' },
        SOC.card('Paths requested', d.paths.length ? SOC.table({ rows: d.paths, columns: [{ label: 'Path', cls: 'mono clip', key: 'path' }, { label: 'Hits', num: true, key: 'count' }] }) : SOC.empty('None recorded.')),
        SOC.card('ATT&CK techniques', d.techniques.length ? h('ul', { class: 'list' }, d.techniques.map((t) => h('li', null, SOC.mitre([t.id]), ' ', t.name))) : SOC.empty('No ATT&CK-mapped activity.'))));
      out.appendChild(SOC.card('Recent events', SOC.table({
        rows: d.recent, onRow: (e) => SOC.openEvent(e.id),
        columns: [{ label: 'Time', render: (e) => when(e.created_at) }, { label: 'Severity', render: (e) => SOC.level(e.level) }, { label: 'Event', key: 'label' },
          { label: 'Request', cls: 'mono clip', render: (e) => e.path ? (e.method || '') + ' ' + e.path : null }]
      })));
      root.appendChild(out);
    }
  });

  /* ============ cases ============ */
  function openCase(id) {
    SOC.drawer.open('Case #' + id, SOC.skeleton());
    api.get('/soc/api/cases/' + id).then(({ case: c }) => {
      const box = h('div', { class: 'stack' });
      const status = select([['open', 'Open'], ['investigating', 'Investigating'], ['contained', 'Contained'], ['resolved', 'Resolved'], ['false_positive', 'False positive']], c.status, { id: 'cStatus' });
      const sev = select(['critical', 'high', 'medium', 'low', 'info'].map((s) => [s, s]), c.severity, { id: 'cSev2' });
      const who = h('input', { class: 'input', id: 'cWho', value: c.assignee || '', maxlength: '60' });
      const res = h('textarea', { class: 'textarea', id: 'cRes', maxlength: '5000' }, c.resolution || '');
      const editable = needs('analyst');
      [status, sev, who, res].forEach((x) => { x.disabled = !editable; });
      box.appendChild(h('div', { class: 'row' }, h('h3', null, c.title), SOC.sev(c.severity)));
      if (c.summary) box.appendChild(h('p', null, c.summary));
      box.appendChild(SOC.kv([['Created', fmt.time(c.created_at) + ' by ' + c.created_by], ['Updated', fmt.time(c.updated_at)], ['Source', c.source_type + (c.source_event_id ? ' · event #' + c.source_event_id : '')]]));
      if (c.source_event_id) box.appendChild(h('button', { class: 'btn ghost small', type: 'button', onclick: () => SOC.openEvent(c.source_event_id) }, 'View source event'));
      const form = h('form', { class: 'stack', onsubmit: (e) => {
        e.preventDefault();
        act(api.post('/soc/api/cases/' + id, { status: status.value, severity: sev.value, assignee: who.value, resolution: res.value }), 'Case updated')
          .then(() => { SOC.refreshPulse(); openCase(id); }).catch(() => {});
      } }, field('Status', status, 'cStatus'), field('Severity', sev, 'cSev2'), field('Assignee', who, 'cWho'), field('Resolution / findings', res, 'cRes'),
        editable ? h('button', { class: 'btn', type: 'submit' }, 'Save changes') : null);
      box.appendChild(form);
      const note = h('textarea', { class: 'textarea', id: 'cNote', maxlength: '5000', placeholder: 'Add an investigation note…' });
      box.appendChild(SOC.card('Timeline', h('div', { class: 'stack' },
        c.notes.length ? h('ul', { class: 'list' }, c.notes.map((n) => h('li', null, h('div', { class: 'note' }, n.author + ' · ' + fmt.time(n.created_at)), h('div', null, n.body)))) : SOC.empty('No notes yet.'),
        editable ? h('form', { class: 'stack', onsubmit: (e) => {
          e.preventDefault();
          act(api.post('/soc/api/cases/' + id + '/notes', { body: note.value }), 'Note added').then(() => openCase(id)).catch(() => {});
        } }, note, h('button', { class: 'btn small', type: 'submit' }, 'Add note')) : null)));
      SOC.drawer.open('Case #' + id, box);
    }).catch((err) => SOC.drawer.open('Case #' + id, SOC.callout(err.message, 'bad')));
  }
  SOC.register({
    id: 'cases', label: 'Cases', title: 'Cases',
    async render(root, ctl) {
      const mode = ctl.params.status || 'open';
      const d = await api.get('/soc/api/cases', { status: mode === 'all' ? '' : mode });
      const top = h('div', { class: 'row' },
        SOC.seg([{ value: 'open', label: 'Open' }, { value: 'resolved', label: 'Resolved' }, { value: 'false_positive', label: 'False positive' }, { value: 'all', label: 'All' }], mode, (v) => SOC.go('cases', { status: v })),
        h('span', { class: 'spacer' }),
        needs('analyst') ? h('button', { class: 'btn', type: 'button', onclick: () => SOC.drawer.open('New case', SOC.caseForm({}, (cid) => openCase(cid))) }, 'New case') : null);
      root.textContent = '';
      root.append(top, h('div', { class: 'stack' }), SOC.table({
        empty: 'No cases here. Open one from an alert, event or lookup.', rows: d.cases, onRow: (c) => openCase(c.id),
        columns: [{ label: '#', key: 'id' }, { label: 'Title', cls: 'clip', key: 'title' }, { label: 'Severity', render: (c) => SOC.sev(c.severity) },
          { label: 'Status', render: (c) => SOC.chip(c.status.replace('_', ' '), 'plain') }, { label: 'Assignee', key: 'assignee' },
          { label: 'Updated', render: (c) => when(c.updated_at) }, { label: 'Source', render: (c) => c.source_type + (c.source_event_id ? ' #' + c.source_event_id : '') }]
      }));
      if (ctl.params.open) openCase(Number(ctl.params.open));
    }
  });

  /* ============ response: bans + risky sessions ============ */
  SOC.register({
    id: 'response', label: 'Response', title: 'Response actions',
    async render(root, ctl) {
      const tab = ctl.params.tab || 'bans';
      const tabs = h('div', { class: 'tabs', role: 'tablist' }, [['bans', 'IP bans'], ['sessions', 'Risky sessions']].map(([k, l]) =>
        h('button', { role: 'tab', 'aria-selected': String(tab === k), onclick: () => SOC.go('response', { tab: k }) }, l)));
      root.textContent = ''; root.appendChild(tabs);
      if (tab === 'sessions') {
        const d = await api.get('/soc/api/sessions');
        root.appendChild(SOC.callout('Sessions the risk engine has scored or flagged, across tenants. Revoking marks a session compromised; the app refuses it on its very next request.'));
        root.appendChild(SOC.table({
          empty: 'No risky sessions right now.', rows: d.sessions,
          columns: [{ label: 'Tenant', key: 'company' }, { label: 'Account', render: (s) => SOC.idLink(s.identifier) }, { label: 'Type', key: 'type' },
            { label: 'Score', num: true, key: 'score' }, { label: 'Status', render: (s) => SOC.chip(s.status, s.status === 'compromised' ? 'bad' : 'plain') },
            { label: 'Reason', cls: 'clip', key: 'reason' }, { label: 'Updated', render: (s) => when(s.updated_at) },
            { label: '', render: (s) => needs('analyst') && s.status !== 'compromised' && s.sid ? h('button', { class: 'btn danger small', type: 'button', onclick: () => {
              if (window.confirm('Revoke this session for ' + s.identifier + '?')) act(api.post('/soc/api/sessions/revoke', { tenant_id: s.tenant_id, sid: s.sid }), 'Session revoked').then(() => SOC.reload()).catch(() => {});
            } }, 'Revoke') : null }]
        }));
        if (d.errors && d.errors.length) root.appendChild(h('p', { class: 'note' }, d.errors.length + ' tenant(s) could not be read.'));
        return;
      }
      const d = await api.get('/soc/api/bans');
      if (needs('analyst')) {
        const ip = h('input', { class: 'input', id: 'nbIp', placeholder: '203.0.113.9', required: true, 'aria-label': 'IP address' });
        const why = h('input', { class: 'input', id: 'nbWhy', placeholder: 'Reason', required: true, maxlength: '240', 'aria-label': 'Reason' });
        const dur = select(DURATIONS, '1440', { 'aria-label': 'Duration' });
        root.appendChild(h('form', { class: 'filters', onsubmit: (e) => {
          e.preventDefault();
          if (!window.confirm('Ban ' + ip.value.trim() + '?')) return;
          act(api.post('/soc/api/bans', { ip: ip.value.trim(), reason: why.value, minutes: Number(dur.value) }), 'Banned').then(() => SOC.reload()).catch(() => {});
        } }, ip, why, dur, h('button', { class: 'btn danger', type: 'submit' }, 'Ban')));
      }
      root.appendChild(SOC.table({
        empty: 'No bans recorded.', rows: d.bans,
        columns: [{ label: 'IP', render: (b) => SOC.ipLink(b.ip) }, { label: 'Status', render: (b) => SOC.chip(b.active ? 'active' : 'expired', b.active ? 'bad' : 'plain') },
          { label: 'Reason', cls: 'clip', key: 'reason' }, { label: 'By', key: 'by' }, { label: 'Since', render: (b) => when(b.at) },
          { label: 'Until', render: (b) => b.expires ? fmt.time(b.expires) : 'lifted manually' },
          { label: '', render: (b) => needs('analyst') && b.active ? h('button', { class: 'btn ghost small', type: 'button', onclick: () => {
            if (window.confirm('Lift the ban on ' + b.ip + '?')) act(api.post('/soc/api/bans/remove', { ip: b.ip }), 'Ban lifted').then(() => SOC.reload()).catch(() => {});
          } }, 'Lift') : null }]
      }));
    }
  });

  /* ============ tenants ============ */
  SOC.register({
    id: 'tenants', label: 'Tenants', title: 'Tenant risk',
    async render(root) {
      const d = await api.get('/soc/api/tenants', { hours: SOC.hours });
      root.textContent = '';
      root.append(SOC.callout(d.note), h('div', { class: 'stack' }), SOC.table({
        empty: 'No tenants.', rows: d.tenants,
        columns: [{ label: 'Company', key: 'company' }, { label: 'Slug', render: (t) => h('a', { class: 'mono', href: '#/events?tenant=' + encodeURIComponent(t.slug) }, t.slug) },
          { label: 'Status', render: (t) => SOC.chip(t.status, t.status === 'active' ? 'ok' : 'bad') }, { label: 'Billing', key: 'billing_state' },
          { label: 'Events', num: true, key: 'events' }, { label: 'Alerts', num: true, key: 'alerts' }, { label: 'Dup. signups', num: true, key: 'duplicate_alerts' },
          { label: 'Risk', render: (t) => { const f = h('i'); f.style.width = Math.max(2, t.risk) + '%'; return h('div', { class: 'row' }, h('span', { class: 'bar ' + (t.risk >= 60 ? 'crit' : t.risk >= 30 ? 'warn' : '') }, f), h('span', { class: 'mono' }, t.risk)); } }]
      }));
    }
  });

  /* ============ attendance integrity (fraud) ============ */
  SOC.register({
    id: 'fraud', label: 'Attendance fraud', title: 'Attendance integrity', icon: 'fraud',
    async render(root, ctl) {
      const days = ctl.params.days || '7';
      const d = await api.get('/soc/api/attendance', { days });
      const t = d.totals;
      root.textContent = '';
      root.append(SOC.seg([{ value: '1', label: '24 h' }, { value: '7', label: '7 days' }, { value: '30', label: '30 days' }], days, (v) => SOC.go('fraud', { days: v })),
        h('div', { class: 'stack' }),
        h('div', { class: 'grid kpis' }, SOC.kpi('Punches', fmt.n(t.punches), '', 'info'), SOC.kpi('Rejected', fmt.n(t.rejected), fmt.pct(t.rejected, t.punches), t.rejected ? 'high' : 'ok'),
          SOC.kpi('Mock location', fmt.n(t.mock_location), '', t.mock_location ? 'medium' : 'ok'), SOC.kpi('Flagged', fmt.n(t.flagged), fmt.pct(t.flagged, t.punches), t.flagged ? 'medium' : 'ok')),
        h('div', { class: 'grid cols-21' },
          SOC.card('By tenant', SOC.table({ empty: 'No punch evidence in this window.', rows: d.tenants,
            columns: [{ label: 'Company', key: 'company' }, { label: 'Punches', num: true, key: 'punches' }, { label: 'Rejected', num: true, key: 'rejected' },
              { label: 'Mock GPS', num: true, key: 'mock_location' }, { label: 'Flagged', num: true, key: 'flagged' }, { label: 'Liveness', num: true, key: 'liveness_issues' }] })),
          SOC.card('Why punches were flagged', d.flags.length ? SOC.bars(d.flags.map((f) => ({ label: f.flag, value: f.count }))) : SOC.empty('No flags.'))));
      if (d.errors && d.errors.length) root.appendChild(h('p', { class: 'note' }, d.errors.length + ' tenant(s) could not be read.'));
    }
  });

  /* ============ detections ============ */
  SOC.register({
    id: 'detections', label: 'Detections', title: 'Detection rules',
    async render(root) {
      const d = await api.get('/soc/api/detections', { hours: Math.max(SOC.hours, 24) });
      root.textContent = '';
      root.append(SOC.card('Rule catalogue', SOC.table({
        rows: d.rules,
        columns: [{ label: 'Rule', render: (r) => [h('strong', null, r.label), h('div', { class: 'note' }, r.meaning)] }, { label: 'Level', render: (r) => SOC.level(r.level) },
          { label: 'Scope', key: 'scope' }, { label: 'Cooldown', render: (r) => r.cooldown_minutes + ' min' },
          { label: 'Thresholds', render: (r) => r.thresholds.length ? h('span', { class: 'mono' }, r.thresholds.join(' / ')) : null },
          { label: 'ATT&CK', render: (r) => SOC.mitre(r.mitre.map((m) => m.id)) },
          { label: 'Last fired', render: (r) => r.last_fired ? when(r.last_fired) : h('span', { class: 'muted' }, 'never') }, { label: 'Times', num: true, key: 'times_fired' }]
      })), h('div', { class: 'stack' }), SOC.card('Recent firings (' + d.hours + ' h)', SOC.table({
        empty: 'No detections fired in this window.', rows: d.events, onRow: (e) => SOC.openEvent(e.id),
        columns: [{ label: 'Time', render: (e) => when(e.created_at) }, { label: 'Rule', key: 'label' }, { label: 'Severity', render: (e) => SOC.level(e.level) }, { label: 'Detail', cls: 'clip', key: 'message' },
          { label: 'Key', render: (e) => e.identifier ? SOC.idLink(e.identifier) : null }]
      })));
    }
  });

  /* ============ audit integrity ============ */
  SOC.register({
    id: 'integrity', label: 'Audit integrity', title: 'Audit-log integrity',
    async render(root) {
      const last = await api.get('/soc/api/integrity');
      const out = h('div', { class: 'stack' });
      const result = h('div');
      const show = (report, at, by) => {
        result.textContent = '';
        if (!report) { result.appendChild(SOC.empty('Not verified since this server started.')); return; }
        const ok = report.ok === true;
        result.appendChild(h('div', { class: 'callout ' + (ok ? '' : 'bad') }, h('strong', null, ok ? 'Chain verified. ' : 'Chain problem detected. '), 'Checked ' + fmt.time(at) + (by ? ' by ' + by : '') + '.'));
        if (!ok) result.appendChild(SOC.callout('Treat this as a possible tamper. Preserve the database, compare the head with the off-box anchors in Loki, and open a case.', 'bad'));
        result.appendChild(SOC.kv(Object.keys(report).filter((k) => typeof report[k] !== 'object' || report[k] === null).map((k) => [k.replace(/_/g, ' '), String(report[k])])));
        Object.keys(report).filter((k) => Array.isArray(report[k]) && report[k].length).forEach((k) => {
          result.appendChild(SOC.card(k.replace(/_/g, ' '), SOC.pre(JSON.stringify(report[k], null, 2))));
        });
      };
      out.append(SOC.callout('Every security event is hash-chained at insert time (migrations/audit_integrity.py). Verification recomputes the chain end to end; any edited, deleted or reordered row breaks it. Anchors are shipped off-box so a database owner cannot rewrite history unnoticed.'),
        needs('analyst') ? h('button', { class: 'btn', type: 'button', id: 'runVerify', onclick: (e) => {
          const b = e.currentTarget; b.disabled = true; b.textContent = 'Verifying…';
          act(api.post('/soc/api/integrity/verify'), 'Verification finished').then((r) => show(r.report, r.at, r.by)).catch(() => {}).finally(() => { b.disabled = false; b.textContent = 'Verify now'; });
        } }, 'Verify now') : null, result);
      show(last.report, last.at, last.by);
      root.textContent = ''; root.appendChild(out);
    }
  });

  /* ============ posture ============ */
  SOC.register({
    id: 'posture', label: 'Posture', title: 'Security posture',
    async render(root) {
      const d = await api.get('/soc/api/posture');
      const s = d.summary;
      root.textContent = '';
      root.append(h('div', { class: 'grid kpis' }, SOC.kpi('Failing', s.fail, '', s.fail ? 'critical' : 'ok'), SOC.kpi('Needs attention', s.warn, '', s.warn ? 'medium' : 'ok'),
        SOC.kpi('Passing', s.pass, '', 'ok'), SOC.kpi('Informational', s.info, '', 'info')),
        SOC.callout('Checks read live configuration. They report whether a control is on and where to change it — never a secret or its value.'),
        h('div', { class: 'stack' }), SOC.card('Controls', h('ul', { class: 'list' }, d.checks.map((c) =>
          h('li', null, h('div', { class: 'row' }, SOC.chip(c.status, c.status === 'pass' ? 'ok' : c.status === 'fail' ? 'bad' : 'plain'), h('strong', null, c.title)),
            h('div', null, c.detail), c.fix && c.status !== 'pass' ? h('div', { class: 'note' }, 'Fix: ' + c.fix) : null)))));
    }
  });

  /* ============ Loki logs (optional) ============ */
  SOC.register({
    id: 'logs', label: 'Logs (Loki)', title: 'Log search', icon: 'logs', min: 'analyst',
    async render(root, ctl) {
      root.textContent = '';
      if (!SOC.loki) { root.appendChild(SOC.callout('Loki is not configured. Set LOKI_URL on the server to search shipped application and access logs here.', 'warn')); return; }
      const q = h('input', { class: 'input', 'aria-label': 'LogQL query', value: ctl.params.q || '{job=~".+"} |= "security_event"' });
      const mins = select([['15', '15 min'], ['60', '1 hour'], ['360', '6 hours'], ['1440', '24 hours']], ctl.params.m || '60', { 'aria-label': 'Range' });
      const out = h('div');
      const run = () => {
        out.textContent = ''; out.appendChild(SOC.skeleton());
        api.post('/soc/api/loki/query', { query: q.value, minutes: Number(mins.value), limit: 300 }).then((r) => {
          out.textContent = '';
          if (!r.lines.length) { out.appendChild(SOC.empty('No log lines matched.')); return; }
          out.appendChild(h('div', { class: 'pre' }, r.lines.map((l) => (new Date(l.ts / 1e6).toISOString().slice(11, 19)) + '  ' + l.line).join('\n')));
          out.appendChild(h('p', { class: 'note' }, r.lines.length + ' lines, newest first.'));
        }).catch((e) => { out.textContent = ''; out.appendChild(SOC.callout(e.message, 'bad')); });
      };
      root.append(h('form', { class: 'filters', onsubmit: (e) => { e.preventDefault(); run(); } }, q, mins, h('button', { class: 'btn', type: 'submit' }, 'Run')),
        SOC.callout('LogQL, read-only, last 24 hours at most, 300 lines. Results appear as plain text.'), out);
    }
  });

  /* ============ administration ============ */
  SOC.register({
    id: 'admin', label: 'Users & audit', title: 'Administration', min: 'admin',
    async render(root, ctl) {
      const tab = ctl.params.tab || 'users';
      root.textContent = '';
      root.appendChild(h('div', { class: 'tabs', role: 'tablist' }, [['users', 'SOC users'], ['audit', 'Audit trail']].map(([k, l]) =>
        h('button', { role: 'tab', 'aria-selected': String(tab === k), onclick: () => SOC.go('admin', { tab: k }) }, l))));
      if (tab === 'audit') {
        const rows = [];
        const box = h('div'), more = h('div', { class: 'row' });
        const load = async (before) => {
          const d = await api.get('/soc/api/audit', { before });
          rows.push(...d.entries);
          box.textContent = ''; box.appendChild(SOC.table({ rows, columns: [{ label: 'Time', render: (r) => when(r.at) }, { label: 'Actor', key: 'actor' }, { label: 'Action', key: 'action' }, { label: 'Target', cls: 'clip', key: 'target' }, { label: 'Detail', cls: 'clip', key: 'detail' }, { label: 'IP', render: (r) => SOC.ipLink(r.ip) }] }));
          more.textContent = '';
          if (d.next_before) more.appendChild(h('button', { class: 'btn ghost small', type: 'button', onclick: () => load(d.next_before) }, 'Older'));
        };
        root.append(SOC.callout('Append-only: the database rejects any update or delete of these rows.'), box, more);
        await load();
        return;
      }
      const d = await api.get('/soc/api/users');
      const u = h('input', { class: 'input', id: 'nuName', placeholder: 'username', required: true, 'aria-label': 'Username' });
      const pw = h('input', { class: 'input', id: 'nuPw', type: 'password', placeholder: 'initial password (12+ chars)', required: true, autocomplete: 'new-password', 'aria-label': 'Password' });
      const role = select([['viewer', 'Viewer (read only)'], ['analyst', 'Analyst (triage + respond)'], ['admin', 'Admin']], 'viewer', { 'aria-label': 'Role' });
      const reveal = h('div');
      const showCode = (title, code) => {
        reveal.textContent = '';
        reveal.appendChild(SOC.callout([h('strong', null, title + ' '), h('span', { class: 'mono' }, code), ' ',
          h('button', { class: 'btn ghost small', type: 'button', onclick: () => SOC.copy(code) }, 'Copy'),
          h('div', { class: 'note' }, 'Shown once. Give it to the user separately from their password; it is needed to enrol their authenticator.')], 'warn'));
      };
      const create = h('form', { class: 'filters', onsubmit: (e) => {
        e.preventDefault();
        act(api.post('/soc/api/users', { username: u.value, password: pw.value, role: role.value }), 'Account created').then((r) => { showCode('Setup code:', r.setup_code); u.value = ''; pw.value = ''; return api.get('/soc/api/users'); })
          .then((n) => table(n.users)).catch(() => {});
      } }, u, pw, role, h('button', { class: 'btn', type: 'submit' }, 'Create account'));
      const tableBox = h('div');
      const call = (id, body, msg) => act(api.post('/soc/api/users/' + id, body), msg);
      const table = (users) => {
        tableBox.textContent = '';
        tableBox.appendChild(SOC.table({ rows: users, columns: [
          { label: 'User', key: 'username' }, { label: 'Role', key: 'role' }, { label: 'MFA', render: (x) => SOC.chip(x.totp_enabled ? 'enrolled' : 'pending', x.totp_enabled ? 'ok' : 'plain') },
          { label: 'State', render: (x) => !x.active ? SOC.chip('disabled', 'bad') : x.locked_until ? SOC.chip('locked?', 'plain') : SOC.chip('active', 'ok') },
          { label: 'Last sign-in', render: (x) => x.last_login_at ? [fmt.time(x.last_login_at), h('div', { class: 'note mono' }, x.last_login_ip || '')] : null },
          { label: '', render: (x) => h('div', { class: 'row' },
            h('select', { class: 'select', 'aria-label': 'Role for ' + x.username, onchange: (e) => call(x.id, { action: 'set_role', role: e.target.value }, 'Role updated').then(refresh).catch(refresh) },
              ['viewer', 'analyst', 'admin'].map((r) => h('option', { value: r, selected: r === x.role }, r))),
            h('button', { class: 'btn ghost small', type: 'button', onclick: () => call(x.id, { action: x.active ? 'deactivate' : 'activate' }, x.active ? 'Disabled' : 'Enabled').then(refresh).catch(() => {}) }, x.active ? 'Disable' : 'Enable'),
            h('button', { class: 'btn ghost small', type: 'button', onclick: () => call(x.id, { action: 'unlock' }, 'Unlocked').then(refresh).catch(() => {}) }, 'Unlock'),
            h('button', { class: 'btn ghost small', type: 'button', onclick: () => { if (window.confirm('Remove ' + x.username + "'s authenticator and issue a new setup code?")) call(x.id, { action: 'reset_mfa' }, 'MFA reset').then((r) => { showCode('New setup code for ' + x.username + ':', r.setup_code); refresh(); }).catch(() => {}); } }, 'Reset MFA'),
            h('button', { class: 'btn ghost small', type: 'button', onclick: () => { const p = window.prompt('New password for ' + x.username + ' (12+ characters):'); if (p) call(x.id, { action: 'reset_password', password: p }, 'Password reset').catch(() => {}); } }, 'Set password')) }
        ] }));
      };
      const refresh = () => api.get('/soc/api/users').then((n) => table(n.users));
      root.append(create, reveal, tableBox);
      table(d.users);
    }
  });
})();
