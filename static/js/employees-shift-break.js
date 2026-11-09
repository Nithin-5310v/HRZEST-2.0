
  /* ── Page-level tab switcher ── */
  function switchPageTab(tab) {
    var isSchedule = tab === 'schedule';
    document.getElementById('panelEmployees').style.display = isSchedule ? 'none' : 'block';
    document.getElementById('panelSchedule').style.display  = isSchedule ? 'block' : 'none';
    document.getElementById('pgTabEmp').style.background    = isSchedule ? '#fff' : '#1e3a8a';
    document.getElementById('pgTabEmp').style.color         = isSchedule ? '#475569' : '#fff';
    document.getElementById('pgTabSched').style.background  = isSchedule ? '#6366f1' : '#fff';
    document.getElementById('pgTabSched').style.color       = isSchedule ? '#fff' : '#475569';
    // update URL so page refresh keeps tab
    history.replaceState(null, '', '/employees?tab=' + tab);
  }
  // On load: restore tab from URL
  (function(){
    var p = new URLSearchParams(window.location.search).get('tab');
    if(p === 'schedule') switchPageTab('schedule');
  })();

  /* ── Schedule modal helpers ── */
  function openAddShiftModal()  { document.getElementById('addShiftModal').style.display='flex'; }
  function openAddBreakModal()  { document.getElementById('addBreakModal').style.display='flex'; }
  function openEditShiftModal(id, name, start, half, end) {
    // Rewrite only the trailing id segment so the tenant-slug prefix that
    // tpath() baked into the form's initial action (e.g. "/acme/edit_shift/0")
    // survives -- a bare '/edit_shift/' + id here would silently drop it
    // and submit outside the tenant's URL scope.
    var shiftForm = document.getElementById('editShiftForm');
    shiftForm.action = shiftForm.action.replace(/\/edit_shift\/[^/?]*$/, '/edit_shift/' + id);
    document.getElementById('editShiftName').value  = name;
    document.getElementById('editShiftStart').value = start;
    document.getElementById('editShiftHalf').value  = half;
    document.getElementById('editShiftEnd').value   = end;
    document.getElementById('editShiftModal').style.display = 'flex';
  }
  function openEditBreakModal(id, name, time, dur, active) {
    active = active === true || active === 'True';  // data-active is the Jinja bool's text
    // Same tenant-prefix-preserving rewrite as openEditShiftModal() above.
    var breakForm = document.getElementById('editBreakForm');
    breakForm.action = breakForm.action.replace(/\/update_break\/[^/?]*$/, '/update_break/' + id);
    document.getElementById('editBreakName').value  = name;
    document.getElementById('editBreakTime').value  = time;
    document.getElementById('editBreakDur').value   = dur;
    document.getElementById('editBreakActive').checked = !!active;
    document.getElementById('editBreakModal').style.display = 'flex';
  }
  // NOTE: this redeclares the page-wide closeModal() above (same global name,
  // later declaration wins). It must handle BOTH modal styles: .modal-backdrop
  // modals (Add/Edit employee...) are shown via the "open" class, so hiding
  // them with an inline display:none left them stuck hidden -- openModal()
  // only re-adds the class, and the inline style kept overriding it, so
  // "Add Employee" did nothing after the first close until a page refresh.
  function closeModal(id) {
    var el = document.getElementById(id);
    if (!el) return;
    if (el.classList.contains('modal-backdrop')) {
      el.classList.remove('open');
      el.style.display = '';
      document.body.style.overflow = '';
    } else {
      el.style.display = 'none';
    }
  }
  // Close on backdrop click
  ['addShiftModal','editShiftModal','addBreakModal','editBreakModal'].forEach(function(id){
    var el = document.getElementById(id);
    if(el) el.addEventListener('click', function(e){ if(e.target===this) closeModal(id); });
  });
  