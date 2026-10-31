/* ==========================================================================
   HRzest - Platform Admin banner: puts the HRZest logo at the left of the
   page header (which static/platform-theme.css turns into the full-width blue
   banner) and adds a live date + time chip next to the page's own buttons
   (Sign Out / Back to Console).
   ========================================================================== */
(function () {
  var header = document.querySelector('.container > .header');
  if (!header) return;

  // HRZest logo: the mark on a white tile (its navy would vanish on the blue)
  var brand = document.createElement('a');
  brand.className = 'pa-brand';
  brand.href = '/super_admin';
  brand.setAttribute('aria-label', 'HRZest platform console');
  var tile = document.createElement('span');
  tile.className = 'pa-brand-logo';
  var mark = document.createElement('img');
  mark.src = '/static/brand/hrzest-mark.svg';
  mark.alt = '';
  tile.appendChild(mark);
  var word = document.createElement('span');
  word.className = 'pa-brand-name';
  word.textContent = 'HRZest';
  brand.appendChild(tile);
  brand.appendChild(word);
  header.insertBefore(brand, header.firstChild);

  // group the right-hand side: date chip + whatever the page already has there
  var right = header.lastElementChild && header.lastElementChild.tagName !== 'H1' ? header.lastElementChild : null;
  var group = document.createElement('div');
  group.className = 'pa-actions';
  var chip = document.createElement('span');
  chip.className = 'pa-date';
  chip.setAttribute('aria-label', 'Today');
  var icon = document.createElement('i');
  icon.className = 'ti ti-calendar';
  icon.setAttribute('aria-hidden', 'true');
  var text = document.createElement('span');
  chip.appendChild(icon);
  chip.appendChild(text);
  group.appendChild(chip);
  if (right) { header.insertBefore(group, right); group.appendChild(right); }
  else header.appendChild(group);

  function tick() {
    var now = new Date();
    text.textContent = now.toLocaleDateString('en-IN', {weekday: 'short', day: '2-digit', month: 'short', year: 'numeric'}) +
      '  ·  ' + now.toLocaleTimeString('en-IN', {hour: '2-digit', minute: '2-digit', hour12: true});
  }
  tick();
  setInterval(tick, 15000);
})();
