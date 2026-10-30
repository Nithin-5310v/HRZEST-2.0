/* HRzest - announcement ribbon: slim bar at the top of every dashboard
   showing the latest announcement (/web/ribbon). Dismissal is remembered
   per announcement id. Text is set with textContent only. */
(function () {
  function dismissed(id) { try { return localStorage.getItem('ribbon-x') === String(id); } catch (e) { return false; } }
  fetch('/web/ribbon', {headers: {'Accept': 'application/json'}, credentials: 'same-origin'})
    .then(function (r) { return r.ok ? r.json() : null; })
    .then(function (d) {
      if (!d || !d.ribbon || dismissed(d.ribbon.id)) return;
      var rb = d.ribbon;
      var bar = document.createElement('div');
      bar.className = 'hz-ribbon' + (/high|urgent/i.test(rb.priority) ? ' hz-ribbon-high' : '');
      bar.setAttribute('role', 'status');
      var ic = document.createElement('i'); ic.className = 'ti ti-speakerphone';
      var t = document.createElement('span'); t.className = 'hz-ribbon-text';
      var b = document.createElement('b'); b.textContent = rb.title;
      t.appendChild(b);
      if (rb.text) t.appendChild(document.createTextNode(' - ' + rb.text));
      var x = document.createElement('button'); x.type = 'button'; x.className = 'hz-ribbon-x';
      x.setAttribute('aria-label', 'Dismiss announcement'); x.textContent = '×';
      x.addEventListener('click', function () {
        try { localStorage.setItem('ribbon-x', String(rb.id)); } catch (e) {}
        bar.remove();
      });
      bar.appendChild(ic); bar.appendChild(t); bar.appendChild(x);
      document.body.insertBefore(bar, document.body.firstChild);
    })
    .catch(function () {});
})();
