// Airlock vault edge — inbound request transform.
//
// Runs inside the vault provider's PCI DSS Level 1 environment on EVERY request that
// ElevenAgents sends to the agent's Custom LLM URL, before the request is forwarded to
// Airlock. It is the only Airlock code that ever sees card digits.
//
//   * The newest user turn, if it is a keypad entry of 3+ digits, becomes a short-lived
//     vault token and is replaced by a placeholder carrying only safe metadata.
//   * Earlier keypad turns (ElevenAgents re-sends the whole history every turn) become
//     [[airlock:kp-prior]]; no new token is made.
//   * Any card-like number anywhere in any message (typed, spoken, spaced, in words) is
//     replaced by [[airlock:spoken]] and never forwarded.
//   * The forwarded request is signed so Airlock can reject anything that skipped the edge.
//   * On any error the transform answers the caller itself and forwards nothing.
//
// Contract: module.exports = async (req) => ({ body, headers }) | ({ res: {...} }).
// `req.bt` is the vault SDK client (token:create only). `req.configuration` holds
// EDGE_SECRET and TOKEN_TTL_SECONDS. Pure helpers are exported for the test-suite.

"use strict";

const crypto = require("crypto");

const KEYPAD = /^[0-9*#\s]+$/;
const WORDS = {
  zero: "0", oh: "0", o: "0", nought: "0", one: "1", won: "1", two: "2", to: "2", too: "2",
  three: "3", four: "4", for: "4", five: "5", six: "6", seven: "7", eight: "8", ate: "8",
  nine: "9", niner: "9",
};
const MULT = { double: 2, triple: 3 };
// Published gateway sandbox test cards (NMI + Authorize.net). Only these pass in demo mode.
const TEST_CARDS = new Set([
  "4111111111111111", "4007000000027", "4012888818888", "4242424242424242",
  "5424000000000015", "5431111111111111", "2223000010309703", "2223000010309711",
  "370000000000002", "341111111111111", "378282246310005",
  "6011000000000012", "6011601160116611", "3088000000000017", "38000000000006",
]);
// Digits optionally separated by single spaces or dashes. Not dots: decimals and timestamps
// ("1791292673.7754595") would otherwise read as card numbers.
// Separators allowed between digits: 1-3 of space , / - (speech-to-text writes "4111, 1111"),
// or a dot followed by a space. A dot directly between digits is never a separator, so
// decimals and timestamps don't read as cards. Must stay identical to airlock/core/cards.py.
const CARDLIKE = /(?<![\d.])(?:\d(?:[ ,\/\-]{1,3}|\.[ ]{1,2})?){12,18}\d(?![\d.]\d)/g;

// Map any Unicode decimal digit (full-width, Arabic-Indic, Devanagari, ...) to ASCII.
// A digit's value is its offset from the start of its contiguous run of digits, mod 10.
const ND = /\p{Nd}/u;
function asciiDigits(s) {
  s = String(s || "");
  if (/^[\x00-\x7F]*$/.test(s)) return s;
  return s.replace(/\p{Nd}/gu, (ch) => {
    const cp = ch.codePointAt(0);
    if (cp < 128) return ch;
    let z = cp;
    while (z > 0 && ND.test(String.fromCodePoint(z - 1))) z--;
    return String((cp - z) % 10);
  });
}

function luhn(d) {
  let sum = 0;
  for (let i = 0; i < d.length; i++) {
    let n = d.charCodeAt(d.length - 1 - i) - 48;
    if (i % 2 === 1) { n *= 2; if (n > 9) n -= 9; }
    sum += n;
  }
  return d.length > 0 && sum % 10 === 0;
}

function brand(d) {
  const p2 = +d.slice(0, 2), p3 = +d.slice(0, 3), p4 = +d.slice(0, 4);
  if (/^3[47]/.test(d)) return "amex";
  if (/^4/.test(d)) return "visa";
  if ((p2 >= 51 && p2 <= 55) || (p4 >= 2221 && p4 <= 2720)) return "mastercard";
  if (/^(6011|65)/.test(d) || (p3 >= 644 && p3 <= 649)) return "discover";
  if (p4 >= 3528 && p4 <= 3589) return "jcb";
  if (/^(36|38|30[0-5])/.test(d)) return "diners";
  return "unknown";
}

function wordsToDigits(text) {
  const parts = String(text || "").split(/(\s+|[,.;:!?\-"'(){}\[\]])/);
  const out = [];
  let pending = 1;
  for (const raw of parts) {
    const w = raw.toLowerCase().trim();
    if (!w) { out.push(raw); continue; }
    if (MULT[w]) { pending = MULT[w]; continue; }
    if (WORDS[w] !== undefined) { out.push(WORDS[w].repeat(pending)); pending = 1; continue; }
    if (/^\d+$/.test(w)) { out.push(w[0].repeat(pending - 1) + w); pending = 1; continue; }
    pending = 1;
    out.push(raw);
  }
  return out.join("").replace(/(?<=\d)\s+(?=\d)/g, "");
}

function hasCardlike(text) {
  text = asciiDigits(text);
  for (const t of [text, wordsToDigits(text)]) {
    for (const m of t.matchAll(CARDLIKE)) {
      const d = m[0].replace(/\D/g, "");
      if (d.length >= 13 && d.length <= 19 && luhn(d)) return true;
    }
  }
  return false;
}

// Remove card numbers said aloud while keeping the rest of the sentence ("pay order A1042,
// my card is 4111 ...": the order id survives). If the number was only recognisable as
// spoken words, the whole message is dropped: words can't be cut out reliably.
function stripSpoken(text) {
  let hit = false;
  const cut = String(text).replace(CARDLIKE, (run) => {
    const d = run.replace(/\D/g, "");
    if (d.length >= 13 && d.length <= 19 && luhn(d)) { hit = true; return "[[airlock:spoken]]"; }
    return run;
  });
  if (hit && !hasCardlike(cut.replace(/\[\[airlock:[^\]]*\]\]/g, " "))) return cut;
  return "[[airlock:spoken]]";
}

function aba(d) {
  if (!/^\d{9}$/.test(d)) return false;
  const w = [3, 7, 1, 3, 7, 1, 3, 7, 1];
  let sum = 0;
  for (let i = 0; i < 9; i++) sum += w[i] * (d.charCodeAt(i) - 48);
  return sum % 10 === 0 && d !== "000000000";
}

function expok(d, now) {
  let mm, yy;
  if (d.length === 4) { mm = +d.slice(0, 2); yy = 2000 + +d.slice(2); }
  else if (d.length === 6) { mm = +d.slice(0, 2); yy = +d.slice(2); }
  else return null;
  const y = now.getUTCFullYear(), m = now.getUTCMonth() + 1;
  if (mm < 1 || mm > 12) return false;
  if (yy < y || (yy === y && mm < m) || yy > y + 20) return false;
  return true;
}

function textOf(content) {
  if (typeof content === "string") return asciiDigits(content);
  if (Array.isArray(content)) return asciiDigits(content.map((p) => (p && p.text) || "").join(" "));
  return "";
}

function isKeypad(text) {
  const t = String(text || "").replace(/\s/g, "");
  return t.length >= 3 && KEYPAD.test(t);
}

function metadata(digits, now) {
  const meta = { len: digits.length, luhn: luhn(digits) ? 1 : 0 };
  if (digits.length >= 13 && digits.length <= 19 && meta.luhn) {
    meta.last4 = digits.slice(-4);
    meta.brand = brand(digits);
  }
  const e = expok(digits, now);
  if (e !== null) meta.expok = e ? 1 : 0;
  // Bank details (eCheck): ABA checksum for 9-digit entries; last four for 8+ digit entries
  // (account numbers). Never for 3-6 digit entries, which may be a security code or expiry.
  if (digits.length === 9) meta.aba = aba(digits) ? 1 : 0;
  if (!meta.last4 && digits.length >= 8) meta.last4 = digits.slice(-4);
  return meta;
}

function placeholder(id, meta) {
  const kv = Object.entries(meta).map(([k, v]) => `${k}=${v}`).join(" ");
  return `[[airlock:kp id=${id} ${kv}]]`;
}

function sign(secret, body, ts) {
  return crypto.createHmac("sha256", secret).update(`${ts}.${body}`).digest("hex");
}

function failClosed(reason) {
  // Answer ElevenAgents directly with a spoken apology, as a Chat Completions SSE stream.
  const chunk = (delta, finish) =>
    `data: ${JSON.stringify({ id: "airlock-edge", object: "chat.completion.chunk", created: 0, model: "airlock-edge", choices: [{ index: 0, delta, finish_reason: finish || null }] })}\n\n`;
  const body =
    chunk({ role: "assistant", content: "I'm sorry, I can't take a card payment right now. I can send you a secure payment link instead." }) +
    chunk({}, "stop") + "data: [DONE]\n\n";
  return { res: { statusCode: 200, headers: { "Content-Type": "text/event-stream", "X-Airlock-Edge-Error": reason }, body } };
}

async function transform(req) {
  const { args, configuration, bt } = req;
  try {
    const now = new Date();
    const ttl = +(configuration.TOKEN_TTL_SECONDS || 120);
    const body = typeof args.body === "string" ? JSON.parse(args.body) : args.body;
    // Keep-warm ping: answered here, never forwarded (keeps the transform runtime hot).
    if (body && body.airlock_ping === true) {
      return { res: { statusCode: 200, headers: { "Content-Type": "application/json" }, body: JSON.stringify({ pong: true }) } };
    }
    const msgs = Array.isArray(body.messages) ? body.messages : [];
    const lastUser = msgs.map((m) => m && m.role).lastIndexOf("user");

    for (let i = 0; i < msgs.length; i++) {
      const m = msgs[i];
      const text = textOf(m.content);
      const isLast = i === lastUser && i === msgs.length - 1;
      if (m.role === "user" && isKeypad(text)) {
        if (!isLast) { m.content = "[[airlock:kp-prior]]"; continue; }
        if (text.includes("*")) { m.content = "*"; continue; }
        const digits = text.replace(/\D/g, "");
        // Public-demo safety: when TEST_CARDS_ONLY is set, a real-looking card number that is not a
        // published sandbox test card is dropped here and never tokenized or forwarded.
        if (configuration.TEST_CARDS_ONLY === "1" && digits.length >= 13 && luhn(digits) && !TEST_CARDS.has(digits)) {
          m.content = "[[airlock:not-test-card]]";
          continue;
        }
        const meta = metadata(digits, now);
        const token = await bt.tokens.create({
          type: "token",
          data: digits,
          expires_at: new Date(now.getTime() + ttl * 1000).toISOString(),
          metadata: { source: "airlock-keypad" },
        });
        m.content = placeholder(token.id, meta);
        continue;
      }
      if (hasCardlike(text)) m.content = stripSpoken(text);
    }

    const out = JSON.stringify(body);
    // Defence in depth: refuse to forward anything still card-like.
    if (hasCardlike(out.replace(/\[\[airlock:[^\]]*\]\]/g, ""))) return failClosed("residual-card-data");
    const ts = Math.floor(now.getTime() / 1000).toString();
    const headers = { ...(args.headers || {}) };
    headers["X-Airlock-Edge-Ts"] = ts;
    headers["X-Airlock-Edge-Sig"] = sign(configuration.EDGE_SECRET, out, ts);
    return { body: out, headers };
  } catch (e) {
    return failClosed("transform-error");
  }
}

// Basis Theory node24 entry point. The vault injects `event.req` (body may arrive parsed),
// `event.configuration`, and `event.applicationOptions` (the transform's own scoped key,
// granted token:create only via runtime.permissions).
async function basisTheory(event) {
  const { req, configuration, applicationOptions } = event;
  const { BasisTheoryClient } = require("@basis-theory/node-sdk");
  const client = new BasisTheoryClient({ apiKey: applicationOptions.apiKey, baseUrl: applicationOptions.baseUrl });
  const out = await transform({
    args: { body: typeof req.body === "string" ? req.body : JSON.stringify(req.body), headers: req.headers || {} },
    configuration,
    bt: { tokens: { create: (t) => client.tokens.create(t) } },
  });
  if (out.res) return { res: out.res };
  return { req: { headers: out.headers, body: out.body } };
}

module.exports = basisTheory;
module.exports.transform = transform;
module.exports.helpers = { asciiDigits, aba, luhn, brand, wordsToDigits, hasCardlike, stripSpoken, expok, isKeypad, metadata, sign };
