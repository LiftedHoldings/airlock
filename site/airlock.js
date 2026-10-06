/* Airlock report — page behaviour. No dependencies except the ElevenLabs
   browser SDK, which is loaded on demand when a call starts. */

// two deployments: "desk" (recommended) hands off to a scripted payment desk; "full" routes every turn
const AGENTS = { desk: "agent_5901m48tr9vwf9xb6v6zbk8hs7j4", full: "agent_5401m48pws3xe9pa193x3th09dmr" };
let MODE = "desk";
const SDK_URL = "https://cdn.jsdelivr.net/npm/@elevenlabs/client@latest/+esm";
const EVENTS_BASE = "/airlock-llm/events/";

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
const esc = (v) => String(v == null ? "" : v).replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const reduceMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const fmtTime = (s) => { s = Math.max(0, Math.floor(s || 0)); return Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0"); };

/* ------------------------------------------------------------------ topbar */
{
  const bar = $("#topbar");
  const on = () => bar.classList.toggle("scrolled", window.scrollY > 8);
  window.addEventListener("scroll", on, { passive: true }); on();
}

/* --------------------------------------------------------------- narration */
(async function narration() {
  const audio = $("#np-audio"), btn = $("#np-play"), seek = $("#np-seek"),
    time = $("#np-time"), box = $("#np-words"),
    iPlay = $("#np-icon-play"), iPause = $("#np-icon-pause");
  let words = [], spans = [], cur = -1, raf = 0;

  try {
    const r = await fetch("./audio/summary.words.json");
    if (!r.ok) throw new Error("HTTP " + r.status);
    words = (await r.json()).words || [];
  } catch (e) {
    box.textContent = "Transcript unavailable.";
  }
  const frag = document.createDocumentFragment();
  words.forEach((w, i) => {
    const s = document.createElement("span");
    s.className = "w"; s.textContent = w.w; s.dataset.i = i;
    frag.appendChild(s); frag.appendChild(document.createTextNode(" "));
    spans.push(s);
  });
  box.appendChild(frag);
  box.addEventListener("click", (e) => {
    const s = e.target.closest(".w"); if (!s) return;
    audio.currentTime = words[+s.dataset.i].s + 0.001;
    if (audio.paused) audio.play().catch(() => {});
    render();
  });

  const dur = () => (isFinite(audio.duration) && audio.duration > 0 ? audio.duration : (words.length ? words[words.length - 1].e : 0)) || 1;
  function findWord(t) {
    let lo = 0, hi = words.length - 1, ans = -1;
    while (lo <= hi) { const m = (lo + hi) >> 1; if (words[m].s <= t) { ans = m; lo = m + 1; } else hi = m - 1; }
    return ans;
  }
  function render() {
    const t = audio.currentTime;
    const i = (audio.paused && t === 0) ? -1 : findWord(t);
    if (i !== cur) {
      const lo = Math.min(i, cur), hi = Math.max(i, cur);
      for (let k = Math.max(0, lo); k <= hi && k < spans.length; k++) {
        spans[k].classList.toggle("done", k < i);
        spans[k].classList.toggle("now", k === i);
      }
      if (cur > i) spans.forEach((s, k) => { s.classList.toggle("done", k < i); s.classList.toggle("now", k === i); });
      cur = i;
      const s = spans[i];
      if (s && !audio.paused) {
        const top = s.offsetTop - box.offsetTop;
        if (top < box.scrollTop || top > box.scrollTop + box.clientHeight - 30)
          box.scrollTo({ top: top - 8, behavior: reduceMotion() ? "auto" : "smooth" });
      }
    }
    seek.max = dur().toFixed(1);
    seek.value = t;
    seek.style.setProperty("--p", (t / dur()) * 100 + "%");
    time.textContent = fmtTime(t) + (dur() > 1 ? " / " + fmtTime(dur()) : "");
  }
  function loop() { render(); if (!audio.paused) raf = requestAnimationFrame(loop); }
  function setIcon() {
    const playing = !audio.paused;
    iPlay.hidden = playing; iPause.hidden = !playing;
    btn.setAttribute("aria-label", playing ? "Pause narrated summary" : "Play narrated summary");
  }
  btn.addEventListener("click", () => { audio.paused ? audio.play().catch(() => {}) : audio.pause(); });
  audio.addEventListener("play", () => { setIcon(); cancelAnimationFrame(raf); loop(); });
  audio.addEventListener("pause", () => { setIcon(); render(); });
  audio.addEventListener("ended", () => { setIcon(); render(); });
  audio.addEventListener("loadedmetadata", render);
  audio.addEventListener("seeked", render);
  seek.addEventListener("input", () => { audio.currentTime = +seek.value; render(); });
  render();
})();

/* ------------------------------------------------------------------ keypad */
const keypad = (() => {
  const disp = $("#kp-digits");
  let buf = "";
  const PH = "Keypad — press # to send";
  function show() {
    if (!buf) { disp.textContent = PH; disp.classList.add("ph"); }
    else { disp.textContent = "‎" + buf; disp.classList.remove("ph"); }
  }
  function press(k) {
    const el = $(`.key[data-k="${CSS.escape(k)}"]`);
    if (el) { el.classList.add("press"); setTimeout(() => el.classList.remove("press"), 120); }
    if (k === "#") { const s = buf + "#"; if (call.sendKeypad(s)) { buf = ""; show(); } return; }
    if (buf.length >= 40) return;
    buf += k; show();
  }
  function back() { buf = buf.slice(0, -1); show(); }
  function type(str) {
    buf = String(str).replace(/[^0-9*]/g, ""); show();
    disp.parentElement.animate?.([{ boxShadow: "0 0 0 3px rgba(94,234,212,.45)" }, { boxShadow: "0 0 0 0 rgba(94,234,212,0)" }], { duration: 700 });
  }
  $("#keypad").addEventListener("click", (e) => { const b = e.target.closest(".key"); if (b) press(b.dataset.k); });
  $("#kp-back").addEventListener("click", back);
  // physical keyboard while focus is inside the keypad area
  $(".kp-wrap").addEventListener("keydown", (e) => {
    if (e.target.tagName === "INPUT") return;
    if (/^[0-9*#]$/.test(e.key)) { e.preventDefault(); press(e.key); }
    else if (e.key === "Backspace") { e.preventDefault(); back(); }
  });
  show();
  return { type, press, clear: () => { buf = ""; show(); }, get value() { return buf; } };
})();

/* ------------------------------------------------------- test instruments */
const TEST = {
  visa: { label: "Visa", number: "4111111111111111", exp: "1229", cvv: "999", zip: "77777", note: "Approves (except order A0050, the decline test)." },
  mastercard: { label: "Mastercard", number: "5424000000000015", exp: "1229", cvv: "999", zip: "77777", note: "" },
  amex: { label: "Amex", number: "370000000000002", exp: "1229", cvv: "1234", zip: "77777", note: "The 4-digit code is on the front." },
};
const ECHECK = { name: "Test Payer", routing: "123123123", account: "123123123" };
function groupNum(n) {
  return n.length === 15 ? [n.slice(0, 4), n.slice(4, 10), n.slice(10)].join(" ") : n.replace(/(\d{4})(?=\d)/g, "$1 ");
}

(function instruments() {
  const cardRoot = $("#card-root"), checkRoot = $("#check-root"), info = $("#inst-info"),
    typeit = $("#typeit"), hint = $("#flip-hint");
  let cardReady = false;

  function ensureCard() {
    if (cardReady || !window.LiftedCard) return;
    cardRoot.innerHTML = window.LiftedCard.html();
    window.LiftedCard.init({ root: cardRoot, els: {} });
    const stage = $(".cc-stage", cardRoot), card = $("#lp-creditcard", cardRoot);
    stage.tabIndex = 0;
    stage.setAttribute("role", "button");
    stage.setAttribute("aria-label", "Test card. Press Enter to flip.");
    stage.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); card.classList.toggle("flipped"); }
    });
    cardReady = true;
  }
  function setCard(key) {
    ensureCard();
    const c = TEST[key];
    if (!cardReady) { cardRoot.textContent = "Card visual unavailable."; return; }
    $("#lp-creditcard", cardRoot).classList.remove("flipped");
    window.LiftedCard.setBrandByNumber(c.number);
    $("#svgnumber", cardRoot).textContent = groupNum(c.number);
    $("#svgname", cardRoot).textContent = "TEST CARDHOLDER";
    $("#svgnameback", cardRoot).textContent = "Test Cardholder";
    $("#svgexpire", cardRoot).textContent = c.exp.slice(0, 2) + "/" + c.exp.slice(2);
    // Amex keeps its 4-digit code on the front; others on the back
    const front = $("#cardfront", cardRoot);
    $$(".amexcid, .amexcid-l", front).forEach((n) => n.remove());
    if (key === "amex") {
      $("#svgsecurity", cardRoot).textContent = "—";
      const NS = "http://www.w3.org/2000/svg";
      const t = document.createElementNS(NS, "text");
      t.setAttribute("class", "amexcid"); t.setAttribute("x", "690"); t.setAttribute("y", "212"); t.setAttribute("text-anchor", "end");
      t.textContent = c.cvv;
      const l = document.createElementNS(NS, "text");
      l.setAttribute("class", "amexcid-l"); l.setAttribute("x", "690"); l.setAttribute("y", "180"); l.setAttribute("text-anchor", "end");
      l.textContent = "CID";
      front.appendChild(l); front.appendChild(t);
    } else {
      $("#svgsecurity", cardRoot).textContent = c.cvv;
    }
    info.innerHTML = `<p><span class="mono">${esc(groupNum(c.number))}</span> · exp <span class="mono">${c.exp.slice(0, 2)}/${c.exp.slice(2)}</span> · ${key === "amex" ? "CID" : "CVV"} <span class="mono">${esc(c.cvv)}</span>${key === "visa" ? ` · ZIP <span class="mono">${c.zip}</span>` : ""}</p>${c.note ? `<p>${esc(c.note)}</p>` : ""}<p>Decline test: pay order <span class="mono">A0050</span> ($0.50) with any card — NMI's sandbox declines amounts under $1.00.</p>`;
    typeit.innerHTML = `<span class="lbl">Type it for me — then press #</span>` +
      [["Card number", c.number], ["Expiry", c.exp], [key === "amex" ? "CID" : "CVV", c.cvv], ["ZIP", c.zip]]
        .map(([l, v]) => `<button class="btn sm" type="button" data-v="${esc(v)}">${esc(l)}</button>`).join("");
  }
  function setCheck() {
    if (!checkRoot.dataset.ready && window.LiftedCheck) {
      checkRoot.innerHTML = window.LiftedCheck.html("Lifted Coffee Roasters");
      window.LiftedCheck.init({ root: checkRoot, els: {} });
      $("#chk-name", checkRoot).textContent = ECHECK.name.toUpperCase();
      $("#chk-signature", checkRoot).textContent = ECHECK.name;
      $("#chk-amount", checkRoot).textContent = "12.00";
      $("#chk-acctsub", checkRoot).textContent = "Checking account";
      $("#chk-micr", checkRoot).textContent = `⑆ ${ECHECK.routing} ⑆ ${ECHECK.account} ⑈`;
      checkRoot.dataset.ready = "1";
    }
    info.innerHTML = `<p>Name <span class="mono">${ECHECK.name}</span> · routing <span class="mono">${ECHECK.routing}</span> · account <span class="mono">${ECHECK.account}</span> · checking</p><p>Ask the agent “pay order A2001 from my bank account”. It asks for the name on the account, then you key the routing number and #, the account number and #, then <span class="mono">1</span> for checking.</p>`;
    typeit.innerHTML = `<span class="lbl">Type it for me — then press #</span>` +
      [["Routing", ECHECK.routing], ["Account", ECHECK.account], ["Checking (1)", "1"]]
        .map(([l, v]) => `<button class="btn sm" type="button" data-v="${esc(v)}">${esc(l)}</button>`).join("") +
      `<button class="btn sm" type="button" data-say="${esc(ECHECK.name)}">Say “${esc(ECHECK.name)}”</button>`;
  }
  function select(key) {
    $$(".inst-tab").forEach((t) => t.setAttribute("aria-selected", String(t.dataset.inst === key)));
    const isCheck = key === "echeck";
    cardRoot.hidden = isCheck; hint.hidden = isCheck; checkRoot.hidden = !isCheck;
    isCheck ? setCheck() : setCard(key);
  }
  $(".inst-tabs").addEventListener("click", (e) => { const t = e.target.closest(".inst-tab"); if (t) select(t.dataset.inst); });
  $(".inst-tabs").addEventListener("keydown", (e) => {
    if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
    const tabs = $$(".inst-tab"), i = tabs.findIndex((t) => t.getAttribute("aria-selected") === "true");
    const n = tabs[(i + (e.key === "ArrowRight" ? 1 : tabs.length - 1)) % tabs.length];
    n.focus(); select(n.dataset.inst);
  });
  typeit.addEventListener("click", (e) => {
    const b = e.target.closest("button"); if (!b) return;
    if (b.dataset.v) keypad.type(b.dataset.v);
    else if (b.dataset.say) call.say(b.dataset.say);
  });
  select("visa");
})();

/* ============================================================ live data flow
   A small rendering engine for the hero visualisation. One requestAnimationFrame
   loop drives every packet, trail and effect from a pausable clock, so replay can
   pause mid-flight and scrubbing can cancel everything cleanly. Only SVG transform,
   opacity and dash-offset attributes change per frame. */
const C = { speech: "#8ab4ff", card: "#ffb454", token: "#5eead4", ok: "#4ade80", bad: "#f87171", grey: "#8b93a0" };

