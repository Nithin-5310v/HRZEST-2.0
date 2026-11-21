/* SOC console -- "Firewall" view (utils/firewall_events.py, docs/FIREWALL.md).
 *
 * Block / allow decisions from every layer in one place: the AWS edge WAF,
 * VPC flow logs, the host firewall, nginx + ModSecurity, fail2ban and the
 * app's own guards. Each reporter's heartbeat is shown, so a layer that has
 * gone quiet reads as "not reporting", never as a reassuring zero.
 * Same rule as the rest of the console: recorded values (paths, rule
 * messages, addresses) are only ever rendered as text nodes. */
(function () {
  'use strict';
  const { h, fmt, api } = SOC;

  const LAYERS = [
    ['edge', 'Edge WAF', 'AWS WAF on the load balancer'],
    ['network', 'Network', 'VPC flow logs: security-group rejects'],
    ['host', 'Host firewall', 'ufw / nftables drops on the server'],
    ['proxy', 'Proxy WAF', 'nginx + ModSecurity (OWASP CRS)'],
    ['fail2ban', 'fail2ban', 'Host-level bans from repeated offences'],
    ['app', 'App', 'IP/range bans, WAF signatures, scanners, rate limits']
  ];
  const LAYER_LABEL = Object.fromEntries(LAYERS.map(([k, l]) => [k, l]));
  const LAYER_COLOR = { edge: '--critical', network: '--info', host: '--medium', proxy: '--high', fail2ban: '--low', app: '--accent' };
  const ACTION_CLS = { block: 'bad', ban: 'bad', challenge: 'sev-medium', count: 'plain', allow: 'ok', unban: 'plain' };
  const WINDOWS = [['1', '1 hour'], ['24', '24 hours'], ['168', '7 days'], ['720', '30 days']];

  const select = (options, value, attrs) => {
    const s = h('select', Object.assign({ class: 'select' }, attrs || {}));
    options.forEach(([v, l]) => s.appendChild(h('option', { value: v, selected: String(v) === String(value) }, l)));
    return s;
  };
  const whenShort = (ts) => ts ? h('span', { class: 'when', title: fmt.time(ts) }, fmt.ago(ts)) : null;
  const blocked = (c) => (c.block || 0) + (c.ban || 0) + (c.challenge || 0);

  function reporterStatus(r) {
    const s = r.status || {};
    const parts = [];
    if (s.version) parts.push('agent ' + String(s.version));
    if (s.mode) parts.push(String(s.mode));
    if (s.layers && typeof s.layers === 'object') {
      Object.keys(s.layers).forEach((k) => {
        const v = s.layers[k];
        if (v && typeof v === 'object') parts.push(k + ': ' + (v.ok ? 'ok' : ('error' + (v.error ? ' (' + String(v.error) + ')' : ''))));
      });
    }
    if (s.blocklist && typeof s.blocklist === 'object') {
      parts.push('blocklist ' + fmt.n(s.blocklist.entries) + (s.blocklist.synced_at ? ' synced ' + fmt.ago(s.blocklist.synced_at) : ''));
    }
    return parts.join(' · ');
  }

  function eventDrawer(e) {
    return h('div', { class: 'stack' }, SOC.kv([
      ['When', fmt.time(e.occurred_at)],
      ['Layer', LAYER_LABEL[e.layer] || e.layer],
      ['Decision', SOC.chip(e.action, ACTION_CLS[e.action] || 'plain')],
      ['Source', SOC.ipLink(e.src_ip)],
      ['Destination port', e.dst_port == null ? null : String(e.dst_port) + (e.protocol ? '/' + e.protocol : '')],
      ['Request', e.method || e.path ? h('span', { class: 'mono' }, (e.method ? e.method + ' ' : '') + (e.host || '') + (e.path || '')) : null],
      ['Rule', e.rule ? h('span', { class: 'mono' }, e.rule) : null],
      ['Detail', e.detail],
      ['Country (as the edge recorded it)', e.country],
      ['Count', fmt.n(e.hits)],
      ['Reported by', e.source]
    ]), e.src_ip && SOC.can('analyst')
      ? h('div', { class: 'row' },
        h('button', { class: 'btn danger', type: 'button', onclick: () => SOC.drawer.open('Ban ' + e.src_ip, SOC.banForm(e.src_ip, () => SOC.reload())) }, 'Ban this address'),
        h('a', { class: 'btn', href: '#/entity?ip=' + encodeURIComponent(e.src_ip) }, 'Look up'))
      : null);
  }

  function eventsTable(rows) {
    return SOC.table({
      empty: 'No decisions match these filters in the selected window.', rows,
      onRow: (e) => SOC.drawer.open('Firewall decision', eventDrawer(e)),
      columns: [
        { label: 'When', render: (e) => whenShort(e.occurred_at) },
        { label: 'Layer', render: (e) => LAYER_LABEL[e.layer] || e.layer },
        { label: 'Decision', render: (e) => SOC.chip(e.action, ACTION_CLS[e.action] || 'plain') },
        { label: 'Source', render: (e) => SOC.ipLink(e.src_ip) },
        { label: 'Rule', cls: 'clip mono', key: 'rule' },
        { label: 'Request', cls: 'clip mono', render: (e) => (e.method ? e.method + ' ' : '') + (e.path || (e.dst_port != null ? ':' + e.dst_port + (e.protocol ? '/' + e.protocol : '') : '')) },
        { label: 'Count', num: true, render: (e) => fmt.n(e.hits) }
      ]
    });
  }

  SOC.register({
    id: 'firewall', label: 'Firewall', title: 'Firewall layers', icon: 'response',
    async render(root, ctl) {
      const p = ctl.params;
      const hours = WINDOWS.some(([v]) => v === p.hours) ? p.hours : '24';
      const layer = LAYERS.some(([k]) => k === p.layer) ? p.layer : '';
      const action = ['block', 'allow', 'count', 'challenge', 'ban', 'unban'].includes(p.action) ? p.action : '';
      const ip = typeof p.ip === 'string' ? p.ip : '';

      const draw = async () => {
        const [o, ev] = await Promise.all([
          api.get('/socperson/api/firewall', { hours }),
          api.get('/socperson/api/firewall/events', { hours, layer, action, ip })
        ]);
        const frag = h('div');

        const win = select(WINDOWS, hours, { 'aria-label': 'Time window', onchange: (e) => SOC.go('firewall', Object.assign({}, p, { hours: e.target.value })) });
        frag.appendChild(h('div', { class: 'filters' }, h('label', { class: 'note' }, 'Window '), win));

        const stale = o.reporters.filter((r) => r.stale);
        if (!o.reporters.some((r) => r.source !== 'app')) {
          frag.appendChild(SOC.callout('No firewall agent has reported yet, so only the app layer is shown. Install the agent on the server (scripts/firewall_agent/install_firewall_agent.sh) to see the host firewall, ModSecurity, fail2ban and, once it exists, the AWS edge.', 'warn'));
        } else if (stale.length) {
          frag.appendChild(SOC.callout(stale.map((r) => r.source).join(', ') + ' stopped reporting more than ' + o.stale_minutes + ' minutes ago. Its layers may still be enforcing, but nothing new is reaching this console.', 'warn'));
        }
        if (o.excluded.internal || o.excluded.test) {
          frag.appendChild(h('p', { class: 'note' }, 'Left out: ' + fmt.n(o.excluded.internal) + ' decisions about the server’s own or private addresses, ' + fmt.n(o.excluded.test) + ' about your test addresses (SOC_TEST_CIDRS).'));
        }

        frag.appendChild(h('div', { class: 'grid kpis' }, LAYERS.map(([k, label, desc]) => {
          const c = o.layers.find((x) => x.layer === k) || {};
          const sub = (c.allow ? fmt.n(c.allow) + ' allowed · ' : '') + (c.count ? fmt.n(c.count) + ' counted · ' : '') + desc;
          return SOC.kpi(label, fmt.n(blocked(c)), sub, blocked(c) ? 'medium' : 'ok', '#/firewall' + SOC.api.qs({ hours, layer: k }), 'response');
        })));

        const canvas = h('canvas', { role: 'img', 'aria-label': 'Blocked requests over time by layer' });
        frag.appendChild(h('div', { class: 'grid cols-21' },
          SOC.card('Blocked over time, by layer', o.timeline.length ? h('div', { class: 'chart' }, canvas) : SOC.empty('Nothing blocked in this window.')),
          SOC.card('Top rules', o.top_rules.length
            ? SOC.bars(o.top_rules.map((r) => ({ label: (LAYER_LABEL[r.layer] || r.layer) + ': ' + r.rule, value: r.hits })))
            : SOC.empty('No rule matched in this window.'))));

        frag.appendChild(h('div', { class: 'grid cols-2' },
          SOC.card('Most-blocked sources', SOC.table({
            empty: 'No blocked sources in this window.', rows: o.top_sources,
            onRow: (r) => SOC.go('firewall', { hours, ip: r.ip }),
            columns: [
              { label: 'Source IP', render: (r) => SOC.ipLink(r.ip) },
              { label: 'Blocked', num: true, render: (r) => fmt.n(r.hits) },
              { label: 'Layers', render: (r) => r.layers.map((l) => SOC.chip(LAYER_LABEL[l] || l, 'plain')) },
              { label: 'Last', render: (r) => whenShort(r.last) }
            ]
          })),
          SOC.card('Reporters', SOC.table({
            empty: 'Nothing has reported yet.', rows: o.reporters,
            columns: [
              { label: 'Source', key: 'source' },
              { label: 'Status', render: (r) => SOC.chip(r.stale ? 'not reporting' : 'reporting', r.stale ? 'bad' : 'ok') },
              { label: 'Last report', render: (r) => whenShort(r.last_seen) },
              { label: 'Details', cls: 'clip', render: (r) => reporterStatus(r) }
            ]
          }))));

        const layerSel = select([['', 'All layers']].concat(LAYERS.map(([k, l]) => [k, l])), layer, { 'aria-label': 'Layer' });
        const actionSel = select([['', 'All decisions'], ['block', 'Block'], ['ban', 'Ban'], ['challenge', 'Challenge'], ['count', 'Count'], ['allow', 'Allow'], ['unban', 'Unban']], action, { 'aria-label': 'Decision' });
        const ipBox = h('input', { class: 'input', 'aria-label': 'Source IP', placeholder: 'Source IP', value: ip });
        const filters = h('form', { class: 'filters', onsubmit: (e) => {
          e.preventDefault();
          SOC.go('firewall', { hours, layer: layerSel.value, action: actionSel.value, ip: ipBox.value.trim() });
        } }, layerSel, actionSel, ipBox, h('button', { class: 'btn', type: 'submit' }, 'Filter'));
        frag.appendChild(SOC.card('Recent decisions', h('div', null, filters, eventsTable(ev.events),
          ev.events.length >= 200 ? h('p', { class: 'note' }, 'Showing the newest 200. Narrow the filters to see older ones.') : null)));

        root.textContent = ''; root.appendChild(frag);
        if (o.timeline.length) {
          const times = Array.from(new Set(o.timeline.map((x) => x.t))).sort();
          const series = (k) => times.map((t) => { const row = o.timeline.find((x) => x.t === t && x.layer === k); return row ? row.blocked : 0; });
          SOC.chart(canvas, {
            type: 'bar',
            data: { labels: times.map((t) => fmt.time(t).slice(5, 16)),
              datasets: LAYERS.filter(([k]) => o.timeline.some((x) => x.layer === k)).map(([k, l]) => ({
                label: l, data: series(k), backgroundColor: SOC.cssVar(LAYER_COLOR[k]) || SOC.cssVar('--info'),
                borderRadius: 3, borderSkipped: false, barPercentage: 0.78, categoryPercentage: 0.9 })) },
            options: { scales: { x: { stacked: true, grid: { display: false }, ticks: { maxTicksLimit: 8, maxRotation: 0, autoSkip: true } },
              y: { stacked: true, beginAtZero: true, border: { display: false }, ticks: { precision: 0 } } },
              interaction: { mode: 'index', intersect: false },
              plugins: { legend: { position: 'bottom', labels: { usePointStyle: true, pointStyle: 'rectRounded', boxWidth: 8, boxHeight: 8, padding: 16 } } } }
          });
        }
      };
      await draw();
      ctl.poll(() => { if (!SOC.drawer.isOpen()) draw().catch(() => {}); }, 30000);
    }
  });
})();
