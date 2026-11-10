/* seat-pay.js -- pay for employee seats (one month each) before adding
 * employees beyond the seats already paid for: the Employees page's Add
 * Employee form and Excel import. Goes through blueprints/seats.py's
 * /api/seats/create_order + /api/seats/verify_payment -- Razorpay Checkout
 * with real keys, a confirm step in test mode (no keys, nothing charged).
 *
 *   HZSeatPay.buy(count, label).then(function (ok) { ... })
 *
 * resolves true once the seats are paid for, false if the payment was
 * cancelled or failed (the reason is shown with HZSeatPay.onError, an alert
 * by default).
 */
(function () {
  'use strict';
  if (window.HZSeatPay) return;

  var CHECKOUT_JS = 'https://checkout.razorpay.com/v1/checkout.js';

  function postJson(url, body) {
    return fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    }).then(function (r) { return r.json(); });
  }

  function loadCheckout() {
    if (window.Razorpay) return Promise.resolve();
    return new Promise(function (resolve, reject) {
      var s = document.createElement('script');
      s.src = CHECKOUT_JS;
      s.onload = function () { resolve(); };
      s.onerror = function () { reject(new Error('Could not load Razorpay. Check your connection.')); };
      document.head.appendChild(s);
    });
  }

  function verify(fields) {
    return postJson('/api/seats/verify_payment', fields).then(function (json) {
      if (!json.ok) throw new Error(json.msg || 'Payment could not be verified.');
      return true;
    });
  }

  function payWithRazorpay(order, label) {
    return loadCheckout().then(function () {
      return new Promise(function (resolve, reject) {
        var settled = false;
        var rzp = new window.Razorpay({
          key: order.key_id,
          amount: order.amount_paise,
          currency: order.currency,
          order_id: order.order_id,
          name: 'HRzest Enterprise',
          description: label,
          handler: function (response) {
            settled = true;
            verify({
              razorpay_order_id: response.razorpay_order_id,
              razorpay_payment_id: response.razorpay_payment_id,
              razorpay_signature: response.razorpay_signature
            }).then(resolve, reject);
          },
          modal: { ondismiss: function () { if (!settled) resolve(false); } },
          theme: { color: '#2563eb' }
        });
        rzp.on('payment.failed', function (resp) {
          settled = true;
          reject(new Error((resp && resp.error && resp.error.description) || 'Payment failed.'));
        });
        rzp.open();
      });
    });
  }

  function payInTestMode(order, label) {
    var ok = window.confirm('Test mode -- no real payment is taken.\n\n' + label + ': ' +
                            order.amount_display + '\n\nConfirm the payment?');
    if (!ok) return Promise.resolve(false);
    return verify({ razorpay_order_id: order.order_id, razorpay_payment_id: 'demo_payment',
                    razorpay_signature: 'demo_signature' });
  }

  var HZSeatPay = {
    onError: function (msg) { window.alert(msg); },
    buy: function (count, label) {
      label = label || (count + ' employee seat' + (count === 1 ? '' : 's') + ' (one month)');
      return postJson('/api/seats/create_order', { additional_seats: count })
        .then(function (order) {
          if (!order.ok) throw new Error(order.msg || 'Could not start the payment.');
          return order.demo ? payInTestMode(order, label) : payWithRazorpay(order, label);
        })
        .catch(function (err) {
          HZSeatPay.onError((err && err.message) || 'Network error. Please try again.');
          return false;
        });
    }
  };
  window.HZSeatPay = HZSeatPay;
})();