const viz = (() => {
  const svg = $("#wf"), NS = "http://www.w3.org/2000/svg";
  const ICONS = {
    keypad: '<circle cx="6" cy="5" r="1.4"/><circle cx="12" cy="5" r="1.4"/><circle cx="18" cy="5" r="1.4"/><circle cx="6" cy="11" r="1.4"/><circle cx="12" cy="11" r="1.4"/><circle cx="18" cy="11" r="1.4"/><circle cx="6" cy="17" r="1.4"/><circle cx="12" cy="17" r="1.4"/><circle cx="18" cy="17" r="1.4"/><circle cx="12" cy="22" r="1.4"/>',
    wave: '<path d="M4 11v2M8 8v8M12 4v16M16 8v8M20 10v4"/>',
    shield: '<path d="M12 3l7 3v6c0 4.5-3 7.5-7 9-4-1.5-7-4.5-7-9V6l7-3z"/><path d="M9 12l2 2 4-4"/>',
    lock: '<rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>',
    chip: '<rect x="6" y="6" width="12" height="12" rx="2"/><path d="M9 3v3M15 3v3M9 18v3M15 18v3M3 9h3M3 15h3M18 9h3M18 15h3"/>',
    shieldOut: '<path d="M12 3l7 3v6c0 4.5-3 7.5-7 9-4-1.5-7-4.5-7-9V6l7-3z"/><path d="M9 12h6M13 10l2 2-2 2"/>',
    bank: '<path d="M3 10l9-6 9 6M5 10v8M9.5 10v8M14.5 10v8M19 10v8M3 20h18"/>',
  };
  const NODES = [
    { id: "n-caller", name: "Caller", sub: "keypad · voice", badge: ["CARDHOLDER", "grey"], icon: "keypad" },
    { id: "n-el", name: "ElevenLabs Agents", sub: "voice + keypad input", badge: ["SEES DIGITS", "amber"], icon: "wave" },
    { id: "n-vin", name: "Vault inbound", sub: "Basis Theory proxy", badge: ["PCI DSS L1", "amber"], icon: "shield" },
    { id: "n-air", name: "Airlock", sub: "open source", badge: ["TOKENS ONLY", "teal"], icon: "lock", cls: "airlock" },
    { id: "n-llm", name: "Agent's language model", sub: ["merchant's own ·", "OpenAI-compatible"], badge: ["NO CARD DATA", "grey"], icon: null, cls: "sub" },
    { id: "n-vout", name: "Vault outbound", sub: "fixed destination", badge: ["PCI DSS L1", "amber"], icon: "shieldOut" },
    { id: "n-gw", name: "NMI / Authorize.net", sub: "gateway + its own vault", badge: ["PCI DSS L1", "amber"], icon: "bank" },
    { id: "n-main", name: "Main agent", sub: "Scarlett · hosted model", badge: ["NEVER SEES CARD DATA", "teal"], icon: "chip" },
  ];
  const LABELS = {
    full: { "n-el": ["ElevenAgents", "keypad buffer", ["PCI DSS L1", "amber"]],
      "n-llm": ["Agent's language model", ["merchant's own ·", "OpenAI-compatible"], ["NEVER SEES CARD DATA", "teal"]] },
    desk: { "n-el": ["Payment desk", "keypad buffer → vault", ["PCI DSS L1", "amber"]],
      "n-llm": ["Call ends", ["end_call system tool ·", "no hand-back"], ["NO CARD DATA", "teal"]] },
  };
  const EDGES = {
    e1: ["n-caller", "n-el"], e2: ["n-el", "n-vin"], e3: ["n-vin", "n-air"],
    e4: ["n-air", "n-llm"], e5: ["n-air", "n-vout"], e6: ["n-vout", "n-gw"],
    e0: ["n-caller", "n-main"], e7: ["n-main", "n-el"],
  };
  const LAYOUTS = {
    wide: { vb: [720, 600], W: 160, H: 80, zone: { y: 262, x: 214 }, zoneD: "M8 14 H712 V262 H214 V578 H8 Z", clean: [704, 592], zlbl: [22, 36],
      nodes: { "n-caller": [16, 58], "n-el": [280, 58], "n-vin": [544, 58], "n-air": [280, 360], "n-llm": [544, 360], "n-vout": [16, 360], "n-gw": [16, 486], "n-main": [280, 58] },
      edges: { e1: "M176 98 C214 78 242 78 280 98", e2: "M440 98 C478 78 506 78 544 98", e3: "M624 138 C624 272 360 236 360 360",
        e4: "M440 400 C478 380 506 380 544 400", e5: "M280 400 C242 380 214 380 176 400", e6: "M96 440 C118 456 118 470 96 486", e0: "M0 0", e7: "M0 0" } },
    deskWide: { vb: [720, 600], W: 160, H: 80, zone: { y: 262, x: 214 }, zoneD: "M8 14 H712 V262 H214 V578 H8 Z", clean: [704, 592], zlbl: [22, 36],
      grp: [266, 44, 188, 212], grpLbl: [446, 56], xfer: [352, 157, "end"],
      nodes: { "n-caller": [16, 58], "n-main": [280, 60], "n-el": [280, 168], "n-vin": [544, 168], "n-air": [280, 360], "n-llm": [544, 360], "n-vout": [16, 360], "n-gw": [16, 486] },
      edges: { e0: "M176 98 C214 82 242 84 280 100", e1: "M176 114 C232 130 226 208 280 208", e7: "M360 140 V168", e2: "M440 208 C478 188 506 188 544 208",
        e3: "M624 248 C624 300 360 262 360 360", e4: "M440 400 C478 380 506 380 544 400", e5: "M280 400 C242 380 214 380 176 400", e6: "M96 440 C118 456 118 470 96 486" } },
    deskTall: { vb: [360, 610], W: 154, H: 76, zone: { y: 290, x: 184 }, zoneD: "M3 10 H357 V290 H184 V578 H3 Z", clean: [354, 600], zlbl: [12, 24],
      grp: [192, 32, 165, 214], grpLbl: [352, 43], xfer: [283, 147, "start"],
      nodes: { "n-caller": [6, 50], "n-main": [200, 50], "n-el": [200, 164], "n-vin": [6, 170], "n-air": [200, 374], "n-llm": [200, 494], "n-vout": [6, 374], "n-gw": [6, 494] },
      edges: { e0: "M160 86 C178 74 182 74 200 86", e1: "M150 126 C176 140 178 192 200 192", e7: "M277 126 V164", e2: "M200 224 C186 224 174 214 160 214",
        e3: "M83 246 C83 330 277 300 277 374", e4: "M277 450 C294 466 294 478 277 494", e5: "M200 412 C182 398 178 398 160 412", e6: "M83 450 C100 466 100 478 83 494" } },
    tall: { vb: [360, 610], W: 154, H: 76, zone: { y: 290, x: 184 }, zoneD: "M3 14 H357 V290 H184 V578 H3 Z", clean: [354, 600], zlbl: [12, 34],
      nodes: { "n-caller": [6, 46], "n-el": [200, 46], "n-vin": [200, 170], "n-air": [200, 374], "n-llm": [200, 494], "n-vout": [6, 374], "n-gw": [6, 494], "n-main": [200, 46] },
      edges: { e1: "M160 84 C178 70 182 70 200 84", e2: "M277 122 C294 140 294 152 277 170", e3: "M277 246 C277 300 277 320 277 374",
        e4: "M277 450 C294 466 294 478 277 494", e5: "M200 412 C182 398 178 398 160 412", e6: "M83 450 C100 466 100 478 83 494", e0: "M0 0", e7: "M0 0" } },
  };
  let L = null, VBW = 720;

  const mk = (tag, attrs, parent) => {
    const n = document.createElementNS(NS, tag);
    for (const k in attrs) n.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(n);
    return n;
  };

  /* ---------- static scene ---------- */
  const glowStops = (id, c) => `<radialGradient id="${id}"><stop offset="0" stop-color="${c}" stop-opacity=".55"/><stop offset=".45" stop-color="${c}" stop-opacity=".18"/><stop offset="1" stop-color="${c}" stop-opacity="0"/></radialGradient>`;
  svg.innerHTML = `<defs>
      <pattern id="dots" width="18" height="18" patternUnits="userSpaceOnUse"><circle cx="1" cy="1" r=".9" fill="#2a3039"/></pattern>
      <linearGradient id="nodeSheen" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#fff" stop-opacity=".06"/><stop offset=".5" stop-color="#fff" stop-opacity="0"/></linearGradient>
      <filter id="nshadow" x="-20%" y="-20%" width="140%" height="160%"><feDropShadow dx="0" dy="8" stdDeviation="9" flood-color="#000" flood-opacity=".55"/></filter>
      ${Object.entries(C).map(([k, c]) => glowStops("g-" + k, c)).join("")}
    </defs>
    <rect class="bgdots" x="0" y="0" width="100%" height="100%" fill="url(#dots)"/>
    <path class="zone" id="zone"/>
    <rect class="grp" id="grp" rx="18"/><text class="grp-lbl" id="grp-lbl" text-anchor="end">ELEVENAGENTS PLATFORM</text>
    <text class="xfer-lbl" id="xfer-lbl">transfer_to_agent</text>
    <text class="zone-lbl" id="zone-lbl">CARD DATA PRESENT: CERTIFIED PROVIDERS ONLY</text>
    <text class="clean-lbl" id="clean-lbl" text-anchor="end">TOKENS ONLY · NO CARD DATA</text>
    <text class="brain-lbl" id="brain-lbl">AGENT'S BRAIN · VIA CUSTOM LLM</text>
    <g id="edges"></g><g id="trails"></g><g id="nodes"></g><g id="lats"></g><g id="packets"></g><g id="fx"></g>`;
  const gEdges = $("#edges", svg), gTrails = $("#trails", svg), gNodes = $("#nodes", svg),
    gLats = $("#lats", svg), gPk = $("#packets", svg), gFx = $("#fx", svg), zone = $("#zone", svg);

  const edgeEl = {}, flowEl = {}, hitEl = {};
  for (const id in EDGES) {
    edgeEl[id] = mk("path", { class: "edge", id }, gEdges);
    flowEl[id] = mk("path", { class: "edge-flow" }, gEdges);
    hitEl[id] = mk("path", { class: "edge-hit", "data-edge": id }, gEdges);
    const t = document.createElementNS(NS, "title"); t.textContent = "Inspect this hop"; hitEl[id].appendChild(t);
  }
  const nodeEl = {};
  NODES.forEach((n) => {
    const g = mk("g", { class: "node" + (n.cls ? " " + n.cls : ""), id: n.id }, gNodes);
    g.innerHTML = n.icon ? `<rect class="nb" rx="14" filter="url(#nshadow)"/><rect class="nb-top" rx="14"/>
      <rect class="nic-bg" x="12" y="13" width="28" height="28" rx="8"/>
      <g class="nic" transform="translate(16 17) scale(.8333)">${ICONS[n.icon]}</g>
      <text class="nt" x="50" y="27">${esc(n.name)}</text><text class="ns" x="50" y="42">${esc(n.sub)}</text>`
      : `<rect class="nb" rx="14"/><text class="nt" x="12" y="20">${esc(n.name)}</text>
      <text class="ns" x="12" y="33">${esc(n.sub[0])}</text><text class="ns" x="12" y="45">${esc(n.sub[1])}</text>`;
    g.innerHTML += `
      <g class="nbadge badge-${n.badge[1]}"><rect rx="5" height="15"/><text x="7" y="10.8">${esc(n.badge[0])}</text></g>
      <text class="nstat"></text>`;
    nodeEl[n.id] = g;
  });
  const lats = {};
  for (const id in EDGES) {
    const g = mk("g", { class: "lat", opacity: 0 }, gLats);
    lats[id] = { g, r: mk("rect", { rx: 6, height: 17 }, g), t: mk("text", { y: 12 }, g), timer: 0 };
  }

  function layout() {
    const narrow = window.matchMedia("(max-width: 600px)").matches;
    L = LAYOUTS[MODE === "desk" ? (narrow ? "deskTall" : "deskWide") : (narrow ? "tall" : "wide")];
    const desk = MODE === "desk";
    for (const id of ["n-el", "n-llm"]) {
      const [name, sub, badge] = LABELS[MODE][id], g = nodeEl[id], ns = $$(".ns", g);
      $(".nt", g).textContent = name;
      if (Array.isArray(sub)) sub.forEach((t, i) => { if (ns[i]) ns[i].textContent = t; }); else ns[0].textContent = sub;
      const b = $(".nbadge", g); b.setAttribute("class", "nbadge badge-" + badge[1]); $("text", b).textContent = badge[0];
    }
    nodeEl["n-main"].style.display = desk ? "" : "none";
    nodeEl["n-llm"].classList.toggle("endnode", desk);
    for (const id of ["e0", "e7"]) { [edgeEl[id], flowEl[id], hitEl[id], lats[id].g].forEach((el) => { el.style.display = desk ? "" : "none"; }); }
    edgeEl.e4.classList.toggle("sub", !desk); flowEl.e4.classList.toggle("sub", !desk);
    edgeEl.e4.classList.toggle("endedge", desk); flowEl.e4.classList.toggle("endedge", desk);
    edgeEl.e7.classList.add("xfer");
    // only the hops that carry raw digits are drawn warm; every other edge is token/clean
    const CARD_EDGES = ["e1", "e2", "e6"];
    for (const id in EDGES) {
      const card = CARD_EDGES.includes(id);
      [edgeEl[id], flowEl[id]].forEach((el) => { el.classList.toggle("card", card); el.classList.toggle("clean", !card); });
    }
    const grp = $("#grp", svg), gl = $("#grp-lbl", svg), xl = $("#xfer-lbl", svg);
    [grp, gl, xl].forEach((el) => { el.style.display = desk ? "" : "none"; });
    $("#brain-lbl", svg).style.display = desk ? "none" : "";
    if (desk) {
      grp.setAttribute("x", L.grp[0]); grp.setAttribute("y", L.grp[1]); grp.setAttribute("width", L.grp[2]); grp.setAttribute("height", L.grp[3]);
      gl.setAttribute("x", L.grpLbl[0]); gl.setAttribute("y", L.grpLbl[1]);
      xl.setAttribute("x", L.xfer[0]); xl.setAttribute("y", L.xfer[1]); xl.setAttribute("text-anchor", L.xfer[2]);
    }
    VBW = L.vb[0];
    svg.setAttribute("viewBox", `0 0 ${L.vb[0]} ${L.vb[1]}`);
    zone.setAttribute("d", L.zoneD);
    const zl = $("#zone-lbl", svg); zl.setAttribute("x", L.zlbl[0]); zl.setAttribute("y", L.zlbl[1]);
    const cl = $("#clean-lbl", svg); cl.setAttribute("x", L.clean[0]); cl.setAttribute("y", L.clean[1]);
    const bl = $("#brain-lbl", svg), [bx, by] = L.nodes["n-llm"]; bl.setAttribute("x", bx + L.W); bl.setAttribute("text-anchor", "end"); bl.setAttribute("y", L === LAYOUTS.tall ? by + L.H + 13 : by - 7);
    svg.setAttribute("aria-label", desk
      ? "Payment desk: caller to the main ElevenLabs agent on its hosted model; transfer_to_agent to the payment desk agent; desk to the vault inbound proxy to Airlock, which scripts every word; Airlock to the vault outbound proxy to NMI or Authorize.net; then the call ends. No merchant model. Card digits exist only at three PCI DSS Level 1 hops: the ElevenAgents keypad buffer, the vault, and the gateway."
      : "Full conversation: caller to ElevenLabs Agents to the vault inbound proxy to Airlock to the vault outbound proxy to NMI or Authorize.net. A dashed side node is the agent's language model, the merchant's own, which Airlock passes normal turns to without card data. Card digits exist only at three PCI DSS Level 1 hops: the ElevenAgents keypad buffer, the vault, and the gateway.");
    for (const id in nodeEl) {
      const g = nodeEl[id], [x, y] = L.nodes[id];
      g.setAttribute("transform", `translate(${x} ${y})`);
      $$("rect.nb, rect.nb-top", g).forEach((r) => { r.setAttribute("width", L.W); r.setAttribute("height", L.H); });
      const nt = $(".nt", g); nt.style.fontSize = g.classList.contains("sub") ? "11.5px" : L.W < 160 ? "12px" : "";
      const b = $(".nbadge", g), bt = $("text", b);
      let w = 0; try { w = bt.getComputedTextLength(); } catch (e) {} if (!w) w = bt.textContent.length * 5.6;
      $("rect", b).setAttribute("width", w + 14);
      b.setAttribute("transform", `translate(12 ${L.H - 24})`);
      const st = $(".nstat", g); st.setAttribute("x", 6); st.setAttribute("y", L.H + 15);
    }
    for (const id in EDGES) {
      [edgeEl[id], flowEl[id], hitEl[id]].forEach((p) => p.setAttribute("d", L.edges[id]));
      edgeEl[id]._len = edgeEl[id].getTotalLength();
      placeLat(id);
    }
  }
  function placeLat(id) {
    const p = edgeEl[id], len = p.getTotalLength(), m = p.getPointAtLength(len / 2),
      a = p.getPointAtLength(len / 2 - 1), b = p.getPointAtLength(len / 2 + 1);
    let nx = -(b.y - a.y), ny = b.x - a.x; const d = Math.hypot(nx, ny) || 1; nx /= d; ny /= d;
    const vertical = Math.abs(b.y - a.y) > Math.abs(b.x - a.x);
    const lat = lats[id], w = +lat.r.getAttribute("width") || 60;
    let x, y;
    if (L === LAYOUTS.deskWide && id === "e1") { x = 182; y = 238; }
    else if ((L === LAYOUTS.tall || L === LAYOUTS.deskTall) && !vertical) { x = m.x - w / 2; y = m.y < 150 ? 128 : m.y < 300 ? 252 : 352; }
    else if (vertical) { const left = m.x > VBW / 2; x = left ? m.x - 14 - w : m.x + 14; y = m.y - 8; }
    else { if (ny > 0) { nx = -nx; ny = -ny; } x = m.x + nx * 14 - w / 2; y = m.y + ny * 14 - 17; }
    x = Math.max(4, Math.min(VBW - w - 4, x));
    lat.g.setAttribute("transform", `translate(${x.toFixed(1)} ${y.toFixed(1)})`);
  }
  layout();
  const mq = window.matchMedia("(max-width: 600px)");
  (mq.addEventListener ? mq.addEventListener.bind(mq, "change") : mq.addListener.bind(mq))(() => { killAll(); layout(); });

  /* ---------- clock + frame loop ---------- */
  const clock = { t: 0, last: performance.now(), paused: false };
  const packets = new Set(), fx = new Set(), timers = new Set();
  let looping = false;
  function ensureLoop() { if (!looping) { looping = true; clock.last = performance.now(); requestAnimationFrame(frame); } }
  function frame(now) {
    const dt = Math.min(64, now - clock.last); clock.last = now;
    if (!clock.paused) clock.t += dt;
    packets.forEach(stepPacket);
    fx.forEach(stepFx);
    timers.forEach((tm) => { if (clock.t >= tm.at) { timers.delete(tm); tm.resolve(true); } });
    let hot = false; packets.forEach((p) => { if (p.isCard && !p.morphed) hot = true; });
    zone.classList.toggle("hot", hot);
    if (packets.size || fx.size || timers.size) requestAnimationFrame(frame); else looping = false;
  }
  function wait(ms) { return new Promise((resolve) => { timers.add({ at: clock.t + (reduceMotion() ? Math.min(ms, 300) : ms), resolve }); ensureLoop(); }); }
  function setPaused(p) { clock.paused = p; ensureLoop(); }

  const ease = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);
  const inZone = (pt) => pt.y <= L.zone.y || pt.x <= L.zone.x;

  function pulse(nodeId, color) {
    const n = nodeEl[nodeId]; if (!n) return;
    n.style.setProperty("--hit", color); n.classList.add("hit");
    clearTimeout(n._t); n._t = setTimeout(() => n.classList.remove("hit"), 700);
  }
  function burst(x, y, color) {
    const c = mk("circle", { class: "burst", cx: x, cy: y, r: 4, stroke: color }, gFx);
    fx.add({ el: c, t0: clock.t, dur: 650, kind: "burst" }); ensureLoop();
  }
  function stepFx(f) {
    const u = Math.min(1, (clock.t - f.t0) / f.dur);
    if (f.kind === "burst") { f.el.setAttribute("r", (4 + 30 * ease(u)).toFixed(1)); f.el.setAttribute("opacity", (1 - u).toFixed(2)); }
    else if (f.kind === "text") { f.el.setAttribute("opacity", (u < 0.12 ? u / 0.12 : u > 0.8 ? (1 - u) / 0.2 : 1).toFixed(2)); }
    if (u >= 1) { f.el.remove(); fx.delete(f); if (f.done) f.done(); }
  }
  function flash(nodeId, text, color) {
    const n = nodeEl[nodeId]; if (!n) return;
    const [x, y] = L.nodes[nodeId];
    const t = mk("text", { class: "flash", x: x + L.W / 2, y: y - 8, "text-anchor": "middle", fill: color, opacity: 0 }, gFx);
    t.textContent = text;
    n.classList.add("alarm");
    fx.add({ el: t, t0: clock.t, dur: 2200, kind: "text", done: () => n.classList.remove("alarm") }); ensureLoop();
  }

  function shapeFor(kind, parent) {
    const g = mk("g", {}, parent);
    if (kind === "card") {
      mk("rect", { x: -12, y: -8, width: 24, height: 16, rx: 3, fill: C.card }, g);
      mk("rect", { x: -12, y: -4, width: 24, height: 3.2, fill: "#5c3a0b", opacity: 0.55 }, g);
      mk("rect", { x: -8.5, y: 1.8, width: 6, height: 3.2, rx: 1, fill: "#fff", opacity: 0.55 }, g);
    } else if (kind === "token") {
      mk("rect", { x: -13, y: -7.5, width: 26, height: 15, rx: 7.5, fill: C.token }, g);
      mk("circle", { cx: -5, cy: 0, r: 2.8, fill: "none", stroke: "#0b3b35", "stroke-width": 1.6 }, g);
      mk("path", { d: "M-2.2 0 H7 M4 0 V2.6 M6.5 0 V2", stroke: "#0b3b35", "stroke-width": 1.6, fill: "none", "stroke-linecap": "round" }, g);
    } else {
      const col = C[kind] || C.speech;
      mk("circle", { r: 5, fill: col }, g);
      mk("circle", { r: 8.5, fill: "none", stroke: col, "stroke-opacity": 0.5, "stroke-width": 1 }, g);
    }
    return g;
  }
  function makeLabel(g, text, color) {
    const lg = mk("g", { class: "pk-lbl" }, g);
    const r = mk("rect", { rx: 7, height: 22, stroke: color, "stroke-opacity": 0.6 }, lg);
    const t = mk("text", { x: 8, y: 15, fill: color }, lg);
    const lab = { lg, r, t, w: 0 };
    setLabel(lab, text, color);
    return lab;
  }
  function setLabel(lab, text, color) {
    lab.t.textContent = text; lab.t.setAttribute("fill", color); lab.r.setAttribute("stroke", color);
    let w = 0; try { w = lab.t.getComputedTextLength(); } catch (e) {} if (!w) w = text.length * 6.3;
    lab.w = w + 16; lab.r.setAttribute("width", lab.w);
  }

  // fly one packet along an edge. kind: speech|card|token|ok|bad. morph: {label} turns a card into a token at the trust boundary
  function fly(edge, o = {}) {
    const path = edgeEl[edge]; if (!path) return Promise.resolve(false);
    const len = path._len || path.getTotalLength();
    const kind = o.kind || "speech", col = C[kind] || C.speech;
    const g = mk("g", { class: "pk" }, gPk);
    const trails = [[16, 3.4, 0.95], [40, 2.4, 0.42], [74, 1.5, 0.16]].map(([l, w, op]) =>
      ({ l, el: mk("path", { class: "trail", d: path.getAttribute("d"), stroke: col, "stroke-width": w, "stroke-opacity": op, "stroke-dasharray": `${l} ${len * 3}` }, gTrails) }));
    const glow = mk("circle", { r: 20, fill: `url(#g-${kind})` }, g);
    const body = mk("g", {}, g);
    const s1 = shapeFor(kind, body);
    const s2 = o.morph ? shapeFor("token", body) : null;
    if (s2) s2.setAttribute("opacity", 0);
    const lab = o.label ? makeLabel(g, o.label, col) : null;
    let u0 = -1;
    if (o.morph) {
      for (let i = 0; i <= 80; i++) { const p = path.getPointAtLength(len * i / 80); if (!inZone(p)) { u0 = i / 80; break; } }
      if (u0 < 0) u0 = 0.5;
    }
    path.classList.add("live");
    const dur = reduceMotion() ? 260 : Math.max(380, (o.dur || 900) * Math.max(0.7, len / 150)) / (o.speed || speedFactor());
    const p = { path, len, g, glow, body, s1, s2, lab, trails, reverse: !!o.reverse, t0: clock.t, dur, u0, morphed: false,
      isCard: kind === "card", o, target: EDGES[edge][o.reverse ? 0 : 1] };
    return new Promise((resolve) => { p.resolve = resolve; packets.add(p); stepPacket(p); ensureLoop(); });
  }
  function stepPacket(p) {
    const raw = Math.min(1, Math.max(0, (clock.t - p.t0) / p.dur)), u = ease(raw);
    const s = p.len * (p.reverse ? 1 - u : u);
    const pt = p.path.getPointAtLength(s);
    p.glow.setAttribute("cx", pt.x); p.glow.setAttribute("cy", pt.y);
    p.body.setAttribute("transform", `translate(${pt.x.toFixed(1)} ${pt.y.toFixed(1)})`);
    p.trails.forEach((tr) => tr.el.setAttribute("stroke-dashoffset", (p.reverse ? -s : tr.l - s).toFixed(1)));
    if (p.s2) {
      const k = Math.min(1, Math.max(0, (u - (p.u0 - 0.05)) / 0.1));
      p.s1.setAttribute("opacity", (1 - k).toFixed(2));
      p.s1.setAttribute("transform", `scale(${(1 - 0.45 * k).toFixed(3)})`);
      p.s2.setAttribute("opacity", k.toFixed(2));
      p.s2.setAttribute("transform", `scale(${(0.55 + 0.45 * k).toFixed(3)})`);
      if (k >= 0.5 && !p.morphed) {
        p.morphed = true;
        burst(pt.x, pt.y, C.token);
        p.glow.setAttribute("fill", "url(#g-token)");
        p.trails.forEach((tr) => tr.el.setAttribute("stroke", C.token));
        if (p.lab) setLabel(p.lab, p.o.morph.label, C.token);
      }
    }
    if (p.lab) {
      let x = pt.x - p.lab.w / 2, y = pt.y - 36;
      x = Math.max(4, Math.min(VBW - p.lab.w - 4, x));
      if (y < 2) y = pt.y + 16;
      p.lab.lg.setAttribute("transform", `translate(${x.toFixed(1)} ${y.toFixed(1)})`);
    }
    if (raw >= 1) finishPacket(p, true);
  }
  function finishPacket(p, arrived) {
    packets.delete(p);
    p.path.classList.remove("live");
    p.g.remove(); p.trails.forEach((tr) => tr.el.remove());
    if (arrived) pulse(p.target, C[p.morphed ? "token" : p.o.kind] || C.speech);
    p.resolve(arrived);
  }
  function killAll() {
    packets.forEach((p) => finishPacket(p, false));
    fx.forEach((f) => { f.el.remove(); if (f.done) f.done(); }); fx.clear();
    timers.forEach((tm) => tm.resolve(false)); timers.clear();
    zone.classList.remove("hot");
  }
  async function hops(list) { for (const h of list) { const ok = await fly(h[0], h[1]); if (!ok) return false; } return true; }

  let backlog = 0;
  const speedFactor = () => (backlog > 3 ? 2.6 : backlog > 1 ? 1.6 : 1);

  function latency(edge, text) {
    const lat = lats[edge]; if (!lat) return;
    lat.t.textContent = text;
    let w = 0; try { w = lat.t.getComputedTextLength(); } catch (e) {} if (!w) w = text.length * 5.8;
    lat.r.setAttribute("width", w + 14); lat.t.setAttribute("x", 7);
    lat.g.setAttribute("opacity", 1);
    placeLat(edge);
    lat.g.classList.add("fresh"); clearTimeout(lat.timer);
    lat.timer = setTimeout(() => lat.g.classList.remove("fresh"), 1800);
  }
  function status(nodeId, text) { const n = nodeEl[nodeId]; if (n) $(".nstat", n).textContent = text || ""; }
  let speakNode = "n-el";
  function speaking(on, node) { if (node) speakNode = node; for (const id of ["n-el", "n-main"]) nodeEl[id].classList.toggle("speaking", !!on && id === speakNode); }
  function resetScene() {
    killAll();
    for (const id in lats) { lats[id].g.setAttribute("opacity", 0); lats[id].g.classList.remove("fresh"); }
    for (const id in nodeEl) { nodeEl[id].classList.remove("hit", "alarm", "speaking"); status(id, ""); }
    status("n-air", MODE === "desk" ? "script only" : "mode: model");
  }
  function selectEdge(edge) { for (const id in edgeEl) edgeEl[id].classList.toggle("sel", id === edge); }
  status("n-air", "mode: model");
  function setMode() { killAll(); layout(); resetScene(); }
  return { fly, hops, flash, latency, status, speaking, resetScene, killAll, wait, setPaused, selectEdge, setMode,
    hitEl, setBacklog: (n) => { backlog = n; }, get paused() { return clock.paused; } };
})();

