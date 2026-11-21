/* SOC console -- Traffic: request counts, status codes and response times
 * recorded by utils/http_telemetry.py. As in the rest of the console, data from
 * the server (route patterns included) is rendered only as text nodes. */
(function () {
  'use strict';
  const { h, fmt, api } = SOC;

  // Response times are histogram upper bounds, never exact figures.
  const ms = (p) => {
    if (!p) return '–';
    if (p.gt != null) return '> ' + fmt.n(p.gt) + ' ms';
    return '≤ ' + fmt.n(p.le) + ' ms';
  };
  const msValue = (p) => (p ? (p.gt != null ? (p.max || p.gt) : p.le) : null);
  const share = (a, b) => (b ? (Math.round(1000 * a / b) / 10) + '%' : '0%');
  const count = (n) => (n ? fmt.n(n) : h('span', { class: 'muted' }, '0'));

  function sourceNotice(src) {
    if (src.state === 'never') {
      return SOC.callout('No telemetry recorded yet. The app counts the requests it answers and writes the counts ' +
        'once a minute; nothing has been written so far.', 'warn');
    }
    if (src.state === 'offline') {
      return SOC.callout(['Source offline: the request collector last wrote at ', h('strong', null, fmt.time(src.last_write)),
        ' (' + fmt.ago(src.last_write) + '). Nothing after that is counted below.'], 'bad');
    }
    return null;
  }

  function chart(d) {
    const canvas = h('canvas', { role: 'img', 'aria-label': 'Requests over time by outcome, with the 95th percentile response time' });
    const label = (t) => (d.step_seconds >= 3600 ? fmt.time(t).slice(5, 16) : fmt.time(t).slice(11, 16));
    const bar = (name, key, color) => ({ type: 'bar', label: name, data: d.series.map((p) => p[key]), backgroundColor: SOC.cssVar(color),
      stack: 'requests', borderRadius: 3, borderSkipped: false, barPercentage: 0.78, categoryPercentage: 0.9 });
    const draw = () => SOC.chart(canvas, {
      data: {
        labels: d.series.map((p) => label(p.t)),
        datasets: [
          bar('OK', 'ok', '--info'), bar('4xx', '4xx', '--medium'), bar('5xx', '5xx', '--critical'),
          { type: 'line', label: 'p95 (ms, upper bound)', data: d.series.map((p) => msValue(p.p95)), yAxisID: 'y1',
            borderColor: SOC.cssVar('--muted'), backgroundColor: SOC.cssVar('--muted'), pointRadius: 0, borderWidth: 1.5, tension: 0.25 }
        ]
      },
      options: {
        scales: {
          x: { stacked: true, grid: { display: false }, ticks: { maxTicksLimit: 8, maxRotation: 0, autoSkip: true } },
          y: { stacked: true, beginAtZero: true, border: { display: false }, ticks: { precision: 0 } },
          y1: { position: 'right', beginAtZero: true, border: { display: false }, grid: { drawOnChartArea: false },
            ticks: { callback: (v) => v + ' ms' } }
        },
        interaction: { mode: 'index', intersect: false },
        plugins: { legend: { position: 'bottom', labels: { usePointStyle: true, pointStyle: 'rectRounded', boxWidth: 8, boxHeight: 8, padding: 16 } } }
      }
    });
    return { el: h('div', { class: 'chart' }, canvas), draw };
  }

  SOC.register({
    id: 'traffic', label: 'Traffic', title: 'Traffic', icon: 'traffic',
    async render(root, ctl) {
      const draw = async (initial) => {
        const d = await api.get('/socperson/api/traffic', { hours: SOC.hours }, !initial);
        const t = d.totals, frag = h('div');
        const notice = sourceNotice(d.source);
        if (notice) frag.appendChild(notice);
        frag.appendChild(h('p', { class: 'note' }, 'Every request the app answered, by route. Left out: this console\'s own ' +
          'requests, static files and health checks. Response times are upper bounds from a histogram (for example ' +
          '"≤ 250 ms"). Kept for ' + d.retention_days + ' days' +
          (d.source.state === 'ok' ? '; last written ' + fmt.ago(d.source.last_write) : '') + '.'));

        if (!t.requests) {
          frag.appendChild(SOC.empty(d.source.state === 'never' ? 'No telemetry recorded yet.' : 'No requests recorded in this window.'));
          root.textContent = ''; root.appendChild(frag);
          return;
        }
        frag.appendChild(h('div', { class: 'grid kpis' },
          SOC.kpi('Requests', fmt.n(t.requests), 'last ' + d.hours + 'h', 'info', null, 'traffic'),
          SOC.kpi('Client errors', fmt.n(t.client_errors), share(t.client_errors, t.requests) + ' of requests (4xx)',
            t.client_errors ? 'medium' : 'ok', null, 'error'),
          SOC.kpi('Server errors', fmt.n(t.server_errors), share(t.server_errors, t.requests) + ' of requests (5xx)',
            t.server_errors ? 'high' : 'ok', null, 'alerts'),
          SOC.kpi('p95 response', ms(t.p95), 'median ' + ms(t.p50), 'info', null, 'posture'),
          SOC.kpi('401 / 403', fmt.n(t.s401) + ' / ' + fmt.n(t.s403), 'not signed in / forbidden',
            t.s401 + t.s403 ? 'medium' : 'ok', null, 'lock'),
          SOC.kpi('404 / 429', fmt.n(t.s404) + ' / ' + fmt.n(t.s429), 'not found / rate-limited',
            t.s404 + t.s429 ? 'medium' : 'ok', null, 'target')));

        const c = chart(d);
        const statuses = SOC.bars(d.statuses.map((s) => ({
          label: String(s.status), value: s.hits, cls: s.status >= 500 ? 'crit' : s.status >= 400 ? 'warn' : ''
        })));
        frag.appendChild(h('div', { class: 'grid cols-21' },
          SOC.card('Requests over time', c.el), SOC.card('By status code', statuses)));

        frag.appendChild(SOC.card('Routes', SOC.table({
          empty: 'No requests recorded in this window.', rows: d.endpoints,
          columns: [
            { label: 'Route', render: (r) => h('span', { class: 'mono' }, r.endpoint) },
            { label: 'Method', key: 'method' },
            { label: 'Requests', num: true, render: (r) => fmt.n(r.hits) },
            { label: 'p50', num: true, render: (r) => ms(r.p50) },
            { label: 'p95', num: true, render: (r) => ms(r.p95) },
            { label: 'p99', num: true, render: (r) => ms(r.p99) },
            { label: 'Slowest', num: true, render: (r) => fmt.n(r.max_ms) + ' ms' },
            { label: '401', num: true, render: (r) => count(r.s401) },
            { label: '403', num: true, render: (r) => count(r.s403) },
            { label: '404', num: true, render: (r) => count(r.s404) },
            { label: '429', num: true, render: (r) => count(r.s429) },
            { label: '5xx', num: true, render: (r) => count(r.s5xx) }
          ]
        }), h('span', { class: 'note' }, 'Busiest ' + d.endpoints.length)));

        root.textContent = ''; root.appendChild(frag);
        c.draw();
      };
      await draw(true);
      ctl.poll(() => { if (!SOC.drawer.isOpen()) draw(false).catch(() => {}); }, 60000);
    }
  });
}());
