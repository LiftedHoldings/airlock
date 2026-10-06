/* Interactive credit-card visual for the Lifted Payments terminal.
   Adapted from quinlo's "Credit Card Payment Form" (CodePen YONMEa) — an SVG card
   that mirrors the typed number/name/expiry/CVV, swaps the brand logo + colour as
   you type, and flips to the back for the security code.

   Lifted adaptations:
   - default (no brand detected) uses our teal palette, not grey
   - exposed as window.LiftedCard.{html, init, setBrandByNumber, reset}
   - sandbox = full live mirroring; live (Collect.js) = name + flip only, since the
     PAN lives in NMI's iframe and must never touch our JS (PCI SAQ-A). */
(function () {
  "use strict";

  var FRONT = `<svg version="1.1" id="cardfront" xmlns="http://www.w3.org/2000/svg" x="0px" y="0px" viewBox="0 0 750 471" xml:space="preserve">
    <g id="Front">
      <g id="CardBackground">
        <g><g><path class="lightcolor lifted" d="M40,0h670c22.1,0,40,17.9,40,40v391c0,22.1-17.9,40-40,40H40c-22.1,0-40-17.9-40-40V40C0,17.9,17.9,0,40,0z"/></g></g>
        <path class="darkcolor lifteddark" d="M750,431V193.2c-217.6-57.5-556.4-13.5-750,24.9V431c0,22.1,17.9,40,40,40h670C732.1,471,750,453.1,750,431z"/>
      </g>
      <text transform="matrix(1 0 0 1 60.106 295.0121)" id="svgnumber" class="st2 st3 st4">•••• •••• •••• ••••</text>
      <text transform="matrix(1 0 0 1 54.1064 428.1723)" id="svgname" class="st2 st5 st6">FULL NAME</text>
      <text transform="matrix(1 0 0 1 54.1074 389.8793)" class="st7 st5 st8">cardholder name</text>
      <text transform="matrix(1 0 0 1 479.7754 388.8793)" class="st7 st5 st8">expiration</text>
      <text transform="matrix(1 0 0 1 65.1054 241.5)" class="st7 st5 st8">card number</text>
      <g>
        <text transform="matrix(1 0 0 1 574.4219 433.8095)" id="svgexpire" class="st2 st5 st9">••/••</text>
        <text transform="matrix(1 0 0 1 479.3848 417.0097)" class="st2 st10 st11">VALID</text>
        <text transform="matrix(1 0 0 1 479.3848 435.6762)" class="st2 st10 st11">THRU</text>
        <polygon class="st2" points="554.5,421 540.4,414.2 540.4,427.9 "/>
      </g>
      <g id="cchip">
        <path class="st2" d="M168.1,143.6H82.9c-10.2,0-18.5-8.3-18.5-18.5V74.9c0-10.2,8.3-18.5,18.5-18.5h85.3c10.2,0,18.5,8.3,18.5,18.5v50.2C186.6,135.3,178.3,143.6,168.1,143.6z"/>
        <g>
          <rect x="82" y="70" class="st12" width="1.5" height="60"/>
          <rect x="167.4" y="70" class="st12" width="1.5" height="60"/>
          <path class="st12" d="M125.5,130.8c-10.2,0-18.5-8.3-18.5-18.5c0-4.6,1.7-8.9,4.7-12.3c-3-3.4-4.7-7.7-4.7-12.3c0-10.2,8.3-18.5,18.5-18.5s18.5,8.3,18.5,18.5c0,4.6-1.7,8.9-4.7,12.3c3,3.4,4.7,7.7,4.7,12.3C143.9,122.5,135.7,130.8,125.5,130.8z M125.5,70.8c-9.3,0-16.9,7.6-16.9,16.9c0,4.4,1.7,8.6,4.8,11.8l0.5,0.5l-0.5,0.5c-3.1,3.2-4.8,7.4-4.8,11.8c0,9.3,7.6,16.9,16.9,16.9s16.9-7.6,16.9-16.9c0-4.4-1.7-8.6-4.8-11.8l-0.5-0.5l0.5-0.5c3.1-3.2,4.8-7.4,4.8-11.8C142.4,78.4,134.8,70.8,125.5,70.8z"/>
          <rect x="82.8" y="82.1" class="st12" width="25.8" height="1.5"/>
          <rect x="82.8" y="117.9" class="st12" width="26.1" height="1.5"/>
          <rect x="142.4" y="82.1" class="st12" width="25.8" height="1.5"/>
          <rect x="142" y="117.9" class="st12" width="26.2" height="1.5"/>
        </g>
      </g>
    </g>
  </svg>`;

  var BACK = `<svg version="1.1" id="cardback" xmlns="http://www.w3.org/2000/svg" x="0px" y="0px" viewBox="0 0 750 471" xml:space="preserve">
    <g id="Back">
      <g><g><path class="darkcolor lifteddark" d="M40,0h670c22.1,0,40,17.9,40,40v391c0,22.1-17.9,40-40,40H40c-22.1,0-40-17.9-40-40V40C0,17.9,17.9,0,40,0z"/></g></g>
      <rect y="61.6" class="st2" width="750" height="78"/>
      <g>
        <path class="st3" d="M701.1,249.1H48.9c-3.3,0-6-2.7-6-6v-52.5c0-3.3,2.7-6,6-6h652.1c3.3,0,6,2.7,6,6v52.5C707.1,246.4,704.4,249.1,701.1,249.1z"/>
        <rect x="42.9" y="198.6" class="st4" width="664.1" height="10.5"/>
        <rect x="42.9" y="224.5" class="st4" width="664.1" height="10.5"/>
        <path class="st5" d="M701.1,184.6H618h-8h-10v64.5h10h8h83.1c3.3,0,6-2.7,6-6v-52.5C707.1,187.3,704.4,184.6,701.1,184.6z"/>
      </g>
      <text transform="matrix(1 0 0 1 621.999 227.2734)" id="svgsecurity" class="st6 st7">•••</text>
      <g class="st8"><text transform="matrix(1 0 0 1 518.083 280.0879)" class="st9 st6 st10">security code</text></g>
      <rect x="58.1" y="378.6" class="st11" width="375.5" height="13.5"/>
      <rect x="58.1" y="405.6" class="st11" width="421.7" height="13.5"/>
      <text transform="matrix(1 0 0 1 59.5073 228.6099)" id="svgnameback" class="st12 st13">Full Name</text>
    </g>
  </svg>`;

  // Brand logos for the card face (#ccsingle). Trimmed to the common networks.
  var SINGLES = {
    visa: `<svg viewBox="0 0 750 471" xmlns="http://www.w3.org/2000/svg"><g fill="#FFF"><polygon points="278.198,334.228 311.558,138.465 364.916,138.465 331.534,334.228"/><path d="M524.307,142.687c-10.57-3.966-27.135-8.222-47.822-8.222c-52.725,0-89.863,26.551-90.18,64.604c-0.297,28.129,26.514,43.821,46.754,53.185c20.77,9.597,27.752,15.716,27.652,24.283c-0.133,13.123-16.586,19.116-31.924,19.116c-21.355,0-32.701-2.967-50.225-10.274l-6.877-3.112l-7.488,43.823c12.463,5.466,35.508,10.199,59.438,10.445c56.09,0,92.502-26.248,92.916-66.884c0.199-22.27-14.016-39.216-44.801-53.188c-18.65-9.056-30.072-15.099-29.951-24.269c0-8.137,9.668-16.838,30.559-16.838c17.447-0.271,30.088,3.534,39.936,7.5l4.781,2.259L524.307,142.687"/><path d="M661.615,138.464h-41.23c-12.773,0-22.332,3.486-27.941,16.234l-79.244,179.402h56.031c0,0,9.16-24.121,11.232-29.418c6.123,0,60.555,0.084,68.336,0.084c1.596,6.854,6.492,29.334,6.492,29.334h49.512L661.615,138.464z M596.198,264.872c4.414-11.279,21.26-54.724,21.26-54.724c-0.314,0.521,4.381-11.334,7.074-18.684l3.607,16.878c0,0,10.217,46.729,12.352,56.527h-44.293z"/><path d="M232.903,138.464l-52.24,133.496l-5.565-27.129c-9.726-31.274-40.025-65.157-73.898-82.12l47.767,171.204l56.455-0.064l84.004-195.386L232.903,138.464"/></g><path d="M131.92,138.464H45.879l-0.682,4.073c66.939,16.204,111.232,55.363,129.618,102.415l-18.709-89.96C152.877,142.596,143.509,138.896,131.92,138.464" fill="#F2AE14"/></svg>`,
    mastercard: `<svg viewBox="0 0 482.51 374" xmlns="http://www.w3.org/2000/svg"><rect x="169.81" y="31.89" width="143.72" height="234.42" fill="#ff5f00"/><path d="M317.05,197.6A149.5,149.5,0,0,1,373.79,80.39a149.1,149.1,0,1,0,0,234.42A149.5,149.5,0,0,1,317.05,197.6Z" transform="translate(-132.74 -48.5)" fill="#eb001b"/><path d="M615.26,197.6a148.95,148.95,0,0,1-241,117.21,149.43,149.43,0,0,0,0-234.42,148.95,148.95,0,0,1,241,117.21Z" transform="translate(-132.74 -48.5)" fill="#f79e1b"/></svg>`,
    amex: `<svg viewBox="0 0 750 471" xmlns="http://www.w3.org/2000/svg"><rect width="750" height="471" rx="40" fill="#2557D6"/><path fill="#FFF" d="M554.594,130.608l-14.521,35.039h29.121L554.594,130.608z M387.03,152.321c2.738-1.422,4.349-4.515,4.349-8.356c0-3.764-1.693-6.49-4.431-7.771c-2.492-1.42-6.328-1.584-10.006-1.584h-25.978v19.523h25.63C380.7,154.134,384.131,154.074,387.03,152.321z M54.142,130.608l-14.357,35.039h28.8L54.142,130.608z M722.565,355.08h-40.742v-18.852h40.578c4.023,0,6.84-0.525,8.537-2.177c1.471-1.358,2.494-3.336,2.494-5.733c0-2.562-1.023-4.596-2.578-5.813c-1.529-1.342-3.76-1.953-7.434-1.953c-19.81-0.67-44.523,0.609-44.523-27.211c0-12.75,8.131-26.172,30.27-26.172h42.025v-17.492h-39.045c-11.783,0-20.344,2.81-26.406,7.181v-7.181h-57.752c-9.233,0-20.074,2.279-25.201,7.181v-7.181H499.655v7.181c-8.207-5.898-22.057-7.181-28.447-7.181H403.18v7.181c-6.492-6.262-20.935-7.181-29.734-7.181h-76.134l-17.42,18.775l-16.318-18.775H149.847v122.675h111.586l17.95-19.076l16.91,19.076l68.78,0.059v-28.859h6.764c9.125,0.145,19.889-0.223,29.387-4.311v33.107h56.731v-31.976h2.736c3.492,0,3.838,0.146,3.838,3.621v28.348h172.344c10.941,0,22.38-2.786,28.712-7.853v7.853h54.668c11.375,0,22.485-1.588,30.938-5.653L722.565,355.08z M372.734,326.113h-26.325v29.488h-41.006L279.425,326.5l-26.997,29.102h-83.569v-87.914h84.855l25.955,28.818l26.835-28.818h67.414c16.743,0,35.555,4.617,35.555,28.963C409.473,321.072,391.176,326.113,372.734,326.113z M651.906,304.764c4.105,4.232,6.311,9.578,6.311,18.625c0,18.914-11.866,27.743-33.143,27.743h-41.09v-18.852h40.926c4.002,0,6.84-0.527,8.619-2.178c1.449-1.359,2.492-3.336,2.492-5.73c0-2.564-1.129-4.598-2.574-5.818c-1.615-1.34-3.842-1.948-7.514-1.948c-19.73-0.673-44.439,0.606-44.439-27.212c0-12.752,8.047-26.174,30.164-26.174h42.297v18.709h-38.703c-3.836,0-6.33,0.146-8.451,1.592c-2.313,1.423-3.17,3.535-3.17,6.322c0,3.316,1.963,5.574,4.615,6.549c2.228,0.771,4.617,0.996,8.211,0.996l11.359,0.308C639.264,297.969,647.127,299.945,651.906,304.764z M512.971,287.066c-2.549-1.508-6.311-1.588-10.066-1.588h-25.979v19.744h25.631c4.104,0,7.594-0.144,10.414-1.812c2.734-1.646,4.371-4.678,4.371-8.438C517.342,291.213,515.705,288.49,512.971,287.066z"/></svg>`,
    discover: `<svg viewBox="0 0 780 501" xmlns="http://www.w3.org/2000/svg"><path d="M409.412,197.758c30.938,0,56.02,23.58,56.02,52.709c0,29.129-25.082,52.742-56.02,52.742c-30.941,0-56.022-23.613-56.022-52.742C353.39,221.338,378.471,197.758,409.412,197.758z" fill="#F47216"/><path fill="#FFF" d="M321.433,198.438c8.836,0,16.247,1.785,25.269,6.09v22.752c-8.544-7.863-15.955-11.154-25.757-11.154c-19.265,0-34.413,15.015-34.413,34.051c0,20.074,14.681,34.195,35.368,34.195c9.313,0,16.586-3.12,24.802-10.856v22.764c-9.343,4.141-16.912,5.775-25.757,5.775c-31.277,0-55.581-22.597-55.581-51.737C265.363,221.49,290.314,198.438,321.433,198.438z M224.32,199.064c11.546,0,22.109,3.721,30.942,10.994l-10.748,13.248c-5.351-5.646-10.411-8.027-16.563-8.027c-8.854,0-15.301,4.745-15.301,10.988c0,5.354,3.618,8.188,15.944,12.482c23.364,8.043,30.289,15.176,30.289,30.926c0,19.193-14.976,32.554-36.319,32.554c-15.631,0-26.993-5.795-36.457-18.871l13.268-12.031c4.73,8.609,12.622,13.223,22.42,13.223c9.163,0,15.947-5.951,15.947-13.984c0-4.164-2.056-7.733-6.158-10.258c-2.066-1.195-6.158-2.977-14.199-5.646c-19.292-6.538-25.91-13.527-25.91-27.186C191.474,211.25,205.688,199.064,224.32,199.064z"/><polygon fill="#FFF" points="459.043,200.793 481.479,200.793 509.563,267.385 538.01,200.793 560.276,200.793 514.783,302.479 503.729,302.479"/><polygon fill="#FFF" points="157.83,200.945 178.371,200.945 178.371,300.088 157.83,300.088"/><path fill="#FFF" d="M91.845,200.945H61.696v99.143h29.992c15.946,0,27.465-3.543,37.573-11.445c12.014-9.36,19.117-23.467,19.117-38.057C148.379,221.327,125.157,200.945,91.845,200.945z M115.842,275.424c-6.454,5.484-14.837,7.879-28.108,7.879H82.22v-65.559h5.513c13.271,0,21.323,2.238,28.108,8.018c7.104,5.956,11.377,15.183,11.377,24.682C127.219,259.957,122.945,269.468,115.842,275.424z"/></svg>`,
  };

  // brand → colour swap key (matches the CSS palette classes)
  var COLORS = { visa: "lime", mastercard: "lightblue", amex: "green", discover: "purple",
    diners: "orange", jcb: "red", maestro: "yellow", unionpay: "cyan" };

  // first-digit / prefix detection (subset of the IMask regexes from the pen)
  function detect(num) {
    var n = (num || "").replace(/\D/g, "");
    if (/^3[47]/.test(n)) return "amex";
    if (/^(6011|65|64[4-9])/.test(n)) return "discover";
    if (/^(5[1-5]|22[2-9]|2[3-7])/.test(n)) return "mastercard";
    if (/^4/.test(n)) return "visa";
    if (/^3(0[0-5]|[68])/.test(n)) return "diners";
    if (/^35/.test(n)) return "jcb";
    if (/^62/.test(n)) return "unionpay";
    return "";
  }

  // every visual operates within a ROOT element so multiple cards (terminal +
  // subscription form) on the same page don't collide on shared ids.
  var _activeRoot = document;   // root of the currently-active LIVE card (for Collect.js brand)

  function $(s, r) { return (r || document).querySelector(s); }

  function swapColor(base, root) {
    (root || document).querySelectorAll(".creditcard .lightcolor").forEach(function (el) {
      el.setAttribute("class", "lightcolor " + base);
    });
    (root || document).querySelectorAll(".creditcard .darkcolor").forEach(function (el) {
      el.setAttribute("class", "darkcolor " + base + "dark");
    });
  }

  function setBrand(key, root) {
    var single = $("#ccsingle", root);
    if (single) single.innerHTML = (key && SINGLES[key]) || "";
    swapColor((key && COLORS[key]) || "lifted", root);
  }

  // group a card number for display (amex 4-6-5, else 4-4-4-4)
  function group(num, brand) {
    var n = (num || "").replace(/\D/g, "");
    var parts = brand === "amex" ? [4, 6, 5] : [4, 4, 4, 4];
    var out = [], i = 0;
    parts.forEach(function (len) { if (i < n.length) { out.push(n.substr(i, len)); i += len; } });
    return out.join(" ");
  }

  function fullLen(brand) { return brand === "amex" ? 15 : (brand === "diners" ? 14 : 16); }

  // what shows ON THE CARD: live digits while typing; once complete, mask the
  // middle group(s) and keep the first 4 + last 4 (per Will's request)
  function displayNumber(digits, brand) {
    if (!digits) return "•••• •••• •••• ••••";
    if (digits.length >= fullLen(brand)) {
      if (brand === "amex") return digits.slice(0, 4) + " •••••• " + digits.slice(10);
      if (brand === "diners") return digits.slice(0, 4) + " •••••• " + digits.slice(10);
      return digits.slice(0, 4) + " •••• •••• " + digits.slice(12);
    }
    return group(digits, brand);
  }

  function html() {
    return `<div class="cc-stage preload"><div class="creditcard" id="lp-creditcard">
      <div class="front"><div id="ccsingle"></div>${FRONT}</div>
      <div class="back">${BACK}</div></div></div>`;
  }

  function flip(on, root) {
    var c = $("#lp-creditcard", root);
    if (c) c.classList.toggle("flipped", on);
  }

  function reset(root) {
    var n = $("#svgnumber", root), nm = $("#svgname", root), nb = $("#svgnameback", root),
        e = $("#svgexpire", root), s = $("#svgsecurity", root);
    if (n) n.textContent = "•••• •••• •••• ••••";
    if (nm) nm.textContent = "FULL NAME";
    if (nb) nb.textContent = "Full Name";
    if (e) e.textContent = "••/••";
    if (s) s.textContent = "•••";
    setBrand("", root);
  }

  // sandbox: full live mirroring from raw inputs
  function initSandbox(els, root) {
    var card = $("#lp-creditcard", root);
    if (card) card.onclick = function () { card.classList.toggle("flipped"); };
    reset(root);
    if (els.number) els.number.addEventListener("input", function () {
      var brand = detect(this.value);
      setBrand(brand, root);
      var digits = this.value.replace(/\D/g, "").slice(0, fullLen(brand));
      this.value = group(digits, brand);       // input keeps the real grouped digits
      var el = $("#svgnumber", root); if (el) el.textContent = displayNumber(digits, brand);
    });
    if (els.exp) els.exp.addEventListener("input", function () {
      var d = this.value.replace(/\D/g, "").slice(0, 4);
      if (d.length >= 3) d = d.slice(0, 2) + "/" + d.slice(2);
      this.value = d;
      var el = $("#svgexpire", root); if (el) el.textContent = d || "••/••";
    });
    if (els.cvv) els.cvv.addEventListener("input", function () {
      this.value = this.value.replace(/\D/g, "").slice(0, 4);
      var el = $("#svgsecurity", root); if (el) el.textContent = this.value || "•••";
    });
    bindName(els.name, root);
    bindFocusFlip(els, root);
  }

  // live (Collect.js): name + flip only; number/exp/cvv stay masked (PAN in iframe)
  function initLive(els, root) {
    var card = $("#lp-creditcard", root);
    if (card) card.onclick = function () { card.classList.toggle("flipped"); };
    reset(root);
    bindName(els.name, root);
  }

  function bindName(input, root) {
    if (!input) return;
    input.addEventListener("input", function () {
      var v = this.value.trim();
      var a = $("#svgname", root), b = $("#svgnameback", root);
      if (a) a.textContent = v || "FULL NAME";
      if (b) b.textContent = v || "Full Name";
    });
  }

  function bindFocusFlip(els, root) {
    ["number", "exp", "name"].forEach(function (k) {
      if (els[k]) els[k].addEventListener("focus", function () { flip(false, root); });
    });
    if (els.cvv) els.cvv.addEventListener("focus", function () { flip(true, root); });
  }

  // resolve a root: explicit element, or the card stage containing the given els
  function resolveRoot(opts) {
    if (opts.root && opts.root.querySelector) return opts.root;
    // climb from a known field to its enclosing .cc-stage's parent container
    var ref = (opts.els && (opts.els.number || opts.els.name));
    if (ref) {
      var p = ref.closest ? ref.closest("[id^='src-card'], [id^='s-src-card'], #tab-terminal, #tab-subscriptions") : null;
      if (p) return p;
    }
    return document;
  }

  function init(opts) {
    opts = opts || {};
    var root = resolveRoot(opts);
    setTimeout(function () {
      var s = (root.querySelector ? root : document).querySelector(".cc-stage.preload");
      if (s) s.classList.remove("preload");
    }, 50);
    if (opts.live) { _activeRoot = root; initLive(opts.els || {}, root); }
    else initSandbox(opts.els || {}, root);
  }

  // map a Collect.js BIN-lookup card type → our brand key
  function brandFromType(t) {
    t = (t || "").toLowerCase().replace(/[^a-z]/g, "");
    return ({visa: "visa", mastercard: "mastercard", amex: "amex", americanexpress: "amex",
             discover: "discover", diners: "diners", dinersclub: "diners", jcb: "jcb",
             maestro: "maestro", unionpay: "unionpay"})[t] || "";
  }

  window.LiftedCard = { html: html, init: init,
    flip: function (on) { flip(on, _activeRoot); }, reset: function () { reset(_activeRoot); },
    setBrandByNumber: function (n) { setBrand(detect(n), _activeRoot); },
    setBrandByType: function (t) { setBrand(brandFromType(t), _activeRoot); } };

  /* ===================== eCheck (ACH) visual ===================== */
  function setText(sel, txt, root) { var el = $(sel, root); if (el) el.textContent = txt; }

  function fmtAmount(v) {
    var n = parseFloat(String(v || "").replace(/[^0-9.]/g, ""));
    if (!n || n < 0) return "0.00";
    return n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  function today() {
    try {
      return new Date().toLocaleDateString("en-US", { year: "numeric", month: "short", day: "numeric" });
    } catch (e) { return ""; }
  }

  function ecEsc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function checkHtml(payee) {
    return `<div class="echeck" id="lp-echeck">
      <div class="ec-watermark">LIFTED&nbsp;PAYMENTS</div>
      <div class="ec-top">
        <div class="ec-payer">
          <div class="ec-name" id="chk-name">ACCOUNT HOLDER</div>
          <div class="ec-sub" id="chk-acctsub">Checking account</div>
        </div>
        <div class="ec-meta"><div class="ec-date" id="chk-date"></div></div>
      </div>
      <div class="ec-payline">
        <span class="ec-pay-l">Pay to the order of</span>
        <span class="ec-payee">${ecEsc(payee || "Lifted Payments")}</span>
        <span class="ec-amtbox"><span class="ec-cur">$</span><span id="chk-amount">0.00</span></span>
      </div>
      <div class="ec-bottom">
        <div class="ec-sign"><span class="ec-sig" id="chk-signature">Account Holder</span>
          <div class="ec-sigline">Authorized signature</div></div>
      </div>
      <div class="ec-micr"><span id="chk-micr">⑆ •••••••• ⑆ •••••••••• ⑈</span></div>
    </div>`;
  }

  function micr(els, root) {
    var r = ((els.routing && els.routing.value) || "").replace(/\D/g, "");
    var a = ((els.account && els.account.value) || "").replace(/\D/g, "");
    setText("#chk-micr", "⑆ " + (r || "••••••••") + " ⑆ " + (a || "••••••••••") + " ⑈", root);
  }

  // resolve the check's container from the holder-name field (scopes shared ids)
  function checkRoot(opts) {
    if (opts.root && opts.root.querySelector) return opts.root;
    var ref = opts.els && (opts.els.name || opts.els.routing);
    if (ref && ref.closest) {
      var p = ref.closest("#s-src-ach, #src-ach, #tab-subscriptions, #tab-terminal");
      if (p) return p;
    }
    return document;
  }

  function initCheck(opts) {
    opts = opts || {};
    var els = opts.els || {};
    var root = checkRoot(opts);
    setText("#chk-date", today(), root);
    if (els.name) els.name.addEventListener("input", function () {
      setText("#chk-name", (this.value || "ACCOUNT HOLDER").toUpperCase(), root);
      setText("#chk-signature", this.value || "Account Holder", root);
    });
    if (els.amount) els.amount.addEventListener("input", function () {
      setText("#chk-amount", fmtAmount(this.value), root);
    });
    if (els.routing) els.routing.addEventListener("input", function () {
      this.value = this.value.replace(/\D/g, "").slice(0, 9); micr(els, root);
    });
    if (els.account) els.account.addEventListener("input", function () {
      this.value = this.value.replace(/\D/g, "").slice(0, 17); micr(els, root);
    });
    if (els.type) els.type.addEventListener("change", function () {
      setText("#chk-acctsub", (this.value === "savings" ? "Savings" : "Checking") + " account", root);
    });
  }

  window.LiftedCheck = { html: checkHtml, init: initCheck };
})();
