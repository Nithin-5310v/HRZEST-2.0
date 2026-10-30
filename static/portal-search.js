/* ==========================================================================
   HRzest - Portal search (one engine for every portal)

     admin / HR / manager  -> templates/admin.html (admin dashboard header)
     employee              -> templates/_employee_sidebar.html, employee portal
                              only (sb_mode == 'portal')

   Results appear only once something is typed, best match first.

   Markup contract:
     <div id="adminSearchWrap" class="admin-search-wrap [ps-floating]">
       <input id="adminSearchInput"
              data-index="admin|employee"        which list below to search
              data-role="admin|hr|manager"       admin index only: hides admin-only entries
              data-base="/ or /<company-slug>/"  tpath('/') so links stay in the company
              data-endpoint="/api/admin/search"> optional live data (employees, tickets, leaves)
       <div id="adminSearchResults"></div>
     </div>

   Entry fields: t title, s subtitle, i tabler icon, k extra keywords,
     u  path to open                      (admin + employee standalone pages)
     v  employee-portal view (#dashboard, #earnings, ...); on the portal page
        itself showView() is called directly, elsewhere /employee_portal#<v>
     et earnings sub-tab (salary | incentives | overtime)
     h  heading text inside the view to scroll to (My Profile sections)
     r  'admin' = full admins only (same as the sidebar), 'hr', 'not-hr'

   Keys: Ctrl+K or "/" focuses, Up/Down move, Enter opens, Esc closes.
   Result text is set with textContent only (names/subjects are user input).
   ========================================================================== */

