// Company registration form on the home page (templates/landing.html,
// section #register). The same helpers the old /create_org form ran
// inline: suggest the company URL from the name, the admin username and
// email domain from the email, and tidy the GSTIN. Kept in a file because
// the page's CSP only runs 'self' and nonce-tagged scripts.
(function () {
  var form = document.getElementById('homeRegisterForm');
  if (!form) return;

  var company = document.getElementById('regCompanyName');
  var subdomain = document.getElementById('regSubdomain');
  var preview = document.getElementById('regUrlPreview');
  var email = document.getElementById('regAdminEmail');
  var username = document.getElementById('regAdminUsername');
  var emailDomain = document.getElementById('regEmailDomain');
  var gst = document.getElementById('regGstNumber');
  var subdomainEdited = false;
  var usernameEdited = false;

  function showPreview(slug) {
    preview.textContent = 'hrzest.com/' + (slug || 'your-company') + '/login';
  }

  function cleanSubdomain(value) {
    var v = value.toLowerCase().trim().replace(/^https?:\/\//, '').split('/')[0].split(':')[0];
    ['.hrzest.com', '.com', '.in', '.org', '.net', '.io'].forEach(function (suffix) {
      if (v.slice(-suffix.length) === suffix) v = v.slice(0, -suffix.length);
    });
    return v.replace(/[^a-z0-9\-]/g, '');
  }

  function deriveFromEmail() {
    var at = email.value.indexOf('@');
    if (at < 1) return;
    // Suggest a username only until the visitor types their own.
    if (!usernameEdited) {
      username.value = email.value.slice(0, at).toLowerCase().replace(/[^a-z0-9_.\-]/g, '');
    }
    emailDomain.value = email.value.slice(at + 1).toLowerCase().trim();
  }

  company.addEventListener('input', function () {
    if (subdomainEdited) return;
    subdomain.value = company.value.toLowerCase().trim().replace(/[^a-z0-9\-]/g, '');
    showPreview(subdomain.value);
  });
  subdomain.addEventListener('input', function () {
    subdomainEdited = true;
    subdomain.value = cleanSubdomain(subdomain.value);
    showPreview(subdomain.value);
  });
  username.addEventListener('input', function () { usernameEdited = true; });
  email.addEventListener('input', deriveFromEmail);
  email.addEventListener('blur', deriveFromEmail);
  gst.addEventListener('input', function () {
    gst.value = gst.value.toUpperCase().replace(/\s+/g, '');
  });
  // email_domain is a required server-side field filled from the email.
  form.addEventListener('submit', deriveFromEmail);
})();
