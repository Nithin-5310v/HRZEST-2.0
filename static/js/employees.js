
      // This file's CSP nonce. The ID-card print popup below inherits the page's
      // Content-Security-Policy, so the <style> it writes needs the nonce too.
      // (When this code was inline, the server's response rewriter stamped it.)
      var _employeesScriptNonce = (document.currentScript && document.currentScript.nonce) || '';

      // ========== FILTERS ==========
      let activeStatusFilter = '';

      function filterByStatus(status) {
        activeStatusFilter = status;
        document.getElementById('filterStatus').value = status;
        applyFilters();
        ['card-all','card-active','card-onleave','card-resigned'].forEach(id => {
          document.getElementById(id).classList.remove('active-filter');
        });
        const map = {'':'card-all','Active':'card-active','On Leave':'card-onleave','Resigned':'card-resigned'};
        if (map[status]) document.getElementById(map[status]).classList.add('active-filter');
      }

      function filterByDept(dept) {
        document.getElementById('filterDept').value = dept;
        document.querySelectorAll('.dept-tab').forEach(btn => {
          btn.classList.toggle('active', btn.dataset.dept === dept);
        });
        applyFilters();
      }

      // ── PAGINATION STATE ──
      const PAGE_SIZE = 20;
      let currentPage = 1;
      let filteredRows = [];

      function applyFilters() {
        const q       = document.getElementById('searchInput').value.toLowerCase();
        const dept    = document.getElementById('filterDept').value.toLowerCase();
        const shift   = document.getElementById('filterShift').value.toLowerCase();
        const status  = document.getElementById('filterStatus').value.toLowerCase();
        const gender  = document.getElementById('filterGender').value.toLowerCase();
        activeStatusFilter = document.getElementById('filterStatus').value;

        const rows = Array.from(document.querySelectorAll('#empTable tbody tr'));
        filteredRows = rows.filter(row => {
          const text      = row.textContent.toLowerCase();
          const rowDept   = (row.dataset.dept   || '').toLowerCase();
          const rowShift  = (row.dataset.shift  || '').toLowerCase();
          const rowStatus = (row.dataset.status || '').toLowerCase();
          const rowGender = (row.dataset.gender || '').toLowerCase();
          return (!q      || text.includes(q))
              && (!dept   || rowDept.includes(dept))
              && (!shift  || rowShift.includes(shift))
              && (!status || rowStatus === status)
              && (!gender || rowGender === gender);
        });

        // Hide all rows first
        rows.forEach(r => r.style.display = 'none');

        currentPage = 1;
        renderPage();

        document.getElementById('empCount').textContent =
          filteredRows.length + ' employee' + (filteredRows.length !== 1 ? 's' : '');

        const anyFilter = q || dept || shift || status || gender;
        document.getElementById('btnClearFilter').style.display = anyFilter ? 'flex' : 'none';
      }

      function renderPage() {
        const start = (currentPage - 1) * PAGE_SIZE;
        const end   = start + PAGE_SIZE;

        // Hide all filtered rows then show current page slice
        filteredRows.forEach((r, i) => r.style.display = (i >= start && i < end) ? '' : 'none');

        const totalPages = Math.max(1, Math.ceil(filteredRows.length / PAGE_SIZE));
        document.getElementById('pageInfo').textContent =
          'Showing ' + (Math.min(start + 1, filteredRows.length)) + '–' +
          Math.min(end, filteredRows.length) + ' of ' + filteredRows.length;

        document.getElementById('btnPrev').disabled = currentPage <= 1;
        document.getElementById('btnNext').disabled = currentPage >= totalPages;

        // Page number buttons (show up to 5)
        const btns = document.getElementById('pageButtons');
        btns.innerHTML = '';
        const rangeStart = Math.max(1, currentPage - 2);
        const rangeEnd   = Math.min(totalPages, rangeStart + 4);
        for (let p = rangeStart; p <= rangeEnd; p++) {
          const b = document.createElement('button');
          b.type = 'button';
          b.textContent = p;
          b.className = 'btn-page' + (p === currentPage ? ' active' : '');
          b.onclick = (function(pg){ return function(){ currentPage = pg; renderPage(); }; })(p);
          btns.appendChild(b);
        }
      }

      // After Add Employee the server redirects here with ?new=<id> -- open
      // the page that row is on and highlight it, since name order usually
      // puts a new hire past the first page.
      function showNewEmployee() {
        const params = new URLSearchParams(window.location.search);
        const newId = params.get('new');
        if (!newId) return;
        params.delete('new');
        const qs = params.toString();
        history.replaceState(null, '', window.location.pathname + (qs ? '?' + qs : ''));
        const idx = filteredRows.findIndex(r => r.dataset.empId === newId);
        if (idx < 0) return;
        currentPage = Math.floor(idx / PAGE_SIZE) + 1;
        renderPage();
        const row = filteredRows[idx];
        row.classList.add('row-new');
        row.scrollIntoView({behavior: 'smooth', block: 'center'});
        setTimeout(() => row.classList.remove('row-new'), 4000);
      }

      function changePage(delta) {
        const totalPages = Math.max(1, Math.ceil(filteredRows.length / PAGE_SIZE));
        currentPage = Math.max(1, Math.min(totalPages, currentPage + delta));
        renderPage();
      }

      // Initialise on load
      document.addEventListener('DOMContentLoaded', function() {
        applyFilters();
        showNewEmployee();
        // Auto-open Add Employee modal if redirected from dashboard button
        if (new URLSearchParams(window.location.search).get('open') === 'add') {
          openModal('addModal');
          history.replaceState(null, '', '/employees');
        }
      });

      function clearFilters() {
        document.getElementById('searchInput').value = '';
        document.getElementById('filterDept').value   = '';
        document.getElementById('filterShift').value  = '';
        document.getElementById('filterStatus').value = '';
        document.getElementById('filterGender').value = '';
        activeStatusFilter = '';
        ['card-all','card-active','card-onleave','card-resigned'].forEach(id =>
          document.getElementById(id).classList.remove('active-filter'));
        document.querySelectorAll('.dept-tab').forEach(btn =>
          btn.classList.toggle('active', btn.dataset.dept === ''));
        applyFilters();
      }

      // Highlight from ?hl=EMP001
      (function(){
        const hl = new URLSearchParams(window.location.search).get('hl');
        if (!hl) return;
        document.getElementById('searchInput').value = hl;
        applyFilters();
        document.querySelectorAll('#empTable tbody tr').forEach(function(row){
          if (row.textContent.includes(hl)) {
            row.style.background = '#fefce8';
            row.style.outline = '2px solid #fbbf24';
            setTimeout(function(){ row.style.transition='background 1.5s,outline 1.5s'; row.style.background=''; row.style.outline=''; }, 2500);
            row.scrollIntoView({ behavior:'smooth', block:'center' });
          }
        });
      })();

      // ========== MODAL HELPERS ==========
      function openModal(id) {
        document.getElementById(id).classList.add('open');
        document.body.style.overflow = 'hidden';
        if (id === 'addModal') { setTimeout(generateEmpId, 50); resetEduRows(); }
      }
      function closeModal(id) {
        document.getElementById(id).classList.remove('open');
        document.body.style.overflow = '';
      }
      document.querySelectorAll('.modal-backdrop').forEach(function(el) {
        el.addEventListener('click', function(e) { if (e.target === el) closeModal(el.id); });
      });

      // ========== TABS ==========
      function switchTab(tabId, btn) {
        document.querySelectorAll('#editModal .tab-pane').forEach(p => p.classList.remove('active'));
        document.querySelectorAll('#editModal .tab-btn').forEach(b => b.classList.remove('active'));
        document.getElementById(tabId).classList.add('active');
        btn.classList.add('active');
      }

      // ========== PHOTO PREVIEW ==========
      function previewPhoto(input, previewId) {
        const preview = document.getElementById(previewId);
        if (!input.files || !input.files[0]) return;
        const file = input.files[0];
        HZPhotoCheck.check(file, function (error) {
          if (error) {
            input.value = '';
            preview.removeAttribute('src');
            preview.style.display = 'none';
            HZPhotoCheck.warn(error);
            return;
          }
          const reader = new FileReader();
          reader.onload = e => { preview.src = e.target.result; preview.style.display = 'block'; };
          reader.readAsDataURL(file);
        });
      }

      // ========== GEOLOCATION ==========
      function useMyLocation(latId, lonId) {
        if (!navigator.geolocation) { alert('Geolocation not supported.'); return; }
        navigator.geolocation.getCurrentPosition(
          pos => {
            document.getElementById(latId).value = pos.coords.latitude.toFixed(6);
            document.getElementById(lonId).value = pos.coords.longitude.toFixed(6);
          },
          () => alert('Could not get location. Please enter manually.')
        );
      }

      // ========== PHOTO LIGHTBOX ==========
      var _lbEmpId = null;
      function openLightbox(empId, empName, hasPhoto) {
        _lbEmpId = empId;
        document.getElementById('lbName').textContent = empName;
        document.getElementById('lbId').textContent   = 'ID: ' + empId;
        var img   = document.getElementById('lbImg');
        var dlBtn = document.getElementById('lbDlBtn');
        if (hasPhoto) {
          img.src              = '/dataset/' + empId + '.jpg?t=' + Date.now();
          img.alt              = empName;
          img.style.display    = 'block';
          dlBtn.href           = '/dataset/' + empId + '.jpg';
          dlBtn.download       = empId + '_photo.jpg';
          dlBtn.style.display  = 'inline-flex';
        } else {
          img.style.display    = 'none';
          document.getElementById('lbImgWrap').innerHTML = '<span class="lb-placeholder">👤</span>';
          dlBtn.style.display  = 'none';
        }
        document.getElementById('lbModal').classList.add('open');
        document.body.style.overflow = 'hidden';
      }
      function closeLightbox() {
        document.getElementById('lbModal').classList.remove('open');
        document.body.style.overflow = '';
        _lbEmpId = null;
      }
      function lbSwitchToEdit() {
        var id = _lbEmpId;
        closeLightbox();
        if (id) openPhotoModal(id, document.getElementById('lbName').textContent);
      }
      document.getElementById('lbModal').addEventListener('click', function(e) {
        if (e.target === this) closeLightbox();
      });

      // ========== ADD MODAL ==========
      function toggleAddLocation(mode) {
        document.getElementById('addLocationBox').style.display = mode === 'wfh' ? 'block' : 'none';
      }

      /* ── EMPLOYEE PAGE CAMERA ── */
      var _empCamStream = null, _empCapturedBlob = null;
      async function openEmpCameraModal() {
        _empCapturedBlob = null;
        var modal = document.getElementById('empCameraModal');
        modal.style.display = 'flex';
        document.getElementById('empCamCanvas').style.display  = 'none';
        document.getElementById('empCamVideo').style.display   = 'block';
        document.getElementById('empCaptureBtn').style.display = 'inline-block';
        document.getElementById('empRetakeBtn').style.display  = 'none';
        document.getElementById('empUseBtn').style.display     = 'none';
        document.getElementById('empCamError').style.display   = 'none';
        try {
          _empCamStream = await navigator.mediaDevices.getUserMedia({video:{facingMode:'user',width:{ideal:640},height:{ideal:480}},audio:false});
          document.getElementById('empCamVideo').srcObject = _empCamStream;
        } catch(e) {
          var errDiv = document.getElementById('empCamError');
          if (e.name === 'NotAllowedError' || e.name === 'PermissionDeniedError') {
            errDiv.innerHTML = '<strong>Camera blocked by browser.</strong><br>'
              + 'Click the <strong>lock icon</strong> in the address bar &rarr; <strong>Camera</strong> &rarr; <strong>Allow</strong>, then click the Camera button again.<br>'
              + 'Or use <strong>Upload Photo</strong> to select an image from your device.';
          } else {
            errDiv.innerHTML = '<strong>Camera not available:</strong> ' + e.message + '<br>Use Upload Photo instead.';
          }
          errDiv.style.display = 'block';
          document.getElementById('empCaptureBtn').style.display = 'none';
        }
      }
      function empCapturePhoto() {
        var video = document.getElementById('empCamVideo'), canvas = document.getElementById('empCamCanvas');
        canvas.width = video.videoWidth||640; canvas.height = video.videoHeight||480;
        canvas.getContext('2d').drawImage(video,0,0);
        if (_empCamStream) { _empCamStream.getTracks().forEach(t=>t.stop()); _empCamStream = null; }
        video.style.display = 'none'; canvas.style.display = 'block';
        document.getElementById('empCaptureBtn').style.display = 'none';
        document.getElementById('empRetakeBtn').style.display  = 'inline-block';
        document.getElementById('empUseBtn').style.display     = 'inline-block';
        canvas.toBlob(blob => { _empCapturedBlob = blob; }, 'image/jpeg', 0.92);
      }
      function empRetakePhoto() { _empCapturedBlob = null; openEmpCameraModal(); }
      function empUsePhoto() {
        if (!_empCapturedBlob) return;
        var file = new File([_empCapturedBlob], 'face_capture.jpg', {type:'image/jpeg'});
        var dt = new DataTransfer(); dt.items.add(file);
        document.getElementById('addFaceInput').files = dt.files;
        var url = URL.createObjectURL(_empCapturedBlob);
        var prev = document.getElementById('addPhotoPreview');
        prev.src = url; prev.style.display = 'block';
        document.getElementById('addPhotoLabel').textContent = '✓ Photo captured from camera';
        document.getElementById('addPhotoBox').style.borderColor = '#22c55e';
        document.getElementById('addPhotoErr').style.display = 'none';
        closeEmpCameraModal();
      }
      function closeEmpCameraModal() {
        if (_empCamStream) { _empCamStream.getTracks().forEach(t=>t.stop()); _empCamStream = null; }
        document.getElementById('empCameraModal').style.display = 'none';
      }
      document.getElementById('empCameraModal').addEventListener('click', function(e) {
        if (e.target === e.currentTarget) closeEmpCameraModal();
      });
      function empPhotoSelected(input) {
        if (!input.files || !input.files[0]) return;
        var file = input.files[0];
        HZPhotoCheck.check(file, function (error) {
          var prev = document.getElementById('addPhotoPreview');
          if (error) {
            input.value = '';
            prev.removeAttribute('src'); prev.style.display = 'none';
            document.getElementById('addPhotoLabel').textContent = 'No photo selected';
            document.getElementById('addPhotoBox').style.borderColor = '';
            HZPhotoCheck.warn(error);
            return;
          }
          var reader = new FileReader();
          reader.onload = function(e) { prev.src = e.target.result; prev.style.display = 'block'; };
          reader.readAsDataURL(file);
          document.getElementById('addPhotoLabel').textContent = '✓ ' + file.name;
          document.getElementById('addPhotoBox').style.borderColor = '#22c55e';
          document.getElementById('addPhotoErr').style.display = 'none';
        });
      }

      function toggleSubSection(id) {
        var el   = document.getElementById(id);
        var icon = document.getElementById(id + '-icon');
        var open = el.style.display === 'none';
        el.style.display = open ? 'block' : 'none';
        if (icon) icon.style.transform = open ? 'rotate(180deg)' : '';
      }

      function validateAddForm(form) {
        // Photo is optional when adding an employee -- it can be added later
        // from the employee's page -- so there is nothing to block here.
        var errDiv = document.getElementById('addPhotoErr');
        if (errDiv) errDiv.style.display = 'none';
        // All paid seats in use: pay for this employee's month first
        // (static/js/seat-pay.js), then send the form.
        if (form.dataset.seatPay === '1' && form.dataset.seatPaid !== '1' && window.HZSeatPay) {
          if (!form.reportValidity()) return false;
          var btn = form.querySelector('button[type=submit]');
          if (btn) btn.disabled = true;
          HZSeatPay.buy(1, form.dataset.seatLabel).then(function (paid) {
            if (btn) btn.disabled = false;
            if (!paid) return;
            form.dataset.seatPaid = '1';
            if (btn) btn.disabled = true;
            form.submit();
          });
          return false;
        }
        return true;
      }

      function makeEduRow(degree, institution, year, pct) {
        var row = document.createElement('div');
        Object.assign(row.style, {display:'grid', gridTemplateColumns:'1fr 1fr 90px 70px 28px', gap:'6px', alignItems:'center'});
        row.innerHTML =
          '<input class="u-p-7-10 u-bd-1-solid-e2e8f0 u-br-8 u-fs-12 u-ff-inherit u-w-100pct" type="text" name="degree[]" value="' + degree + '" placeholder="B.Tech" />' +
          '<input class="u-p-7-10 u-bd-1-solid-e2e8f0 u-br-8 u-fs-12 u-ff-inherit u-w-100pct" type="text" name="institution[]" value="' + institution + '" placeholder="University" />' +
          '<input class="u-p-7-8 u-bd-1-solid-e2e8f0 u-br-8 u-fs-12 u-ff-inherit u-w-100pct" type="text" name="year_of_passing[]" value="' + year + '" placeholder="2020" />' +
          '<input class="u-p-7-8 u-bd-1-solid-e2e8f0 u-br-8 u-fs-12 u-ff-inherit u-w-100pct" type="text" name="percentage[]" value="' + pct + '" placeholder="%" />' +
          '<button class="u-w-26 u-h-26 u-bd-1-solid-fecaca u-br-7 u-bg-fff5f5 u-c-dc2626 u-fs-14 u-cur-pointer u-d-flex u-ai-center u-jc-center u-ff-inherit" type="button" data-on-click="HZ.removeParent(this)">×</button>';
        return row;
      }
      function addEduRow() {
        document.getElementById('addEduList').appendChild(makeEduRow('', '', '', ''));
      }
      function resetEduRows() {
        var list = document.getElementById('addEduList');
        if (list) list.innerHTML = '';
      }

      async function generateEmpId() {
        var hint = document.getElementById('empIdHint');
        var inp  = document.getElementById('addEmpId');
        if (!inp) return;
        inp.placeholder = 'Generating…';
        try {
          var res  = await fetch('/api/generate_emp_id');
          if (!res.ok) { throw new Error('HTTP ' + res.status); }
          var data = await res.json();
          if (data.error) { throw new Error(data.error); }
          inp.value = data.emp_id;
          inp.placeholder = '';
          if (hint) {
            hint.textContent = '✓ ' + data.emp_id + ' — employee #' + data.seq;
            hint.style.color = '#16a34a';
          }
        } catch(e) {
          inp.placeholder = 'Enter manually';
          if (hint) { hint.textContent = 'Could not auto-generate. Enter manually.'; hint.style.color = '#dc2626'; }
        }
      }

      // ========== EDIT MODAL ==========
      function toggleEditLocation(mode) {
        document.getElementById('editLocationBox').style.display = mode === 'wfh' ? 'block' : 'none';
      }

      async function openEdit(empId) {
        openModal('editModal');
        // reset to first tab
        switchTab('tab-basic', document.querySelector('#editModal .tab-btn'));
        document.getElementById('edit_emp_id').value         = empId;
        document.getElementById('edit_emp_id_display').value = empId;
        document.getElementById('editEmpBadge').textContent  = '';
        try {
          const res  = await fetch('/api/employee_info/' + empId);
          const d    = await res.json();
          document.getElementById('editEmpBadge').textContent  = '— ' + (d.name || '');
          document.getElementById('edit_name').value          = d.name         || '';
          document.getElementById('edit_email').value         = d.email        || '';
          document.getElementById('edit_phone').value         = d.phone        || '';
          document.getElementById('edit_role').value          = d.role         || '';
          var editAssignedHr = document.getElementById('edit_assigned_hr');
          if (editAssignedHr) editAssignedHr.value = d.assigned_hr_username || '';
          document.getElementById('edit_department').value    = d.department   || '';
          document.getElementById('edit_gender').value        = d.gender       || '';
          document.getElementById('edit_dob').value           = d.dob          || '';
          document.getElementById('edit_blood_group').value   = d.blood_group  || '';
          document.getElementById('edit_doj').value           = d.doj          || '';
          document.getElementById('edit_shift_id').value      = d.shift_id     || '';
          document.getElementById('edit_manager_name').value  = d.manager_name || '';
          document.getElementById('edit_manager_id').value    = d.manager_id   || '';
          document.getElementById('edit_address').value       = d.address      || '';
          document.getElementById('edit_city').value          = d.city         || '';
          document.getElementById('edit_state').value         = d.state        || '';
          document.getElementById('edit_pincode').value       = d.pincode      || '';
          document.getElementById('edit_ec_name').value       = d.ec_name      || '';
          document.getElementById('edit_ec_rel').value        = d.ec_rel       || '';
          document.getElementById('edit_ec_phone').value      = d.ec_phone     || '';
          const wm = d.work_mode || 'office';
          document.getElementById('emp_edit_wm_office').checked = wm === 'office';
          document.getElementById('emp_edit_wm_wfh').checked    = wm === 'wfh';
          document.getElementById('emp_edit_work_lat').value    = d.work_lat || '';
          document.getElementById('emp_edit_work_lon').value    = d.work_lon || '';
          toggleEditLocation(wm);
          // Check fingerprint enrollment status
          const fpSection = document.getElementById('editFpSection');
          const fpStatus  = document.getElementById('editFpStatus');
          const fpMsg     = document.getElementById('editFpMsg');
          fpSection.style.display = 'none';
          fpMsg.style.display = 'none';
          try {
            const fpR = await fetch('/api/employee/' + encodeURIComponent(empId) + '/webauthn-credential');
            const fpD = await fpR.json();
            if (fpD.ok) {
              fpSection.style.display = 'block';
              fpMsg.style.display = 'none';
              fpMsg.textContent = '';
              if (fpD.credential_id) {
                fpStatus.textContent = '— Enrolled';
                fpStatus.style.color = '#15803d';
                document.getElementById('editFpResetBtn').style.display = 'inline-block';
                document.getElementById('editFpEnrollBtn').style.display = 'none';
              } else {
                fpStatus.textContent = '— Not enrolled';
                fpStatus.style.color = '#94a3b8';
                document.getElementById('editFpResetBtn').style.display = 'none';
                document.getElementById('editFpEnrollBtn').style.display = 'inline-block';
                document.getElementById('editFpEnrollBtn').disabled = false;
                document.getElementById('editFpEnrollBtn').textContent = '👆 Enroll Fingerprint';
              }
            }
          } catch(_) {}
        } catch(e) { alert('Could not load employee data.'); closeModal('editModal'); }
      }

      function resetEditedEmployeeFingerprint() {
        return resetEmployeeFingerprint(document.getElementById('edit_emp_id').value);
      }

      async function resetEmployeeFingerprint(empId) {
        if (!(await hzConfirm('Reset fingerprint for ' + empId + '? They will need to re-enroll from their employee portal.'))) return;
        const fpMsg = document.getElementById('editFpMsg');
        fpMsg.style.display = 'none';
        try {
          const r = await fetch('/api/admin/employee/' + encodeURIComponent(empId) + '/reset-fingerprint', { method: 'POST' });
          const d = await r.json();
          fpMsg.style.display = 'block';
          if (d.ok) {
            fpMsg.textContent = '✓ Fingerprint reset. Click Enroll Fingerprint to re-enroll.';
            fpMsg.style.color = '#15803d';
            document.getElementById('editFpStatus').textContent = '— Not enrolled';
            document.getElementById('editFpStatus').style.color = '#94a3b8';
            document.getElementById('editFpResetBtn').style.display = 'none';
            const enrollBtn = document.getElementById('editFpEnrollBtn');
            enrollBtn.style.display = 'inline-block';
            enrollBtn.disabled = false;
            enrollBtn.textContent = '👆 Enroll Fingerprint';
          } else {
            fpMsg.textContent = '✗ ' + (d.msg || 'Reset failed.');
            fpMsg.style.color = '#dc2626';
          }
        } catch(e) {
          fpMsg.style.display = 'block';
          fpMsg.textContent = '✗ Network error.';
          fpMsg.style.color = '#dc2626';
        }
      }

      // ========== PHOTO MODAL ==========
      function openPhotoModal(empId, name) {
        document.getElementById('photoModalSubtitle').textContent = 'Updating photo for: ' + name + ' (' + empId + ')';
        document.getElementById('photoForm').action = '/update_employee_photo/' + empId;
        document.getElementById('photoFileInput').value = '';
        document.getElementById('photoPreviewImg').style.display = 'none';
        openModal('photoModal');
      }

      // ========== QR MODAL ==========
      function openQR(empId, name, hasQr) {
        document.getElementById('qrEmpName').textContent = name;
        document.getElementById('qrEmpId').textContent   = 'ID: ' + empId;
        const imgEl     = document.getElementById('qrImg');
        const dlBtn     = document.getElementById('qrDownload');
        const regenForm = document.getElementById('qrRegenForm');
        regenForm.action = '/regenerate_qr/' + empId;
        // qr_code is now an opaque private-storage reference (see
        // qr_generator.py's generate_qr()), never a usable relative URL --
        // the only way to fetch the image itself is this authenticated,
        // ownership-checked route, same reasoning as regenForm.action above.
        const qrUrl = '/employees/' + empId + '/qr';
        if (hasQr && hasQr !== 'None' && hasQr !== '') {
          imgEl.src           = qrUrl;
          imgEl.style.display = 'block';
          dlBtn.href          = qrUrl;
          dlBtn.download      = empId + '_qr.png';
          dlBtn.style.display = 'inline-flex';
        } else {
          imgEl.style.display = 'none';
          dlBtn.style.display = 'none';
        }
        openModal('qrModal');
      }
      // ========== ID CARD MODAL ==========
      var _idCardEmpId = '';
      function idCardLoaded(img) {
        document.getElementById('idCardLoading').style.display = 'none';
        img.style.display = 'block';
      }

      function idCardFailed() {
        document.getElementById('idCardLoading').textContent = 'Failed to generate ID card.';
      }

      function openIDCard(empId) {
        _idCardEmpId = empId;
        var img     = document.getElementById('idCardImg');
        var loading = document.getElementById('idCardLoading');
        var dlBtn   = document.getElementById('idCardDownload');

        img.style.display = 'none';
        loading.style.display = 'block';
        loading.textContent = 'Generating ID card…';

        var viewUrl = '/admin_view_id_card/' + encodeURIComponent(empId);
        var dlUrl   = '/admin_id_card/'      + encodeURIComponent(empId);

        img.src         = viewUrl + '?t=' + Date.now();
        dlBtn.href      = dlUrl;
        dlBtn.download  = 'IDCard_' + empId + '.png';

        openModal('idCardModal');
      }

      function printIDCard() {
        var img = document.getElementById('idCardImg');
        if (!img.src || img.style.display === 'none') return;
        var win = window.open('', '_blank', 'width=620,height=900');
        win.document.write('<html><head><title>ID Card - ' + _idCardEmpId + '<\/title>'
          + '<style nonce="' + _employeesScriptNonce + '">body{margin:0;display:flex;justify-content:center;align-items:center;min-height:100vh;background:#f1f5f9;}'
          + 'img{max-width:520px;border-radius:12px;box-shadow:0 4px 24px rgba(0,0,0,.15);}'
          + '@media print{body{background:#fff;}}<\/style><\/head>'
          + '<body><img src="' + img.src + '" \/><\/body><\/html>');
        win.document.close();
        var printImg = win.document.querySelector('img');
        if (printImg.complete) win.print();
        else printImg.addEventListener('load', function () { win.print(); });
      }

      document.addEventListener('keydown', function(e) {
        if (e.key !== 'Escape') return;
        closeLightbox();
        ['addModal','editModal','photoModal','qrModal','idCardModal'].forEach(id => closeModal(id));
      });

      // ========== FINGERPRINT ENROLLMENT ==========
      async function enrollFingerprint() {
        var btn    = document.getElementById('fpEnrollBtn');
        var status = document.getElementById('fpEnrollStatus');
        var box    = document.getElementById('fpEnrollBox');

        if (!window.PublicKeyCredential) {
          status.textContent = '⚠ WebAuthn not supported. Use Chrome or Edge.';
          status.style.color = '#f59e0b'; return;
        }

        // Check for built-in fingerprint / biometric (Touch ID, Windows Hello, Android)
        var hasPlatform = await window.PublicKeyCredential
          .isUserVerifyingPlatformAuthenticatorAvailable().catch(function(){ return false; });

        if (!hasPlatform) {
          status.innerHTML = '🔌 No built-in fingerprint sensor detected.<br>'
            + 'Connect a <strong>USB fingerprint scanner</strong> then click Enroll.';
          status.style.color = '#f59e0b';
          btn.textContent = '🔌 Enroll via USB Scanner';
        }

        btn.disabled = true;
        if (hasPlatform) {
          btn.textContent = '⏳ Waiting for fingerprint…';
          status.textContent = 'Touch your fingerprint sensor when prompted…';
          status.style.color = '#6366f1';
        } else {
          btn.textContent = '⏳ Waiting for USB scanner…';
        }

        try {
          var empId   = (document.getElementById('addEmpId').value || 'employee').trim().toUpperCase();
          var empName = (document.querySelector('[name="name"]').value || empId).trim();

          var optResp = await fetch('/webauthn/registration-options?emp_id=' + encodeURIComponent(empId) + '&name=' + encodeURIComponent(empName));
          if (!optResp.ok) {
            var errBody = await optResp.text().catch(function(){ return ''; });
            var errMsg = '';
            try { errMsg = JSON.parse(errBody).error || JSON.parse(errBody).msg || ''; } catch(e) {}
            status.textContent = '❌ Server error getting enrolment options (HTTP ' + optResp.status + ')' + (errMsg ? ': ' + errMsg : '') + '. Check server logs.';
            status.style.color = '#dc2626';
            btn.disabled = false; btn.textContent = '👆 Enroll Fingerprint';
            return;
          }
          var regOpts = await optResp.json();
          if (regOpts.ok === false) {
            status.textContent = '❌ ' + (regOpts.msg || regOpts.error || 'Could not start enrolment.');
            status.style.color = '#dc2626';
            btn.disabled = false; btn.textContent = '👆 Enroll Fingerprint';
            return;
          }

          function toBytes(b64url) {
            var b64 = b64url.replace(/-/g,'+').replace(/_/g,'/');
            var pad = b64.length % 4 ? '='.repeat(4 - b64.length % 4) : '';
            return Uint8Array.from(atob(b64 + pad), function(c){ return c.charCodeAt(0); });
          }
          function toB64url(buf) {
            var s = ''; new Uint8Array(buf).forEach(function(b){ s += String.fromCharCode(b); });
            return btoa(s).replace(/\+/g,'-').replace(/\//g,'_').replace(/=/g,'');
          }

          var cred = await navigator.credentials.create({
            publicKey: {
              challenge:              toBytes(regOpts.challenge),
              rp:                     regOpts.rp,
              user:                   { id: toBytes(regOpts.user.id), name: regOpts.user.name, displayName: regOpts.user.displayName },
              pubKeyCredParams:       regOpts.pubKeyCredParams,
              authenticatorSelection: regOpts.authenticatorSelection,
              timeout:              regOpts.timeout || 60000,
              attestation:          'none',
              excludeCredentials:   [],
            }
          });

          function regCredentialToJSON(c) {
            if (c.toJSON) return c.toJSON();
            return {
              id: c.id,
              rawId: toB64url(c.rawId),
              type: c.type,
              response: {
                clientDataJSON:    toB64url(c.response.clientDataJSON),
                attestationObject: toB64url(c.response.attestationObject),
              },
              clientExtensionResults: c.getClientExtensionResults ? c.getClientExtensionResults() : {},
            };
          }
          document.getElementById('fpAttestation').value = JSON.stringify(regCredentialToJSON(cred));
          var label = hasPlatform ? 'Fingerprint enrolled!' : 'USB fingerprint enrolled!';
          status.innerHTML = '✅ <strong>' + label + '</strong> Employee can use biometric attendance.';
          status.style.color = '#16a34a';
          btn.textContent = '✅ Enrolled';
          btn.style.background = '#16a34a';
          box.style.borderColor = '#22c55e';
          box.style.background  = '#f0fdf4';
          document.getElementById('fpClearBtn').style.display = 'inline-block';
        } catch (e) {
          btn.disabled = false;
          btn.textContent = hasPlatform ? '👆 Enroll Fingerprint' : '🔌 Enroll via USB Scanner';
          status.style.color = '#dc2626';
          if (e.name === 'NotAllowedError') {
            status.textContent = '❌ Cancelled or timed out. Click to try again.';
          } else if (e.name === 'InvalidStateError') {
            status.textContent = '❌ A passkey for this employee already exists on this device. Clear it or use a different device.';
          } else if (e.name === 'SecurityError') {
            status.textContent = '❌ Security error: ' + e.message + ' — enrolment requires HTTPS with a domain name, not an IP address.';
          } else {
            status.textContent = '❌ ' + (e.message || 'Enrollment failed. Try a different browser.');
          }
        }
      }

      async function enrollFingerprintForEdit() {
        var enrollBtn = document.getElementById('editFpEnrollBtn');
        var fpStatus  = document.getElementById('editFpStatus');
        var fpMsg     = document.getElementById('editFpMsg');
        var empId     = (document.getElementById('edit_emp_id').value || '').trim().toUpperCase();
        var empName   = (document.getElementById('edit_name').value || empId).trim();

        if (!window.PublicKeyCredential) {
          fpMsg.textContent = '⚠ WebAuthn not supported. Use Chrome or Edge.';
          fpMsg.style.color = '#f59e0b';
          fpMsg.style.display = 'block';
          return;
        }

        var hasPlatform = await window.PublicKeyCredential
          .isUserVerifyingPlatformAuthenticatorAvailable().catch(function(){ return false; });

        enrollBtn.disabled = true;
        enrollBtn.textContent = hasPlatform ? '⏳ Waiting for fingerprint…' : '⏳ Waiting for USB scanner…';
        fpMsg.style.display = 'none';

        try {
          var optResp = await fetch('/webauthn/registration-options?emp_id=' + encodeURIComponent(empId) + '&name=' + encodeURIComponent(empName));
          if (!optResp.ok) {
            var errBody = await optResp.text().catch(function(){ return ''; });
            var errMsg = '';
            try { errMsg = JSON.parse(errBody).error || JSON.parse(errBody).msg || ''; } catch(e) {}
            fpMsg.textContent = '❌ Server error (' + optResp.status + ')' + (errMsg ? ': ' + errMsg : '');
            fpMsg.style.color = '#dc2626';
            fpMsg.style.display = 'block';
            enrollBtn.disabled = false; enrollBtn.textContent = '👆 Enroll Fingerprint';
            return;
          }
          var regOpts = await optResp.json();
          if (regOpts.ok === false) {
            fpMsg.textContent = '❌ ' + (regOpts.msg || regOpts.error || 'Could not start enrolment.');
            fpMsg.style.color = '#dc2626';
            fpMsg.style.display = 'block';
            enrollBtn.disabled = false; enrollBtn.textContent = '👆 Enroll Fingerprint';
            return;
          }

          function toBytes(b64url) {
            var b64 = b64url.replace(/-/g,'+').replace(/_/g,'/');
            var pad = b64.length % 4 ? '='.repeat(4 - b64.length % 4) : '';
            return Uint8Array.from(atob(b64 + pad), function(c){ return c.charCodeAt(0); });
          }
          function toB64url(buf) {
            var s = ''; new Uint8Array(buf).forEach(function(b){ s += String.fromCharCode(b); });
            return btoa(s).replace(/\+/g,'-').replace(/\//g,'_').replace(/=/g,'');
          }

          var cred = await navigator.credentials.create({
            publicKey: {
              challenge:              toBytes(regOpts.challenge),
              rp:                     regOpts.rp,
              user:                   { id: toBytes(regOpts.user.id), name: regOpts.user.name, displayName: regOpts.user.displayName },
              pubKeyCredParams:       regOpts.pubKeyCredParams,
              authenticatorSelection: regOpts.authenticatorSelection,
              timeout:                regOpts.timeout || 60000,
              attestation:            'none',
              excludeCredentials:     [],
            }
          });

          function credToJSON(c) {
            if (c.toJSON) return c.toJSON();
            return {
              id: c.id,
              rawId: toB64url(c.rawId),
              type: c.type,
              response: {
                clientDataJSON:    toB64url(c.response.clientDataJSON),
                attestationObject: toB64url(c.response.attestationObject),
              },
              clientExtensionResults: c.getClientExtensionResults ? c.getClientExtensionResults() : {},
            };
          }

          var regResp = await fetch('/api/employee/webauthn-register-kiosk', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ emp_id: empId, credential: credToJSON(cred) }),
          });
          var regData = await regResp.json();
          if (!regResp.ok || regData.ok === false) {
            fpMsg.textContent = '❌ ' + (regData.msg || 'Enrollment failed. Try again.');
            fpMsg.style.color = '#dc2626';
            fpMsg.style.display = 'block';
            enrollBtn.disabled = false; enrollBtn.textContent = '👆 Enroll Fingerprint';
            return;
          }

          fpStatus.textContent = '— Enrolled';
          fpStatus.style.color = '#15803d';
          fpMsg.textContent = '✅ Fingerprint enrolled successfully!';
          fpMsg.style.color = '#16a34a';
          fpMsg.style.display = 'block';
          enrollBtn.style.display = 'none';
          document.getElementById('editFpResetBtn').style.display = 'inline-block';

        } catch (e) {
          enrollBtn.disabled = false; enrollBtn.textContent = '👆 Enroll Fingerprint';
          fpMsg.style.color = '#dc2626';
          fpMsg.style.display = 'block';
          if (e.name === 'NotAllowedError') {
            fpMsg.textContent = '❌ Cancelled or timed out. Click to try again.';
          } else if (e.name === 'InvalidStateError') {
            fpMsg.textContent = '❌ A passkey for this employee already exists on this device.';
          } else if (e.name === 'SecurityError') {
            fpMsg.textContent = '❌ Security error: ' + e.message;
          } else {
            fpMsg.textContent = '❌ ' + (e.message || 'Enrollment failed.');
          }
        }
      }

      function clearFingerprint() {
        document.getElementById('fpAttestation').value = '';
        var status = document.getElementById('fpEnrollStatus');
        var box    = document.getElementById('fpEnrollBox');
        var btn    = document.getElementById('fpEnrollBtn');
        status.textContent = 'Not enrolled — employee can use QR + Face by default';
        status.style.color = '#64748b';
        btn.disabled = false;
        btn.textContent = '👆 Enroll Fingerprint';
        btn.style.background = '#6366f1';
        box.style.borderColor = '#bfdbfe';
        box.style.background  = '#f8fafc';
        document.getElementById('fpClearBtn').style.display = 'none';
      }
    