/* "Complete your registration" pop-up on the admin dashboard: one step at a
   time, starting on the first unfinished one. <dialog> gives it Esc, focus
   handling and the backdrop. Closing it keeps it shut until the next login, or
   until a step gets finished, when it reopens on the next one. */
(function () {
  var modal = document.getElementById('regModal');
  if (!modal || typeof modal.showModal !== 'function') return;

  var key = 'reg-popup-closed:' + location.pathname + ':' + modal.dataset.login;
  var finished = modal.dataset.finished;
  var panels = Array.prototype.slice.call(modal.querySelectorAll('.reg-panel'));
  var dots = modal.querySelectorAll('.reg-dot');
  var num = document.getElementById('regStepNum');
  var back = document.getElementById('regBack');
  var next = document.getElementById('regNext');
  var current = 0;

  function closedAt() { try { return sessionStorage.getItem(key); } catch (e) { return null; } }
  function remember() { try { sessionStorage.setItem(key, finished); } catch (e) {} }

  function show(index) {
    current = index;
    panels.forEach(function (panel, i) { panel.hidden = i !== index; });
    Array.prototype.forEach.call(dots, function (dot, i) { dot.classList.toggle('current', i === index); });
    num.textContent = index + 1;
    back.hidden = index === 0;
    next.textContent = index === panels.length - 1 ? 'Finish' : 'Next';
  }

  function firstUnfinished() {
    for (var i = 0; i < panels.length; i++) {
      var status = panels[i].dataset.status;
      if (status === 'todo' || status === 'rejected') return i;
    }
    return 0;
  }

  modal.addEventListener('close', remember);
  modal.addEventListener('click', function (e) {
    if (e.target === modal) modal.close();
  });
  modal.querySelectorAll('[data-reg-close]').forEach(function (btn) {
    btn.addEventListener('click', function () { modal.close(); });
  });
  back.addEventListener('click', function () { show(current - 1); });
  next.addEventListener('click', function () {
    if (current === panels.length - 1) modal.close();
    else show(current + 1);
  });

  show(firstUnfinished());
  if (closedAt() !== finished) modal.showModal();
})();