/* ---------------------------------------------------------------- step rail */
const tracker = (() => {
  const el = $("#steps");
  const CARD = [["pan", "Card number"], ["exp", "Expiry"], ["cvv", "Code"], ["zip", "ZIP"], ["save", "Save"], ["confirm", "Confirm"], ["done", "Charged"]];
  const BANK = [["routing", "Routing"], ["account", "Account"], ["acct_type", "Type"], ["confirm", "Authorize"], ["done", "Debited"]];
  let set = CARD, cur = null, curStatus = null;
  function draw() {
    const idx = cur ? set.findIndex((s) => s[0] === cur) : -1;
    el.innerHTML = set.map(([k, l], i) => {
      let c = "";
      if (idx >= 0) {
        if (i < idx) c = "done";
        else if (i === idx) c = k === "done" ? (curStatus === "approved" ? "ok" : curStatus ? "fail" : "now") : "now";
      }
      let lab = l;
      if (k === "done" && i === idx && curStatus && curStatus !== "approved")
        lab = curStatus === "duplicate" ? "Already paid" : curStatus === "declined" ? "Declined" : curStatus.charAt(0).toUpperCase() + curStatus.slice(1);
      return `<li class="${c}"${i === idx ? ' aria-current="step"' : ""}>${esc(lab)}</li>`;
    }).join("");
  }
  function step(s, st) {
    if (s === null) { return; }
    if (!s) return;
    if (["routing", "account", "acct_type"].includes(s)) set = BANK;
    else if (["pan", "exp", "cvv", "zip"].includes(s)) set = CARD;
    if (s === "charging") s = "done";
    if (set === BANK && s === "save") s = "confirm";
    if (s === "cancelled") { cur = null; curStatus = null; draw(); return; }
    cur = s; curStatus = s === "done" ? (st || null) : null;
    draw();
  }
  function reset(bank) { set = bank ? BANK : CARD; cur = null; curStatus = null; draw(); }
  reset();
  return { step, reset };
})();

