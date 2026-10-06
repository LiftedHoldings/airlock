// Airlock vault edge — outbound proxy transforms (request + response), one file for
// both gateways. Configured on a pre-configured outbound proxy whose destination URL
// is fixed to the gateway (NMI transact.php or Authorize.net request.api), so card data
// can only ever leave the vault toward that gateway.
//
// Request: Airlock sends JSON whose card fields are detokenization expressions, e.g.
//   {"order_id":"A-1042","amount":"84.20","currency":"USD",
//    "pan":"{{ <token> }}","exp":"{{ <token> }}","cvv":"{{ <token> }}","zip":"{{ <token> }}",
//    "save_card":true}
// The vault replaces the expressions with the real values before this transform runs.
// This transform validates every field, then builds the gateway's native body itself.
// Gateway credentials come from the proxy configuration, never from Airlock.
//
// Response: reduce the gateway reply to a normalized result; refuse anything card-like.

"use strict";

// Merchant order ids: letters, digits, dashes, max 20. A card number smuggled in as an order id
// is refused by the card-data scan of every non-card field below.
const ORDER_ID = /^[A-Za-z0-9][A-Za-z0-9-]{0,19}$/;
const AMOUNT = /^\d{1,6}\.\d{2}$/;
const CARDLIKE = /(?<![\d.])(?:\d(?:[ ,\/\-]{1,3}|\.[ ]{1,2})?){12,18}\d(?![\d.]\d)/g;

function luhn(d) {
  let s = 0;
  for (let i = 0; i < d.length; i++) {
    let n = d.charCodeAt(d.length - 1 - i) - 48;
    if (i % 2 === 1) { n *= 2; if (n > 9) n -= 9; }
    s += n;
  }
  return d.length > 0 && s % 10 === 0;
}
function cardlike(s) {
  for (const m of String(s || "").matchAll(CARDLIKE)) {
    const d = m[0].replace(/\D/g, "");
    if (d.length >= 13 && d.length <= 19 && luhn(d)) return true;
  }
  return false;
}
function reject(code, msg) {
  return { res: { statusCode: 400, headers: { "Content-Type": "application/json" }, body: JSON.stringify({ status: "error", reason: code, message: msg }) } };
}

function validate(b, maxAmount) {
  if (!ORDER_ID.test(b.order_id || "")) return "order_id";
  if (!AMOUNT.test(b.amount || "") || +b.amount <= 0 || +b.amount > maxAmount) return "amount";
  if ((b.currency || "USD") !== "USD") return "currency";
  const pan = String(b.pan || "").replace(/\D/g, "");
  if (pan.length < 13 || pan.length > 19 || !luhn(pan)) return "pan";
  if (!/^\d{4}$|^\d{6}$/.test(String(b.exp || ""))) return "exp";
  if (!/^\d{3,4}$/.test(String(b.cvv || ""))) return "cvv";
  if (b.zip && !/^\d{5}(\d{4})?$/.test(String(b.zip))) return "zip";
  // Nothing but the card fields may carry card data (blocks steering a PAN into an echoed field).
  for (const k of Object.keys(b)) {
    if (["pan", "exp", "cvv", "zip"].includes(k)) continue;
    if (cardlike(JSON.stringify(b[k]))) return "card-data-in-" + k;
  }
  for (const k of ["pan", "exp", "cvv", "zip"]) if (/\{\{|\}\}/.test(String(b[k] || ""))) return "template-" + k;
  return null;
}

function abaOk(d) {
  if (!/^\d{9}$/.test(d)) return false;
  const w = [3, 7, 1, 3, 7, 1, 3, 7, 1];
  let s = 0;
  for (let i = 0; i < 9; i++) s += w[i] * (d.charCodeAt(i) - 48);
  return s % 10 === 0;
}