(function () {
  var input = document.getElementById('adminSearchInput');
  var results = document.getElementById('adminSearchResults');
  var wrap = document.getElementById('adminSearchWrap');
  if (!input || !results || !wrap) return;

  var INDEX = input.dataset.index || 'admin';
  var ENDPOINT = input.dataset.endpoint || '';
  var BASE = (input.dataset.base || '/').replace(/\/?$/, '/');
  var ROLE = input.dataset.role || 'admin';
  var FLOATING = wrap.classList.contains('ps-floating');

  // ── Admin / HR / Manager ──────────────────────────────────────────────
  var ADMIN_MENU = [
    {t: 'Dashboard', s: 'Overview, today’s attendance, quick actions', u: '/admin', i: 'home', k: 'home overview summary main', r: 'not-hr'},
    {t: 'Dashboard', s: 'HR overview of your assigned employees', u: '/hr_dashboard', i: 'home', k: 'home overview summary main', r: 'hr'},
    {t: 'Employees', s: 'Employee directory and profiles', u: '/employees', i: 'users', k: 'staff people team directory members workforce departments department'},
    {t: 'Attendance', s: 'Monthly attendance report', u: '/monthly_report', i: 'calendar-check', k: 'present absent late check in punch timesheet report monthly export download excel'},
    {t: 'Salary & Payslips', s: 'Monthly salary report and payslips', u: '/salary_report', i: 'coin', k: 'payroll pay salary wages payslip net gross deduction export excel lock payroll send salary slips email payslips generate', r: 'admin'},
    {t: 'Leaves & Holidays', s: 'Leave requests, tickets, holidays, announcements', u: '/leave_holidays', i: 'calendar-event', k: 'leave vacation time off holiday absence quota'},
    {t: 'OT & Comp-off', s: 'Overtime and compensatory off', u: '/overtime', i: 'clock-bolt', k: 'overtime extra hours comp off compensatory'},
    {t: 'Performance & Comms', s: 'Reviews, KPIs, hikes and bonuses', u: '/performance', i: 'chart-line', k: 'appraisal review kpi rating hike increment bonus communication'},
    {t: 'Onboarding', s: 'New-hire onboarding checklists', u: '/onboarding', i: 'checklist', k: 'new hire joining induction checklist'},
    {t: 'Analytics', s: 'Charts, trends and smart alerts', u: '/analytics', i: 'chart-bar', k: 'reports insights charts statistics trends graphs alerts gender attrition', r: 'admin'},
    {t: 'Admin Tools', s: 'Org chart and activity log', u: '/admin_tools', i: 'tool', k: 'tools utilities system', r: 'admin'},
    {t: 'Settings', s: 'Company, payroll, billing and email settings', u: '/settings', i: 'settings', k: 'configuration preferences setup options', r: 'admin'},
    {t: 'Profile', s: 'Company profile, subscription and payments', u: '/profile', i: 'id-badge-2', k: 'account company profile subscription', r: 'admin'}
  ];

  var ADMIN_FEATURES = [
    // Employees
    {t: 'Add Employee', s: 'Employees › Register a new employee', u: '/employees?open=add', i: 'user-plus', k: 'new hire create register onboard add staff register employee'},
    {t: 'Employee Schedule', s: 'Employees › Shift roster', u: '/employees?tab=schedule', i: 'calendar-time', k: 'roster schedule shifts weekly'},
    {t: 'Org Chart', s: 'Admin Tools › Reporting structure', u: '/org_chart', i: 'hierarchy-2', k: 'organisation organization hierarchy reporting manager tree structure'},
    {t: 'Activity Log', s: 'Admin Tools › Audit trail of every admin action', u: '/admin_tools?tab=activity_log', i: 'history', k: 'audit log history changes trail who did activity', r: 'admin'},
    {t: 'Shifts', s: 'Create and assign work shifts', u: '/shifts', i: 'clock', k: 'shift timing working hours schedule shifts and breaks'},
    {t: 'Breaks', s: 'Break timings configuration', u: '/break_config', i: 'coffee', k: 'break lunch coffee tea timing shifts and breaks'},
    {t: 'Companies / Branches', s: 'Manage companies and branches', u: '/companies', i: 'building', k: 'company branch location office', r: 'admin'},

    // Attendance
    {t: 'Present Today', s: 'Attendance › Employees present today', u: '/today_present', i: 'user-check', k: 'present today in office checked in'},
    {t: 'Absent Today', s: 'Attendance › Employees absent today', u: '/today_absent', i: 'user-x', k: 'absent today missing not checked in absentees'},
    {t: 'Late Today', s: 'Attendance › Late check-ins today', u: '/today_late', i: 'alarm', k: 'late today late login late check in'},
    {t: 'Bulk Mark Attendance', s: 'Attendance › Mark attendance for many employees', u: '/bulk_mark_attendance', i: 'list-check', k: 'mark attendance manual bulk correct regularize'},
    {t: 'Attendance Methods', s: 'Settings › Face, QR, GPS, passkey check-in', u: '/settings?tab=attendance', i: 'fingerprint', k: 'face recognition qr gps geofence passkey biometric punch method', r: 'admin'},

    // Leaves & Holidays
    {t: 'Leave Requests', s: 'Leaves & Holidays › Approve or reject leave', u: '/leave_holidays?tab=leaves', i: 'calendar-event', k: 'leave approve reject pending request time off quota'},
    {t: 'Support Tickets', s: 'Leaves & Holidays › Employee tickets', u: '/leave_holidays?tab=tickets', i: 'ticket', k: 'ticket helpdesk support issue complaint query'},
    {t: 'Announcements', s: 'Leaves & Holidays › Company-wide notices', u: '/leave_holidays?tab=announcements', i: 'speakerphone', k: 'announcement announcements notice notices broadcast news message post publish circular'},
    {t: 'Holidays', s: 'Leaves & Holidays › Public holiday calendar', u: '/leave_holidays?tab=holidays', i: 'beach', k: 'holiday holidays public holiday festival calendar off day add holiday'},
    {t: 'Resignations', s: 'Leaves & Holidays › Resignation requests', u: '/leave_holidays?tab=resignations', i: 'door-exit', k: 'resignation resign exit notice period quit'},
    {t: 'Leave Balance', s: 'Leave balances for every employee', u: '/leave_balance', i: 'scale', k: 'leave balance quota remaining casual sick earned'},
    {t: 'Leave Calendar', s: 'Who is on leave, by date', u: '/leave_calendar', i: 'calendar', k: 'leave calendar who is off schedule'},

    // OT & Comp-off
    {t: 'Overtime', s: 'OT & Comp-off › Overtime records', u: '/overtime?tab=ot', i: 'clock-bolt', k: 'overtime ot extra hours'},
    {t: 'Comp-off', s: 'OT & Comp-off › Compensatory off', u: '/overtime?tab=compoff', i: 'calendar-plus', k: 'comp off compoff compensatory off'},
    {t: 'Overtime Settings', s: 'OT & Comp-off › OT rules', u: '/overtime?tab=settings', i: 'adjustments', k: 'overtime rules rate settings'},

    // Performance
    {t: 'Reviews & KPIs', s: 'Performance › Reviews and KPIs', u: '/performance?tab=performance', i: 'star', k: 'performance review kpi appraisal rating goals'},
    {t: 'Hikes & Bonus', s: 'Performance › Salary hikes and bonuses', u: '/performance?tab=hike', i: 'trending-up', k: 'hike increment raise bonus promotion'},

    // Onboarding
    {t: 'Active Onboardings', s: 'Onboarding › In-progress new hires', u: '/onboarding?tab=active', i: 'progress-check', k: 'onboarding active new joiner in progress overdue'},
    {t: 'Onboarding Templates', s: 'Onboarding › Checklist templates', u: '/onboarding?tab=templates', i: 'template', k: 'onboarding template checklist tasks'},

    // Salary
    {t: 'Payroll Settings', s: 'Salary › PF, PT, TDS and salary structure', u: '/payroll_settings', i: 'adjustments-dollar', k: 'payroll pf pt tds esi tax deduction salary structure statutory', r: 'admin'},
    {t: 'Payslip Lookup', s: 'Salary › Find any employee’s payslip', u: '/admin_payslips', i: 'file-invoice', k: 'payslip salary slip download pdf', r: 'admin'},

    // Settings
    {t: 'Payroll Rules', s: 'Settings › Deduction rules and policies', u: '/settings?tab=payroll', i: 'receipt', k: 'payroll rules late deduction half day policy', r: 'admin'},
    {t: 'Daily Salary Rates', s: 'Settings › Per-employee daily rate', u: '/settings?tab=salary', i: 'currency-rupee', k: 'salary daily rate wage pay per day set daily salary', r: 'admin'},
    {t: 'Salary Disbursement', s: 'Settings › Bank transfer and payslip emails', u: '/settings?tab=disbursement', i: 'building-bank', k: 'disbursement bank transfer payout pay salary account', r: 'admin'},
    {t: 'Seats & Billing', s: 'Settings › Seat limit and auto-debit', u: '/settings?tab=billing', i: 'credit-card', k: 'billing seats plan subscription auto debit razorpay payment', r: 'admin'},
    {t: 'Email Settings', s: 'Settings › SMTP and notification emails', u: '/settings?tab=email', i: 'mail-cog', k: 'email smtp mail notification template brevo', r: 'admin'},
    {t: 'Company Details', s: 'Settings › Name, GSTIN, logo, address', u: '/settings/company_details', i: 'building-skyscraper', k: 'company details gstin gst logo address name', r: 'admin'},
    {t: 'Company Verification', s: 'Settings › Business documents (KYC)', u: '/settings/company_verification', i: 'file-certificate', k: 'verification kyc documents certificate registration', r: 'admin'},
    {t: 'Buy Seats', s: 'Settings › Add more employee seats', u: '/settings/seats', i: 'users-plus', k: 'seats buy more employees limit upgrade', r: 'admin'},

    // Profile
    {t: 'Subscription', s: 'Profile › Plan and billing', u: '/profile/billing', i: 'receipt-2', k: 'subscription plan billing invoice', r: 'admin'},
    {t: 'Payment History', s: 'Profile › All payments and invoices', u: '/profile/payments', i: 'cash', k: 'payments history invoices receipts transactions', r: 'admin'},
    {t: 'My Support Tickets', s: 'Profile › Tickets raised with HRzest', u: '/profile/tickets', i: 'help', k: 'support help hrzest ticket contact', r: 'admin'}
  ];

  // ── Employee portal ──────────────────────────────────────────────────
  var EMP_MENU = [
    {t: 'Dashboard', s: 'Today, announcements, quick stats', v: 'dashboard', i: 'home', k: 'home overview summary main'},
    {t: 'Attendance', s: 'Your monthly attendance', v: 'attendance', i: 'calendar-check', k: 'attendance history present absent late calendar monthly punch'},
    {t: 'Apply Leave', s: 'Request leave and see leave history', v: 'apply-leave', i: 'calendar-event', k: 'leave apply request time off vacation sick casual'},
    {t: 'Earnings', s: 'Salary, payslips, incentives, overtime', v: 'earnings', i: 'coin', k: 'salary payslip pay earnings money'},
    {t: 'Holidays', s: 'Public holidays this year', v: 'holidays', i: 'beach', k: 'holiday holidays public festival calendar'},
    {t: 'Comp-off / OT', s: 'Your comp-offs and overtime', u: '/my_compoff', i: 'clock-bolt', k: 'comp off compoff overtime ot compensatory'},
    {t: 'My Performance', s: 'Your reviews and KPIs', u: '/my_performance', i: 'chart-line', k: 'performance review kpi appraisal rating goals'},
    {t: 'My Onboarding', s: 'Your joining checklist', u: '/my_onboarding', i: 'checklist', k: 'onboarding joining checklist tasks documents'},
    {t: 'Support Tickets', s: 'Raise and track tickets', v: 'tickets', i: 'ticket', k: 'ticket complaint help issue support raise'},
    {t: 'My Profile', s: 'Personal, contact, bank and work details', v: 'my-profile', i: 'user-circle', k: 'profile personal details account'},
    {t: 'Policies & Guidelines', s: 'Company policies', v: 'policies', i: 'book', k: 'policy policies rules terms posh guidelines instructions limitations'},
    {t: 'Resignation', s: 'Submit a resignation', v: 'resign', i: 'door-exit', k: 'resign resignation exit quit notice period'}
  ];

  var EMP_FEATURES = [
    {t: 'Announcements', s: 'Dashboard › Company announcements', v: 'dashboard', h: 'Announcements', i: 'speakerphone', k: 'announcement announcements notice notices news circular'},
    {t: 'Mark Attendance', s: 'Dashboard › Check in / check out', v: 'dashboard', h: "Today's Attendance", i: 'fingerprint', k: 'mark attendance check in check out punch face qr'},
    {t: 'My QR Code', s: 'Dashboard › Your attendance QR', v: 'dashboard', h: 'Your QR', i: 'qrcode', k: 'qr code scan attendance'},
    {t: 'Break Schedule', s: 'Dashboard › Today’s breaks', v: 'dashboard', h: 'Break Schedule', i: 'coffee', k: 'break lunch coffee schedule'},
    {t: 'Leave History', s: 'Apply Leave › Your past requests', v: 'apply-leave', h: 'Leave History', i: 'history', k: 'leave history status approved rejected pending balance'},
    {t: 'Salary & Payslips', s: 'Earnings › Monthly salary and payslips', v: 'earnings', et: 'salary', i: 'file-invoice', k: 'salary payslip slip download pay'},
    {t: 'Incentives', s: 'Earnings › Bonuses and incentives', v: 'earnings', et: 'incentives', i: 'gift', k: 'incentive bonus reward'},
    {t: 'Overtime Pay', s: 'Earnings › Overtime earnings', v: 'earnings', et: 'overtime', i: 'clock-bolt', k: 'overtime ot extra pay'},
    {t: 'Raise a Ticket', s: 'Support Tickets › New complaint or request', v: 'tickets', h: 'Support Tickets', i: 'message-plus', k: 'raise ticket complaint request new help'},
    {t: 'Personal Information', s: 'My Profile › Name, DOB, gender', v: 'my-profile', h: 'Personal Information', i: 'user', k: 'personal info dob birth gender marital'},
    {t: 'Contact Details & Address', s: 'My Profile › Phone, email, address', v: 'my-profile', h: 'Contact Details', i: 'address-book', k: 'contact phone mobile email address'},
    {t: 'Emergency Contact', s: 'My Profile › Emergency contact person', v: 'my-profile', h: 'Emergency Contact', i: 'urgent', k: 'emergency contact family'},
    {t: 'Identity & Bank Details', s: 'My Profile › PAN, Aadhaar, bank account', v: 'my-profile', h: 'Bank Details', i: 'building-bank', k: 'bank account ifsc pan aadhaar identity id proof'},
    {t: 'Education', s: 'My Profile › Qualifications', v: 'my-profile', h: 'Education', i: 'school', k: 'education degree qualification college'},
    {t: 'Work Experience', s: 'My Profile › Previous jobs', v: 'my-profile', h: 'Work Experience', i: 'briefcase', k: 'experience previous job employer'},
    {t: 'Role & Work Information', s: 'My Profile › Department, role, joining date', v: 'my-profile', h: 'Role & Work Information', i: 'id-badge-2', k: 'role department designation joining shift'},
    {t: 'Reporting Manager', s: 'My Profile › Hierarchy information', v: 'my-profile', h: 'Hierarchy Information', i: 'hierarchy-2', k: 'manager reporting hierarchy boss'},
    {t: 'About Me', s: 'My Profile › Short bio', v: 'my-profile', h: 'About Me', i: 'notes', k: 'about me bio'},
    {t: 'Change Password', s: 'My Profile › Update your password', v: 'my-profile', h: 'Change Password', i: 'lock', k: 'password change reset security pin'},
    {t: 'Fingerprint Enrollment', s: 'My Profile › Passkey / fingerprint login', v: 'my-profile', h: 'Fingerprint Enrollment', i: 'fingerprint', k: 'fingerprint passkey biometric webauthn enroll'},
    {t: 'My ID Card', s: 'Download your employee ID card', u: '/my_id_card', i: 'id', k: 'id card badge identity download'}
  ];

  function allowed(item) {
    if (!item.r) return true;
    if (item.r === 'admin') return ROLE === 'admin';
    if (item.r === 'hr') return ROLE === 'hr';
    if (item.r === 'not-hr') return ROLE !== 'hr';
    return true;
  }
  var menu = (INDEX === 'employee' ? EMP_MENU : ADMIN_MENU).filter(allowed);
  var features = (INDEX === 'employee' ? EMP_FEATURES : ADMIN_FEATURES).filter(allowed);

  function href(item) {
    var path = item.u || item.url || ('/employee_portal#' + item.v);
    if (/^https?:/i.test(path)) return path;
    return BASE + path.replace(/^\//, '');
  }

  function norm(s) { return (s || '').toLowerCase().replace(/&/g, ' and ').replace(/[^a-z0-9 ]+/g, ' '); }

  // Every query word must match the title, keywords or subtitle; title-start
  // beats word-start beats anywhere, so "pay" ranks Payroll Settings and
  // Payslip Lookup above entries that only mention pay in passing.
  function score(item, words) {
    var title = norm(item.t), sub = norm(item.s), keys = norm(item.k);
    var total = 0;
    for (var i = 0; i < words.length; i++) {
      var w = words[i], best = 0;
      if (title.indexOf(w) === 0) best = 100;
      else if ((' ' + title).indexOf(' ' + w) !== -1) best = 80;
      else if (title.indexOf(w) !== -1) best = 60;
      else if ((' ' + keys).indexOf(' ' + w) !== -1) best = 45;
      else if (keys.indexOf(w) !== -1) best = 30;
      else if (sub.indexOf(w) !== -1) best = 20;
      if (!best) return 0;
      total += best;
    }
    return total;
  }

  // Returns {items, top}: matching entries best-first, plus the best score so
  // groups can be ordered too -- an exact feature match ("Announcements")
  // must come before a menu item that only mentions it in its description.
  function rank(list, words, limit) {
    var hits = list
      .map(function (it) { return {it: it, sc: score(it, words)}; })
      .filter(function (x) { return x.sc > 0; })
      .sort(function (a, b) { return b.sc - a.sc; })
      .slice(0, limit);
    return {items: hits.map(function (x) { return x.it; }), top: hits.length ? hits[0].sc : 0};
  }

  // ── Opening a result ─────────────────────────────────────────────────
  function onPortalPage() {
    return typeof window.showView === 'function' && document.getElementById('view-dashboard');
  }

  function scrollToHeading(view, text) {
    var root = document.getElementById('view-' + view);
    if (!root || !text) return;
    var needle = text.toLowerCase();
    var nodes = root.querySelectorAll('h1,h2,h3,h4,div,span');
    for (var i = 0; i < nodes.length; i++) {
      var n = nodes[i];
      if (n.children.length > 2) continue; // headings, not whole containers
      if ((n.textContent || '').toLowerCase().indexOf(needle) !== -1) {
        n.scrollIntoView({behavior: 'smooth', block: 'start'});
        return;
      }
    }
  }

  function openItem(item) {
    close();
    input.blur();
    if (item.v && onPortalPage()) {
      window.showView(item.v);
      if (item.et && typeof window.switchEarningsTab === 'function') window.switchEarningsTab(item.et);
      if (item.h) setTimeout(function () { scrollToHeading(item.v, item.h); }, 60);
      try { history.replaceState(null, '', '#' + item.v); } catch (e) {}
      return;
    }
    window.location.href = href(item);
  }

  // ── Rendering ────────────────────────────────────────────────────────
  var flat = [], active = -1, timer = null, seq = 0;

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  function titleNode(text, words) {
    var span = el('span', 'asr-label');
    var lower = text.toLowerCase(), hit = -1, len = 0;
    for (var i = 0; i < words.length && hit === -1; i++) {
      var at = words[i] ? lower.indexOf(words[i]) : -1;
      if (at !== -1) { hit = at; len = words[i].length; }
    }
    if (hit === -1) { span.textContent = text; return span; }
    span.appendChild(document.createTextNode(text.slice(0, hit)));
    span.appendChild(el('mark', 'asr-mark', text.slice(hit, hit + len)));
    span.appendChild(document.createTextNode(text.slice(hit + len)));
    return span;
  }

  function place() {
    if (!FLOATING) return;
    var r = input.getBoundingClientRect();
    var width = Math.min(400, window.innerWidth - 16);
    results.style.position = 'fixed';
    results.style.top = (r.bottom + 6) + 'px';
    results.style.left = Math.max(8, Math.min(r.left, window.innerWidth - width - 8)) + 'px';
    results.style.width = width + 'px';
    results.style.right = 'auto';
  }

  function render(groups, words, emptyText) {
    flat = []; active = -1;
    results.textContent = '';
    var any = false;
    groups.forEach(function (g) {
      if (!g.items.length) return;
      any = true;
      results.appendChild(el('div', 'asr-group-label', g.label));
      g.items.forEach(function (item) {
        var idx = flat.length;
        flat.push(item);
        var a = el('a', 'asr-item');
        a.href = href(item);
        a.setAttribute('role', 'option');
        var icon = el('span', 'asr-icon');
        icon.appendChild(el('i', 'ti ti-' + (item.i || item.icon || g.icon || 'point')));
        var text = el('span', 'asr-text');
        text.appendChild(titleNode(item.t || item.label || '', words));
        var sub = item.s || item.sub;
        if (sub) text.appendChild(el('span', 'asr-sub', sub));
        a.appendChild(icon);
        a.appendChild(text);
        a.appendChild(el('i', 'ti ti-corner-down-left asr-enter'));
        a.addEventListener('mouseenter', function () { setActive(idx, false); });
        a.addEventListener('click', function (e) {
          if (e.ctrlKey || e.metaKey || e.shiftKey || e.button === 1) return; // let "open in new tab" work
          e.preventDefault();
          openItem(item);
        });
        results.appendChild(a);
      });
    });
    if (!any) results.appendChild(el('div', 'asr-empty', emptyText || 'No matches found.'));
    var foot = el('div', 'asr-foot');
    foot.appendChild(el('span', '', '↑ ↓ move'));
    foot.appendChild(el('span', '', 'Enter open'));
    foot.appendChild(el('span', '', 'Esc close'));
    results.appendChild(foot);
    open();
    if (flat.length) setActive(0, false);
  }

  function setActive(idx, scroll) {
    var items = results.querySelectorAll('.asr-item');
    items.forEach(function (it) { it.classList.remove('active'); it.setAttribute('aria-selected', 'false'); });
    if (idx >= 0 && idx < items.length) {
      items[idx].classList.add('active');
      items[idx].setAttribute('aria-selected', 'true');
      if (scroll !== false) items[idx].scrollIntoView({block: 'nearest'});
      active = idx;
    }
  }

  function open() { place(); results.classList.add('open'); input.setAttribute('aria-expanded', 'true'); }
  function close() { results.classList.remove('open'); input.setAttribute('aria-expanded', 'false'); }

  function search(q) {
    var words = norm(q).split(/\s+/).filter(Boolean);
    // Nothing typed -> show nothing (no full list of every menu item).
    if (!words.length) { seq++; close(); return; }
    var m = rank(menu, words, 6), f = rank(features, words, 8);
    var local = [
      {label: 'Menu', items: m.items, top: m.top},
      {label: 'Features & settings', items: f.items, top: f.top}
    ].sort(function (a, b) { return b.top - a.top; });
    if (!ENDPOINT || q.length < 2) { render(local, words, 'No matches for “' + q + '”.'); return; }
    render(local, words, 'Searching…');

    var mine = ++seq;
    var sep = ENDPOINT.indexOf('?') === -1 ? '?' : '&';
    fetch(ENDPOINT + sep + 'q=' + encodeURIComponent(q), {headers: {'Accept': 'application/json'}})
      .then(function (r) { return r.ok ? r.json() : {results: []}; })
      .then(function (data) {
        if (mine !== seq) return; // a newer keystroke already re-rendered
        var byType = {employee: [], ticket: [], leave: []};
        (data.results || []).forEach(function (r) { if (byType[r.type]) byType[r.type].push(r); });
        render(local.concat([
          {label: 'Employees', icon: 'user', items: byType.employee},
          {label: 'Tickets', icon: 'ticket', items: byType.ticket},
          {label: 'Leave requests', icon: 'calendar-event', items: byType.leave}
        ]), words, 'No matches for “' + q + '”.');
      })
      .catch(function () {
        if (mine === seq) render(local, words, 'No matches for “' + q + '”.');
      });
  }

  // ── Events ───────────────────────────────────────────────────────────
  input.addEventListener('input', function () {
    clearTimeout(timer);
    var q = input.value.trim();
    timer = setTimeout(function () { search(q); }, q.length < 2 ? 0 : 160);
  });

  input.addEventListener('focus', function () {
    if (input.value.trim()) search(input.value.trim());
  });

  input.addEventListener('keydown', function (e) {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      if (!results.classList.contains('open')) { if (input.value.trim()) search(input.value.trim()); return; }
      setActive(Math.min(active + 1, flat.length - 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setActive(Math.max(active - 1, 0));
    } else if (e.key === 'Enter') {
      var target = flat[active >= 0 ? active : 0];
      if (target) { e.preventDefault(); openItem(target); }
    } else if (e.key === 'Escape') {
      close();
      input.blur();
    }
  });

  document.addEventListener('click', function (e) {
    if (!wrap.contains(e.target) && !results.contains(e.target)) close();
  });
  window.addEventListener('resize', function () { if (results.classList.contains('open')) place(); });
  window.addEventListener('scroll', function () { if (results.classList.contains('open')) place(); }, true);

  // Ctrl+K / Cmd+K anywhere, or "/" when not already typing somewhere.
  document.addEventListener('keydown', function (e) {
    var t = e.target || {};
    var typing = /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName || '') || t.isContentEditable;
    if ((e.key === 'k' || e.key === 'K') && (e.ctrlKey || e.metaKey)) { e.preventDefault(); input.focus(); input.select(); }
    else if (e.key === '/' && !typing) { e.preventDefault(); input.focus(); }
  });
})();
