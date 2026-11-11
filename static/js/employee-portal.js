
      // Server-provided values (templates/employee_portal.html, #portal-data):
      // a JSON block, never interpolated into this code.
      const _portalData = JSON.parse(document.getElementById('portal-data').textContent);
      // This page's own address: /employee_portal, or /my_workspace when a
      // role-holder views their own self-service inside the admin shell.
      const _portalBase = _portalData.base || '/employee_portal';
      // my_workspace.html sets these before loading this file so it keeps its
      // own "last view" memory and landing tab instead of sharing the
      // employee portal's (same origin, so the same localStorage).
      const _viewKey = window.HZ_VIEW_STORAGE_KEY || 'portalView';
      const _defaultView = window.HZ_DEFAULT_VIEW || 'dashboard';

      // Server-sourced strings (incentive titles/notes below, and
      // result.name further down this same block) reach innerHTML with no
      // escaping elsewhere in this file -- admin-set text (an incentive
      // bonus title, an employee's own display name) rendered unescaped
      // into another employee's authenticated session is a stored-XSS
      // sink. Mirrors the escape helper already used in admin.html and
      // _admin_chat_widget.html.
      function escHtml(s) { return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#x27;'); }
      // Server values for HTML built in template literals below come from the
      // #portal-data JSON block and are inserted with escHtml(). Never splice a
      // server value straight into a `...` literal: a backtick or ${ in it would
      // break out and run as script.
      const _portalEmp = _portalData.emp;

      function loadPayslip(year, month, label) {
        const modal   = document.getElementById('payslip-modal');
        const title   = document.getElementById('payslip-modal-title');
        const content = document.getElementById('payslip-modal-content');
        title.textContent = '💳 ' + label + ' — Salary Breakdown';
        content.innerHTML = '<div class="u-ta-center u-c-94a3b8 u-p-40 u-fs-15">Loading…</div>';
        modal.style.display = 'block';
        document.body.style.overflow = 'hidden';
        fetch('/my_payslip_summary/' + year + '/' + month)
          .then(r => r.json())
          .then(d => {
            content.innerHTML = `
              <div class="u-bg-f0f7ff u-br-12 u-p-16-20 u-mb-20 u-d-flex u-ai-center u-gap-14">
                <div class="u-fs-36">👤</div>
                <div>
                  <div class="u-fs-16 u-fw-700 u-c-1e293b">${escHtml(_portalEmp.name)}</div>
                  <div class="u-fs-13 u-c-64748b">${escHtml(_portalEmp.role)} &nbsp;|&nbsp; ${escHtml(label)}</div>
                  <div class="u-fs-12 u-c-94a3b8 u-mt-2">Daily Rate: ₹${d.salary_per_day.toFixed(2)}</div>
                </div>
              </div>
              <div class="u-bd-1-solid-dbeafe u-br-14 u-ov-hidden u-mb-20">
                <div class="u-d-grid u-gtc-1fr-auto-auto u-gap-0 u-p-12-18 u-bg-1e3a8a u-fs-12 u-fw-700 u-c-bfdbfe u-ttr-uppercase u-ls-d05em">
                  <span>Description</span><span class="u-ta-center u-pr-28">Days</span><span class="u-ta-right">Amount</span>
                </div>
                <div class="u-d-grid u-gtc-1fr-auto-auto u-p-14-18 u-fs-14 u-bdt-1-solid-e2e8f0 u-ai-center">
                  <span class="u-d-flex u-ai-center u-gap-8"><span class="u-w-10 u-h-10 u-br-50pct u-bg-16a34a u-d-inline-block"></span><span class="u-c-16a34a u-fw-600">Full Day Present</span></span>
                  <span class="u-ta-center u-pr-28 u-c-1e293b u-fw-600">${Number(d.full_days)}</span>
                  <span class="u-ta-right u-c-1e293b">₹${d.full_earn.toFixed(2)}</span>
                </div>
                <div class="u-d-grid u-gtc-1fr-auto-auto u-p-14-18 u-fs-14 u-bdt-1-solid-e2e8f0 u-bg-fefce8 u-ai-center">
                  <span class="u-d-flex u-ai-center u-gap-8"><span class="u-w-10 u-h-10 u-br-50pct u-bg-d97706 u-d-inline-block"></span><span class="u-c-d97706 u-fw-600">Late – Full Day</span></span>
                  <span class="u-ta-center u-pr-28 u-c-1e293b u-fw-600">${Number(d.late_days)}</span>
                  <span class="u-ta-right u-c-1e293b">₹${d.late_earn.toFixed(2)}</span>
                </div>
                <div class="u-d-grid u-gtc-1fr-auto-auto u-p-14-18 u-fs-14 u-bdt-1-solid-e2e8f0 u-ai-center">
                  <span class="u-d-flex u-ai-center u-gap-8"><span class="u-w-10 u-h-10 u-br-50pct u-bg-f97316 u-d-inline-block"></span><span class="u-c-f97316 u-fw-600">Half Day (×0.5)</span></span>
                  <span class="u-ta-center u-pr-28 u-c-1e293b u-fw-600">${Number(d.half_days)}</span>
                  <span class="u-ta-right u-c-1e293b">₹${d.half_earn.toFixed(2)}</span>
                </div>
                <div class="u-d-grid u-gtc-1fr-auto-auto u-p-14-18 u-fs-14 u-fw-700 u-bdt-2-solid-dbeafe u-bg-f0f7ff u-ai-center">
                  <span class="u-c-1e3a8a">Gross Pay</span><span></span>
                  <span class="u-ta-right u-c-1e3a8a u-fs-16">₹${d.gross.toFixed(2)}</span>
                </div>
                <div class="u-d-grid u-gtc-1fr-auto-auto u-p-14-18 u-fs-14 u-bdt-1-solid-e2e8f0 u-bg-fff5f5 u-ai-center">
                  <span class="u-c-dc2626 u-fw-600">PF, PT &amp; TDS</span><span></span>
                  <span class="u-ta-right u-c-dc2626">−₹${d.statutory.toFixed(2)}</span>
                </div>
                ${(d.incentive && d.incentive > 0) ? `
                <div class="u-d-grid u-gtc-1fr-auto-auto u-p-14-18 u-fs-14 u-bdt-1-solid-e2e8f0 u-bg-f0fdf4 u-ai-center">
                  <div>
                    <span class="u-c-16a34a u-fw-700">🏆 Incentive Bonus</span>
                    ${d.incentive_details && d.incentive_details.length ? '<div class="u-fs-11 u-c-64748b u-mt-4">' + d.incentive_details.map(function(i){return escHtml(i.title) + (i.notes?' — '+escHtml(i.notes):'');}).join(' &nbsp;·&nbsp; ') + '</div>' : ''}
                  </div>
                  <span></span>
                  <span class="u-ta-right u-c-16a34a u-fw-700">+₹${d.incentive.toFixed(2)}</span>
                </div>` : ''}
                <div class="u-d-grid u-gtc-1fr-auto-auto u-p-18-18 u-fs-18 u-fw-700 u-bdt-2-solid-16a34a u-bg-linear-gradien16a897 u-ai-center">
                  <span class="u-c-15803d">💰 Net Pay</span><span></span>
                  <span class="u-ta-right u-c-15803d">₹${d.net.toFixed(2)}</span>
                </div>
              </div>
              <div class="u-d-flex u-gap-12 u-fxw-wrap">
                <a class="u-fx-1 u-minw-160 u-d-inline-flex u-ai-center u-jc-center u-gap-8 u-p-13-24 u-bg-linear-gradiene7a6cb u-c-fff u-br-11 u-fs-14 u-fw-600 u-tdec-none u-shad-0-4-14-rgba37_e838f4" href="/view_payslip/${encodeURIComponent(_portalEmp.id)}/${Number(year)}/${Number(month)}">
                  🖨️ Open & Print Full Slip
                </a>
                <button class="u-fx-1 u-minw-120 u-p-13-24 u-bg-f1f5f9 u-bd-1-solid-e2e8f0 u-c-475569 u-br-11 u-fs-14 u-fw-600 u-cur-pointer" data-on-click="closePayslipModal()">
                  Close
                </button>
              </div>`;
          })
          .catch(() => { content.innerHTML = '<div class="u-c-dc2626 u-p-20 u-ta-center">Failed to load. Please try again.</div>'; });
      }

      function closePayslipModal() {
        document.getElementById('payslip-modal').style.display = 'none';
        document.body.style.overflow = '';
      }
      document.getElementById('payslip-modal').addEventListener('click', function(e) {
        if (e.target === this) closePayslipModal();
      });

      // (date + time now shown in the blue top banner -- static/employee-topbar.js)

      /* ── SIDEBAR TOGGLE: toggleSidebar()/closeSidebar() now come from
         static/employee_sidebar.js, shared with the standalone
         my_performance/my_compoff/my_onboarding pages. ── */
      function showPolicy(tab) {
        var tabs = ['terms','rules','limitations','instructions','posh','resignation','other'];
        tabs.forEach(function(t) {
          var el = document.getElementById('policy-' + t);
          var btn = document.getElementById('ptab-' + t);
          if (el) el.style.display = (t === tab) ? 'block' : 'none';
          if (btn) {
            if (t === tab) {
              btn.style.border = '1.5px solid #3b82f6';
              btn.style.background = '#eff6ff';
              btn.style.color = '#1e40af';
            } else {
              btn.style.border = '1.5px solid #e2e8f0';
              btn.style.background = '#f8fafc';
              btn.style.color = '#64748b';
            }
          }
        });
      }

      var POLICY_CATEGORIES = ['terms','rules','limitations','instructions','posh','resignation'];

      function loadPolicies() {
        fetch('/api/employee/policies')
          .then(function(r) { return r.json(); })
          .then(function(d) {
            var byCategory = {};
            var custom = [];
            (d.policies || []).forEach(function(p) {
              if (POLICY_CATEGORIES.indexOf(p.category) === -1) custom.push(p);
              else byCategory[p.category] = p;
            });
            var otherEl = document.getElementById('policy-other');
            var otherTab = document.getElementById('ptab-other');
            if (otherEl && otherTab && custom.length) {
              otherTab.classList.remove('u-d-none');
              custom.forEach(function(p) {
                var head = document.createElement('div');
                head.className = 'u-bg-eff6ff u-bdl-4-solid-3b82f6 u-p-14-18 u-br-0-10-10-0 u-mb-22';
                var strong = document.createElement('strong');
                strong.textContent = p.title;
                var cat = document.createElement('div');
                cat.className = 'u-fs-13 u-c-64748b';
                cat.textContent = p.category;
                head.appendChild(strong);
                head.appendChild(cat);
                var body = document.createElement('div');
                body.className = 'u-mb-22';
                body.style.whiteSpace = 'pre-line';
                body.textContent = p.body;
                otherEl.appendChild(head);
                otherEl.appendChild(body);
              });
            }
            POLICY_CATEGORIES.forEach(function(cat) {
              var titleEl = document.getElementById('policy-' + cat + '-title');
              var bodyEl  = document.getElementById('policy-' + cat + '-body');
              var p = byCategory[cat];
              if (p) {
                if (titleEl) titleEl.textContent = p.title;
                if (bodyEl) bodyEl.textContent = p.body;
              } else if (bodyEl) {
                bodyEl.textContent = 'No policy has been published for this category yet.';
              }
            });
          })
          .catch(function() {
            POLICY_CATEGORIES.forEach(function(cat) {
              var bodyEl = document.getElementById('policy-' + cat + '-body');
              if (bodyEl) bodyEl.textContent = 'Failed to load policies. Please try again later.';
            });
          });
      }
      loadPolicies();

      /* ── VIEW SWITCHER ── */
      function showView(viewId) {
        document.querySelectorAll('.view').forEach(function(v) {
          v.classList.remove('active');
          v.style.display = 'none';
        });
        var target = document.getElementById('view-' + viewId);
        if (target) { target.classList.add('active'); target.style.display = 'block'; }

        document.querySelectorAll('.sb-item').forEach(function(b) { b.classList.remove('active'); });
        var sbnEl = document.getElementById('sbn-' + viewId);
        if (sbnEl) sbnEl.classList.add('active');

        var ac = document.getElementById('appContent');
        if (ac) ac.scrollTo({ top: 0, behavior: 'smooth' });
        history.replaceState(null, '', '#' + viewId);
        try { localStorage.setItem(_viewKey, viewId); } catch(e) {}
        // static/employee_sidebar.js (which defines closeSidebar) loads via
        // a <script src> further down this document than this block -- the
        // very first showView() call happens synchronously during this
        // block's own page-load-time "restore last active view" IIFE below,
        // before that later tag has run. Calling closeSidebar()
        // unconditionally at that point threw a ReferenceError and aborted
        // the rest of this script block, silently leaving every var
        // declared after this point (attCfg included) unset -- breaking
        // Mark Attendance and anything else defined later in this block.
        if (typeof closeSidebar === 'function') closeSidebar();
      }


      /* ── CALENDAR ── */
      (function() {
        const data     = _portalData.cal.data;
        const holNames = _portalData.cal.holNames;
        const firstDow = _portalData.cal.firstDow;
        const year     = _portalData.cal.year;
        const month    = _portalData.cal.month;
        const daysInMonth = new Date(year, month, 0).getDate();
        const now      = new Date();
        const todayDay = (now.getFullYear() === year && now.getMonth() + 1 === month) ? now.getDate() : -1;

        const tipMap = {
          full:    "Full Day",
          late:    "Late – Full Day",
          half:    "Half Day",
          absent:  "Absent",
          holiday: "Holiday",
          weekend: "Sunday",
          future:  ""
        };

        const grid = document.getElementById("calGrid");

        for (let i = 0; i < firstDow; i++) {
          const el = document.createElement("div");
          el.className = "cal-day cal-empty";
          grid.appendChild(el);
        }
        for (let d = 1; d <= daysInMonth; d++) {
          const type = data[d] || "future";
          const el   = document.createElement("div");
          el.className = `cal-day cal-${type}`;
          if (d === todayDay) el.classList.add("cal-today");
          const tip = type === "holiday" && holNames[d] ? holNames[d] : tipMap[type];
          if (tip) el.setAttribute("data-tip", tip);
          el.innerHTML = `<span class="day-num">${Number(d)}</span>`;
          grid.appendChild(el);
        }
      })();

      function applyFilter() {
        const m = document.getElementById("selMonth").value;
        const y = document.getElementById("selYear").value;
        window.location.href = `${_portalBase}?month=${m}&year=${y}#attendance`;
      }

      // Keep PDF link in sync with dropdowns
      document.getElementById("selMonth").addEventListener("change", updatePdfLink);
      document.getElementById("selYear").addEventListener("change",  updatePdfLink);
      function updatePdfLink() {
        const m = document.getElementById("selMonth").value;
        const y = document.getElementById("selYear").value;
        document.getElementById("pdfLink").href = `/my_attendance_pdf?month=${m}&year=${y}`;
      }

      // Default leave date to tomorrow
      (function() {
        const d = new Date();
        d.setDate(d.getDate() + 1);
        const iso = d.toISOString().split('T')[0];
        var _ldEl = document.getElementById('leave-date-start') || document.getElementById('leave-date');
        if (_ldEl) { _ldEl.value = iso; _ldEl.min = iso; }
        var _ldEl2 = document.getElementById('leave-date-end');
        if (_ldEl2) { _ldEl2.min = iso; }
      })();

      // Reason chip selection
      document.querySelectorAll('.reason-chip').forEach(function(chip) {
        chip.addEventListener('click', function() {
          document.querySelectorAll('.reason-chip').forEach(function(c) {
            c.classList.remove('active');
          });
          chip.classList.add('active');
          var reason = chip.dataset.reason;
          if (reason === '__other__') {
            document.getElementById('other-group').style.display = 'block';
            document.getElementById('reason-input').value = '';
            document.getElementById('other-text').focus();
          } else {
            document.getElementById('other-group').style.display = 'none';
            document.getElementById('reason-input').value = reason;
          }
        });
      });

      document.getElementById('other-text').addEventListener('input', function() {
        document.getElementById('reason-input').value = this.value;
      });

      // Leave date range counter
      function toggleHalfDay(checked) {
        var wrap    = document.getElementById('to-date-wrap');
        var badge   = document.getElementById('leave-days-badge');
        var picker  = document.getElementById('session-picker');
        if (checked) {
          wrap.style.display   = 'none';
          picker.style.display = 'block';
          highlightSession();
          var sel = document.querySelector('input[name="half_day_session"]:checked');
          badge.textContent = '½ Half Day – ' + (sel ? sel.value : 'Morning') + ' selected';
          badge.style.display = 'block';
        } else {
          wrap.style.display   = 'flex';
          picker.style.display = 'none';
          updateLeaveDays();
        }
      }
      function highlightSession() {
        var sel = document.querySelector('input[name="half_day_session"]:checked');
        var lm  = document.getElementById('lbl-morning');
        var la  = document.getElementById('lbl-afternoon');
        if (!lm || !la) return;
        lm.style.borderColor = (sel && sel.value === 'Morning')   ? '#1e3a8a' : '#dbeafe';
        la.style.borderColor = (sel && sel.value === 'Afternoon') ? '#1e3a8a' : '#dbeafe';
        lm.style.background  = (sel && sel.value === 'Morning')   ? '#eff6ff' : '#fff';
        la.style.background  = (sel && sel.value === 'Afternoon') ? '#eff6ff' : '#fff';
        var badge = document.getElementById('leave-days-badge');
        if (badge && badge.style.display !== 'none')
          badge.textContent = '½ Half Day – ' + (sel ? sel.value : 'Morning') + ' selected';
      }
      function updateLeaveDays() {
        var isHalf = document.getElementById('half-day-chk') && document.getElementById('half-day-chk').checked;
        if (isHalf) return;
        var s = document.getElementById('leave-date-start').value;
        var e = document.getElementById('leave-date-end').value;
        var badge = document.getElementById('leave-days-badge');
        if (!s) { badge.style.display = 'none'; return; }
        if (!e || e < s) {
          document.getElementById('leave-date-end').value = s;
          e = s;
        }
        var days = Math.round((new Date(e) - new Date(s)) / 86400000) + 1;
        badge.textContent = days + ' day' + (days > 1 ? 's' : '') + ' selected';
        badge.style.display = 'block';
      }
      var _ldsEl = document.getElementById('leave-date-start');
      var _ldeEl = document.getElementById('leave-date-end');
      if (_ldsEl) _ldsEl.addEventListener('change', function() {
        if (_ldeEl && (!_ldeEl.value || _ldeEl.value < this.value)) _ldeEl.value = this.value;
        updateLeaveDays();
      });
      if (_ldeEl) _ldeEl.addEventListener('change', updateLeaveDays);

      var _lfEl = document.getElementById('leave-form');
      if (_lfEl) _lfEl.addEventListener('submit', function(e) {
        var s  = _ldsEl ? _ldsEl.value : '';
        var en = _ldeEl ? _ldeEl.value : '';
        if (!s || !en) { e.preventDefault(); alert('Please select both From and To dates.'); return; }
        if (en < s)    { e.preventDefault(); alert('To Date cannot be before From Date.'); return; }
        if (!document.getElementById('reason-input').value.trim()) {
          e.preventDefault();
          alert('Please select a reason or click "Other..." to type your reason.');
        }
      });

      // Resign chip selection — shared initializer for both forms
      function initResignForm(sfx) {
        var form = document.getElementById('resign-form' + sfx);
        if (!form) return;
        var chipsEl    = document.getElementById('resign-chips' + sfx);
        var reasonEl   = document.getElementById('resign-reason-input' + sfx);
        var otherGrp   = document.getElementById('resign-other-group' + sfx);
        var otherTxt   = document.getElementById('resign-other-text' + sfx);
        var dateEl     = document.getElementById('resign-date' + sfx);
        var confirmEl  = document.getElementById('resign-confirm' + sfx);

        chipsEl.querySelectorAll('.reason-chip').forEach(function(chip) {
          chip.addEventListener('click', function() {
            chipsEl.querySelectorAll('.reason-chip').forEach(function(c) { c.classList.remove('active'); });
            chip.classList.add('active');
            var reason = chip.dataset.reason;
            if (reason === '__resign_other__') {
              otherGrp.style.display = 'block';
              reasonEl.value = '';
              otherTxt.focus();
            } else {
              otherGrp.style.display = 'none';
              reasonEl.value = reason;
            }
          });
        });

        otherTxt.addEventListener('input', function() {
          reasonEl.value = this.value;
        });

        // Set min last working day to 30 days from today (1-month notice period)
        (function() {
          var d = new Date();
          d.setDate(d.getDate() + 30);
          var iso = d.toISOString().split('T')[0];
          dateEl.min   = iso;
          dateEl.value = iso;
        })();

        form.addEventListener('submit', function(e) {
          if (!reasonEl.value.trim()) {
            e.preventDefault();
            alert('Please select a reason for resignation.');
            return;
          }
          if (!confirmEl.checked) {
            e.preventDefault();
            alert('Please confirm you understand this action is official.');
          }
        });
      }

      initResignForm('');    // main form (no active resignation)
      initResignForm('-d');  // re-submit form (after Declined)

      // Set payslip selector to current month/year on load
      (function() {
        var now = new Date();
        var sel = document.getElementById('ps-month-sel');
        var ySel = document.getElementById('ps-year-sel');
        if (sel) sel.value = now.getMonth() + 1;
        if (ySel) ySel.value = now.getFullYear();
      })();

      function viewInlinePayslip() {
        var month = document.getElementById('ps-month-sel').value;
        var year  = document.getElementById('ps-year-sel').value;
        var monthNames = ['','January','February','March','April','May','June','July','August','September','October','November','December'];
        var label = monthNames[parseInt(month)] + ' ' + year;
        var box   = document.getElementById('inline-payslip-box');
        var empty = document.getElementById('inline-payslip-empty');

        box.style.display = 'block';
        empty.style.display = 'none';
        box.innerHTML = '<div class="u-ta-center u-p-40 u-c-94a3b8 u-fs-14"><i class="ti ti-loader u-fs-28 u-d-block u-mb-10"></i>Loading…</div>';

        fetch('/my_payslip_summary/' + year + '/' + month)
          .then(r => r.json())
          .then(d => {
            box.innerHTML = `
              <div class="u-bd-1-solid-dbeafe u-br-12 u-ov-hidden">
                <!-- Header -->
                <div class="u-bg-linear-gradienff64c5 u-p-18-22 u-d-flex u-ai-center u-jc-space-between u-fxw-wrap u-gap-12">
                  <div class="u-d-flex u-ai-center u-gap-14">
                    <div class="u-w-46 u-h-46 u-br-12 u-bg-rgba255_255_255_d15 u-d-flex u-ai-center u-jc-center u-fs-22">👤</div>
                    <div>
                      <div class="u-fs-16 u-fw-800 u-c-fff">${escHtml(_portalEmp.name)}</div>
                      <div class="u-fs-12 u-c-rgba255_255_255_d7">${escHtml(_portalEmp.role)} &nbsp;·&nbsp; ${escHtml(label)}</div>
                      <div class="u-fs-11 u-c-rgba255_255_255_d5 u-mt-2">Daily Rate: ₹${d.salary_per_day.toFixed(2)}</div>
                    </div>
                  </div>
                  <div class="u-d-flex u-gap-8">
                    <a class="u-d-inline-flex u-ai-center u-gap-6 u-p-8-16 u-bg-rgba255_255_255_d15 u-c-fff u-bd-1-solid-rgba255b647a u-br-8 u-fs-12 u-fw-700 u-tdec-none" href="/download_payslip/${encodeURIComponent(_portalEmp.id)}/${Number(year)}/${Number(month)}" download>
                      <i class="ti ti-download"></i> Download
                    </a>
                    <a class="u-d-inline-flex u-ai-center u-gap-6 u-p-8-16 u-bg-fff u-c-1e3a8a u-br-8 u-fs-12 u-fw-700 u-tdec-none" href="/view_payslip/${encodeURIComponent(_portalEmp.id)}/${Number(year)}/${Number(month)}">
                      <i class="ti ti-printer"></i> Print
                    </a>
                    <a class="u-d-inline-flex u-ai-center u-gap-6 u-p-8-16 u-bg-rgba255_255_255_d15 u-c-fff u-bd-1-solid-rgba255b647a u-br-8 u-fs-12 u-fw-700 u-tdec-none" href="/download_tax_statement/${encodeURIComponent(_portalEmp.id)}/${Number(year)}" title="Annual Form 16 / tax statement for ${Number(year)}">
                      <i class="ti ti-file-invoice"></i> Tax Statement
                    </a>
                  </div>
                </div>

                <!-- Earnings rows -->
                <div class="u-p-0-22-4">
                  <div class="u-p-8-0 u-mt-8 u-fs-11 u-fw-700 u-c-94a3b8 u-ttr-uppercase u-ls-d5 u-d-grid u-gtc-1fr-80-110 u-bdb-1-solid-f1f5f9">
                    <span>Description</span><span class="u-ta-center">Days</span><span class="u-ta-right">Amount</span>
                  </div>
                  <div class="u-p-12-0 u-d-grid u-gtc-1fr-80-110 u-ai-center u-bdb-1-solid-f1f5f9">
                    <span class="u-d-flex u-ai-center u-gap-8"><span class="u-w-8 u-h-8 u-br-50pct u-bg-16a34a u-d-inline-block"></span><span class="u-fs-13 u-c-16a34a u-fw-600">Full Day Present</span></span>
                    <span class="u-ta-center u-fs-13 u-fw-700 u-c-1e293b">${Number(d.full_days)}</span>
                    <span class="u-ta-right u-fs-13 u-c-1e293b">₹${d.full_earn.toFixed(2)}</span>
                  </div>
                  <div class="u-p-12-0 u-d-grid u-gtc-1fr-80-110 u-ai-center u-bdb-1-solid-f1f5f9 u-bg-fefce8">
                    <span class="u-d-flex u-ai-center u-gap-8 u-pl-2"><span class="u-w-8 u-h-8 u-br-50pct u-bg-d97706 u-d-inline-block"></span><span class="u-fs-13 u-c-d97706 u-fw-600">Late – Full Day</span></span>
                    <span class="u-ta-center u-fs-13 u-fw-700 u-c-1e293b">${Number(d.late_days)}</span>
                    <span class="u-ta-right u-fs-13 u-c-1e293b">₹${d.late_earn.toFixed(2)}</span>
                  </div>
                  <div class="u-p-12-0 u-d-grid u-gtc-1fr-80-110 u-ai-center u-bdb-1-solid-f1f5f9">
                    <span class="u-d-flex u-ai-center u-gap-8 u-pl-2"><span class="u-w-8 u-h-8 u-br-50pct u-bg-f97316 u-d-inline-block"></span><span class="u-fs-13 u-c-f97316 u-fw-600">Half Day (×0.5)</span></span>
                    <span class="u-ta-center u-fs-13 u-fw-700 u-c-1e293b">${Number(d.half_days)}</span>
                    <span class="u-ta-right u-fs-13 u-c-1e293b">₹${d.half_earn.toFixed(2)}</span>
                  </div>
                  <div class="u-p-12-0 u-d-grid u-gtc-1fr-80-110 u-ai-center u-bdb-2-solid-dbeafe u-bg-f0f7ff">
                    <span class="u-fs-13 u-fw-800 u-c-1e3a8a">Gross Pay</span><span></span>
                    <span class="u-ta-right u-fs-15 u-fw-800 u-c-1e3a8a">₹${d.gross.toFixed(2)}</span>
                  </div>
                  <div class="u-p-12-0 u-d-grid u-gtc-1fr-80-110 u-ai-center u-bdb-1-solid-f1f5f9 u-bg-fff5f5">
                    <span class="u-fs-13 u-c-dc2626 u-fw-600">PF, PT &amp; TDS</span><span></span>
                    <span class="u-ta-right u-fs-13 u-c-dc2626">−₹${d.statutory.toFixed(2)}</span>
                  </div>
                  ${d.incentive > 0 ? `<div class="u-p-12-0 u-d-grid u-gtc-1fr-80-110 u-ai-center u-bdb-1-solid-f1f5f9 u-bg-f0fdf4">
                    <span class="u-fs-13 u-c-16a34a u-fw-700">🏆 Incentive Bonus</span><span></span>
                    <span class="u-ta-right u-fs-13 u-fw-700 u-c-16a34a">+₹${d.incentive.toFixed(2)}</span>
                  </div>` : ''}
                  ${d.ot_pay && d.ot_pay > 0 ? `<div class="u-p-12-0 u-d-grid u-gtc-1fr-80-110 u-ai-center u-bdb-1-solid-f1f5f9 u-bg-fefce8">
                    <span class="u-fs-13 u-c-d97706 u-fw-700">⏰ Approved Overtime <span class="u-fs-11 u-fw-600">(not included in payroll)</span></span><span></span>
                    <span class="u-ta-right u-fs-13 u-fw-700 u-c-d97706">₹${d.ot_pay.toFixed(2)}</span>
                  </div>` : ''}
                  <div class="u-p-16-0 u-d-grid u-gtc-1fr-80-110 u-ai-center u-bg-linear-gradien16a897 u-bdt-2-solid-16a34a">
                    <span class="u-fs-16 u-fw-800 u-c-15803d">💰 Net Pay</span><span></span>
                    <span class="u-ta-right u-fs-20 u-fw-800 u-c-15803d">₹${d.net.toFixed(2)}</span>
                  </div>
                </div>
              </div>
            `;
          })
          .catch(() => {
            box.innerHTML = '<div class="u-ta-center u-p-30 u-c-ef4444 u-fs-13">Failed to load payslip. Please try again.</div>';
          });
      }

      function switchEarningsTab(tab) {
        ['salary','incentives','overtime'].forEach(function(t) {
          document.getElementById('epanel-' + t).style.display    = tab === t ? '' : 'none';
          document.getElementById('etab-' + t).style.background   = tab === t ? '#1e3a8a' : 'transparent';
          document.getElementById('etab-' + t).style.color        = tab === t ? '#fff' : '#64748b';
          document.getElementById('etab-' + t).style.fontWeight   = tab === t ? '700' : '600';
        });
      }

      // On page load: restore last active view from hash → localStorage → default
      (function() {
        var validViews = ['dashboard','workspace-overview','attendance','apply-leave','earnings','holidays','tickets','my-profile','resign','policies','compoff','performance','onboarding'];
        var hash    = window.location.hash.replace('#', '');
        var stored  = '';
        try { stored = localStorage.getItem(_viewKey) || ''; } catch(e) {}
        var view = (hash && validViews.indexOf(hash) !== -1)   ? hash
                 : (stored && validViews.indexOf(stored) !== -1) ? stored
                 : _defaultView;
        showView(view);
      })();

      /* ── PHOTO PREVIEW ── */
      function openResignGate() {
        document.getElementById('resign-gate-input').value = '';
        document.getElementById('resign-gate-btn').disabled = true;
        document.getElementById('resign-gate-btn').style.background = '#d1d5db';
        document.getElementById('resign-gate-btn').style.color = '#9ca3af';
        document.getElementById('resign-gate-btn').style.cursor = 'not-allowed';
        document.getElementById('resign-gate-overlay').style.display = 'flex';
      }
      function closeResignGate() {
        document.getElementById('resign-gate-overlay').style.display = 'none';
      }
      function checkResignGate() {
        var val = document.getElementById('resign-gate-input').value.trim().toUpperCase();
        var btn = document.getElementById('resign-gate-btn');
        if (val === 'RESIGN') {
          btn.disabled = false;
          btn.style.background = '#b91c1c';
          btn.style.color = '#fff';
          btn.style.cursor = 'pointer';
        } else {
          btn.disabled = true;
          btn.style.background = '#d1d5db';
          btn.style.color = '#9ca3af';
          btn.style.cursor = 'not-allowed';
        }
      }
      function confirmResignGate() {
        closeResignGate();
        showView('resign');
        document.getElementById('sbn-resign').classList.add('active');
      }
      // Close modal on overlay click
      document.getElementById('resign-gate-overlay').addEventListener('click', function(e) {
        if (e.target === this) closeResignGate();
      });

      /* ── ATTENDANCE SCANNER (QR / Face / Fingerprint, per company auth config) ── */
      var wfhStream   = null;
      var wfhScanLoop = null;
      var attCfg      = _portalData.attCfg;
      var myEmpId     = _portalData.emp.id;

      function pickAuthCombo() {
        // Face enabled => only face-inclusive methods (server-enforced too).
        if (attCfg.face_enabled) return attCfg.qr_enabled ? "qr_face" : null;
        if (attCfg.qr_enabled && attCfg.fingerprint_enabled) return "qr_fingerprint";
        if (attCfg.fingerprint_enabled) return "fingerprint_only";
        if (attCfg.qr_enabled) return "qr_only";
        return null;
      }

      function openWfhScanner() {
        resetWfhUI();
        var combo = pickAuthCombo();
        var labels = {
          qr_face: "Start QR + Face Scanner", qr_fingerprint: "Start QR + Fingerprint Check-In",
          fingerprint_only: "Start Fingerprint Check-In", qr_only: "Start QR Check-In",
        };
        var subtitles = {
          qr_face: "QR code + face verification + location check",
          qr_fingerprint: "QR code + fingerprint verification + location check",
          fingerprint_only: "Fingerprint verification + location check",
          qr_only: "QR code + location check",
        };
        document.getElementById("wfhStartBtnLabel").textContent = labels[combo] || "Start Attendance Check-In";
        document.getElementById("wfhSubtitle").textContent = subtitles[combo] || "Location check + verification";
        document.getElementById("wfhScanModal").style.display = "flex";
      }
      function closeWfhScanner() {
        stopWfhCamera();
        document.getElementById("wfhScanModal").style.display = "none";
      }
      function resetWfhUI() {
        document.getElementById("wfhStartBtn").disabled       = false;
        document.getElementById("wfhResult").style.display    = "none";
        document.getElementById("wfhError").style.display     = "none";
        document.getElementById("wfhStatus").style.display    = "none";
        document.getElementById("wfhPhaseLabel").style.display= "none";
        document.getElementById("wfhScanLine").style.display  = "none";
        document.getElementById("wfhPlaceholder").style.display = "flex";
        document.getElementById("wfhVideo").style.display     = "none";
      }
      function wfhSetStatus(msg) {
        var s = document.getElementById("wfhStatus");
        s.textContent   = msg;
        s.style.display = "block";
      }
      function wfhShowError(msg) {
        document.getElementById("wfhError").textContent   = "❌ " + msg;
        document.getElementById("wfhError").style.display = "block";
        document.getElementById("wfhStatus").style.display= "none";
      }
      function wfhSetPhase(phase) {
        var lbl = document.getElementById("wfhPhaseLabel");
        lbl.style.display = "block";
        if (phase === "qr") {
          lbl.textContent = "📷 Show your QR code to the camera";
          lbl.style.color = "#2563eb";
          document.getElementById("wfhScanLine").style.display = "block";
        } else if (phase === "face") {
          lbl.textContent = "👤 QR detected! Now look at the camera";
          lbl.style.color = "#7c3aed";
          document.getElementById("wfhScanLine").style.display = "none";
        } else if (phase === "fingerprint") {
          lbl.textContent = "👆 Touch your fingerprint sensor now…";
          lbl.style.color = "#7c3aed";
          document.getElementById("wfhScanLine").style.display = "none";
        } else if (phase === "ok") {
          lbl.textContent = "✅ Verified! Submitting…";
          lbl.style.color = "#16a34a";
        }
      }
      async function wfhOpenCamera(facingMode) {
        if (wfhStream) { wfhStream.getTracks().forEach(function(t){ t.stop(); }); }
        wfhStream = await navigator.mediaDevices.getUserMedia({
          video: { facingMode: { ideal: facingMode }, width: { ideal: 640 }, height: { ideal: 640 } },
          audio: false,
        });
        var vid = document.getElementById("wfhVideo");
        vid.srcObject = wfhStream;
        vid.style.display = "block";
        document.getElementById("wfhPlaceholder").style.display = "none";
        return new Promise(function(res) { vid.onloadedmetadata = function() { vid.play(); res(); }; });
      }
      function stopWfhCamera() {
        if (wfhScanLoop) { cancelAnimationFrame(wfhScanLoop); wfhScanLoop = null; }
        if (wfhStream)   { wfhStream.getTracks().forEach(function(t){ t.stop(); }); wfhStream = null; }
      }
      function wfhCaptureFrame() {
        var vid = document.getElementById("wfhVideo");
        var c   = document.getElementById("wfhCanvas");
        c.width = vid.videoWidth || 640; c.height = vid.videoHeight || 640;
        c.getContext("2d").drawImage(vid, 0, 0);
        return c.toDataURL("image/jpeg", 0.85).split(",")[1];
      }
      /* Liveness burst -- same protocol as templates/index.html (utils/liveness.py). */
      function wfhSleep(ms) { return new Promise(function(r) { setTimeout(r, ms); }); }
      function wfhCaptureSmallFrame(maxW) {
        var vid = document.getElementById("wfhVideo");
        var w0 = vid.videoWidth || 640, h0 = vid.videoHeight || 480;
        var scale = Math.min(1, maxW / w0);
        var c = document.createElement("canvas");
        c.width = Math.round(w0 * scale); c.height = Math.round(h0 * scale);
        c.getContext("2d").drawImage(vid, 0, 0, c.width, c.height);
        return c.toDataURL("image/jpeg", 0.7).split(",")[1];
      }
      async function wfhCaptureLivenessBurst(employeeId) {
        var faceImage = wfhCaptureFrame();
        var challenge = null;
        try {
          var r = await fetch("/api/attendance/challenge", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ employee_id: employeeId }),
          });
          var j = await r.json();
          if (j && j.ok) challenge = j;
        } catch (e) { /* older server / transient failure: single-frame fallback */ }
        if (!challenge) return { faceImage: faceImage, liveness: null };
        wfhSetStatus(challenge.instruction);
        await wfhSleep(1200);
        var frames = [];
        var total = Math.min(12, challenge.max_frames || 12);
        for (var i = 0; i < total; i++) {
          frames.push(wfhCaptureSmallFrame(480));
          await wfhSleep(150);
        }
        return {
          faceImage: faceImage,
          liveness: { challenge_id: challenge.challenge_id, nonce: challenge.nonce, frames: frames },
        };
      }
      function wfhStartQRScan() {
        return new Promise(function(resolve, reject) {
          var vid      = document.getElementById("wfhVideo");
          var c        = document.getElementById("wfhCanvas");
          var deadline = Date.now() + 30000;
          function frame() {
            if (Date.now() > deadline) { reject(new Error("QR code not detected within 30 seconds. Please try again.")); return; }
            if (vid.readyState >= 2) {
              c.width = vid.videoWidth; c.height = vid.videoHeight;
              var ctx  = c.getContext("2d");
              ctx.drawImage(vid, 0, 0);
              var img  = ctx.getImageData(0, 0, c.width, c.height);
              var code = jsQR(img.data, img.width, img.height, { inversionAttempts: "dontInvert" });
              if (code && code.data.trim()) { resolve(code.data.trim()); return; }
            }
            wfhScanLoop = requestAnimationFrame(frame);
          }
          wfhScanLoop = requestAnimationFrame(frame);
        });
      }
      function wfhToBytes(b64url) {
        var b64 = b64url.replace(/-/g,'+').replace(/_/g,'/');
        var pad = b64.length % 4 ? '='.repeat(4 - b64.length % 4) : '';
        return Uint8Array.from(atob(b64 + pad), function(c) { return c.charCodeAt(0); });
      }
      function wfhToB64url(buf) {
        var s = ""; new Uint8Array(buf).forEach(function(b){ s += String.fromCharCode(b); });
        return btoa(s).replace(/\+/g,'-').replace(/\//g,'_').replace(/=/g,'');
      }
      function wfhCredentialToJSON(cred) {
        if (cred.toJSON) return cred.toJSON();
        return {
          id: cred.id, rawId: wfhToB64url(cred.rawId), type: cred.type,
          response: {
            clientDataJSON:    wfhToB64url(cred.response.clientDataJSON),
            authenticatorData: wfhToB64url(cred.response.authenticatorData),
            signature:         wfhToB64url(cred.response.signature),
            userHandle:        cred.response.userHandle ? wfhToB64url(cred.response.userHandle) : null,
          },
          clientExtensionResults: cred.getClientExtensionResults ? cred.getClientExtensionResults() : {},
        };
      }
      async function wfhVerifyFingerprint(employeeId) {
        // audit-ok: rejects into startWfhFlow(), which catches and shows the error
        var optR = await fetch("/webauthn/authentication-options?emp_id=" + encodeURIComponent(employeeId));
        var opts = await optR.json();
        if (opts.ok === false || !opts.allowCredentials || !opts.allowCredentials.length) {
          throw new Error((opts && (opts.error || opts.msg)) || "No fingerprint enrolled for this account. Enroll one in Settings first.");
        }
        var assertion = await navigator.credentials.get({
          publicKey: {
            challenge:        wfhToBytes(opts.challenge),
            rpId:             opts.rpId,
            allowCredentials: (opts.allowCredentials || []).map(function(c) {
              return { type: c.type, id: wfhToBytes(c.id), transports: c.transports || ["internal"] };
            }),
            userVerification: opts.userVerification || "required",
            timeout:          opts.timeout || 60000,
          }
        });
        // audit-ok: rejects into startWfhFlow(), which catches and shows the error
        var verR = await fetch("/api/employee/webauthn-verify-challenge", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ emp_id: employeeId, credential: wfhCredentialToJSON(assertion) }),
        });
        var verJson = await verR.json();
        if (!verJson.ok) { throw new Error(verJson.msg || "Fingerprint verification failed."); }
      }

      async function startWfhFlow() {
        var combo = pickAuthCombo();
        if (!combo) { wfhShowError("No check-in method is enabled. Contact your admin."); return; }

        document.getElementById("wfhStartBtn").disabled    = true;
        document.getElementById("wfhResult").style.display = "none";
        document.getElementById("wfhError").style.display  = "none";
        try {
          var lat = null, lon = null;
          if (attCfg.location_enabled) {
            wfhSetStatus("Getting your location…");
            var position = await new Promise(function(res, rej) {
              navigator.geolocation.getCurrentPosition(res, rej, { timeout: 10000 });
            });
            lat = position.coords.latitude;
            lon = position.coords.longitude;
          }

          var employeeId = myEmpId;
          var faceImage  = null;
          var wfhLiveness = null;

          if (combo === "qr_face" || combo === "qr_fingerprint" || combo === "qr_only") {
            wfhSetStatus("Opening camera…");
            await wfhOpenCamera("environment");
            wfhSetPhase("qr");
            document.getElementById("wfhStatus").style.display = "none";
            employeeId = await wfhStartQRScan();
            if (wfhScanLoop) { cancelAnimationFrame(wfhScanLoop); wfhScanLoop = null; }
          }

          if (combo === "qr_face") {
            wfhSetPhase("face");
            wfhSetStatus("Switching to face camera…");
            await wfhOpenCamera("user");
            document.getElementById("wfhStatus").style.display = "none";
            await new Promise(function(r) { setTimeout(r, 2000); });
            wfhSetPhase("ok");
            var burst = await wfhCaptureLivenessBurst(employeeId);
            faceImage = burst.faceImage;
            wfhLiveness = burst.liveness;
          }
          stopWfhCamera();

          if (combo === "qr_fingerprint" || combo === "fingerprint_only") {
            wfhSetPhase("fingerprint");
            document.getElementById("wfhStatus").style.display = "none";
            await wfhVerifyFingerprint(employeeId);
            wfhSetPhase("ok");
          }

          wfhSetStatus("Verifying identity…");

          var body = { employee_id: employeeId, auth_combo: combo, channel: "portal" };
          if (faceImage)   body.face_image = faceImage;
          if (wfhLiveness) body.liveness = wfhLiveness;
          if (lat !== null) { body.lat = lat; body.lon = lon; }

          var resp = await fetch("/attendance", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
          });
          var result = await resp.json();

          document.getElementById("wfhStatus").style.display    = "none";
          document.getElementById("wfhPhaseLabel").style.display = "none";
          document.getElementById("wfhStartBtn").disabled        = false;

          if (!result.ok) { wfhShowError(result.msg); return; }

          var statusMap = {
            "Full Day Login":  "Full Day Login ✅",  "Late Login": "Late Login ⚠️",
            "Half Day Login":  "Half Day Login ⚠️",  "Completed":  "Completed ✅",
            "Early Logout":    "Early Logout ⚠️",    "Half Day Logout": "Half Day Logout ⚠️",
          };
          var typeMap = {
            "Full Day": "Full Day ✅", "Late - Full Day": "Late – Full Day ⚠️", "Half Day": "Half Day ⚠️",
          };
          var resBox    = document.getElementById("wfhResult");
          var resTitle  = result.type === "login" ? "📍 Login Recorded ✅" : "📍 Logout Recorded ✅";
          var resStatus = statusMap[result.status] || result.status;
          var resType   = result.att_type ? (typeMap[result.att_type] || result.att_type) : "";
          resBox.innerHTML =
            "<div style='font-size:17px;font-weight:800;color:#15803d;margin-bottom:12px;'>" + resTitle + "</div>" +
            "<div style='display:flex;justify-content:space-between;padding:7px 0;border-bottom:1px solid #d1fae5;font-size:15px;'><span style='color:#64748b;'>Employee</span><span style='font-weight:700;color:#2563eb;'>" + escHtml(result.name) + "</span></div>" +
            "<div style='display:flex;justify-content:space-between;padding:7px 0;border-bottom:1px solid #d1fae5;font-size:15px;'><span style='color:#64748b;'>Time</span><span style='font-weight:700;color:#2563eb;'>" + escHtml(result.time) + "</span></div>" +
            (resType
              ? "<div style='display:flex;justify-content:space-between;padding:7px 0;border-bottom:1px solid #d1fae5;font-size:15px;'>"
              : "<div style='display:flex;justify-content:space-between;padding:7px 0;font-size:15px;'>"
            ) + "<span style='color:#64748b;'>Status</span><span style='font-weight:700;color:#16a34a;'>" + escHtml(resStatus) + "</span></div>" +
            (resType ? "<div style='display:flex;justify-content:space-between;padding:7px 0;font-size:15px;'><span style='color:#64748b;'>Attendance Type</span><span style='font-weight:700;color:#16a34a;'>" + escHtml(resType) + "</span></div>" : "");
          resBox.style.display = "block";
        } catch(err) {
          stopWfhCamera();
          document.getElementById("wfhStartBtn").disabled = false;
          document.getElementById("wfhStatus").style.display = "none";
          if (err.code === 1) {
            wfhShowError("Location access denied. Please allow location in your browser settings.");
          } else {
            wfhShowError(err.message || "Something went wrong. Please try again.");
          }
        }
      }

      function previewPhoto(input) {
        if (!input.files || !input.files[0]) return;
        var file = input.files[0];
        HZPhotoCheck.check(file, function (error) {
          if (error) {
            input.value = '';
            document.getElementById('photoFileName').textContent = 'No file chosen';
            HZPhotoCheck.warn(error);
            return;
          }
          uploadPhoto(file);
        });
      }

      function uploadPhoto(file) {
        document.getElementById('photoFileName').textContent = 'Uploading ' + file.name + '…';
        var reader = new FileReader();
        reader.onload = function(e) {
          var prev = document.getElementById('profilePreview');
          var init = document.getElementById('profileInitial');
          if (prev) {
            prev.src = e.target.result;
          } else if (init) {
            var img = document.createElement('img');
            img.id = 'profilePreview';
            img.src = e.target.result;
            Object.assign(img.style, {width:'100%', height:'100%', objectFit:'cover'});
            init.parentNode.replaceChild(img, init);
          }
        };
        reader.readAsDataURL(file);
        // Submit immediately instead of waiting on a separate "Upload"
        // click -- the photo picker is a single <label>-triggered file
        // input with no visible affordance suggesting a second step is
        // needed, so requiring one meant a selected photo could look
        // "changed" here (via the local preview above) while never
        // actually reaching the server -- explaining reports of a photo
        // update that showed in Profile but never appeared anywhere else
        // (sidebar, Dashboard), because it was never actually saved.
        document.getElementById('photoForm').submit();
      }
    