/* ---------------------------------------------------------------- event log */
const evlog = (() => {
  const el = $("#evlog"), count = $("#ev-count");
  // only these fields are ever displayed, whatever arrives
  const FIELDS = ["hop", "field", "len", "brand", "last4", "mode", "step", "status", "ms", "gateway", "say"];
  let first = true, n = 0;
  function add(ev, note) {
    if (first) { el.innerHTML = ""; first = false; }
    const d = ev.t ? new Date(ev.t) : new Date();
    const ts = isNaN(d) ? "" : d.toTimeString().slice(0, 8);
    const parts = FIELDS.filter((f) => ev[f] != null && ev[f] !== "").map((f) => {
      let v = String(ev[f]); if (f === "say" && v.length > 70) v = v.slice(0, 68) + "…";
      return `${f}=${f === "say" ? "“" + esc(v) + "”" : esc(v)}`;
    });
    const k = String(ev.kind || "event");
    const line = document.createElement("div");
    line.className = "ln";
    line.innerHTML = `<span class="t">${ts}</span>  <span class="k ${esc(k)}">${esc(k)}</span>  ${parts.join(" ")}${note ? " " + esc(note) : ""}`;
    el.appendChild(line);
    while (el.children.length > 300) el.firstChild.remove();
    el.scrollTop = el.scrollHeight;
    count.textContent = ++n + (n === 1 ? " event" : " events");
  }
  function clear() { el.innerHTML = ""; first = false; n = 0; count.textContent = "0 events"; }
  return { add, clear };
})();

function caption(who, text) {
  const c = $("#wf-caption");
  const cls = /agent/.test(who) ? "agent" : /caller/.test(who) ? "caller" : /done|approved/.test(who) ? "done" : "";
  c.innerHTML = `<span class="who ${cls}">${esc(who)}</span><span>${esc(text)}</span>`;
}

