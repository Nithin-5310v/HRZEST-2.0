
    /* ── handlers bound through data-on-* (static/hz-actions.js) ── */
    function useCurrentOfficeLocation() {
      if (!navigator.geolocation) return;
      navigator.geolocation.getCurrentPosition(function (p) {
        document.querySelector('[name=office_lat]').value = p.coords.latitude;
        document.querySelector('[name=office_lon]').value = p.coords.longitude;
      });
    }

    function showEmail2faLost(ev, link) {
      ev.preventDefault();
      document.getElementById('email-2fa-lost').classList.remove('u-d-none');
      link.classList.add('u-d-none');
    }

    function editRecoveryEmail() {
      document.getElementById('recoveryEmailView').classList.add('u-d-none');
      document.getElementById('recoveryEmailForm').classList.remove('u-d-none');
      document.getElementById('recoveryEmailInput').focus();
    }

    function markFilledInput(input) {
      input.style.borderColor = input.value ? '#1e3a8a' : '#e2e8f0';
    }

    /* ── 3-TAB + DROPDOWN SUB-NAV ── */
    var groupMap  = { company:'sys', attendance:'org',payroll:'fin',salary:'fin',disbursement:'fin',billing:'fin',email:'sys' };
    var firstSub  = { org:'attendance', fin:'payroll', sys:'company' };
    var crumbData = {
      attendance: { icon:'<i class="ti ti-fingerprint u-c-16a34a"></i>', bg:'#f0fdf4', title:'Attendance',     sub:'Punch-in methods' },
      company:    { icon:'<i class="ti ti-building u-c-1e3a8a"></i>',    bg:'#eff6ff', title:'Company',        sub:'Company management' },
      payroll:    { icon:'<i class="ti ti-coin u-c-ca8a04"></i>',        bg:'#fefce8', title:'Payroll',        sub:'Salary rules' },
      salary:     { icon:'<i class="ti ti-cash u-c-16a34a"></i>',        bg:'#f0fdf4', title:'Salary',         sub:'Per-employee rates' },
      disbursement: { icon:'<i class="ti ti-building-bank u-c-1e3a8a"></i>', bg:'#eff6ff', title:'Disbursement', sub:'Automated salary payout' },
      billing:    { icon:'<i class="ti ti-ticket u-c-1e3a8a"></i>',        bg:'#eff6ff', title:'Billing',        sub:'Seats and auto-debit' },
      email:      { icon:'<i class="ti ti-mail u-c-0284c7"></i>',        bg:'#f0f9ff', title:'Email',          sub:'SMTP and notifications' }
    };

    /* ── SET PIN MODAL ── */
    function openSetPin(cid, cname, hasPin) {
      document.getElementById('spn_cid').value = cid;
      document.getElementById('spn_label').textContent = cname;
      document.getElementById('spn_pin').value = '';
      // Set toggle state based on whether PIN exists
      var tog = document.getElementById('spn_toggle');
      tog.checked = !!hasPin;
      spnToggleChange(tog, true);
      document.getElementById('setPinModal').style.display = 'flex';
      if (hasPin) setTimeout(function(){ document.getElementById('spn_pin').focus(); }, 100);
    }
    function closeSetPin() { document.getElementById('setPinModal').style.display = 'none'; }
    function spnToggleChange(cb, skipAnim) {
      var on = cb.checked;
      document.getElementById('spn_track').style.background = on ? '#1e3a8a' : '#e2e8f0';
      document.getElementById('spn_thumb').style.left = on ? '24px' : '3px';
      document.getElementById('spn_pin_section').style.display = on ? 'block' : 'none';
      // When OFF, clear the pin field so empty string is submitted (removes PIN)
      if (!on) document.getElementById('spn_pin').value = '';
    }

    /* ── COMPANY INLINE EDIT ── */
    function openEditCompany(id) {
      document.getElementById('co-view-' + id).style.display = 'none';
      document.getElementById('co-edit-' + id).style.display = 'block';
    }
    function closeEditCompany(id) {
      document.getElementById('co-edit-' + id).style.display = 'none';
      document.getElementById('co-view-' + id).style.display = 'block';
    }

    /* ── Add Company modal: dynamic shift & break rows ── */
    var _acDefaultShifts = [
      {name:'General Shift', start:'09:00', half:'13:00', end:'18:00'},
    ];
    var _acDefaultBreaks = [
      {name:'Coffee Break 1', time:'11:00', dur:10},
      {name:'Lunch Break',    time:'13:00', dur:60},
      {name:'Coffee Break 2', time:'16:00', dur:10},
    ];

    function acMakeShiftRow(name, start, half, end) {
      var row = document.createElement('div');
      Object.assign(row.style, {display:'grid', gridTemplateColumns:'1fr 82px 82px 82px 28px', gap:'6px', alignItems:'center'});
      var inp = 'padding:7px 6px;border:1px solid #e2e8f0;border-radius:8px;font-size:12px;font-family:inherit;width:100%;';
      row.innerHTML =
        '<input type="text" name="shift_name[]"  value="'+name+'" placeholder="Shift name" />' +
        '<input type="time" name="shift_start[]" value="'+start+'" />' +
        '<input type="time" name="shift_half[]"  value="'+half+'" />' +
        '<input type="time" name="shift_end[]"   value="'+end+'" />' +
        '<button class="u-w-26 u-h-26 u-bd-1-solid-fecaca u-br-7 u-bg-fff5f5 u-c-dc2626 u-fs-14 u-cur-pointer u-d-flex u-ai-center u-jc-center u-ff-inherit" type="button" data-on-click="HZ.removeParent(this)">×</button>';
      return row;
    }
    function acAddShiftRow() {
      document.getElementById('acShiftsList').appendChild(acMakeShiftRow('','','',''));
    }

    function acMakeBreakRow(name, time, dur) {
      var row = document.createElement('div');
      Object.assign(row.style, {display:'grid', gridTemplateColumns:'1fr 90px 70px 28px', gap:'6px', alignItems:'center'});
      row.innerHTML =
        '<input class="u-p-7-10 u-bd-1-solid-e2e8f0 u-br-8 u-fs-12 u-ff-inherit u-w-100pct" type="text"   name="break_name[]"     value="'+name+'" placeholder="Break name" />' +
        '<input class="u-p-7-8 u-bd-1-solid-e2e8f0 u-br-8 u-fs-12 u-ff-inherit u-w-100pct" type="time"   name="break_time[]"     value="'+time+'" />' +
        '<input class="u-p-7-8 u-bd-1-solid-e2e8f0 u-br-8 u-fs-12 u-ff-inherit u-w-100pct" type="number" name="break_duration[]" value="'+dur+'"  min="1" max="240" placeholder="mins" />' +
        '<button class="u-w-26 u-h-26 u-bd-1-solid-fecaca u-br-7 u-bg-fff5f5 u-c-dc2626 u-fs-14 u-cur-pointer u-d-flex u-ai-center u-jc-center u-ff-inherit" type="button" data-on-click="HZ.removeParent(this)">×</button>';
      return row;
    }
    function acAddBreakRow() {
      document.getElementById('acBreaksList').appendChild(acMakeBreakRow('','','15'));
    }

    function acOpenModal() {
      var sList = document.getElementById('acShiftsList');
      sList.innerHTML = '';
      _acDefaultShifts.forEach(function(s) { sList.appendChild(acMakeShiftRow(s.name, s.start, s.half, s.end)); });
      var bList = document.getElementById('acBreaksList');
      bList.innerHTML = '';
      _acDefaultBreaks.forEach(function(b) { bList.appendChild(acMakeBreakRow(b.name, b.time, b.dur)); });
      document.getElementById('addCompanyModal').classList.add('open');
    }
    function acCloseModal() {
      document.getElementById('addCompanyModal').classList.remove('open');
    }
    function toggleCoAddShift(cid) {
      var el = document.getElementById('co-as-' + cid);
      if (!el) return;
      var shown = el.style.display !== 'none';
      el.style.display = shown ? 'none' : 'block';
      if (!shown) el.querySelector('input[name="shift_name"]').focus();
    }
    function toggleCoEditShift(sid) {
      var el = document.getElementById('co-es-' + sid);
      if (!el) return;
      var shown = el.style.display !== 'none';
      el.style.display = shown ? 'none' : 'block';
      if (!shown) el.querySelector('input[name="name"]').focus();
    }
    function toggleCoAddBreak(cid, sid) {
      var el = document.getElementById('co-ab-' + cid + '-' + sid);
      if (!el) return;
      var shown = el.style.display !== 'none';
      el.style.display = shown ? 'none' : 'block';
      if (!shown) el.querySelector('input[name="break_name"]').focus();
    }
    function toggleCoEditBreak(bid) {
      var el = document.getElementById('co-eb-' + bid);
      if (!el) return;
      var shown = el.style.display !== 'none';
      el.style.display = shown ? 'none' : 'block';
      if (!shown) el.querySelector('input[name="break_name"]').focus();
    }
    function styleCoWd(cb) {
      // Toggles the .on class (not inline styles) so dark mode's own
      // body.dark-mode .py-day-chip.on CSS applies correctly -- an inline
      // style here would override it regardless of theme.
      var span = cb.nextElementSibling;
      span.classList.toggle('on', cb.checked);
    }
    document.getElementById('addCompanyModal').addEventListener('click', function(e) {
      if (e.target === this) this.classList.remove('open');
    });

    function toggleEdit(key) {
      var view = document.getElementById(key + '-view');
      var edit = document.getElementById(key + '-edit');
      var btn  = document.getElementById(key + '-editbtn');
      if (!view || !edit) return;
      var isEditing = edit.style.display !== 'none';
      if (isEditing) {
        edit.style.display = 'none';
        view.style.display = '';
        btn.innerHTML = '<i class="ti ti-pencil u-fs-13"></i> Edit';
      } else {
        view.style.display = 'none';
        edit.style.display = '';
        btn.innerHTML = '<i class="ti ti-x u-fs-13"></i> Cancel';
        var inp = edit.querySelector('input[type=text]');
        if (inp) inp.focus();
      }
    }

    function toggleDd(id) {
      var menu = document.getElementById(id);
      if (!menu) return;
      var isOpen = menu.classList.contains('open');
      document.querySelectorAll('.subnav-dd-menu.open').forEach(function(m){ m.classList.remove('open'); });
      if (!isOpen) menu.classList.add('open');
    }
    document.addEventListener('click', function(e) {
      if (!e.target.closest('.subnav-dd-wrap')) {
        document.querySelectorAll('.subnav-dd-menu.open').forEach(function(m){ m.classList.remove('open'); });
      }
    });

    function switchGroup(grp) {
      document.querySelectorAll('.s3-tab').forEach(function(b){ b.classList.remove('active'); });
      document.querySelectorAll('.s3-panel').forEach(function(p){ p.classList.remove('active'); });
      var btn = document.getElementById('s3tab-' + grp);
      if (btn) btn.classList.add('active');
      var panel = document.getElementById('s3panel-' + grp);
      if (panel) panel.classList.add('active');
      history.replaceState(null, '', '?tab=' + (firstSub[grp] || grp));
    }

    function switchSub(grp, sec) {
      var panel = document.getElementById('s3panel-' + grp);
      if (!panel) return;
      panel.querySelectorAll('.s3-section').forEach(function(s){ s.classList.remove('active'); });
      panel.querySelectorAll('.subnav-dd-item').forEach(function(b){ b.classList.remove('active'); });
      var s = document.getElementById('ss-' + sec);
      if (s) s.classList.add('active');
      var ddi = document.getElementById('ddi-' + sec);
      if (ddi) ddi.classList.add('active');
      var d = crumbData[sec];
      if (d) {
        var ci = document.getElementById('crumb-icon-' + grp);
        if (ci) { ci.innerHTML = d.icon; ci.style.background = d.bg; }
        var ct = document.getElementById('crumb-text-' + grp);
        if (ct) ct.textContent = d.title;
        var cs = document.getElementById('crumb-sub-' + grp);
        if (cs) cs.textContent = d.sub;
      }
      document.querySelectorAll('.subnav-dd-menu.open').forEach(function(m){ m.classList.remove('open'); });
      history.replaceState(null, '', '?tab=' + sec);
    }

    /* Init from URL — use requestAnimationFrame to ensure DOM is painted first */
    (function() {
      var tab = (new URLSearchParams(window.location.search).get('tab'))
             || (sessionStorage.getItem('settings_tab'))
             || 'company';
      sessionStorage.removeItem('settings_tab');
      var grp = groupMap[tab] || 'org';
      function applyTab() {
        switchGroup(grp);
        if (document.getElementById('ss-' + tab)) switchSub(grp, tab);
      }
      if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', applyTab);
      } else {
        requestAnimationFrame(function(){ requestAnimationFrame(applyTab); });
      }
    })();

    /* ── Accessibility: ARIA roles + keyboard nav for the tab/dropdown system
       (see static/a11y-tabs.js — wraps the switchGroup/switchSub logic above
       without changing it) ── */
    if (window.ezWireTabList) {
      window.ezWireTabList('.s3-tab', function (tab) { return 's3panel-' + tab.id.replace('s3tab-', ''); }, { observe: true });
      document.querySelectorAll('.subnav-dd-menu').forEach(function (menu) {
        window.ezWireTabList('#' + menu.id + ' .subnav-dd-item', function (item) { return 'ss-' + item.id.replace('ddi-', ''); }, { observe: true });
      });
    }
    if (window.ezWireDropdown) {
      window.ezWireDropdown('.subnav-dd-btn', '.subnav-dd-menu');
    }

    /* ── FEATURE TOGGLE (AJAX) ── */
    function saveToggle(feature, value, extraId) {
      fetch('/toggle_feature', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ feature: feature, value: value })
      })
      .then(function(r) { return r.json(); })
      .then(function(d) {
        if (!d.ok) { alert('Could not save: ' + (d.error || 'unknown error')); }
        if (extraId) {
          var el = document.getElementById(extraId);
          if (el) el.style.display = value ? 'flex' : 'none';
        }
      })
      .catch(function() { alert('Network error saving setting.'); });
    }

    /* ── WORKING DAYS PILL STYLING ── */
    function styleWd(cb, day) {
      var span = document.getElementById('wdspan_' + day);
      if (!span) return;
      if (cb.checked) {
        span.style.borderColor = '#1e3a8a'; span.style.background = '#eff6ff'; span.style.color = '#1e3a8a';
      } else {
        span.style.borderColor = '#e2e8f0'; span.style.background = '#f8fafc'; span.style.color = '#64748b';
      }
    }

    /* ── SALARY EDIT INLINE OPEN ── */
    function openSalaryEdit(eid) {
      var form = document.getElementById('ef_' + eid);
      if (form) form.classList.toggle('open');
    }

    /* ── SALARY RULES LIVE PREVIEW ── */
    function fmt(t) { // "HH:MM" → "HH:MM AM/PM"
      if (!t) return t;
      var p = t.split(':'); var h = parseInt(p[0]); var m = p[1];
      var s = h >= 12 ? 'PM' : 'AM'; h = h % 12 || 12;
      return h + ':' + m + ' ' + s;
    }
    function updatePreviews() {
      var s = document.getElementById('inp_start') ? document.getElementById('inp_start').value : '';
      var h = document.getElementById('inp_half')  ? document.getElementById('inp_half').value  : '';
      var e = document.getElementById('inp_end')   ? document.getElementById('inp_end').value   : '';
      var fs = fmt(s), fh = fmt(h), fe = fmt(e);
      ['preview_start','preview_start2'].forEach(function(id){var el=document.getElementById(id);if(el)el.textContent=fs;});
      ['preview_half','preview_half2'].forEach(function(id){var el=document.getElementById(id);if(el)el.textContent=fh;});
      ['preview_end','preview_end2','preview_half3','preview_half4','preview_end3'].forEach(function(id){var el=document.getElementById(id);if(el)el.textContent=fe;});
      var tl=document.getElementById('tl_start'); if(tl)tl.textContent=fs;
      var th=document.getElementById('tl_half');  if(th)th.textContent=fh;
      var te=document.getElementById('tl_end');   if(te)te.textContent=fe;
    }
    function updateHolidayPreview(v) {
      var b = document.getElementById('preview_holiday_badge');
      if (b) {
        b.textContent = v === 'paid' ? '0% deducted' : '100% deducted';
        b.style.background = v === 'paid' ? '#e0f2fe' : '#fee2e2';
        b.style.color = v === 'paid' ? '#075985' : '#991b1b';
      }
      var pl = document.getElementById('hp_paid_lbl'), ul = document.getElementById('hp_unpaid_lbl');
      if (pl) { pl.style.borderColor = v==='paid'?'#bae6fd':'#e2e8f0'; pl.style.background = v==='paid'?'#f0f9ff':'#fafafa'; }
      if (ul) { ul.style.borderColor = v==='unpaid'?'#fca5a5':'#e2e8f0'; ul.style.background = v==='unpaid'?'#fff8f8':'#fafafa'; }
    }
    function updateLeavePreview(v) {
      var b = document.getElementById('preview_leave_badge');
      if (b) {
        b.textContent = v === 'exclude' ? 'No deduction' : '100% deducted';
        b.style.background = v === 'exclude' ? '#ede9fe' : '#fee2e2';
        b.style.color = v === 'exclude' ? '#5b21b6' : '#991b1b';
      }
      var el = document.getElementById('lp_excl_lbl'), al = document.getElementById('lp_abs_lbl');
      if (el) { el.style.borderColor = v==='exclude'?'#ddd6fe':'#e2e8f0'; el.style.background = v==='exclude'?'#f5f3ff':'#fafafa'; }
      if (al) { al.style.borderColor = v==='absent'?'#fca5a5':'#e2e8f0'; al.style.background = v==='absent'?'#fff8f8':'#fafafa'; }
    }
    document.addEventListener('DOMContentLoaded', updatePreviews);


    /* ── TEST EMAIL ── */
    function sendTestEmail() {
      var email = document.getElementById('testEmail').value.trim();
      var res   = document.getElementById('testResult');
      if (!email) { res.textContent = 'Please enter a recipient email.'; res.className = 'err'; res.style.display = 'block'; return; }
      res.textContent = '⏳ Sending SMTP diagnostic test…'; res.className = ''; res.style.display = 'block';
      fetch('/api/admin/test_email', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ to_email: email })
      })
        .then(function(r) { return r.json(); })
        .then(function(d) {
          res.textContent = d.ok ? ('✅ ' + d.msg) : ('❌ ' + (d.msg || d.error || 'Failed'));
          res.className   = d.ok ? 'ok' : 'err';
        })
        .catch(function() { res.textContent = '❌ Network error during SMTP test.'; res.className = 'err'; });
    }

    /* ── EMAIL SETTINGS 2FA GATE ──────────────────────────────────────────
       Unlock state lives ONLY in this closure variable — never localStorage,
       never sessionStorage. A page reload always starts locked again, which
       is the point: nothing survives if the machine is left unattended. The
       server session flag (utils/auth.py:email_settings_step_up_valid) is
       the real authority; this JS timer is a UX/defense-in-depth mirror of
       it, not a substitute — every fetch below still gets a 403 from the
       server if the session-side window already expired. */
    (function() {
      var unlocked = false;
      var idleTimer = null;
      var IDLE_MS = 15 * 60 * 1000;

      function resetIdleTimer() {
        if (idleTimer) clearTimeout(idleTimer);
        if (!unlocked) return;
        idleTimer = setTimeout(lockEmailSettings, IDLE_MS);
      }
      ['mousemove', 'keydown', 'click', 'scroll'].forEach(function(evt) {
        document.addEventListener(evt, function() { if (unlocked) resetIdleTimer(); }, { passive: true });
      });

      window.lockEmailSettings = function() {
        unlocked = false;
        if (idleTimer) { clearTimeout(idleTimer); idleTimer = null; }
        document.getElementById('email-settings-panel').classList.add('u-d-none');
        document.getElementById('email-2fa-gate').classList.remove('u-d-none');
        document.getElementById('es-pass').value = '';
        fetch('/api/settings/2fa/lock', { method: 'POST' }).catch(function() {});
      };

      window.initEmail2faGate = function() {
        fetch('/api/settings/2fa/setup')
          .then(function(r) { return r.json(); })
          .then(function(d) {
            if (!d.ok) return;
            if (d.already_enabled) {
              document.getElementById('email-2fa-verify').classList.remove('u-d-none');
              document.getElementById('email-2fa-enroll').classList.add('u-d-none');
            } else {
              document.getElementById('email-2fa-qr').src = d.qr_code;
              document.getElementById('email-2fa-secret').textContent = d.secret;
              document.getElementById('email-2fa-enroll').classList.remove('u-d-none');
              document.getElementById('email-2fa-verify').classList.add('u-d-none');
            }
          })
          .catch(function() {});
      };

      window.confirmEmail2faEnroll = function() {
        var code = document.getElementById('email-2fa-enroll-code').value.trim();
        var err  = document.getElementById('email-2fa-enroll-err');
        err.classList.add('u-d-none');
        fetch('/api/settings/2fa/enable', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ code: code }),
        })
          .then(function(r) { return r.json(); })
          .then(function(d) {
            if (!d.ok) { err.textContent = d.msg || 'Invalid code'; err.classList.remove('u-d-none'); return; }
            unlockEmailSettingsUI();
          })
          .catch(function() { err.textContent = 'Network error.'; err.classList.remove('u-d-none'); });
      };

      window.verifyEmail2fa = function() {
        var code = document.getElementById('email-2fa-code').value.trim();
        var err  = document.getElementById('email-2fa-err');
        err.classList.add('u-d-none');
        fetch('/api/settings/verify-2fa', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ code: code }),
        })
          .then(function(r) { return r.json(); })
          .then(function(d) {
            if (!d.ok) { err.textContent = d.msg || 'Invalid code'; err.classList.remove('u-d-none'); return; }
            unlockEmailSettingsUI();
          })
          .catch(function() { err.textContent = 'Network error.'; err.classList.remove('u-d-none'); });
      };

      window.resetEmail2fa = function() {
        var password = document.getElementById('email-2fa-reset-password').value;
        var err = document.getElementById('email-2fa-reset-err');
        err.classList.add('u-d-none');
        fetch('/api/settings/2fa/reset', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ password: password }),
        })
          .then(function(r) { return r.json(); })
          .then(function(d) {
            if (!d.ok) { err.textContent = d.msg || 'Reset failed'; err.classList.remove('u-d-none'); return; }
            document.getElementById('email-2fa-reset-password').value = '';
            document.getElementById('email-2fa-lost').classList.add('u-d-none');
            window.initEmail2faGate();
          })
          .catch(function() { err.textContent = 'Network error.'; err.classList.remove('u-d-none'); });
      };

      function unlockEmailSettingsUI() {
        unlocked = true;
        fetch('/api/settings/email')
          .then(function(r) { return r.json(); })
          .then(function(d) {
            if (!d.ok) { unlocked = false; window.initEmail2faGate(); return; }
            var c = d.config || {};
            document.getElementById('es-host').value       = c.host || '';
            document.getElementById('es-port').value       = c.port || 587;
            document.getElementById('es-user').value       = c.user || '';
            document.getElementById('es-pass').value       = c.password || '';
            document.getElementById('es-from-name').value  = c.from_name || '';
            document.getElementById('es-from-email').value = c.from_email || '';
            document.getElementById('email-2fa-gate').classList.add('u-d-none');
            document.getElementById('email-settings-panel').classList.remove('u-d-none');
            resetIdleTimer();
          })
          .catch(function() { window.lockEmailSettings(); });
      }

      window.loadEmailTemplatePreview = function() {
        var val = document.getElementById('email-tmpl-select').value;
        var subj = document.getElementById('email-tmpl-subject');
        var prev = document.getElementById('email-tmpl-preview');
        if (!subj || !prev) return;
        if (val === 'welcome') {
          subj.value = "Welcome to {company_name}! Get started with your portal";
          prev.innerHTML = "<strong style='color:#1e293b;'>Hello {employee_name},</strong><br>Welcome aboard! Your employee profile has been created at <strong>{company_name}</strong>.<br>Log in to your portal using your ID: <code>{employee_id}</code>.";
        } else if (val === 'payslip') {
          subj.value = "Your Payslip for {month} {year} is ready";
          prev.innerHTML = "<strong style='color:#1e293b;'>Dear {employee_name},</strong><br>Your payslip for <strong>{month} {year}</strong> has been generated.<br>Net Payable Amount: <strong>{net_salary}</strong>.<br>Attached is your password-protected PDF payslip.";
        } else if (val === 'leave_approved') {
          subj.value = "Leave Request Approved - {leave_type}";
          prev.innerHTML = "<strong style='color:#1e293b;'>Hi {employee_name},</strong><br>Your <strong>{leave_type}</strong> request from <strong>{start_date}</strong> to <strong>{end_date}</strong> has been <strong>APPROVED</strong> by your manager.";
        } else if (val === 'attendance_alert') {
          subj.value = "Attendance Alert: Late Check-in Notice";
          prev.innerHTML = "<strong style='color:#1e293b;'>Attention {employee_name},</strong><br>A late check-in was recorded for your shift on <strong>{date}</strong> at <strong>{time}</strong>.<br>Please update your attendance log or contact HR if this was an error.";
        }
      };

      window.revealEmailPassword = function() {
        fetch('/api/settings/email/reveal-password', { method: 'POST' })
          .then(function(r) { return r.json(); })
          .then(function(d) {
            if (!d.ok) { alert(d.msg || 'Could not reveal password.'); return; }
            var field = document.getElementById('es-pass');
            field.value = d.password;
            field.type  = 'text';
            setTimeout(function() { field.type = 'password'; }, 8000);
          })
          .catch(function() { alert('Network error.'); });
      };

      window.saveEmailSettings = function() {
        var err = document.getElementById('email-settings-err');
        err.style.display = 'none';
        fetch('/api/settings/email', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            host:       document.getElementById('es-host').value.trim(),
            port:       document.getElementById('es-port').value.trim(),
            user:       document.getElementById('es-user').value.trim(),
            password:   document.getElementById('es-pass').value,
            from_name:  document.getElementById('es-from-name').value.trim(),
            from_email: document.getElementById('es-from-email').value.trim(),
          }),
        })
          .then(function(r) { return r.json(); })
          .then(function(d) {
            if (!d.ok) { err.textContent = d.msg || 'Save failed.'; err.style.display = 'block'; return; }
            resetIdleTimer();
            alert('SMTP settings saved.');
          })
          .catch(function() { err.textContent = 'Network error.'; err.style.display = 'block'; });
      };

      document.addEventListener('DOMContentLoaded', window.initEmail2faGate);
      if (document.readyState !== 'loading') window.initEmail2faGate();
    })();

    /* ── SHIFT ASSIGN ── */
    async function assignShift(empId, sel) {
      var shiftId   = sel.value;
      var shiftName = sel.options[sel.selectedIndex].text;
      try {
        var res  = await fetch('/assign_shift', {
          method: 'POST',
          headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
          body: 'emp_id=' + encodeURIComponent(empId) + '&shift_id=' + encodeURIComponent(shiftId)
        });
        var data = await res.json();
        if (data.ok) {
          var tick = document.getElementById('tick-' + empId);
          tick.style.display = 'inline';
          setTimeout(function() { tick.style.display = 'none'; }, 2000);
          var cur = document.getElementById('cur-' + empId);
          cur.innerHTML = '<span class="badge ' + (shiftId ? 'b-green' : 'b-gray') + '">' + shiftName + '</span>';
        }
      } catch(e) {}
    }

    /* ── BULK SELECT ── */
    function toggleSelectAll() {
      var master = document.getElementById('masterCheck');
      var checks = document.querySelectorAll('.emp-check');
      checks.forEach(function(c) { c.checked = master.checked; });
      updateSelCount();
    }
    function updateSelCount() {
      var n = document.querySelectorAll('.emp-check:checked').length;
      var el = document.getElementById('selCount');
      el.textContent = n + ' selected';
      el.style.display = n > 0 ? 'inline' : 'none';
    }
    function confirmBulk() {
      var n = document.querySelectorAll('.emp-check:checked').length;
      if (!n) { alert('Select at least one employee.'); return false; }
      return confirm('Assign shift to ' + n + ' employee(s)?');
    }
    function filterShiftRows(q) {
      q = q.toLowerCase();
      document.querySelectorAll('#shiftEmpTbody tr').forEach(function(r) {
        r.style.display = (r.dataset.search || r.textContent).toLowerCase().includes(q) ? '' : 'none';
      });
    }

    /* ── SALARY CALC ── */
    var WD = 26;
    function r2(v) { return Math.round(v * 100) / 100; }
    function setEl(id, val) { var el = document.getElementById(id); if (el) el.value = val > 0 ? val.toFixed(2) : ''; }
    function updLPA(i) {
      var y = parseFloat(document.getElementById('sal-yr-' + i).value) || 0;
      var el = document.getElementById('sal-lpa-' + i);
      if (el) el.textContent = y > 0 ? (parseFloat((y/100000).toFixed(2))) + ' LPA' : '';
    }
    function salFromDay(i) {
      var d = parseFloat(document.getElementById('sal-day-' + i).value) || 0;
      setEl('sal-mon-' + i, r2(d * WD)); setEl('sal-yr-' + i, r2(d * WD * 12)); updLPA(i);
    }
    function salFromMon(i) {
      var m = parseFloat(document.getElementById('sal-mon-' + i).value) || 0;
      setEl('sal-day-' + i, r2(m / WD)); setEl('sal-yr-' + i, r2(m * 12)); updLPA(i);
    }
    function salFromYr(i) {
      var y = parseFloat(document.getElementById('sal-yr-' + i).value) || 0;
      setEl('sal-mon-' + i, r2(y / 12)); setEl('sal-day-' + i, r2(y / (WD * 12))); updLPA(i);
    }
    function prepSalSubmit(i) {
      var d = parseFloat(document.getElementById('sal-day-' + i).value) || 0;
      document.getElementById('sal-hidden-' + i).value = d.toFixed(2);
    }
    function startSalEdit(i) {
      document.getElementById('sal-disp-' + i).style.display = 'none';
      document.getElementById('sal-form-' + i).classList.add('open');
      document.getElementById('sal-editbtn-' + i).style.display = 'none';
      document.getElementById('sal-day-' + i).focus();
    }
    function cancelSalEdit(i) {
      document.getElementById('sal-disp-' + i).style.display = '';
      document.getElementById('sal-form-' + i).classList.remove('open');
      document.getElementById('sal-editbtn-' + i).style.display = '';
    }
    document.querySelectorAll('[id^="sal-editbtn-"]').forEach(function(btn) {
      btn.addEventListener('click', function() { startSalEdit(btn.dataset.idx); });
    });
    document.querySelectorAll('#salTbody .btn-cancel-sm').forEach(function(btn) {
      btn.addEventListener('click', function() { cancelSalEdit(btn.dataset.idx); });
    });
    function filterSalTable(q) {
      q = q.toLowerCase();
      document.querySelectorAll('#salTbody tr').forEach(function(r) {
        r.style.display = (r.dataset.search || r.textContent).toLowerCase().includes(q) ? '' : 'none';
      });
    }

    function showCodeChangeWarning(input) {
      var original = (input.getAttribute('data-original') || '').toUpperCase();
      var current  = input.value.toUpperCase();
      var warning  = document.getElementById('codeChangeWarning');
      var preview  = document.getElementById('newCodePreview');
      if (!warning) return;
      if (original && current && current !== original) {
        if (preview) preview.textContent = current;
        warning.style.display = 'block';
      } else {
        warning.style.display = 'none';
      }
    }

    function confirmCodeChange(form) {
      var input    = document.getElementById('companyCodeInput');
      var original = (input.getAttribute('data-original') || '').toUpperCase();
      var current  = input.value.toUpperCase();
      if (original && current && current !== original) {
        return confirm(
          'Warning: Changing the company code from "' + original + '" to "' + current + '" will restart employee ID numbering from 001.\n\nExisting employees keep their old IDs.\n\nAre you sure you want to change it?'
        );
      }
      return true;
    }

    // ── Settings page company modals ──

    function toggleAuthMethod(method, on) {
      var csrf = document.querySelector('meta[name="csrf-token"]');
      var form = document.createElement('form');
      form.method = 'POST';
      form.action = '/toggle_auth_method';
      [['_csrf_token', csrf ? csrf.content : ''], ['method', method], ['enabled', on ? '1' : '0']].forEach(function(pair) {
        var inp = document.createElement('input');
        inp.type = 'hidden'; inp.name = pair[0]; inp.value = pair[1];
        form.appendChild(inp);
      });
      document.body.appendChild(form);
      form.submit();
    }

    function toggleFingerprint(on) { toggleAuthMethod('fingerprint', on); }

    function stepDed(id, delta) {
      const inp = document.getElementById(id);
      let v = parseFloat(inp.value) || 0;
      v = Math.min(100, Math.max(0, v + delta));
      inp.value = v;
    }
  