/* employee-import.js -- the Employees page's "Import from Excel" modal
 * (templates/employees/_modal_import.html, blueprints/employees/bulk_import.py).
 *
 *   Check File  -> /api/employees/import/preview: ready rows, skipped rows
 *                  (with the reason) and what has to be paid first
 *   Register    -> pays for the extra seats if needed (static/js/seat-pay.js),
 *                  then /api/employees/import with the same file
 *
 * Everything from the sheet is shown with textContent, never as HTML.
 */
(function () {
  'use strict';
  var modal = document.getElementById('importModal');
  if (!modal) return;

  var fileInput = document.getElementById('importFile');
  var msg = document.getElementById('importMsg');
  var checkBtn = document.getElementById('importCheckBtn');
  var goBtn = document.getElementById('importGoBtn');
  var closeBtn = document.getElementById('importCloseBtn');
  var credsBtn = document.getElementById('importCredsBtn');
  var preview = null;   // last preview response
  var created = [];     // employees registered by the last import
  var changed = false;  // reload the list when the modal closes

  function show(id, on) { document.getElementById(id).hidden = !on; }
  function setText(id, text) { document.getElementById(id).textContent = text; }
  function plural(n, word) { return n + ' ' + word + (n === 1 ? '' : 's'); }

  function showError(text) {
    msg.textContent = text;
    msg.hidden = !text;
  }

  function fillList(id, items, render) {
    var list = document.getElementById(id);
    list.replaceChildren();
    items.forEach(function (item) {
      var li = document.createElement('li');
      li.textContent = render(item);
      list.appendChild(li);
    });
  }

  function reset() {
    preview = null;
    showError('');
    show('importPreview', false);
    show('importResult', false);
    goBtn.hidden = true;
    checkBtn.hidden = false;
    checkBtn.disabled = false;
  }

  function sendFile(url) {
    var data = new FormData();
    data.append('file', fileInput.files[0]);
    return fetch(url, { method: 'POST', body: data }).then(function (r) {
      return r.json().then(function (json) { json._status = r.status; return json; });
    });
  }

  function renderPreview(p) {
    preview = p;
    var ready = p.ready.length;
    setText('importSummary', plural(ready, 'employee') + ' ready to register' +
            (p.problems.length ? ', ' + plural(p.problems.length, 'row') + ' with problems' : '') + '.');
    var pay = p.payment || {};
    if (ready && pay.to_pay) {
      setText('importPayLine', (pay.free ? 'You have ' + plural(pay.free, 'free paid seat') + '. ' : 'All your paid seats are in use. ') +
              'The other ' + plural(pay.to_pay, 'employee') + ' cost ' + pay.rate_display + ' each for this month: ' +
              pay.amount_display + ', paid now.' +
              (pay.renews_on ? ' From ' + pay.renews_on + ' they are included in your monthly bill.' : ''));
      show('importPayLine', true);
    } else {
      show('importPayLine', false);
    }
    fillList('importProblemList', p.problems, function (x) {
      return 'Row ' + x.row + (x.name ? ' (' + x.name + ')' : '') + ': ' + x.msg;
    });
    show('importProblems', p.problems.length > 0);
    fillList('importReadyList', p.ready, function (x) {
      return x.name + (x.emp_id ? ' - ' + x.emp_id : ' - ID made automatically') + (x.email ? ' - ' + x.email : '');
    });
    show('importReady', ready > 0);
    show('importPreview', true);
    if (ready) {
      goBtn.textContent = pay.to_pay
        ? 'Pay ' + pay.amount_display + ' & Register ' + ready
        : 'Register ' + plural(ready, 'Employee');
      goBtn.hidden = false;
      goBtn.disabled = false;
    }
  }

  function renderResult(r) {
    created = r.created || [];
    changed = changed || created.length > 0;
    show('importPreview', false);
    goBtn.hidden = true;
    checkBtn.hidden = true;
    setText('importResultText', plural(created.length, 'employee') + ' registered.');
    var notes = [];
    if (r.emailing) notes.push('Welcome emails with sign-in details are on their way to ' + plural(r.emailing, 'employee') + '.');
    if (created.length > (r.emailing || 0)) notes.push('Download the sign-in details for anyone without an email address -- they are not shown again.');
    if (r.problems && r.problems.length) notes.push(plural(r.problems.length, 'row') + ' skipped: fix them in the sheet and import just those rows again.');
    setText('importResultNote', notes.join(' '));
    credsBtn.hidden = created.length === 0;
    show('importResult', true);
    if (r.problems && r.problems.length) {
      fillList('importProblemList', r.problems, function (x) {
        return 'Row ' + x.row + (x.name ? ' (' + x.name + ')' : '') + ': ' + x.msg;
      });
      show('importPreview', true);
      setText('importSummary', '');
      show('importPayLine', false);
      show('importReady', false);
      show('importProblems', true);
    }
  }

  checkBtn.addEventListener('click', function () {
    showError('');
    if (!fileInput.files.length) { showError('Choose the Excel file first.'); return; }
    checkBtn.disabled = true;
    sendFile('/api/employees/import/preview').then(function (p) {
      checkBtn.disabled = false;
      if (!p.ok) { showError(p.msg || 'The file could not be checked.'); return; }
      renderPreview(p);
    }).catch(function () {
      checkBtn.disabled = false;
      showError('Network error. Please try again.');
    });
  });

  goBtn.addEventListener('click', function () {
    if (!preview || !fileInput.files.length) return;
    goBtn.disabled = true;
    var pay = preview.payment || {};
    var paid = pay.to_pay
      ? HZSeatPay.buy(pay.to_pay, plural(pay.to_pay, 'employee seat') + ' for this month (Excel import)')
      : Promise.resolve(true);
    paid.then(function (ok) {
      if (!ok) { goBtn.disabled = false; return null; }
      if (pay.to_pay) {
        // Paid: a retry after a failed request must not ask to pay again.
        changed = true;
        preview.payment = { to_pay: 0 };
        goBtn.textContent = 'Register ' + plural(preview.ready.length, 'Employee');
      }
      return sendFile('/api/employees/import');
    }).then(function (r) {
      if (!r) return;
      if (r._status === 402 && r.payment) {
        // Someone used a seat in the meantime: show the new amount.
        showError(r.msg);
        renderPreview(Object.assign({}, preview, { payment: r.payment }));
        return;
      }
      if (!r.ok) { goBtn.disabled = false; showError(r.msg || 'Import failed. Please try again.'); return; }
      renderResult(r);
    }).catch(function () {
      goBtn.disabled = false;
      showError('Network error. Please try again.');
    });
  });

  function csvCell(v) {
    v = v == null ? '' : String(v);
    if (/^[=+\-@]/.test(v)) v = "'" + v;  // keep spreadsheet formulas out
    return '"' + v.replace(/"/g, '""') + '"';
  }

  credsBtn.addEventListener('click', function () {
    var lines = [['Name', 'Employee ID', 'Email', 'Temporary password'].map(csvCell).join(',')];
    created.forEach(function (c) {
      lines.push([c.name, c.emp_id, c.email || '', c.password].map(csvCell).join(','));
    });
    var blob = new Blob([lines.join('\r\n')], { type: 'text/csv' });
    var a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'hrzest-new-employees-sign-in.csv';
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(function () { URL.revokeObjectURL(a.href); }, 1000);
  });

  fileInput.addEventListener('change', reset);
  closeBtn.addEventListener('click', function () {
    closeModal('importModal');
    if (changed) window.location.reload();
  });
  modal.addEventListener('click', function (e) {  // closed by clicking outside
    if (e.target === modal && changed) window.location.reload();
  });
})();
