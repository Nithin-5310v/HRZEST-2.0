/* HRZest logo loader, shown in two places:
   1. After a successful login. The server sets a short-lived hrz_login_ok
      cookie on the redirect that follows accepted credentials (app.py's
      _flag_login_success); the dashboard page (admin_base.html,
      employee_portal.html) consumes it, shows the loader until the page has
      loaded, then fades it out. A wrong password never sets the cookie.
   2. When a new company's registration completes (create_org_pay.html,
      create_org_setup_trial.html call window.hrzLoaderThen(url)).
   Needs static/login-loader.css. */
(function () {
  var COOKIE = 'hrz_login_ok';
  var MIN_MS = 2500;

  var script = document.currentScript;
  var logo = (script && script.getAttribute('data-logo')) || '/static/favicon.svg';

  function show() {
    var el = document.createElement('div');
    el.id = 'hrzLoginLoader';
    el.setAttribute('role', 'status');
    el.setAttribute('aria-label', 'Loading');
    el.innerHTML = '<div class="hrz-ll-brand"><div class="hrz-ll-mark"><img alt="" src="' + logo + '"></div><span class="hrz-ll-word">HRZEST</span></div><div class="hrz-ll-bar"></div><p class="hrz-ll-note">Preparing your workspace</p>';
    document.documentElement.appendChild(el);
    return el;
  }

  // Registration complete: show the loader, then go to the new portal.
  window.hrzLoaderThen = function (url) {
    show();
    setTimeout(function () { window.location.href = url; }, MIN_MS);
  };

  if (document.cookie.split('; ').indexOf(COOKIE + '=1') === -1) return;
  document.cookie = COOKIE + '=; Max-Age=0; path=/; SameSite=Lax';

  var el = show();
  var start = Date.now();
  var done = false;
  function hide() {
    if (done) return;
    done = true;
    var wait = Math.max(0, MIN_MS - (Date.now() - start));
    setTimeout(function () {
      el.classList.add('hrz-hide');
      setTimeout(function () {
        if (el.parentNode) el.parentNode.removeChild(el);
        window.dispatchEvent(new Event('hrz-loader-done')); // toast.js shows any held popups
      }, 700);
    }, wait);
  }
  if (document.readyState === 'complete') hide();
  else window.addEventListener('load', hide);
  setTimeout(hide, 10000); // never trap the user behind the loader
})();