/* ---------------------------------------------------------- payload inspector */
const inspector = (() => {
  const HOPS = [
    { id: "c-el", edge: "e1", label: "Caller → ElevenLabs" },
    { id: "el-vin", edge: "e2", label: "ElevenLabs → Vault in" },
    { id: "vin-air", edge: "e3", label: "Vault in → Airlock" },
    { id: "air-llm", edge: "e4", label: "Airlock ⇄ agent's model" },
    { id: "air-el", edge: "e3", label: "Reply → caller" },
    { id: "air-vout", edge: "e5", label: "Airlock → Vault out" },
    { id: "vout-gw", edge: "e6", label: "Vault out → Gateway" },
    { id: "gw-air", edge: "e6", label: "Gateway reply" },
  ];
  const tabs = $("#insp-tabs"), body = $("#insp-body"), followBtn = $("#insp-follow");
  const data = {};
  let sel = null, follow = true;
  const DESK_LABEL = { "c-el": "Caller → payment desk", "el-vin": "Desk agent → Vault in", "air-llm": "Airlock → end_call" };
  const FULL_LABEL = {};
  HOPS.forEach((h) => { FULL_LABEL[h.id] = h.label; });
  function labels() {
    HOPS.forEach((h) => { h.label = (MODE === "desk" && DESK_LABEL[h.id]) || FULL_LABEL[h.id]; });
    tabs.innerHTML = HOPS.map((h) => `<button class="insp-tab" role="tab" type="button" id="tab-${h.id}" data-hop="${h.id}" aria-selected="false"><i></i>${esc(h.label)}</button>`).join("");
  }
  labels();

  function hl(html) {
    return html
      .replace(/(\[\[airlock:[^\]]*\]\])/g, '<span class="tok">$1</span>')
      .replace(/(\{\{[^}]*\}\})/g, '<span class="tok">$1</span>')
      .replace(/((?:\d{4} )?•[•\s\d#…]*\d*#?)/g, (m) => (/•{2,}/.test(m) ? `<span class="pan">${m}</span>` : m));
  }
  function render() {
    $$(".insp-tab", tabs).forEach((t) => {
      const h = t.dataset.hop, d = data[h];
      t.setAttribute("aria-selected", String(h === sel));
      t.classList.toggle("has", !!d);
      if (d) t.style.setProperty("--z", d.zone === "card" ? C.card : C.token);
    });
    const h = HOPS.find((x) => x.id === sel);
    viz.selectEdge(h ? h.edge : null);
    const d = data[sel];
    if (!d) { body.innerHTML = `<p class="insp-empty">${sel ? "Nothing has crossed this hop yet." : "Select a hop, or watch a packet move. You'll see the exact (redacted) payload at that point."}</p>`; return; }
    body.innerHTML = `<div class="insp-meta"><b>${esc(h.label)}</b><span class="insp-zone ${d.zone === "card" ? "card" : "clean"}">${d.zone === "card" ? "card data present · certified provider" : "no card data"}</span>${d.meta ? `<span>${esc(d.meta)}</span>` : ""}</div>
      <pre>${hl(typeof d.json === "string" ? esc(d.json) : jsonHtml(d.json))}</pre>${d.note ? `<p class="insp-note">${esc(d.note)}</p>` : ""}`;
  }
  function set(hop, d) {
    data[hop] = d;
    const t = $("#tab-" + hop, tabs);
    if (t) { t.classList.remove("ping"); void t.offsetWidth; t.classList.add("ping"); }
    if (follow) sel = hop;
    render();
    if (follow && t) { const r = t.offsetLeft - tabs.clientWidth / 2 + t.clientWidth / 2; tabs.scrollTo({ left: r, behavior: "auto" }); }
  }
  function pick(hop) {
    sel = hop; follow = false;
    followBtn.setAttribute("aria-pressed", "false"); followBtn.textContent = "Follow latest hop";
    render();
  }
  function clear() { for (const k in data) delete data[k]; sel = null; render(); }
  tabs.addEventListener("click", (e) => { const t = e.target.closest(".insp-tab"); if (t) pick(t.dataset.hop); });
  tabs.addEventListener("keydown", (e) => {
    if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
    const all = $$(".insp-tab", tabs), i = all.findIndex((t) => t.dataset.hop === sel);
    const n = all[(Math.max(0, i) + (e.key === "ArrowRight" ? 1 : all.length - 1)) % all.length];
    n.focus(); pick(n.dataset.hop);
  });
  followBtn.addEventListener("click", () => {
    follow = !follow;
    followBtn.setAttribute("aria-pressed", String(follow));
    followBtn.textContent = follow ? "Following latest hop" : "Follow latest hop";
  });
  for (const id in viz.hitEl) viz.hitEl[id].addEventListener("click", () => { const h = HOPS.find((x) => x.edge === id); if (h) pick(h.id); });
  render();
  return { set, clear, setMode: () => { labels(); clear(); } };
})();

/* ------------------------------------------------------------- the handler
   One handler for live and replayed events: apply() updates every piece of
   state at once (so scrubbing can replay state without animation); animate()
   returns a promise for the packets. */
const PREFIX = { visa: "4111", mastercard: "5424", amex: "3700" };
const trunc = (s, n) => { s = String(s || ""); return s.length > n ? s.slice(0, n - 1) + "…" : s; };
const fmtMs = (ms) => (ms >= 1000 ? (ms / 1000).toFixed(ms >= 10000 ? 0 : 1) + " s" : Math.round(ms) + " ms");

const flow = (() => {
  const S = { brand: "", last4: "", lastSent: "", mode: "model", sdkUserAt: 0, chargeReq: null, transferred: false };
  function reset() {
    Object.assign(S, { brand: "", last4: "", lastSent: "", mode: "model", sdkUserAt: 0, chargeReq: null, transferred: false });
    viz.resetScene(); tracker.reset(); inspector.clear(); evlog.clear();
  }
  function maskedTyped(ev, cap) {
    if (cap && cap.caller) return cap.caller.replace(/\s*\(.*\)$/, "");
    const d = (S.lastSent || "").replace(/\D/g, "");
    if (d && ev.last4 && d.endsWith(ev.last4) && d.length >= 12) return d.slice(0, 4) + " •••• •••• " + d.slice(-4) + "#";
    if (PREFIX[ev.brand] && ev.last4) return PREFIX[ev.brand] + " •••• •••• " + ev.last4 + "#";
    return "•".repeat(Math.min(+ev.len || 4, 16)) + "#";
  }
  const markerOf = (ev, cap) => (cap && cap.received && cap.received.startsWith("[[airlock:")) ? cap.received
    : `[[airlock:kp id=<token> len=${ev.len ?? "?"}${ev.last4 ? " last4=" + ev.last4 : ""}${ev.brand ? " brand=" + ev.brand : ""}]]`;
  const elRequest = (content) => ({
    request: MODE === "desk" ? "POST <vault inbound proxy>  (the payment desk agent's Custom LLM URL)" : "POST <vault inbound proxy>  (the agent's Custom LLM URL)",
    headers: MODE === "desk" ? { "BT-PROXY-KEY": "<workspace secret>", "X-Airlock-Mode": "desk" } : { "BT-PROXY-KEY": "<workspace secret>" },
    body: { stream: true, messages: [{ role: "system", content: "…" }, "… every earlier turn, re-sent on each request …", { role: "user", content } ] },
  });
  const airRequest = (content) => ({
    request: "forwarded by the vault to Airlock (signed; unsigned requests get HTTP 401)",
    body: { stream: true, messages: [{ role: "system", content: "…" }, "… earlier turns, already tokenized …", { role: "user", content } ] },
  });

  function apply(ev, ctx = {}) {
    const cap = ctx.cap || {};
    const live = !!ctx.live;
    evlog.add(ev);
    const k = ev.kind;
    const desk = MODE === "desk";
    if (k === "main-agent") {
      if (cap.caller) caption("caller", cap.caller);
      inspector.set("c-el", { zone: "clean", json: { to: "main agent (ElevenLabs-hosted model)", text: cap.caller || "" }, note: "Before the transfer, the caller talks to the main agent. Nothing here goes through the vault or Airlock." });
      return;
    }
    if (k === "main-agent-reply") { caption("agent", "Scarlett answers on ElevenLabs' hosted model, then hands off: “connecting you to the secure payment line”."); return; }
    if (k === "transfer") {
      S.transferred = true;
      caption("transfer", "transfer_to_agent → payment desk. ElevenLabs sends the whole conversation to whichever agent speaks next, so the desk never hands back.");
      viz.status("n-air", "script only");
      return;
    }
    if (k === "end-call") {
      tracker.step(null);
      viz.status("n-air", "call ended");
      caption("agent", "Thanks for calling. Goodbye.");
      inspector.set("air-llm", { zone: "clean", json: { say: "Thanks for calling. Goodbye.", tool: "end_call", hand_back_to_main_agent: false }, note: "The desk ends the call rather than hand back: the main agent's hosted model is not behind the vault, and the next agent to speak would receive the whole conversation, typed digits included." });
      return;
    }
    if (k === "turn") {
      if (ev.mode) { S.mode = ev.mode; viz.status("n-air", desk ? "script only" : "mode: " + ev.mode); }
      if (cap.caller) caption("caller", cap.caller);
      if (ev.field === "keypad-token") {
        if (ev.brand) S.brand = ev.brand; if (ev.last4) S.last4 = ev.last4;
        const typed = maskedTyped(ev, cap), marker = markerOf(ev, cap);
        if (!live) inspector.set("c-el", { zone: "card", json: { input: "keypad", text: typed }, meta: "digits masked on this page", note: "A phone keypad sends tones; ElevenLabs delivers the digits to the model request as an ordinary user message." });
        inspector.set("el-vin", { zone: "card", json: elRequest(typed), note: "Card digits exist in this request. It goes only to the vault, which is PCI DSS Level 1." });
        inspector.set("vin-air", { zone: "clean", json: airRequest(marker), meta: cap.ttfb ? "round trip " + fmtMs(cap.ttfb) : "", note: "The vault's inbound transform swapped the digits for a token and this placeholder before Airlock saw the request." });
        if (MODE !== "desk") inspector.set("air-llm", { zone: "clean", json: { model_called: false, reason: "a capture is open — Airlock answers keypad turns itself with a scripted line" } });
      } else if (ev.field === "spoken-card-removed") {
        const said = cap.caller || "“… my card is 4111 •••• •••• 1111 …”";
        if (!live) inspector.set("c-el", { zone: "card", json: { input: "voice", transcript: said }, meta: "number masked on this page" });
        inspector.set("el-vin", { zone: "card", json: elRequest(said) });
        inspector.set("vin-air", { zone: "clean", json: airRequest(cap.received || "… [[airlock:spoken]] …"), note: "The spoken card number was cut from the sentence at the vault; the rest of what the caller said survives." });
      } else if (ev.field === "refused-not-test-card") {
        inspector.set("vin-air", { zone: "clean", json: { field: "refused-not-test-card" }, note: "This demo's vault edge discards any card number that is not a published sandbox test card." });
      } else {
        const said = cap.caller || ctx.text || "(caller speech)";
        if (!live) inspector.set("c-el", { zone: "card", json: { input: "voice", transcript: said } });
        inspector.set("el-vin", { zone: "card", json: elRequest(said), note: "Nothing card-like in this turn; the vault forwards it unchanged." });
        inspector.set("vin-air", { zone: "clean", json: airRequest(cap.received || said), meta: cap.ttfb ? "round trip " + fmtMs(cap.ttfb) : "" });
        inspector.set("air-llm", MODE === "desk"
          ? { zone: "clean", json: { model_called: false, reason: "payment desk — no language model is called; Airlock scripts every word" } }
          : ev.mode === "capture"
          ? { zone: "clean", json: { model_called: false, reason: "a capture is open — Airlock's script handles this turn" } }
          : { zone: "clean", json: { request: "POST <merchant's model endpoint>/chat/completions", body: { stream: true, messages: ["… tokenized history …", { role: "user", content: cap.received || said }] } } });
      }
      if (cap.ttfb && !live) viz.latency("e2", "round trip " + fmtMs(cap.ttfb));
    } else if (k === "model-first-token") {
      if (desk) return;
      if (ev.ms != null) viz.latency("e4", "1st token " + fmtMs(ev.ms));
      inspector.set("air-llm", { zone: "clean", json: { stream: "first token", ms: ev.ms ?? null }, meta: ev.ms != null ? "first token in " + fmtMs(ev.ms) : "" });
      if (cap.agent && !cap.capture) { caption("agent", cap.agent); inspector.set("air-el", { zone: "clean", json: { role: "assistant", content: cap.agent, source: "agent's model (the merchant's own)" } }); }
    } else if (k === "capture-open") {
      S.mode = "capture"; viz.status("n-air", "mode: capture");
      tracker.step(ctx.bank ? "routing" : "pan");
    } else if (k === "capture") {
      tracker.step(ev.step, ev.status);
      viz.status("n-air", "capture · " + (ev.step || ""));
    } else if (k === "capture-reply") {
      tracker.step(ev.step, ev.status);
      viz.status("n-air", ev.step === "done" || ev.step === "cancelled" ? (desk ? "script only" : "mode: model") : "capture · " + (ev.step || ""));
      if (ev.step === "done" || ev.step === "cancelled") S.mode = "model";
      if (ev.ms > 0) viz.latency("e3", "turn " + fmtMs(ev.ms));
      const say = cap.agent || ev.say || "";
      if (say) caption("agent", say);
      inspector.set("air-el", { zone: "clean", json: { role: "assistant", content: say || "(scripted line)", source: "Airlock capture script", step: ev.step || null, status: ev.status || null } });
    } else if (k === "charge-start") {
      tracker.step("charging");
      viz.status("n-air", "charging…");
      const b = ev.brand || S.brand, l4 = ev.last4 || S.last4;
      const req = (ctx.charge && ctx.charge.airlock_to_vault_request) || (ctx.bank
        ? { order_id: "…", amount: "…", routing: "{{ <routing token> }}", account: "{{ <account token> }}", account_type: "checking", name: "…" }
        : { order_id: "…", amount: "…", currency: "USD", pan: "{{ <card token> }}", exp: "{{ <expiry token> }}", cvv: "{{ <code token> }}", zip: "{{ <zip token> }}" });
      inspector.set("air-vout", { zone: "clean", json: req, meta: (ctx.charge && !ctx.charge.synthetic) ? "exact request from the recorded run" : "request shape", note: "Token expressions only. Airlock's key holds token:delete only; detokenization happens on a separate vault application attached only to this fixed-destination proxy." });
      inspector.set("vout-gw", { zone: "card", json: { destination: (ev.gateway || (ctx.charge && ctx.charge.vault_reply_to_airlock && ctx.charge.vault_reply_to_airlock.gateway) || "gateway") + " (fixed — a caller cannot redirect it)", body: "built inside the vault from the token values", instrument: ctx.bank ? "bank account (masked)" : (b ? b + " " : "") + "•••• " + (l4 || "") }, note: "The only hop after the keypad where card data travels — inside the vault, to the gateway." });
    } else if (k === "charge") {
      if (ev.ms != null) viz.latency("e5", "charge " + fmtMs(ev.ms));
      viz.status("n-air", ev.status || "");
      const rep = (ctx.charge && ctx.charge.vault_reply_to_airlock && !ctx.charge.synthetic) ? ctx.charge.vault_reply_to_airlock : { status: ev.status || null, gateway: ev.gateway || null, ms: ev.ms ?? null };
      inspector.set("gw-air", { zone: "clean", json: rep, meta: (ctx.charge && !ctx.charge.synthetic) ? "exact reduced reply from the recorded run" : "", note: "Reduced by the outbound proxy before Airlock sees it: no card data. Airlock then deletes the tokens." });
      if (ev.status === "approved") caption("approved", (ev.brand || S.brand ? (ev.brand || S.brand) + " " + (ev.last4 || S.last4) + " · " : "") + "approved by " + (ev.gateway || "the gateway"));
    } else if (k === "refused") {
      viz.status("n-air", "refused");
    }
  }

  function animate(ev, ctx = {}) {
    const k = ev.kind, cap = ctx.cap || {}, live = !!ctx.live, desk = MODE === "desk";
    if (k === "main-agent") return viz.fly("e0", { label: "“" + trunc(cap.caller || "speech", 30) + "”" });
    if (k === "main-agent-reply") { viz.speaking(true, "n-main"); return viz.fly("e0", { reverse: true, label: "hosted model replies" }).then((ok) => { viz.speaking(false); return ok; }); }
    if (k === "transfer") { viz.flash("n-main", "transfer_to_agent", C.speech); return viz.fly("e7", { label: "transfer · full history", dur: 1100 }); }
    if (k === "end-call") { return viz.fly("e4", { kind: "grey", label: "“Goodbye.” · end_call", dur: 1000 }).then((ok) => { if (ok) viz.flash("n-llm", "call ended", C.grey); return ok; }); }
    const skipE1 = live && performance.now() - S.sdkUserAt < 8000;
    const callerHop = (list) => (skipE1 ? list.slice(1) : list);
    if (k === "turn") {
      if (ev.field === "keypad-token") {
        const typed = maskedTyped(ev, cap), marker = markerOf(ev, cap).replace(/ id=[^\s\]]+/, "");
        return viz.hops(callerHop([
          ["e1", { kind: "card", label: typed }], ["e2", { kind: "card", label: typed }],
          ["e3", { kind: "card", label: typed, morph: { label: marker }, dur: 1300 }],
        ]));
      }
      if (ev.field === "spoken-card-removed") {
        const t = "“… my card is 4111 •••• …”";
        return viz.hops(callerHop([["e1", { kind: "card", label: t }], ["e2", { kind: "card", label: t }],
          ["e3", { kind: "card", label: t, morph: { label: "“… [[airlock:spoken]] …”" }, dur: 1300 }]]))
          .then((ok) => (ok && ev.mode !== "capture" && !desk ? viz.fly("e4", { kind: "speech", label: "speech · card removed" }) : ok));
      }
      if (ev.field === "refused-not-test-card") {
        return viz.hops(callerHop([["e1", { kind: "card", label: "real-looking card number" }], ["e2", { kind: "card", label: "real-looking card number" }]]))
          .then((ok) => { if (!ok) return ok; viz.flash("n-vin", "discarded · not a test card", C.bad); return viz.fly("e3", { kind: "bad", label: "[[refused: not a test card]]" }); });
      }
      const words = "“" + trunc(cap.caller || ctx.text || "speech", 30) + "”";
      const list = [["e1", { label: words }], ["e2", { label: words }], ["e3", { label: words }]];
      if (ev.mode !== "capture" && !desk) list.push(["e4", { label: "speech · no card data" }]);
      return viz.hops(callerHop(list));
    }
    if (k === "model-first-token") {
      if (desk) return viz.wait(60);
      const list = [["e4", { reverse: true, label: ev.ms != null ? "1st token · " + fmtMs(ev.ms) : "reply" }],
        ["e3", { reverse: true, label: "reply text" }], ["e2", { reverse: true, label: "reply text" }]];
      if (!live) list.push(["e1", { reverse: true, label: "agent speaks" }]);
      return viz.hops(list);
    }
    if (k === "capture-reply") {
      const say = "“" + trunc(cap.agent || ev.say || "scripted line", 34) + "”";
      const list = [["e3", { reverse: true, label: say }], ["e2", { reverse: true, label: say }]];
      if (!live) list.push(["e1", { reverse: true, label: say }]);
      const fin = desk && live && (ev.step === "done" || ev.step === "cancelled");
      return viz.hops(list).then((ok) => (ok && fin ? animate({ kind: "end-call" }, {}) : ok));
    }
    if (k === "capture-open") { viz.flash("n-air", "capture open", C.token); return viz.wait(450); }
    if (k === "charge-start") {
      const b = ev.brand || S.brand, l4 = ev.last4 || S.last4;
      return viz.hops([
        ["e5", { kind: "token", label: "{{ token }} ×" + (ctx.bank ? 2 : 4) + " → " + (ev.gateway || "gateway"), dur: 1100 }],
        ["e6", { kind: "card", label: ctx.bank ? "bank account · inside vault" : (b ? b + " " : "") + "•••• " + (l4 || "") + " · inside vault", dur: 1100 }],
      ]);
    }
    if (k === "charge") {
      const ok = ev.status === "approved", kind = ok ? "ok" : "bad";
      return viz.hops([
        ["e6", { reverse: true, kind, label: `${ev.status || "reply"}${ev.ms != null ? " · " + fmtMs(ev.ms) : ""}`, dur: 1000 }],
        ["e5", { reverse: true, kind, label: `${ev.status || "reply"} · ${ctx.bank ? "account" : (ev.brand || S.brand || "card") + " " + (ev.last4 || S.last4 || "")}` }],
      ]);
    }
    if (k === "refused") { viz.flash("n-air", "refused · 401", C.bad); return viz.wait(900); }
    if (k === "upstream-error") { viz.flash(desk ? "n-air" : "n-llm", "upstream error", C.bad); return viz.wait(900); }
    return viz.wait(80);
  }

  /* live: Airlock's stream is queued so packets never overlap; SDK signals bypass the queue */
  let chain = Promise.resolve(), pending = 0;
  function transferNow() {
    if (MODE !== "desk" || S.transferred) return;
    S.transferred = true;
    const t = { kind: "transfer" };
    evlog.add(t, "(seen on the call)");
    caption("transfer", "transfer_to_agent → payment desk. ElevenLabs sends the whole conversation to whichever agent speaks next, so the desk never hands back.");
    pending++; viz.setBacklog(pending);
    chain = chain.then(() => animate(t, {})).catch(() => {}).finally(() => { pending--; viz.setBacklog(pending); });
  }
  function live(ev) {
    transferNow();
    apply(ev, { live: true });
    if (MODE === "desk" && ev.kind === "capture-reply" && (ev.step === "done" || ev.step === "cancelled")) apply({ kind: "end-call" }, {});
    pending++; viz.setBacklog(pending);
    chain = chain.then(() => animate(ev, { live: true })).catch(() => {}).finally(() => { pending--; viz.setBacklog(pending); });
  }
  let turnAt = 0, spokeThisTurn = false;
  function sdkUser(text, keypad) {
    S.sdkUserAt = performance.now(); turnAt = S.sdkUserAt; spokeThisTurn = false;
    if (keypad) S.lastSent = text;
    const d = text.replace(/\D/g, "");
    const shown = keypad && d.length >= 12 ? d.slice(0, 4) + " •••• •••• " + d.slice(-4) + "#" : keypad ? "•".repeat(Math.min(d.length, 12)) + "#" : text;
    evlog.add({ kind: "sdk" }, keypad ? "caller keyed " + (d.length) + " digits + #" : "caller: " + trunc(text, 60));
    caption("caller", shown);
    inspector.set("c-el", { zone: keypad ? "card" : "clean", json: keypad ? { input: "keypad (web stand-in: one text message)", text: shown } : { input: "caller", text }, meta: keypad ? "digits masked on this page" : "" });
    const edge = MODE === "desk" && !S.transferred ? "e0" : "e1";
    viz.fly(edge, { kind: keypad ? "card" : "speech", label: keypad ? shown : "“" + trunc(text, 30) + "”", speed: 1.3 });
  }
  function sdkAgent(text) {
    if (text) caption("agent", text);
    const pre = MODE === "desk" && !S.transferred, edge = pre ? "e0" : "e1";
    if (!spokeThisTurn) {
      spokeThisTurn = true;
      if (turnAt) viz.latency(edge, "heard in " + fmtMs(performance.now() - turnAt));
      viz.fly(edge, { reverse: true, label: text ? "“" + trunc(text, 30) + "”" : "agent speaking", speed: 1.3 });
    }
    if (pre && /secure payment line|payment desk/i.test(text || "")) transferNow();
  }
  function sdkSpeaking(on) { viz.speaking(on, MODE === "desk" && !S.transferred ? "n-main" : "n-el"); if (on) sdkAgent(""); }
  function sdkAgentDone() { spokeThisTurn = false; turnAt = 0; }
  return { apply, animate, reset, live, sdkUser, sdkAgent, sdkSpeaking, sdkAgentDone };
})();