// eCheck (ACH) requests: routing + account are detokenized; the holder name is plain text.
function validateCheck(b, maxAmount) {
  if (!ORDER_ID.test(b.order_id || "")) return "order_id";
  if (!AMOUNT.test(b.amount || "") || +b.amount <= 0 || +b.amount > maxAmount) return "amount";
  if ((b.currency || "USD") !== "USD") return "currency";
  if (!abaOk(String(b.routing || ""))) return "routing";
  if (!/^\d{4,17}$/.test(String(b.account || ""))) return "account";
  if (!["checking", "savings"].includes(b.account_type)) return "account_type";
  if (!/^[A-Za-z][A-Za-z .'\-]{0,59}$/.test(String(b.holder_name || ""))) return "holder_name";
  for (const k of Object.keys(b)) {
    if (["routing", "account"].includes(k)) continue;
    // Order ids may be long numbers; every other field may not hold a routing/account-sized run.
    const v = JSON.stringify(b[k]);
    if (cardlike(v) || (!["order_id", "customer_ref"].includes(k) && /\d{9,}/.test(v))) return "bank-data-in-" + k;
  }
  return null;
}

function nmiCheckBody(b, cfg) {
  const p = new URLSearchParams();
  p.set("security_key", cfg.NMI_SECURITY_KEY);
  p.set("type", "sale");
  p.set("payment", "check");
  p.set("amount", b.amount);
  p.set("orderid", b.order_id);
  p.set("checkname", b.holder_name);
  p.set("checkaba", b.routing);
  p.set("checkaccount", b.account);
  p.set("account_holder_type", "personal");
  p.set("account_type", b.account_type);
  p.set("sec_code", cfg.ECHECK_SEC_CODE || "TEL"); // TEL = authorized over the phone (NACHA)
  if (b.save_card) p.set("customer_vault", "add_customer");
  return { body: p.toString(), headers: { "Content-Type": "application/x-www-form-urlencoded" } };
}

function anetCheckBody(b, cfg) {
  const tr = {
    transactionType: "authCaptureTransaction",
    amount: b.amount,
    // Schema order: accountType, routingNumber, accountNumber, nameOnAccount, echeckType.
    payment: {
      bankAccount: {
        accountType: b.account_type,
        routingNumber: b.routing,
        accountNumber: b.account,
        nameOnAccount: b.holder_name.slice(0, 22),
        echeckType: cfg.ECHECK_SEC_CODE || "TEL",
      },
    },
  };
  if (b.save_card) tr.profile = { createProfile: true };
  tr.order = { invoiceNumber: b.order_id, description: "Phone payment (eCheck)" };
  if (b.save_card) tr.customer = { id: String(b.customer_ref || b.order_id).slice(0, 20) };
  return {
    body: JSON.stringify({
      createTransactionRequest: {
        merchantAuthentication: { name: cfg.ANET_LOGIN_ID, transactionKey: cfg.ANET_TRANSACTION_KEY },
        refId: b.order_id.slice(0, 20),
        transactionRequest: tr,
      },
    }),
    headers: { "Content-Type": "application/json" },
  };
}

function mmyy(exp) { return exp.length === 6 ? exp.slice(0, 2) + exp.slice(4) : exp; }

function nmiBody(b, cfg) {
  const p = new URLSearchParams();
  p.set("security_key", cfg.NMI_SECURITY_KEY);
  p.set("type", "sale");
  p.set("amount", b.amount);
  p.set("currency", "USD");
  p.set("orderid", b.order_id);
  p.set("ccnumber", b.pan);
  p.set("ccexp", mmyy(b.exp));
  p.set("cvv", b.cvv);
  if (b.zip) p.set("zip", b.zip);
  p.set("industry", "moto");
  if (b.save_card) p.set("customer_vault", "add_customer");
  return { body: p.toString(), headers: { "Content-Type": "application/x-www-form-urlencoded" } };
}

function anetBody(b, cfg) {
  const e = mmyy(b.exp);
  // Authorize.net validates JSON against an XML schema: element ORDER matters.
  const tr = {
    transactionType: "authCaptureTransaction",
    amount: b.amount,
    payment: { creditCard: { cardNumber: b.pan, expirationDate: `20${e.slice(2)}-${e.slice(0, 2)}`, cardCode: b.cvv } },
  };
  if (b.save_card) tr.profile = { createProfile: true };
  tr.order = { invoiceNumber: b.order_id, description: "Phone payment" };
  if (b.save_card) tr.customer = { id: String(b.customer_ref || b.order_id).slice(0, 20) };
  if (b.zip) tr.billTo = { zip: b.zip };
  tr.retail = { marketType: "1" }; // 1 = MOTO
  return {
    body: JSON.stringify({
      createTransactionRequest: {
        merchantAuthentication: { name: cfg.ANET_LOGIN_ID, transactionKey: cfg.ANET_TRANSACTION_KEY },
        refId: b.order_id.slice(0, 20),
        transactionRequest: tr,
      },
    }),
    headers: { "Content-Type": "application/json" },
  };
}

async function request(req) {
  const { args, configuration: cfg } = req;
  try {
    const b = typeof args.body === "string" ? JSON.parse(args.body) : args.body;
    // Keep-warm ping: answered here, never sent to the gateway.
    if (b && b.airlock_ping === true) return { res: { statusCode: 200, headers: { "Content-Type": "application/json" }, body: '{"pong":true}' } };
    const check = b.method === "check";
    const bad = check ? validateCheck(b, +(cfg.MAX_AMOUNT || 500)) : validate(b, +(cfg.MAX_AMOUNT || 500));
    if (bad) return reject("invalid", bad);
    const anet = cfg.GATEWAY === "authorizenet";
    const built = check ? (anet ? anetCheckBody(b, cfg) : nmiCheckBody(b, cfg)) : anet ? anetBody(b, cfg) : nmiBody(b, cfg);
    return { body: built.body, headers: { ...(args.headers || {}), ...built.headers } };
  } catch (e) {
    return reject("transform-error", "request transform failed");
  }
}

function normalizeNmi(text) {
  const q = new URLSearchParams(text);
  const r = q.get("response");
  // NMI refuses the same card + amount inside its duplicate window ("Duplicate transaction REFID:...").
  const dup = r === "3" && /^duplicate transaction/i.test(q.get("responsetext") || "");
  return {
    status: r === "1" ? "approved" : r === "2" ? "declined" : dup ? "duplicate" : "error",
    gateway: "nmi",
    response_code: q.get("response_code") || "",
    message: (q.get("responsetext") || "").slice(0, 80),
    transaction_id: q.get("transactionid") || "",
    auth_code: q.get("authcode") || "",
    avs: q.get("avsresponse") || "",
    cvv_result: q.get("cvvresponse") || "",
    vault_id: q.get("customer_vault_id") || "",
  };
}

function normalizeAnet(text) {
  const j = JSON.parse(String(text).replace(/^﻿/, "")); // Authorize.net prefixes a BOM
  const t = j.transactionResponse || {};
  const code = t.responseCode;
  const errs = (t.errors || []).map((e) => `${e.errorCode}:${e.errorText}`).join("; ");
  const msgs = ((j.messages && j.messages.message) || []).map((m) => `${m.code}:${m.text}`).join("; ");
  // Authorize.net error 11 = "A duplicate transaction has been submitted."
  const dup = (t.errors || []).some((e) => e.errorCode === "11");
  return {
    status: code === "1" ? "approved" : code === "2" ? "declined" : code === "4" ? "held" : dup ? "duplicate" : "error",
    gateway: "authorizenet",
    response_code: code || (j.messages && j.messages.resultCode) || "",
    message: (errs || msgs).slice(0, 120),
    transaction_id: t.transId && t.transId !== "0" ? t.transId : "",
    auth_code: t.authCode || "",
    avs: t.avsResultCode || "",
    cvv_result: t.cvvResultCode || "",
    vault_id: (j.profileResponse && j.profileResponse.customerProfileId) || "",
    payment_profile_id: (j.profileResponse && (j.profileResponse.customerPaymentProfileIdList || [])[0]) || "",
  };
}

async function response(req) {
  const { args, configuration: cfg } = req;
  try {
    const raw = typeof args.body === "string" ? args.body : JSON.stringify(args.body);
    const norm = cfg.GATEWAY === "authorizenet" ? normalizeAnet(raw) : normalizeNmi(raw);
    const out = JSON.stringify(norm);
    if (cardlike(out)) return { body: JSON.stringify({ status: "error", reason: "card-data-in-response" }), headers: { "Content-Type": "application/json" } };
    return { body: out, headers: { "Content-Type": "application/json" } };
  } catch (e) {
    return { body: JSON.stringify({ status: "unknown", reason: "unparseable-gateway-response" }), headers: { "Content-Type": "application/json" } };
  }
}

// Basis Theory node24 entry points. Deploy `requestTransform` as the proxy's request
// transform and `responseTransform` as its response transform (see cli/provision).
async function requestTransform(event) {
  const { req, configuration } = event;
  const out = await request({ args: { body: req.body, headers: req.headers || {} }, configuration });
  if (out.res) return { res: out.res };
  return { req: { headers: out.headers, body: out.body } };
}
async function responseTransform(event) {
  const { res, configuration } = event;
  const out = await response({ args: { body: res.body, headers: res.headers || {} }, configuration });
  return { res: { headers: out.headers, body: out.body } };
}

module.exports = { request, response, requestTransform, responseTransform, helpers: { validate, validateCheck, nmiCheckBody, anetCheckBody, abaOk, nmiBody, anetBody, normalizeNmi, normalizeAnet } };
