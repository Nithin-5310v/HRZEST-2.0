/* SOC console -- Health: server, PostgreSQL and app-service readings taken once a
 * minute by utils/health_telemetry.py. Data from the server is rendered only as
 * text nodes, as everywhere in the console. */
(function () {
  'use strict';
  const { h, fmt, api } = SOC;

  const NAMES = { host: 'Server', postgres: 'Database', app: 'App service' };
  const dur = (s) => {
    if (s == null) return '–';
    s = Math.max(0, Math.floor(s));
    const d = Math.floor(s / 86400), hr = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
    return d ? d + 'd ' + hr + 'h' : hr ? hr + 'h ' + m + 'm' : m ? m + 'm' : s + 's';
  };
  const pc = (v) => (v == null ? '–' : v + '%');
  const tone = (v, warn, bad) => (v == null ? 'info' : v >= bad ? 'high' : v >= warn ? 'medium' : 'ok');
  const STATE_CHIP = { ok: ['reading now', 'ok'], offline: ['offline', 'bad'], error: ['failed', 'bad'], never: ['no data yet', 'plain'] };

  function sourceNotice(name, src) {
    const label = NAMES[name];
    if (src.state === 'never') {
      return SOC.callout(label + ': no telemetry recorded yet. Readings are taken once a minute by the app\'s scheduler.', 'warn');
    }
    if (src.state === 'offline') {
      return SOC.callout([label + ': source offline since ', h('strong', null, fmt.time(src.at)), ' (' + fmt.ago(src.at) +
        '). Nothing newer has been recorded; the numbers below are from that reading.'], 'bad');
    }
    if (src.state === 'error') {
      return SOC.callout([label + ': the latest reading failed at ' + fmt.time(src.at) + ': ' + (src.error || 'unknown failure') + '. ',
        src.last_ok ? 'Last good reading ' + fmt.time(src.last_ok) + '.' : 'No reading has succeeded yet.'], 'bad');
    }
    return null;
  }

  function statusCard(name, src) {
    const [text, cls] = STATE_CHIP[src.state];
    return h('div', { class: 'card kpi info' },
      h('div', { class: 'lbl' }, NAMES[name]),
      h('div', { class: 'val' }, SOC.chip(text, cls)),
      h('div', { class: 'sub' }, src.at ? 'last reading ' + fmt.ago(src.at) + (src.host ? ' on ' + src.host : '') : 'never read'));
  }

  function lineChart(title, labels, datasets, yOpts, y1Opts) {
    const canvas = h('canvas', { role: 'img', 'aria-label': title });
    const draw = () => SOC.chart(canvas, {
      type: 'line',
      data: { labels, datasets: datasets.map((d) => Object.assign({ pointRadius: 0, borderWidth: 1.6, tension: 0.25, spanGaps: true }, d)) },
      options: {
        scales: Object.assign({
          x: { grid: { display: false }, ticks: { maxTicksLimit: 8, maxRotation: 0, autoSkip: true } },
          y: Object.assign({ beginAtZero: true, border: { display: false } }, yOpts || {})
        }, y1Opts ? { y1: Object.assign({ position: 'right', beginAtZero: true, border: { display: false }, grid: { drawOnChartArea: false } }, y1Opts) } : {}),
        interaction: { mode: 'index', intersect: false },
        plugins: { legend: { position: 'bottom', labels: { usePointStyle: true, boxWidth: 8, boxHeight: 8, padding: 16 } } }
      }
    });
    return { el: SOC.card(title, h('div', { class: 'chart short' }, canvas)), draw };
  }

  SOC.register({
    id: 'health', label: 'Health', title: 'Health', icon: 'health',
    async render(root, ctl) {
      const draw = async (initial) => {
        const d = await api.get('/socperson/api/health', { hours: SOC.hours }, !initial);
        const s = d.sources, frag = h('div'), charts = [];
        ['host', 'postgres', 'app'].forEach((n) => { const c = sourceNotice(n, s[n]); if (c) frag.appendChild(c); });
        const host = s.host.metrics || {}, pg = s.postgres.metrics || {}, app = s.app.metrics || {};
        if (host.missing) {
          frag.appendChild(SOC.callout('Server: ' + host.missing.join(', ') + ' could not be read (' + host.missing_reason + ').', 'warn'));
        }
        if (s.postgres.metrics && !pg.sees_all_sessions) {
          frag.appendChild(h('p', { class: 'note' }, 'The app\'s database role cannot read every session\'s state (it is not in ' +
            'pg_read_all_stats), so active and idle-in-transaction counts cover only the sessions it may see. The connection total is complete.'));
        }
        frag.appendChild(h('p', { class: 'note' }, 'Readings taken once a minute, kept ' + d.retention_days + ' days. A source with no ' +
          'reading for ' + Math.round(d.offline_after_seconds / 60) + ' minutes is shown as offline. Nothing here is estimated.'));
        frag.appendChild(h('div', { class: 'grid kpis' }, ['host', 'postgres', 'app'].map((n) => statusCard(n, s[n]))));

        const disk = (host.disks || [])[0];
        const connShare = pg.max_connections ? Math.round(100 * pg.connections / pg.max_connections) : null;
        frag.appendChild(h('div', { class: 'grid kpis' },
          SOC.kpi('CPU busy', pc(host.cpu_percent), host.load1 != null ? 'load ' + host.load1 + ' on ' + host.cpu_count + ' cores' : '',
            tone(host.cpu_percent, 75, 90), null, 'posture'),
          SOC.kpi('Memory used', pc(host.mem_used_percent), host.mem_available != null ? fmt.bytes(host.mem_available) + ' free of ' + fmt.bytes(host.mem_total) : '',
            tone(host.mem_used_percent, 80, 92), null, 'posture'),
          SOC.kpi('Disk used', pc(disk && disk.used_percent), disk ? fmt.bytes(disk.free) + ' free on ' + disk.path : '',
            tone(disk && disk.used_percent, 80, 90), null, 'posture'),
          SOC.kpi('Server up', dur(host.uptime_seconds), '', 'info', null, 'integrity'),
          SOC.kpi('DB connections', pg.connections == null ? '–' : fmt.n(pg.connections) + ' / ' + fmt.n(pg.max_connections),
            pg.active != null ? pg.active + ' active, ' + pg.idle_in_transaction + ' idle in transaction' : '', tone(connShare, 75, 90), null, 'globe'),
          SOC.kpi('DB round trip', pg.latency_ms == null ? '–' : pg.latency_ms + ' ms', 'SELECT 1 from the app', tone(pg.latency_ms, 20, 100), null, 'traffic'),
          SOC.kpi('Longest query', dur(pg.longest_query_seconds), 'open transaction ' + dur(pg.longest_transaction_seconds),
            tone(pg.longest_query_seconds, 30, 300), null, 'events'),
          SOC.kpi('Waiting locks', fmt.n(pg.waiting_locks), 'deadlocks in window: ' + fmt.n(d.window.deadlocks),
            pg.waiting_locks || d.window.deadlocks ? 'medium' : 'ok', null, 'lock'),
          SOC.kpi('Database size', fmt.bytes(pg.db_size_bytes), d.window.cache_hit_percent != null ? d.window.cache_hit_percent + '% cache hits' : '', 'info', null, 'logs'),
          SOC.kpi('Service up', dur(app.service_uptime_seconds), app.server ? app.server + (app.rss_bytes ? ', worker ' + fmt.bytes(app.rss_bytes) : '') : '',
            'info', null, 'response')));

        const label = (t) => (d.step_seconds >= 3600 ? fmt.time(t).slice(5, 16) : fmt.time(t).slice(11, 16));
        if (d.host_series.length) {
          const hs = d.host_series;
          charts.push(lineChart('Server load (%)', hs.map((p) => label(p.t)), [
            { label: 'CPU', data: hs.map((p) => p.cpu_avg), borderColor: SOC.cssVar('--info'), backgroundColor: SOC.cssVar('--info') },
            { label: 'Memory', data: hs.map((p) => p.mem), borderColor: SOC.cssVar('--medium'), backgroundColor: SOC.cssVar('--medium') },
            { label: 'Disk', data: hs.map((p) => p.disk), borderColor: SOC.cssVar('--muted'), backgroundColor: SOC.cssVar('--muted') }
          ], { max: 100, ticks: { callback: (v) => v + '%' } }));
        }
        if (d.db_series.length) {
          const ds = d.db_series;
          charts.push(lineChart('Database', ds.map((p) => label(p.t)), [
            { label: 'Connections', data: ds.map((p) => p.conns_avg), borderColor: SOC.cssVar('--info'), backgroundColor: SOC.cssVar('--info') },
            { label: 'Round trip (ms)', data: ds.map((p) => p.latency_avg), yAxisID: 'y1', borderColor: SOC.cssVar('--medium'), backgroundColor: SOC.cssVar('--medium') }
          ], { ticks: { precision: 0 } }, { ticks: { callback: (v) => v + ' ms' } }));
          charts.push(lineChart('Transactions per minute', ds.map((p) => label(p.t)), [
            { label: 'Commits', data: ds.map((p) => p.commits_per_min), borderColor: SOC.cssVar('--info'), backgroundColor: SOC.cssVar('--info') },
            { label: 'Rollbacks', data: ds.map((p) => p.rollbacks_per_min), borderColor: SOC.cssVar('--critical'), backgroundColor: SOC.cssVar('--critical') }
          ]));
        }
        if (charts.length) frag.appendChild(h('div', { class: 'grid cols-2' }, charts.map((c) => c.el)));
        else frag.appendChild(SOC.empty('No readings in this window.'));

        frag.appendChild(SOC.card('Disks', SOC.table({
          empty: s.host.metrics ? 'No filesystem could be read.' : 'No server reading yet.', rows: host.disks || [],
          columns: [
            { label: 'Path', render: (r) => h('span', { class: 'mono' }, r.path) },
            { label: 'Size', num: true, render: (r) => fmt.bytes(r.total) },
            { label: 'Used', num: true, render: (r) => fmt.bytes(r.used) },
            { label: 'Free', num: true, render: (r) => fmt.bytes(r.free) },
            { label: 'Used %', num: true, render: (r) => pc(r.used_percent) }
          ]
        })));

        root.textContent = ''; root.appendChild(frag);
        charts.forEach((c) => c.draw());
      };
      await draw(true);
      ctl.poll(() => { if (!SOC.drawer.isOpen()) draw(false).catch(() => {}); }, 60000);
    }
  });
}());