/* ------------------------------------------------------------------- replay */
let EVIDENCE = null;
const evidenceReady = fetch("./data/evidence.json").then((r) => { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
  .then((d) => { EVIDENCE = d; return d; });

function stepFromSay(say) {
  const s = say || "";
  if (/already went through/i.test(s)) return { step: "done", status: "duplicate" };
  if (/went through/i.test(s)) return { step: "done", status: "approved" };
  if (/declined/i.test(s)) return /type your card number/i.test(s) ? { step: "pan", status: "declined" } : { step: "done", status: "declined" };
  if (/cancelled/i.test(s)) return { step: "cancelled" };
  if (/press 1 to (pay|authorize)/i.test(s)) return { step: "confirm" };
  if (/save this (card|account)/i.test(s)) return { step: "save" };
  if (/checking or a savings/i.test(s)) return { step: "acct_type" };
  if (/type your account number/i.test(s)) return { step: "account" };
  if (/routing number/i.test(s)) return { step: "routing" };
  if (/ZIP/.test(s)) return { step: "zip" };
  if (/security code/i.test(s)) return { step: "cvv" };
  if (/expiration/i.test(s)) return { step: "exp" };
  if (/type your card number/i.test(s)) return { step: "pan" };
  return { step: null };
}

function runToEvents(run) {
  const out = []; let capture = false, ci = 0, awaitingConfirm = false, brand = "", last4 = "";
  const bank = !!run.bank;
  run.steps.forEach((st) => {
    const rec = st.airlock_received || "";
    const cap = { caller: st.caller_sent_redacted, agent: st.agent_said, received: rec, ttfb: st.ttfb_ms };
    const meta = stepFromSay(st.agent_said);
    if (rec.startsWith("[[airlock:kp")) {
      const m = (k) => (new RegExp("\\b" + k + "=([^\\s\\]]+)").exec(rec) || [])[1];
      const ev = { kind: "turn", field: "keypad-token", mode: "capture" };
      if (m("len")) ev.len = +m("len");
      if (m("brand")) { ev.brand = m("brand"); brand = ev.brand; }
      if (m("last4")) { ev.last4 = m("last4"); last4 = ev.last4; }
      out.push([ev, { cap, bank }]);
    } else if (rec.includes("[[airlock:spoken]]")) {
      out.push([{ kind: "turn", field: "spoken-card-removed", mode: capture ? "capture" : "model" }, { cap, bank }]);
    } else {
      out.push([{ kind: "turn", field: "speech", mode: capture ? "capture" : "model" }, { cap, bank }]);
    }
    if (!capture) {
      out.push([{ kind: "model-first-token", ms: st.ttfb_ms }, { cap: { agent: st.agent_said, capture: !!meta.step }, bank }]);
      if (meta.step && meta.step !== "cancelled") { out.push([{ kind: "capture-open" }, { bank }]); capture = true; }
    }
    if (capture && awaitingConfirm && (st.caller_sent_redacted || "").trim() === "1" && run.charges[ci]) {
      const ch = run.charges[ci++], rep = ch.vault_reply_to_airlock || {};
      out.push([{ kind: "charge-start", hop: "vault-out", gateway: rep.gateway, brand: bank ? undefined : brand, last4: bank ? undefined : last4 }, { charge: ch, bank }]);
      out.push([{ kind: "charge", status: rep.status, gateway: rep.gateway, brand: bank ? undefined : brand, last4: bank ? undefined : last4 }, { charge: ch, bank }]);
    }
    awaitingConfirm = meta.step === "confirm";
    if (capture) {
      const ev = { kind: "capture-reply", step: meta.step || undefined, say: st.agent_said, ms: st.total_ms };
      if (meta.status) ev.status = meta.status;
      out.push([ev, { cap, bank }]);
      if (meta.step === "done" || meta.step === "cancelled") capture = false;
    }
  });
  return out;
}

// The recorded runs were captured in full-conversation mode. Airlock's capture is identical on the
// payment desk, so the desk view replays the same recorded capture steps on the desk path: turns before
// the capture are shown going to the main agent, then a transfer, then the call ends after the result.
function deskify(list) {
  const out = []; let inCap = false;
  for (const [ev, ctx] of list) {
    if (!inCap) {
      if (ev.kind === "turn") { out.push([{ kind: "main-agent" }, { cap: { caller: ctx.cap && ctx.cap.caller } }]); continue; }
      if (ev.kind === "model-first-token") { out.push([{ kind: "main-agent-reply" }, {}]); continue; }
      if (ev.kind === "capture-open") { out.push([{ kind: "transfer" }, {}]); out.push([ev, ctx]); inCap = true; continue; }
      continue;
    }
    out.push([ev, ctx]);
    if (ev.kind === "capture-reply" && (ev.step === "done" || ev.step === "cancelled")) { out.push([{ kind: "end-call" }, {}]); break; }
  }
  return out;
}

function echeckRun(ec) {
  const steps = ec.turns.filter((t) => t.s != null).map((t) => {
    const kp = /keypad/.test(t.caller);
    return { caller_sent_redacted: t.caller, airlock_received: kp ? "[[airlock:kp …]]" : t.caller, agent_said: t.agent, ttfb_ms: Math.round(t.s * 1000), total_ms: Math.round(t.s * 1000) };
  });
  return { scenario: "echeck", conversation: ec.conversation_id, bank: true, steps,
    charges: [{ synthetic: true, vault_reply_to_airlock: { status: "approved", gateway: "nmi" } }],
    greeting: (ec.turns[0] && ec.turns[0].s == null) ? ec.turns[0].agent : "" };
}

const player = (() => {
  const btn = $("#btn-replay"), lbl = $("#pl-lbl"), ico = $("#pl-ico"), pick = $("#replay-pick"),
    scrub = $("#pl-scrub"), pos = $("#pl-pos"), pill = $("#live-pill"), pillT = $("#live-pill-t"), src = $("#wf-src");
  const LABEL = { "approve-save": "Card: approve + save", "decline-then-second-card": "Card: decline, then a second card", "hostile": "Hostile caller", "echeck": "eCheck through the live agent" };
  const PLAY = '<path d="M7 4.5v15a1 1 0 0 0 1.52.85l12-7.5a1 1 0 0 0 0-1.7l-12-7.5A1 1 0 0 0 7 4.5Z"/>';
  const PAUSE = '<rect x="6" y="4" width="4" height="16" rx="1"/><rect x="14" y="4" width="4" height="16" rx="1"/>';
  let runs = [], list = [], idx = 0, playing = false, gen = 0, loaded = -1, running = false;

  let all = [];
  function options() {
    // the hostile run opens with a card read aloud to the model, which only the full mode routes through the vault
    runs = MODE === "desk" ? all.filter((r) => r.scenario !== "hostile") : all.slice();
    pick.innerHTML = runs.map((r, i) => `<option value="${i}">${esc(LABEL[r.scenario] || r.scenario)}</option>`).join("");
    loaded = -1; list = []; idx = 0; ui();
  }
  evidenceReady.then((d) => {
    all = d.runs.slice();
    if (d.echeck_agent_run) all.push(echeckRun(d.echeck_agent_run));
    options();
  }).catch(() => { btn.disabled = true; pick.innerHTML = "<option>Evidence unavailable</option>"; });

  function ui() {
    ico.innerHTML = playing ? PAUSE : PLAY;
    lbl.textContent = playing ? "Pause" : (loaded >= 0 && idx > 0 && idx < list.length ? "Resume" : "Replay a real run");
    btn.setAttribute("aria-label", playing ? "Pause replay" : "Play replay");
    scrub.max = String(list.length); scrub.value = String(idx);
    pos.textContent = `${idx} / ${list.length}`;
  }
  function setPill(mode, text) { pill.className = "live-pill" + (mode ? " " + mode : ""); pillT.textContent = text; }

  function load(i) {
    gen++; running = false;
    const run = runs[i]; if (!run) return false;
    list = runToEvents(run); if (MODE === "desk") list = deskify(list); idx = 0; loaded = i;
    flow.reset(); tracker.reset(!!run.bank);
    src.textContent = "replay · " + run.conversation;
    if (run.greeting) caption("agent", run.greeting);
    else caption("replay", "Recorded run against the sandboxes · " + (LABEL[run.scenario] || run.scenario));
    if (MODE === "desk") caption("replay", "Recorded capture steps (" + (LABEL[run.scenario] || run.scenario) + "), shown on the payment-desk path. Airlock's capture is the same in both modes.");
    ui();
    return true;
  }
  async function loop() {
    if (running) return;
    running = true;
    const my = gen;
    while (my === gen && idx < list.length) {
      const [ev, ctx] = list[idx];
      flow.apply(ev, ctx);
      idx++; ui();
      const ok = await flow.animate(ev, ctx);
      if (my !== gen) return;
      if (ok === false && my !== gen) return;
      await viz.wait(260);
      if (my !== gen) return;
    }
    if (my === gen) {
      running = false; playing = false; viz.setPaused(false); setPill("", "Replay done"); ui();
      caption("done", "That was a recorded run. Every payload in the inspector matches the evidence further down this page.");
    }
  }
  function play() {
    if (call.active) return;
    if (loaded !== +pick.value || idx >= list.length) load(+pick.value || 0);
    playing = true; viz.setPaused(false); setPill("replay", "Replay"); ui();
    loop();
  }
  function pause() { playing = false; viz.setPaused(true); setPill("replay", "Paused"); ui(); }
  function seek(k) {
    if (loaded !== +pick.value) load(+pick.value || 0);
    gen++; running = false;
    flow.reset(); tracker.reset(!!runs[loaded].bank);
    k = Math.max(0, Math.min(list.length, k));
    for (let i = 0; i < k; i++) flow.apply(list[i][0], list[i][1]);
    viz.resetScene();
    // re-apply quietly so badges and status lines reflect position k
    for (let i = 0; i < k; i++) { const [ev, ctx] = list[i]; if (ev.kind === "model-first-token" && ev.ms != null) viz.latency("e4", "1st token " + fmtMs(ev.ms)); if (ev.kind === "capture-reply" && ev.ms != null) viz.latency("e3", "turn " + fmtMs(ev.ms)); if (ctx.cap && ctx.cap.ttfb && ev.kind === "turn") viz.latency("e2", "round trip " + fmtMs(ctx.cap.ttfb)); }
    idx = k; ui();
    if (playing) { viz.setPaused(false); loop(); } else setPill("replay", "Paused");
  }
  function stop() { gen++; running = false; playing = false; viz.setPaused(false); viz.killAll(); ui(); }

  btn.addEventListener("click", () => (playing ? pause() : play()));
  pick.addEventListener("change", () => { const was = playing; stop(); load(+pick.value); if (was) play(); else setPill("", "Ready"); });
  scrub.addEventListener("input", () => seek(+scrub.value));
  ui();
  function setMode() { stop(); options(); flow.reset(); setPill("", "Ready"); src.textContent = "replay a real run, or start a call"; }
  return { stop, setPill, setMode, get playing() { return playing; } };
})();

/* -------------------------------------------------------------- live call */
const callHooks = { agent: [], end: [], state: [], event: [] };
const fireHook = (k, v) => callHooks[k].forEach((fn) => { try { fn(v); } catch (e) {} });

const call = (() => {
  const bCall = $("#btn-call"), bText = $("#btn-text"), bEnd = $("#btn-end"),
    status = $("#call-status"), dot = $("#call-dot"), state = $("#call-state"),
    tr = $("#transcript"), form = $("#say-form"), input = $("#say-input"), send = $("#say-send");
  let conv = null, es = null, active = false, starting = false, lastLocal = "", esErrors = 0, convId = "", heardAgent = false, connectedAt = 0;

  function setStatus(msg, err) { status.textContent = msg; status.classList.toggle("err", !!err); }
  function setState(s) {
    state.textContent = s;
    dot.className = "dot" + (s === "Connected" || s === "Listening" || s === "Speaking" ? " on" : s === "Connecting…" ? " wait" : s === "Error" ? " err" : "");
  }
  function addMsg(role, text) {
    const empty = $(".empty", tr); if (empty) empty.remove();
    const m = document.createElement("div");
    m.className = "msg " + role; m.textContent = text;
    tr.appendChild(m); tr.scrollTop = tr.scrollHeight;
  }
  function controls() {
    bCall.disabled = active || starting; bText.disabled = active || starting; bEnd.disabled = !active && !starting;
    input.disabled = !active; send.disabled = !active;
    fireHook("state", { active, starting });
  }

  // subscribe to Airlock's per-conversation event stream as soon as the id is known
  function openEvents(id) {
    if (!id || es) return;
    convId = id;
    $("#wf-src").textContent = "live · " + id;
    try {
      es = new EventSource(EVENTS_BASE + encodeURIComponent(id));
      es.onopen = () => { esErrors = 0; evlog.add({ kind: "sdk" }, "subscribed to Airlock's event stream"); };
      es.onmessage = (m) => {
        let ev; try { ev = JSON.parse(m.data); } catch (e) { return; }
        if (!ev || typeof ev !== "object" || typeof ev.kind !== "string") return;
        esErrors = 0;
        flow.live(ev);
        fireHook("event", ev);
      };
      es.onerror = () => {
        esErrors++;
        if (esErrors >= 4 || es.readyState === EventSource.CLOSED) {
          evlog.add({ kind: "err" }, "Airlock event stream unavailable — the call still works; the diagram follows the call's own signals only");
          closeEvents();
        }
      };
    } catch (e) { evlog.add({ kind: "err" }, "event stream unavailable"); }
  }
  function closeEvents() { if (es) { es.close(); es = null; } }

  function explain(err) {
    const s = String((err && (err.message || err.reason)) || err || "");
    if (/permission|notallowed|denied/i.test(s)) return "Microphone access was blocked. Allow the mic, or use Text only.";
    const host = location.hostname;
    if (!/(^|\.)liftedholdings\.com$/.test(host))
      return "Couldn't connect. The live demo runs on liftedholdings.com — or press “Replay a real run”.";
    return "Couldn't connect to the agent" + (s ? ": " + s : ".") + " Try again, or press “Replay a real run”.";
  }

  function goLive() {
    player.stop(); flow.reset(); tracker.reset();
    player.setPill("live", "Live");
    caption("live", "Connected. Every packet now comes from your call.");
  }

  async function start(textOnly, startOpts = {}) {
    if (active || starting) return false;
    starting = true; heardAgent = false; controls(); setState("Connecting…");
    setStatus(textOnly ? "Connecting in text-only mode…" : "Connecting… your browser will ask for the microphone.");
    let failed = false;
    try {
      if (!textOnly) {
        if (!navigator.mediaDevices?.getUserMedia) throw new Error("This browser has no microphone access. Use Text only.");
        const s = await navigator.mediaDevices.getUserMedia({ audio: true });
        s.getTracks().forEach((t) => t.stop());
      }
      const { Conversation } = await import(SDK_URL);
      const opts = {
        agentId: AGENTS[MODE],
        onConnect: ({ conversationId }) => { goLive(); openEvents(conversationId); },
        onDisconnect: (d) => {
          const wasActive = active;
          active = false; starting = false; controls(); closeEvents();
          flow.sdkSpeaking(false);
          setState(failed ? "Error" : "Idle");
          if (!failed) {
            const reason = d && d.reason;
            if (!wasActive && reason === "error") { failed = true; setState("Error"); setStatus(explain(d.message || ""), true); }
            else setStatus(reason === "agent" ? (MODE === "desk" ? "The payment desk ended the call (end_call), as designed." : "The agent ended the call.") : "Call ended.");
          }
          if (wasActive) { addMsg("sys", "call ended"); player.setPill("", "Call ended"); }
          // dropped straight after connecting, before the agent said anything: the agent refused this page
          const refused = wasActive && !heardAgent && performance.now() - connectedAt < 5000;
          if (refused) { setState("Error"); setStatus(explain(""), true); }
          fireHook("end", { reason: d && d.reason, refused: refused || (!wasActive && failed) });
        },
        onError: (message) => {
          if (!active) { failed = true; setState("Error"); setStatus(explain(message), true); }
          else evlog.add({ kind: "err" }, String(message).slice(0, 160));
        },
        onMessage: ({ message, role, source }) => { if ((role || source) !== "user") heardAgent = true;
          const r = role || (source === "ai" ? "agent" : "user");
          if (!message) return;
          if (r === "user") {
            if (message.trim() === lastLocal.trim()) return;
            addMsg("user", message);
            flow.sdkUser(message, /^[\d*#\s]+#$/.test(message.trim()));
          } else {
            addMsg("agent", message);
            flow.sdkAgent(message);
            fireHook("agent", message);
            if (textOnly) flow.sdkAgentDone();
          }
        },
        onModeChange: ({ mode }) => {
          if (!active) return;
          if (!textOnly) setState(mode === "speaking" ? "Speaking" : "Listening");
          flow.sdkSpeaking(mode === "speaking");
          if (mode !== "speaking") flow.sdkAgentDone();
        },
      };
      if (textOnly) opts.textOnly = true;
      opts.quiet = !!startOpts.quiet;
      conv = await Conversation.startSession(opts);
      if (failed) { try { await conv.endSession(); } catch (e) {} conv = null; return false; }
      active = true; starting = false; controls(); connectedAt = performance.now();
      setState(textOnly ? "Connected" : "Listening");
      setStatus(textOnly ? "Text-only call connected. Type below, use the order chips, or the keypad." : "Call connected. Speak, or use the keypad.");
      addMsg("sys", textOnly ? "text-only call started" : "call started");
      if (!convId) goLive();
      let id = ""; try { id = conv.getId(); } catch (e) {}
      openEvents(id);
      if (!opts.quiet) input.focus();
      return true;
    } catch (err) {
      failed = true;
      setState("Error");
      setStatus(explain(err), true);
      conv = null;
      return false;
    } finally {
      starting = false; controls();
    }
  }

  async function end() {
    const c = conv; conv = null;
    const was = active || starting;
    try { if (c) await c.endSession(); } catch (e) {}
    if (was) fireHook("end", { reason: "user", refused: false });
    active = false; starting = false; controls(); closeEvents(); convId = "";
    flow.sdkSpeaking(false);
    setState("Idle"); setStatus("Call ended."); player.setPill("", "Call ended");
  }

  function say(text, keypad) {
    text = String(text || "").trim(); if (!text) return false;
    if (!active || !conv) {
      if (!keypad) { input.value = text; setStatus("Start a call first — then send this.", true); }
      return false;
    }
    lastLocal = text;
    addMsg("user", text);
    flow.sdkUser(text, !!keypad);
    try { conv.sendUserMessage(text); } catch (e) { setStatus("Couldn't send: " + (e.message || e), true); }
    return true;
  }

  function sendKeypad(s) {
    if (!active || !conv) {
      setStatus(`Keyed “${s.length > 4 ? "…" + s.slice(-5) : s}” — start a call, then press # again to send it to the agent.`, true);
      return false;
    }
    return say(s, true);
  }

  bCall.addEventListener("click", () => start(false));
  bText.addEventListener("click", () => start(true));
  bEnd.addEventListener("click", end);
  form.addEventListener("submit", (e) => { e.preventDefault(); const v = input.value; input.value = ""; say(v); });
  $$(".order").forEach((b) => b.addEventListener("click", () => {
    const o = b.dataset.order;
    say(o === "A2001" && $(".inst-tab[data-inst=echeck]").getAttribute("aria-selected") === "true"
      ? `I'd like to pay for order ${o} from my bank account.` : `I'd like to pay for order ${o}.`);
  }));
  window.addEventListener("pagehide", () => { if (conv) try { conv.endSession(); } catch (e) {} });
  controls();
  return { start, end, say, sendKeypad, explain, get active() { return active || starting; } };
})();

/* ------------------------------------------------------------ mode switch */
const modeSwitch = (() => {
  const DESC = {
    desk: `<p><b>Payment desk.</b> Scarlett runs on ElevenLabs' own hosted model — no Custom LLM, no merchant model. Ask about an order, then say “I'd like to pay”: she connects you to the secure payment line with <code>transfer_to_agent</code>. The desk says “You're on the secure payment line. Say ready when you have your card or bank details.” Say <b>ready</b>, then key the card. No language model is called on the desk; Airlock scripts every word, charges through the vault, says goodbye and ends the call.</p>`,
    full: `<p><b>Full conversation.</b> Every turn goes through the vault to Airlock and on to the merchant's own model, so the conversation carries on after the payment. The vault adds its hop to every turn (~0.65 s median).</p>`,
  };
  const NOTE = {
    desk: `<b>The payment path is the only path through the vault.</b> Scarlett's normal turns run on ElevenLabs' hosted model and never touch Airlock. After the transfer the desk scripts every word and ends the call, because ElevenLabs re-sends the whole conversation, typed digits included, to whichever agent speaks next.`,
    full: `<b>The dashed box is the agent's own brain, relocated.</b> With Custom LLM, ElevenAgents hands the thinking to your endpoint; Airlock passes normal turns to the merchant's model and never sends it card data. Voice, turn-taking and keypad input stay in ElevenAgents.`,
  };
  const tabs = $$(".mode-tab"), desc = $("#mode-desc"), note = $("#viz-note");
  function set(m, focus) {
    if (m === MODE && desc.innerHTML) return;
    if (call.active) { desc.insertAdjacentHTML("beforeend", `<p class="mode-warn">End the call before switching modes.</p>`); return; }
    MODE = m;
    tabs.forEach((t) => { const on = t.dataset.mode === m; t.setAttribute("aria-selected", String(on)); t.tabIndex = on ? 0 : -1; if (on && focus) t.focus(); });
    desc.innerHTML = DESC[m]; note.innerHTML = NOTE[m];
    viz.setMode(); inspector.setMode(); player.setMode(); tracker.reset();
    caption("ready", m === "desk" ? "Payment desk: the hero path runs Caller → ElevenAgents → transfer → payment desk → vault → Airlock → vault → gateway, then the call ends." : "Every packet is labelled with what is actually inside it at that hop.");
  }
  tabs.forEach((t) => t.addEventListener("click", () => set(t.dataset.mode)));
  $(".mode-switch").addEventListener("keydown", (e) => {
    if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
    e.preventDefault(); set(MODE === "desk" ? "full" : "desk", true);
  });
  MODE = "desk"; desc.innerHTML = ""; set("desk");
  return { set };
})();

/* ---------------------------------------------------------------- autopilot
   "Watch it pay itself": a real text-only call, driven turn by turn. Each caller
   turn waits for the agent's actual reply and is chosen from the agent's words,
   so the script stays in sync with the live agent. */
const autopilot = (() => {
  const go = $("#ap-go"), stopBtn = $("#ap-stop"), chk = $("#ap-echeck"), st = $("#ap-status"),
    res = $("#ap-result"), link = $("#ap-link"), input = $("#say-input"), form = $("#say-form");
  const ORDERS = { A2001: "12.00", A1042: "84.20", A3003: "250.00" };
  const CARDS = { visa: TEST.visa, mastercard: TEST.mastercard, amex: TEST.amex };
  const KEY = "airlock.autopilot.last";
  let run = null;

  function pickCombo() {
    let last = ""; try { last = localStorage.getItem(KEY) || ""; } catch (e) {}
    const all = [];
    for (const o in ORDERS) for (const c in CARDS) all.push(o + ":" + c);
    const pool = all.filter((x) => x !== last);
    const pick = pool[Math.floor(Math.random() * pool.length)];
    try { localStorage.setItem(KEY, pick); } catch (e) {}
    const [order, card] = pick.split(":");
    return { order, card };
  }
  const say = (t) => { st.textContent = t; };
  const alive = (r) => run === r && !r.done;

  async function typeSpeech(r, text) {
    input.value = "";
    for (const ch of text) { if (!alive(r)) return; input.value += ch; await sleep(reduceMotion() ? 0 : 22); }
    await sleep(250);
    if (!alive(r)) return;
    sent(r, text);
    form.requestSubmit ? form.requestSubmit() : form.dispatchEvent(new Event("submit", { cancelable: true }));
  }
  function highlight(sel) {
    $$(".ap-hl").forEach((n) => n.classList.remove("ap-hl"));
    if (sel) $$(sel).forEach((n) => n.classList.add("ap-hl"));
  }
  async function typeKeys(r, digits, field) {
    const card = $("#lp-creditcard");
    const HL = { pan: "#svgnumber", exp: "#svgexpire", cvv: r.card === "amex" ? ".amexcid" : "#svgsecurity", zip: null, routing: "#chk-micr", account: "#chk-micr" };
    if (card) card.classList.toggle("flipped", field === "cvv" && r.card !== "amex");
    highlight(HL[field] || null);
    keypad.clear();
    for (const d of digits) { if (!alive(r)) return; keypad.press(d); await sleep(reduceMotion() ? 0 : 90); }
    await sleep(220);
    if (!alive(r)) return;
    sent(r, digits + "#");
    keypad.press("#");
    setTimeout(() => { highlight(null); if (card && field === "cvv") card.classList.remove("flipped"); }, 900);
  }
  function sent(r, what) {
    r.waiting = true; r.lastSentAt = performance.now();
    clearTimeout(r.t25); clearTimeout(r.t45);
    r.t25 = setTimeout(() => { if (alive(r) && r.waiting) say("Waiting on the agent…"); }, 25000);
    r.t45 = setTimeout(() => { if (alive(r) && r.waiting) finish(r, "timeout"); }, 45000);
  }

  // what the caller does next, decided from what the agent just said
  function decide(r, text) {
    const t = text || "";
    if (/already went through/i.test(t)) return { done: "duplicate" };
    if (/went through/i.test(t)) return { done: "approved" };
    if (/declined/i.test(t)) return { done: "declined" };
    if (/say ready/i.test(t)) return { speak: "ready" };
    if (/name on (the|your) (bank )?account|account holder'?s? name/i.test(t)) return { speak: ECHECK.name };
    if (/routing number/i.test(t)) return { keys: ECHECK.routing, field: "routing" };
    if (/account number/i.test(t)) return { keys: ECHECK.account, field: "account" };
    if (/checking or a savings/i.test(t)) return { keys: "1", field: "type" };
    if (/save this/i.test(t)) return { keys: "2", field: "save" };
    if (/to confirm|to authori[sz]e/i.test(t)) return { keys: "1", field: "confirm" };
    if (/ZIP/.test(t)) return { keys: CARDS[r.card].zip, field: "zip" };
    if (/security code/i.test(t)) return { keys: CARDS[r.card].cvv, field: "cvv" };
    if (/expiration/i.test(t)) return { keys: CARDS[r.card].exp, field: "exp" };
    if (/card number/i.test(t) && /type|keypad/i.test(t)) return { keys: CARDS[r.card].number, field: "pan" };
    return null;
  }

  function onAgent(text) {
    const r = run; if (!r || r.done) return;
    r.waiting = false; clearTimeout(r.t25); clearTimeout(r.t45);
    r.agentLines.push(text);
    const ref = /ends in ((?:\d\s?){4})/i.exec(text); if (ref) r.ref = ref[1].replace(/\s/g, "");
    // record the outcome at once: the desk may end the call before the reading pause is over
    const dd = r.phase === "paying" ? decide(r, text) : null;
    if (dd && dd.done) r.outcome = dd.done;
    clearTimeout(r.next);
    r.next = setTimeout(() => act(r, text), reduceMotion() ? 400 : 1200);
  }
  async function act(r, text) {
    if (!alive(r) || r.busy) return;
    r.busy = true;
    try {
      if (r.phase === "greet") {
        r.phase = "asked"; say(`Asking about order ${r.order}…`);
        await typeSpeech(r, `What's in order ${r.order}?`);
        return;
      }
      if (r.phase === "asked") {
        r.phase = "paying"; say("Asking to pay…");
        const line = r.echeck ? `I'd like to pay for order ${r.order} from my bank account.`
          : MODE === "desk" ? "Great, I'd like to pay for it." : `I'd like to pay for order ${r.order}.`;
        await typeSpeech(r, line);
        return;
      }
      const d = decide(r, text);
      if (!d) { say("Listening to the agent…"); return; }
      if (d.done) { r.outcome = d.done; say(d.done === "approved" ? "Approved — waiting for the call to end…" : "Finishing…"); if (MODE !== "desk") setTimeout(() => finish(r), 2500); return; }
      if (d.speak) { say(`Saying “${d.speak}”…`); await typeSpeech(r, d.speak); return; }
      if (d.keys) {
        const names = { pan: "card number", exp: "expiry", cvv: "security code", zip: "ZIP", save: "2 (don't save)", confirm: "1 (pay)", routing: "routing number", account: "account number", type: "1 (checking)" };
        say(`Keying the ${names[d.field] || "digits"}…`);
        await typeKeys(r, d.keys, d.field);
      }
    } finally { r.busy = false; }
  }

  function finish(r, why) {
    if (!r || r.done) return;
    r.done = true;
    clearTimeout(r.t25); clearTimeout(r.t45); clearTimeout(r.next);
    highlight(null);
    if (why === "timeout" || why === "stopped") { if (call.active) call.end(); }
    if (why === "local" || (why === "stopped" && !r.agentLines.length)) {
      res.hidden = true; stopBtn.hidden = true; run = null; sync();
      say(why === "local" ? "The live demo runs on liftedholdings.com — or press Replay a real run." : "Stopped before the call started.");
      return;
    }
    const secs = ((performance.now() - r.t0) / 1000).toFixed(1);
    const outcome = r.outcome || (why === "refused" ? "busy" : why === "stopped" ? "stopped" : why === "timeout" ? "timeout" : "ended");
    const LABEL = { approved: "Approved", declined: "Declined", duplicate: "Already paid", busy: "Demo busy", stopped: "Stopped", timeout: "No reply", ended: "Call ended" };
    const amount = ORDERS[r.order];
    const ending = r.echeck ? "account ending " + ECHECK.account.slice(-4) : CARDS[r.card].label + " ending " + CARDS[r.card].number.slice(-4);
    let note = "";
    if (outcome === "duplicate") note = "NMI's sandbox refuses the same card and amount for about 20 minutes, whatever the order id — duplicate protection, working as designed. Run it again: the next run picks a different order and card.";
    else if (outcome === "busy") note = "The demo is busy — try Replay a real run.";
    else if (outcome === "timeout") note = "The agent stopped replying, so the run was ended. Try again, or Replay a real run.";
    res.innerHTML = `<div class="apr-h"><span class="apr-out ${esc(outcome)}">${esc(LABEL[outcome] || outcome)}</span><span class="apr-sub">${esc(MODE === "desk" ? "Payment desk" : "Full conversation")} · real call</span></div>
      <dl class="apr-grid">
        <div><dt>Amount</dt><dd>$${esc(amount)} · order ${esc(r.order)}</dd></div>
        <div><dt>Paid with</dt><dd>${esc(ending)}</dd></div>
        <div><dt>Reference</dt><dd>${r.ref ? "…" + esc(r.ref) : "—"}</dd></div>
        <div><dt>Call time</dt><dd>${esc(secs)} s</dd></div>
        <div><dt>Live events</dt><dd>${r.events} from Airlock</dd></div>
      </dl>${note ? `<p class="apr-note">${esc(note)}</p>` : ""}
      <button class="btn sm" type="button" id="ap-again">Run it again</button>`;
    res.hidden = false;
    $("#ap-again").addEventListener("click", start);
    say(outcome === "approved" ? "Done: a real payment, start to finish, with no one at the keyboard." : (note || "Done."));
    stopBtn.hidden = true;
    run = null;
    sync();
  }

  async function start() {
    if (run || call.active) return;
    const echeck = !!chk.checked;
    const combo = echeck ? { order: "A2001", card: "echeck" } : pickCombo();
    run = { ...combo, echeck, phase: "greet", t0: performance.now(), events: 0, agentLines: [], done: false, outcome: null, ref: "" };
    const r = run;
    res.hidden = true;
    stopBtn.hidden = false;
    sync();
    // show the instrument being used
    const tab = $(`.inst-tab[data-inst="${echeck ? "echeck" : combo.card}"]`); if (tab) tab.click();
    say(`Calling the ${MODE === "desk" ? "payment desk demo" : "full-conversation demo"} — order ${combo.order}, ${echeck ? "eCheck" : CARDS[combo.card].label} test card…`);
    sent(r, "connect");
    const ok = await call.start(true, { quiet: true });
    if (!ok && alive(r)) {
      const local = !/(^|\.)liftedholdings\.com$/.test(location.hostname);
      finish(r, local ? "local" : "refused");
    }
  }
  function stop() { if (run) finish(run, "stopped"); else if (call.active) call.end(); }

  function sync() {
    const busy = !!run || call.active;
    go.disabled = busy;
    link.classList.toggle("disabled", busy);
    link.setAttribute("aria-disabled", String(busy));
    chk.disabled = busy;
  }

  callHooks.agent.push(onAgent);
  callHooks.event.push(() => { if (run) run.events++; });
  callHooks.end.push((e) => {
    const r = run; if (!r || r.done) return;
    setTimeout(() => finish(r, e && e.refused ? "refused" : "ended"), 600);
  });
  callHooks.state.push(sync);
  go.addEventListener("click", start);
  stopBtn.addEventListener("click", stop);
  link.addEventListener("click", (e) => {
    e.preventDefault();
    if (run || call.active) return;
    $("#phone").scrollIntoView({ behavior: reduceMotion() ? "auto" : "smooth", block: "start" });
    setTimeout(start, reduceMotion() ? 0 : 700);
  });
  sync();
  return { start, stop, get running() { return !!run; } };
})();

/* --------------------------------------------------------------- evidence */
function jsonHtml(obj) {
  const s = JSON.stringify(obj, null, 2);
  return esc(s).replace(/(&quot;(?:[^&]|&(?!quot;))*?&quot;)(\s*:)?|\b(true|false|null)\b|(-?\b\d+(?:\.\d+)?\b)/g, (m, str, colon, bool, num) => {
    if (str) return colon ? `<span class="jk">${str}</span>${colon}` : `<span class="js">${str}</span>`;
    if (bool) return `<span class="jb">${bool}</span>`;
    if (num) return `<span class="jn">${num}</span>`;
    return m;
  });
}

evidenceReady.then((d) => {
  const LABEL = { "approve-save": "Approve and save the card", "decline-then-second-card": "Decline, then a second card", "hostile": "Hostile caller: spoken card, injection, bad number, cancel" };
  const runs = d.runs.map((run) => {
    const steps = run.steps.map((st, i) => `
      <details class="step">
        <summary><span class="sn">${String(i + 1).padStart(2, "0")}</span><span class="st">${esc(st.caller_sent_redacted)}</span><span class="sm">${esc(st.ttfb_ms)} ms</span></summary>
        <div class="hops">
          <div class="hop in"><div class="hl"><span>What the caller typed (redacted)</span></div><div class="hv">${esc(st.caller_sent_redacted)}</div></div>
          <div class="hop at"><div class="hl"><span>What Airlock actually received</span></div><div class="hv">${esc(st.airlock_received)}</div></div>
          <div class="hop"><div class="hl"><span>What the agent said</span><span>first byte ${esc(st.ttfb_ms)} ms · total ${esc(st.total_ms)} ms</span></div><div class="hv" style="font-family:var(--sans);font-size:14px">${esc(st.agent_said)}</div></div>
        </div>
      </details>`).join("");
    const charges = (run.charges || []).map((c, i) => {
      const r = c.vault_reply_to_airlock || {};
      return `
      <details class="ev" style="margin:8px 0 0">
        <summary>Charge ${i + 1} <span class="pill ${r.status === "approved" ? "ok" : "no"}">${esc(r.status)}</span><span class="sm">${esc(r.gateway)} · ${esc(c.airlock_to_vault_request?.amount)} ${esc(c.airlock_to_vault_request?.currency)}</span></summary>
        <div class="ev-body">
          <div class="ev-sub">Airlock → vault outbound proxy (token expressions only)</div>
          <pre class="json">${jsonHtml(c.airlock_to_vault_request)}</pre>
          <div class="ev-sub">Vault → Airlock (reduced gateway reply)</div>
          <pre class="json">${jsonHtml(r)}</pre>
        </div>
      </details>`;
    }).join("");
    return `
    <details class="ev">
      <summary>${esc(LABEL[run.scenario] || run.scenario)}<span class="sm">${run.steps.length} steps · ${(run.charges || []).length} charge${(run.charges || []).length === 1 ? "" : "s"}</span></summary>
      <div class="ev-body">
        <p>Scenario <code>${esc(run.scenario)}</code> · conversation <code>${esc(run.conversation)}</code></p>
        ${steps}
        ${charges ? `<div class="ev-sub">Charges</div>${charges}` : `<div class="ev-sub">Charges</div><p style="font-size:14px;color:var(--muted);margin:0">None — nothing was charged.</p>`}
      </div>
    </details>`;
  }).join("");

  const vt = d.live_vault_tests || [];
  const passed = vt.filter((t) => t.result === "PASS").length;
  const vault = `
    <details class="ev">
      <summary>Live vault tests<span class="sm">${passed}/${vt.length} pass</span></summary>
      <div class="ev-body">
        <p>Run against the Basis Theory test tenant with the real keys each component holds.</p>
        <div class="tbl-wrap" style="margin:0"><table><thead><tr><th>Result</th><th>Check</th><th>Evidence</th></tr></thead><tbody>
        ${vt.map((t) => `<tr><td><span class="pill ${t.result === "PASS" ? "ok" : "no"}">${esc(t.result)}</span></td><td>${esc(t.check.replace(/^\d+\s+/, ""))}</td><td><code>${esc(t.evidence)}</code></td></tr>`).join("")}
        </tbody></table></div>
      </div>
    </details>`;

  const ec = d.echeck_agent_run;
  const echeck = ec ? `
    <details class="ev">
      <summary>${esc(ec.title)}<span class="sm">${ec.turns.length} turns</span></summary>
      <div class="ev-body">
        <p>Conversation <code>${esc(ec.conversation_id)}</code> · ${esc(ec.date || "")}. ${esc(ec.note || "")}</p>
        <div class="tbl-wrap" style="margin:0"><table><thead><tr><th>Caller</th><th>Agent</th><th class="num">s</th></tr></thead><tbody>
        ${ec.turns.map((t) => `<tr><td><code>${esc(t.caller)}</code></td><td>${esc(t.agent)}</td><td class="num">${t.s == null ? "—" : esc(t.s)}</td></tr>`).join("")}
        </tbody></table></div>
      </div>
    </details>` : "";

  $("#evidence").innerHTML = runs + vault + echeck +
    `<p style="font-size:13.5px;color:var(--muted);margin-top:14px">Generated ${esc(d.generated)}. ${esc(d.environment)}</p>`;

  // eCheck turn timing bars
  if (ec) {
    const turns = ec.turns.filter((t) => t.s != null);
    const max = Math.max(...turns.map((t) => t.s));
    $("#echeck-bars").innerHTML = turns.map((t) => {
      const l = t.caller.replace(/\s+\(.*\)$/, "").replace(/•+/, "••• ");
      const what = /routing/.test(t.caller) ? "Routing number (keypad)" : /account, keypad/.test(t.caller) ? "Account number (keypad)" : l;
      return `<div class="bar-row"><span class="bl" title="${esc(t.caller)}">${esc(what)}</span><span class="bt"><span class="bf" style="width:${(t.s / max * 100).toFixed(1)}%;display:block"></span></span><span class="bv">${esc(t.s)} s</span></div>`;
    }).join("");
    $("#echeck-cap").textContent = `Seconds per turn in the eCheck call through the live agent (${ec.conversation_id}). ${ec.note}`;
  }
}).catch(() => {
  $("#evidence").innerHTML = `<p style="color:var(--muted)">The evidence file could not be loaded.</p>`;
});
