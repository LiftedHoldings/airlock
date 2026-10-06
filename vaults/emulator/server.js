// Local stand-in for the vault provider, for development and the test-suite.
// It runs the SAME transform files that are deployed to Basis Theory:
//   POST /inbound/<path>      inbound proxy: run inbound.js, forward to AIRLOCK_URL/<path>,
//                             stream the reply back unchanged (so streaming is testable)
//   POST /outbound/<gateway>  outbound proxy: detokenize {{ id }}, run outbound.request,
//                             POST to the fixed gateway URL, run outbound.response
//   DELETE /tokens/<id>       delete a token (Airlock's only token permission)
//   GET  /tokens/<id>         always 403: Airlock's key cannot read tokens
// Token values live only in this process's memory and expire.
//
// Env: PORT, AIRLOCK_URL, EDGE_SECRET, TOKEN_TTL_SECONDS, MAX_AMOUNT,
//      NMI_SECURITY_KEY, NMI_URL, ANET_LOGIN_ID, ANET_TRANSACTION_KEY, ANET_URL,
//      PROXY_KEY (required header BT-PROXY-KEY on inbound), EMULATOR_LOG (path, optional)

"use strict";

const http = require("http");
const https = require("https");
const crypto = require("crypto");
const fs = require("fs");
const path = require("path");
const inbound = require("../basis_theory/inbound.js").transform;
const outbound = require("../basis_theory/outbound.js");

const env = process.env;
const PORT = +(env.PORT || 8920);
const tokens = new Map(); // id -> { data, expires }
const GATEWAY_URL = {
  nmi: env.NMI_URL || "https://secure.nmi.com/api/transact.php",
  authorizenet: env.ANET_URL || "https://apitest.authorize.net/xml/v1/request.api",
};

function log(rec) {
  if (!env.EMULATOR_LOG) return;
  fs.appendFileSync(env.EMULATOR_LOG, JSON.stringify({ t: new Date().toISOString(), ...rec }) + "\n");
}

const bt = {
  tokens: {
    async create({ data, expires_at }) {
      const id = crypto.randomUUID();
      tokens.set(id, { data: String(data), expires: Date.parse(expires_at) || Date.now() + 120000 });
      return { id };
    },
  },
};

function live(id) {
  const t = tokens.get(id);
  if (!t) return null;
  if (Date.now() > t.expires) { tokens.delete(id); return null; }
  return t.data;
}

function readBody(req) {
  return new Promise((resolve) => {
    const parts = [];
    req.on("data", (c) => parts.push(c));
    req.on("end", () => resolve(Buffer.concat(parts).toString("utf8")));
  });
}

function send(res, code, body, headers) {
  res.writeHead(code, { "Content-Type": "application/json", ...(headers || {}) });
  res.end(typeof body === "string" ? body : JSON.stringify(body));
}

function forward(urlStr, method, headers, body, onResponse) {
  const u = new URL(urlStr);
  const lib = u.protocol === "https:" ? https : http;
  const r = lib.request(
    { hostname: u.hostname, port: u.port || (u.protocol === "https:" ? 443 : 80), path: u.pathname + u.search, method, headers: { ...headers, "Content-Length": Buffer.byteLength(body) } },
    onResponse,
  );
  r.on("error", (e) => onResponse(null, e));
  r.setTimeout(25000, () => r.destroy(new Error("timeout")));
  r.end(body);
}

async function handleInbound(req, res, rest) {
  if (env.PROXY_KEY && req.headers["bt-proxy-key"] !== env.PROXY_KEY) return send(res, 401, { error: "proxy key" });
  const t0 = Date.now();
  const body = await readBody(req);
  const headers = { "content-type": req.headers["content-type"] || "application/json", authorization: req.headers.authorization || "" };
  const out = await inbound({ args: { body, headers }, configuration: { EDGE_SECRET: env.EDGE_SECRET, TOKEN_TTL_SECONDS: env.TOKEN_TTL_SECONDS || "120" }, bt });
  if (out.res) {
    log({ kind: "inbound-short-circuit", reason: out.res.headers["X-Airlock-Edge-Error"] });
    res.writeHead(out.res.statusCode, out.res.headers);
    return res.end(out.res.body);
  }
  const tEdge = Date.now() - t0;
  forward(`${env.AIRLOCK_URL}/${rest}`, "POST", out.headers, out.body, (up, err) => {
    if (err || !up) return send(res, 502, { error: "airlock unreachable" });
    res.writeHead(up.statusCode, { "Content-Type": up.headers["content-type"] || "text/event-stream", "X-Edge-Ms": String(tEdge) });
    up.pipe(res); // stream through, chunk by chunk
  });
}

async function handleOutbound(req, res, gateway) {
  if (!GATEWAY_URL[gateway]) return send(res, 404, { error: "unknown gateway" });
  const raw = await readBody(req);
  // Detokenize {{ <id> }} expressions (and only those).
  let missing = false;
  const detok = raw.replace(/\{\{\s*([0-9a-f-]{36})\s*\}\}/g, (_, id) => {
    const v = live(id);
    if (v === null) { missing = true; return ""; }
    return v;
  });
  // Same shape as the real vault's detokenization failure, so Airlock classifies it identically.
  if (missing) return send(res, 400, { proxy_error: { title: "Invalid proxy request", status: 400, detail: "token-expired-or-deleted" } });
  const cfg = { GATEWAY: gateway, MAX_AMOUNT: env.MAX_AMOUNT || "500", NMI_SECURITY_KEY: env.NMI_SECURITY_KEY, ANET_LOGIN_ID: env.ANET_LOGIN_ID, ANET_TRANSACTION_KEY: env.ANET_TRANSACTION_KEY };
  const r1 = await outbound.request({ args: { body: detok, headers: {} }, configuration: cfg });
  if (r1.res) { res.writeHead(r1.res.statusCode, r1.res.headers); return res.end(r1.res.body); }
  const t0 = Date.now();
  forward(GATEWAY_URL[gateway], "POST", r1.headers, r1.body, async (up, err) => {
    if (err || !up) return send(res, 504, { status: "unknown", reason: "gateway-timeout-or-unreachable" });
    const body = await readBody(up);
    const r2 = await outbound.response({ args: { body, headers: up.headers }, configuration: cfg });
    let result = {};
    try { result = JSON.parse(r2.body); } catch (e) { result = { status: "unparseable" }; }
    log({ kind: "outbound", gateway, gateway_ms: Date.now() - t0, http: up.statusCode, result });
    res.writeHead(200, { "Content-Type": "application/json", "X-Gateway-Ms": String(Date.now() - t0) });
    res.end(r2.body);
  });
}

http
  .createServer(async (req, res) => {
    try {
      const [, kind, ...restParts] = req.url.split("/");
      const rest = restParts.join("/");
      if (req.method === "POST" && kind === "inbound") return handleInbound(req, res, rest);
      if (req.method === "POST" && kind === "outbound") return handleOutbound(req, res, restParts[0]);
      if (req.method === "DELETE" && kind === "tokens") { tokens.delete(restParts[0]); return send(res, 204, ""); }
      if (req.method === "GET" && kind === "tokens") return send(res, 403, { error: "forbidden: key lacks token:read" });
      if (req.method === "GET" && kind === "health") return send(res, 200, { ok: true, live_tokens: tokens.size });
      return send(res, 404, { error: "not found" });
    } catch (e) {
      return send(res, 500, { error: "emulator error" });
    }
  })
  .listen(PORT, "127.0.0.1", () => console.log(`vault emulator on :${PORT}`));
