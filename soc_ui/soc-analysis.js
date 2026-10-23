/* SOC console -- log analysis view. Log lines are attacker-controlled: they are
 * rendered as text only (see soc-core.js). The log text itself is uploaded as a
 * FILE so the WAF (which inspects form fields, not file bodies) does not treat a
 * log full of attack strings as an attack on the console. */
(function () {
  'use strict';
  const { h, fmt, api } = SOC;

  const csvSafe = (v) => {
    const s = v == null ? '' : String(v);
    const cell = /^[=+\-@\t\r]/.test(s) ? "'" + s : s;
    return /[",\n]/.test(cell) ? '"' + cell.replace(/"/g, '""') + '"' : cell;
  };

  function render(root) {
    root.textContent = '';
    const out = h('div', { class: 'stack' });
    const fileIn = h('input', { type: 'file', id: 'logFile', accept: '.log,.txt,.json,.jsonl,text/plain', 'aria-label': 'Log file' });
    const paste = h('textarea', { class: 'textarea', id: 'logPaste', 'aria-label': 'Pasted log text', placeholder: 'Or paste log lines here — nginx / Apache access log, JSON lines, sshd / auth.log…' });
    const drop = h('div', { class: 'dropzone' }, h('p', null, 'Drop a log file here, or choose one'), fileIn);
    const go = h('button', { class: 'btn', type: 'submit' }, 'Analyse');
    const results = h('div', { class: 'stack' });

    ['dragenter', 'dragover'].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add('over'); }));
    ['dragleave', 'drop'].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove('over'); }));
    drop.addEventListener('drop', (e) => { if (e.dataTransfer.files.length) fileIn.files = e.dataTransfer.files; });

    const form = h('form', { class: 'stack', onsubmit: (e) => {
      e.preventDefault();
      const fd = new FormData();
      if (fileIn.files.length) fd.append('log', fileIn.files[0], fileIn.files[0].name);
      else if (paste.value.trim()) fd.append('log', new File([paste.value], 'pasted.log', { type: 'text/plain' }));
      else { SOC.toast('Choose a file or paste some log lines first.', 'bad'); return; }
      go.disabled = true; go.textContent = 'Analysing…';
      results.textContent = ''; results.appendChild(SOC.skeleton());
      api.form('/soc/api/loganalysis', fd).then((r) => show(r.report))
        .catch((err) => { results.textContent = ''; results.appendChild(SOC.callout(err.message, 'bad')); })
        .finally(() => { go.disabled = false; go.textContent = 'Analyse'; });
    } }, drop, paste, h('div', { class: 'row' }, go,
      h('span', { class: 'note' }, 'Read-only. Nothing is stored. Up to 5 MB / 100,000 lines; format is detected per line.')));

    out.append(SOC.callout('Finds SQL injection, XSS, traversal, command injection, SSRF, scanners, probes, brute force, credential stuffing, enumeration, floods and error spikes — and recommends which sources to block.'), form, results);
    root.appendChild(out);

    function show(r) {
      results.textContent = '';
      const s = r.summary, lim = r.limits;
      results.appendChild(h('div', { class: 'grid kpis' },
        SOC.kpi('Lines', fmt.n(s.lines_read), fmt.n(s.records) + ' parsed', 'info'), SOC.kpi('Sources', fmt.n(s.unique_ips), 'unique IPs', 'info'),
        SOC.kpi('Findings', fmt.n(s.findings), s.critical + ' critical · ' + s.high + ' high', s.critical ? 'critical' : s.high ? 'high' : s.findings ? 'medium' : 'ok'),
        SOC.kpi('Block candidates', fmt.n(s.block_candidates), 'public addresses', s.block_candidates ? 'high' : 'ok'),
        SOC.kpi('Time range', s.first ? s.first.slice(0, 10) : '–', s.last ? 'to ' + s.last.slice(0, 16) : '', 'info'),
        SOC.kpi('Analysed in', s.elapsed_ms + ' ms', Object.keys(s.formats).join(', ') || 'no format', 'info')));
      const warn = [];
      if (lim.truncated_bytes) warn.push('input was cut at the size limit');
      if (lim.truncated_lines) warn.push('only the first 100,000 lines were read');
      if (lim.time_budget_hit) warn.push('analysis stopped at its time budget');
      if (lim.ip_table_full) warn.push('too many distinct sources — the busiest were tracked');
      if (lim.long_lines_clipped) warn.push(lim.long_lines_clipped + ' very long line(s) were clipped');
      if (s.unparsed) warn.push(s.unparsed + ' line(s) had no recognisable structure');
      if (warn.length) results.appendChild(SOC.callout('Partial result: ' + warn.join('; ') + '.', 'warn'));

      /* findings */
      results.appendChild(SOC.card('Findings', r.findings.length ? h('div', { class: 'stack' }, r.findings.map((f) =>
        h('div', { class: 'callout ' + (f.severity === 'critical' || f.severity === 'high' ? 'bad' : f.severity === 'medium' ? 'warn' : '') },
          h('div', { class: 'row' }, SOC.sev(f.severity), h('strong', null, f.title), h('span', { class: 'note' }, fmt.n(f.count) + ' hit' + (f.count === 1 ? '' : 's')), SOC.mitre(f.mitre.map((m) => m.id))),
          f.advice ? h('p', { class: 'note' }, f.advice) : null,
          f.ips.length ? h('div', { class: 'row' }, f.ips.slice(0, 6).map((i) => h('span', { class: 'row' }, SOC.ipLink(i.ip), h('span', { class: 'note' }, '×' + i.count)))) : null,
          f.evidence.length ? h('details', null, h('summary', { class: 'note' }, 'Evidence (' + f.evidence.length + ')'),
            SOC.pre(f.evidence.map((e) => (e.line ? 'line ' + e.line + ': ' : '') + e.text).join('\n'))) : null))) : SOC.empty('No attack patterns found in this log.')));

      /* sources */
      const canAct = SOC.can('analyst');
      results.appendChild(SOC.card('Sources', SOC.table({
        empty: 'No source addresses were found.', rows: r.sources,
        columns: [
          { label: 'Source', render: (x) => [SOC.ipLink(x.ip), x.non_public ? SOC.chip(' non-public', 'plain') : null] },
          { label: 'Requests', num: true, key: 'requests' }, { label: 'Errors', num: true, key: 'errors' },
          { label: 'Paths', num: true, key: 'distinct_paths' }, { label: 'Login POSTs', num: true, key: 'login_posts' }, { label: 'Accounts', num: true, key: 'accounts' },
          { label: 'Sent', num: true, render: (x) => fmt.bytes(x.bytes) },
          { label: 'Flags', render: (x) => x.flags.map((f) => SOC.chip(f, 'plain')) },
          { label: 'Score', render: (x) => { const i = h('i'); i.style.width = Math.max(2, x.score) + '%'; return h('div', { class: 'row' }, h('span', { class: 'bar ' + (x.score >= 60 ? 'crit' : x.score >= 25 ? 'warn' : '') }, i), h('span', { class: 'mono' }, x.score)); } },
          { label: 'Verdict', render: (x) => SOC.chip(x.verdict, x.verdict === 'block' ? 'bad' : x.verdict === 'watch' ? 'plain' : 'ok') },
          { label: '', render: (x) => canAct && x.verdict !== 'ok' && !x.non_public ? h('button', { class: 'btn danger small', type: 'button', onclick: () => SOC.drawer.open('Ban ' + x.ip, SOC.banForm(x.ip)) }, 'Ban') : null }
        ]
      })));

      /* charts */
      const tl = h('canvas', { role: 'img', 'aria-label': 'Requests over time' }), st = h('canvas', { role: 'img', 'aria-label': 'HTTP status classes' }), hr = h('canvas', { role: 'img', 'aria-label': 'Requests by hour of day' });
      results.appendChild(h('div', { class: 'grid cols-21' },
        SOC.card('Traffic over time' + (r.timeline.bucket ? ' (per ' + r.timeline.bucket + ')' : ''), r.timeline.points.length ? h('div', { class: 'chart short' }, tl) : SOC.empty('No timestamps were found in this log.')),
        SOC.card('Status classes', Object.keys(r.status).length ? h('div', { class: 'chart short' }, st) : SOC.empty('No HTTP statuses.'))));
      results.appendChild(SOC.card('Requests by hour of day', h('div', { class: 'chart short' }, hr)));
      if (r.timeline.points.length) {
        SOC.chart(tl, { type: 'line', data: { labels: r.timeline.points.map((p) => p.t.slice(r.timeline.bucket === 'day' ? 5 : 5, r.timeline.bucket === 'day' ? 10 : 16)), datasets: [
          { label: 'Requests', data: r.timeline.points.map((p) => p.requests), borderColor: SOC.cssVar('--accent'), backgroundColor: SOC.cssVar('--accent'), tension: .25, pointRadius: 0 },
          { label: 'From flagged sources', data: r.timeline.points.map((p) => p.attack), borderColor: SOC.cssVar('--critical'), backgroundColor: SOC.cssVar('--critical'), tension: .25, pointRadius: 0 },
          { label: '5xx', data: r.timeline.points.map((p) => p.errors5xx), borderColor: SOC.cssVar('--medium'), backgroundColor: SOC.cssVar('--medium'), tension: .25, pointRadius: 0 }] },
          options: { scales: { x: { ticks: { maxTicksLimit: 10 } }, y: { beginAtZero: true, ticks: { precision: 0 } } }, plugins: { legend: { position: 'bottom' } } } });
      }
      if (Object.keys(r.status).length) {
        const colors = { 200: SOC.cssVar('--ok'), 300: SOC.cssVar('--info'), 400: SOC.cssVar('--medium'), 500: SOC.cssVar('--critical') };
        SOC.chart(st, { type: 'bar', data: { labels: Object.keys(r.status).map((k) => k.replace('00', 'xx')), datasets: [{ data: Object.values(r.status), backgroundColor: Object.keys(r.status).map((k) => colors[k] || SOC.cssVar('--muted')) }] },
          options: { plugins: { legend: { display: false } }, scales: { y: { beginAtZero: true, ticks: { precision: 0 } } } } });
      }
      SOC.chart(hr, { type: 'bar', data: { labels: r.hourly.map((_, i) => String(i).padStart(2, '0')), datasets: [{ data: r.hourly, backgroundColor: SOC.cssVar('--accent') }] },
        options: { plugins: { legend: { display: false } }, scales: { y: { beginAtZero: true, ticks: { precision: 0 } } } } });

      /* paths + user agents */
      results.appendChild(h('div', { class: 'grid cols-2' },
        SOC.card('Top paths', r.top_paths.length ? SOC.table({ rows: r.top_paths, columns: [{ label: 'Path', cls: 'mono clip', key: 'path' }, { label: 'Hits', num: true, key: 'count' }] }) : SOC.empty('None.')),
        SOC.card('Top user agents', r.top_user_agents.length ? SOC.table({ rows: r.top_user_agents, columns: [{ label: 'User agent', cls: 'mono clip', render: (u) => [u.ua, u.scanner ? SOC.chip(' scanner', 'bad') : null] }, { label: 'Hits', num: true, key: 'count' }] }) : SOC.empty('None.'))));

      /* response */
      const ioc = r.iocs;
      const ips = ioc.filter((i) => i.type === 'ip').map((i) => i.value);
      const actions = h('div', { class: 'row' },
        h('button', { class: 'btn ghost', type: 'button', disabled: !ioc.length, onclick: () => {
          const rows = [['type', 'value', 'reason']].concat(ioc.map((i) => [i.type, i.value, i.reason]));
          SOC.download('iocs-' + (r.filename || 'log').replace(/[^\w.-]/g, '_') + '.csv', rows.map((row) => row.map(csvSafe).join(',')).join('\n'), 'text/csv');
        } }, 'Export IOCs (CSV)'),
        h('button', { class: 'btn ghost', type: 'button', disabled: !ips.length, onclick: () => SOC.copy(ips.join('\n')) }, 'Copy IP list'),
        SOC.can('analyst') ? h('button', { class: 'btn danger', type: 'button', disabled: !ips.length, onclick: () => {
          if (!window.confirm('Ban ' + ips.length + ' address(es) for 24 hours? Private and shared addresses are skipped automatically.')) return;
          api.post('/soc/api/bans/bulk', { ips, reason: 'Log analysis: ' + (r.filename || 'pasted log'), minutes: 1440 }).then((res) => {
            const okN = res.results.filter((x) => x.ok).length;
            SOC.toast(okN + ' banned, ' + (res.results.length - okN) + ' skipped', okN ? 'good' : 'bad');
            SOC.refreshPulse();
            const skipped = res.results.filter((x) => !x.ok);
            if (skipped.length) results.appendChild(SOC.card('Skipped', h('ul', { class: 'list' }, skipped.map((x) => h('li', null, h('span', { class: 'mono' }, x.ip), ' — ', x.message)))));
          }).catch((e) => SOC.toast(e.message, 'bad'));
        } }, 'Ban all recommended (' + ips.length + ')') : null,
        SOC.can('analyst') ? h('button', { class: 'btn', type: 'button', onclick: () => SOC.drawer.open('New case', SOC.caseForm({
          title: 'Log analysis: ' + (r.filename || 'pasted log'), severity: s.critical ? 'critical' : s.high ? 'high' : 'medium', source_type: 'log_analysis',
          summary: s.findings + ' findings (' + s.critical + ' critical, ' + s.high + ' high); ' + s.block_candidates + ' block candidates. Top: ' + r.findings.slice(0, 4).map((f) => f.title).join('; ')
        })) }, 'Open case') : null);
      results.appendChild(SOC.card('Response', h('div', { class: 'stack' }, h('p', { class: 'note' }, ioc.length + ' indicators of compromise: ' + ips.length + ' public IPs and scanner user agents. Non-public addresses are never offered.'), actions)));
    }
  }

  SOC.register({ id: 'analysis', label: 'Log analysis', title: 'Log analysis', icon: 'analysis', min: 'analyst', render });
})();
