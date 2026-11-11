// Proxies requests to the local Studio (host_permissions bypass CORS) and handles the context menu.
const DEFAULTS = { server: "http://127.0.0.1:8080", token: "", voice: "af_heart", speed: 1.0 };
const getCfg = async () => ({ ...DEFAULTS, ...(await chrome.storage.local.get(Object.keys(DEFAULTS))) });

function b64(buf) {
  const u = new Uint8Array(buf); let s = "";
  for (let i = 0; i < u.length; i += 0x8000) s += String.fromCharCode.apply(null, u.subarray(i, i + 0x8000));
  return btoa(s);
}
async function call(path, opts = {}) {
  const c = await getCfg();
  const r = await fetch(c.server + path, { ...opts, headers: { "X-B2A-Token": c.token, "Content-Type": "application/json", ...(opts.headers || {}) } });
  return r;
}
async function probe() {
  const c = await getCfg();
  const tried = [c.server];
  for (let p = 8080; p < 8090; p++) tried.push(`http://127.0.0.1:${p}`);
  for (const base of tried) {
    try {
      const r = await fetch(base + "/api/web/ping", { signal: AbortSignal.timeout(900) });
      const j = await r.json();
      if (j && j.app === "b2a-studio") { await chrome.storage.local.set({ server: base }); return base; }
    } catch (_) {}
  }
  return null;
}

chrome.runtime.onMessage.addListener((msg, _s, send) => {
  (async () => {
    try {
      if (msg.type === "speak") {
        const c = await getCfg();
        const r = await call("/api/web/speak", { method: "POST", body: JSON.stringify({ text: msg.text, voice: msg.voice || c.voice, speed: msg.speed || c.speed }) });
        if (!r.ok) {
          let e = ""; try { e = (await r.json()).error; } catch (_) {}
          return send({ ok: false, status: r.status, error: e || ("Server answered " + r.status) });
        }
        const dur = parseFloat(r.headers.get("X-B2A-Duration") || "0");
        return send({ ok: true, b64: b64(await r.arrayBuffer()), duration: dur });
      }
      if (msg.type === "sendPage") {
        const r = await call("/api/web/jobs", { method: "POST", body: JSON.stringify(msg.payload) });
        return send(await r.json());
      }
      if (msg.type === "status") {
        let base = (await getCfg()).server, up = false, meta = null;
        try { const j = await (await fetch(base + "/api/web/ping", { signal: AbortSignal.timeout(900) })).json(); up = j && j.app === "b2a-studio"; } catch (_) {}
        if (!up) { base = await probe(); up = !!base; }
        if (up) { try { const r = await call("/api/web/meta"); meta = r.ok ? await r.json() : { ok: false, status: r.status }; } catch (_) {} }
        return send({ ok: true, up, server: base, meta });
      }
      if (msg.type === "probe") return send({ ok: true, server: await probe() });
      send({ ok: false, error: "Unknown message" });
    } catch (e) { send({ ok: false, error: String(e && e.message || e) }); }
  })();
  return true;
});

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({ id: "b2a-from-here", title: "Read aloud from here", contexts: ["selection"] });
  chrome.contextMenus.create({ id: "b2a-page", title: "Read this page aloud", contexts: ["page"] });
});
chrome.contextMenus.onClicked.addListener((info, tab) => {
  if (!tab || tab.id == null) return;
  const msg = { type: info.menuItemId === "b2a-from-here" ? "readFromSelection" : "readPage" };
  chrome.tabs.sendMessage(tab.id, msg).catch(async () => {
    try { await chrome.scripting.executeScript({ target: { tabId: tab.id }, files: ["content.js"] }); await chrome.tabs.sendMessage(tab.id, msg); } catch (_) {}
  });
});
