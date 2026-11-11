// Supras\u016bt\u0101 Read Aloud: page reader with synced highlight (CSS Custom Highlight API, page DOM untouched).
(() => {
  if (window.__b2aReader) return; window.__b2aReader = true;
  const SKIP = "nav,aside,footer,header,script,style,noscript,form,button,select,textarea,svg,iframe,figcaption,[role=navigation],[role=banner],[role=complementary],[aria-hidden=true],.sidebar,.comments,.advert,.ad,.cookie";
  const BLOCKS = "h1,h2,h3,h4,h5,h6,p,li,blockquote";
  const send = (m) => new Promise((r) => { try { chrome.runtime.sendMessage(m, (x) => r(x || { ok: false, error: "No answer from extension" })); } catch (e) { r({ ok: false, error: "Extension was reloaded. Refresh this page." }); } });

  /* ---------- extract sentences with DOM ranges ---------- */
  function visible(el) { const r = el.getBoundingClientRect(); const cs = getComputedStyle(el); return cs.display !== "none" && cs.visibility !== "hidden" && (r.width > 0 || r.height > 0); }
  function pickRoot() {
    const c = [...document.querySelectorAll("article,main,[role=main]")].filter(visible);
    c.sort((a, b) => b.innerText.length - a.innerText.length);
    return c[0] || document.body;
  }
  function textNodes(el) {
    const out = [], w = document.createTreeWalker(el, NodeFilter.SHOW_TEXT, { acceptNode: (n) => n.nodeValue.trim() ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT });
    while (w.nextNode()) out.push(w.currentNode); return out;
  }
  const SPLIT = /[^.!?\u2026]+(?:[.!?\u2026]+["')\]\u201d\u2019]*(?=\s|$)|$)/g;
  function extract() {
    const root = pickRoot(), seen = new Set(), sents = [], heads = [];
    root.querySelectorAll(BLOCKS).forEach((el) => {
      if (el.closest(SKIP) && el.closest(SKIP) !== root) return;
      if (el.querySelector(BLOCKS) || !visible(el)) return;
      const nodes = textNodes(el); if (!nodes.length) return;
      let full = "", map = [];
      nodes.forEach((n) => { map.push({ n, s: full.length }); full += n.nodeValue.replace(/\s+/g, " "); /* keep lengths equal */ });
      // lengths can differ after whitespace collapse; rebuild with a precise per-char map instead
      full = ""; map = [];
      nodes.forEach((n) => { const v = n.nodeValue; for (let i = 0; i < v.length; i++) { map.push([n, i]); full += /\s/.test(v[i]) ? " " : v[i]; } });
      if (full.trim().length < 2) return;
      const isH = /^H[1-6]$/.test(el.tagName); let m;
      SPLIT.lastIndex = 0;
      while ((m = SPLIT.exec(full))) {
        let s = m.index, e = m.index + m[0].length;
        while (s < e && full[s] === " ") s++; while (e > s && full[e - 1] === " ") e--;
        if (e - s < 2) continue;
        const text = full.slice(s, e).replace(/\s+/g, " ");
        if (!/[A-Za-z0-9\u00c0-\uffff]/.test(text)) continue;
        sents.push({ text, map, s, e, head: isH, el });
      }
    });
    return sents;
  }
  function rangeOf(sent, from, to) { // char offsets relative to sentence start
    const a = sent.map[sent.s + from], b = sent.map[Math.min(sent.e, sent.s + to) - 1]; if (!a || !b) return null;
    const r = document.createRange(); r.setStart(a[0], a[1]); r.setEnd(b[0], b[1] + 1); return r;
  }

  /* ---------- state ---------- */
  const P = { sents: [], i: 0, playing: false, ctx: null, src: null, startAt: 0, dur: 0, buf: new Map(), gen: 0, raf: 0, offset: 0, paused: false };
  const hl = { s: null, w: null };
  if (window.CSS && CSS.highlights) { hl.s = new Highlight(); hl.w = new Highlight(); CSS.highlights.set("b2a-sent", hl.s); CSS.highlights.set("b2a-word", hl.w); }
  const st = document.createElement("style");
  st.textContent = "::highlight(b2a-sent){background:rgba(240,149,74,.28);color:inherit}::highlight(b2a-word){background:#f0954a;color:#0c0e15}";
  document.documentElement.append(st);

  /* ---------- UI (shadow DOM) ---------- */
  const host = document.createElement("div"); host.style.cssText = "all:initial;position:fixed;z-index:2147483647;left:0;top:0;width:0;height:0";
  const sh = host.attachShadow({ mode: "closed" });
  sh.innerHTML = `<style>
  *{box-sizing:border-box;font:13px system-ui,sans-serif}
  .bar{position:fixed;left:50%;bottom:22px;transform:translateX(-50%);display:none;align-items:center;gap:8px;background:#0c0e15f2;color:#e8e6f0;border:1px solid #f0954a88;border-radius:999px;padding:8px 14px;box-shadow:0 0 28px #f0954a40,0 8px 30px #000a;max-width:92vw}
  .bar.on{display:flex}
  button{background:#151827;color:#e8e6f0;border:1px solid #2a2f45;border-radius:999px;min-width:34px;height:34px;padding:0 10px;cursor:pointer}
  button:hover{border-color:#9c8cf0}button.p{background:#f0954a22;border-color:#f0954a;color:#f0954a}
  select{background:#080a10;color:#e8e6f0;border:1px solid #2a2f45;border-radius:8px;padding:5px}
  .msg{max-width:260px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#a7a3bd;font-size:12px}.msg.err{color:#f08a8a}
  .rf{position:fixed;display:none;background:#f0954a;color:#0c0e15;border:0;border-radius:999px;height:32px;padding:0 14px;font-weight:700;box-shadow:0 0 20px #f0954a80,0 4px 14px #0008;cursor:pointer}
  .rf.on{display:block}</style>
  <div class="bar" id="bar"><button id="prev" title="Previous sentence">&#9664;&#9664;</button><button class="p" id="pp" title="Play / pause">&#9654;</button><button id="next" title="Next sentence">&#9654;&#9654;</button>
  <select id="rate"><option>0.75</option><option selected>1</option><option>1.25</option><option>1.5</option></select><span class="msg" id="msg"></span><button id="x" title="Close">&#10005;</button></div>
  <button class="rf" id="rf">Read from here</button>`;
  (document.body || document.documentElement).append(host);
  const $ = (i) => sh.getElementById(i);
  const say = (t, err) => { $("msg").textContent = t || ""; $("msg").className = "msg" + (err ? " err" : ""); };

  /* ---------- playback ---------- */
  const ac = () => (P.ctx ||= new (window.AudioContext || window.webkitAudioContext)());
  async function fetchBuf(i, gen) {
    const sent = P.sents[i]; if (!sent) return null;
    if (P.buf.has(i)) return P.buf.get(i);
    const text = sent.text.slice(sent.skip || 0);
    const pr = (async () => {
      const cfg = await chrome.storage.local.get(["voice", "speed"]);
      const r = await send({ type: "speak", text, voice: cfg.voice, speed: (cfg.speed || 1) * parseFloat($("rate").value) });
      if (!r.ok) throw new Error(r.status === 403 ? "Not connected. Open Studio > Web to Audio > Connect extension." : r.error);
      const bin = atob(r.b64), u = new Uint8Array(bin.length); for (let k = 0; k < bin.length; k++) u[k] = bin.charCodeAt(k);
      return await ac().decodeAudioData(u.buffer);
    })();
    P.buf.set(i, pr); pr.catch(() => P.buf.delete(i)); return pr;
  }
  function clearHl() { hl.s && hl.s.clear(); hl.w && hl.w.clear(); }
  function stopSrc() { cancelAnimationFrame(P.raf); if (P.src) { P.src.onended = null; try { P.src.stop(); } catch (_) {} P.src = null; } }
  async function playIndex(i, skip) {
    stopSrc(); const gen = ++P.gen; P.i = i;
    if (i >= P.sents.length) return finish();
    const sent = P.sents[i]; if ((skip || 0) !== (sent.skip || 0) || skip) P.buf.delete(i); sent.skip = skip || 0;
    say("Preparing voice\u2026");
    let buf; try { buf = await fetchBuf(i, gen); } catch (e) { if (gen === P.gen) { say(e.message, true); setPlaying(false); } return; }
    if (gen !== P.gen) return;
    say("");
    [i + 1, i + 2].forEach((k) => { if (P.sents[k]) { P.sents[k].skip = 0; fetchBuf(k, gen).catch(() => {}); } });
    if (P.ctx.state === "suspended") await P.ctx.resume();
    const src = P.ctx.createBufferSource(); src.buffer = buf; src.connect(P.ctx.destination);
    P.src = src; P.dur = buf.duration; P.startAt = P.ctx.currentTime; src.onended = () => { if (gen === P.gen && P.playing) playIndex(i + 1, 0); };
    src.start(); setPlaying(true);
    const base = sent.skip, total = sent.text.length - base;
    const rs = rangeOf(sent, 0, sent.text.length); clearHl(); if (rs) hl.s.add(rs);
    const tr = rs && rs.startContainer.parentElement; if (tr && !P.userScrolledRecently()) tr.scrollIntoView({ block: "center", behavior: "smooth" });
    const words = []; { const re = /\S+/g; let m; const t = sent.text.slice(base); while ((m = re.exec(t))) words.push([base + m.index, base + m.index + m[0].length]); }
    let w0 = -1;
    const tick = () => {
      if (gen !== P.gen) return;
      const f = Math.min(1, (P.ctx.currentTime - P.startAt) / P.dur), cpos = f * total + base;
      let wi = words.findIndex((w) => cpos < w[1]); if (wi < 0) wi = words.length - 1;
      if (wi !== w0 && words[wi]) { w0 = wi; hl.w.clear(); const rw = rangeOf(sent, words[wi][0], words[wi][1]); if (rw) hl.w.add(rw); }
      P.raf = requestAnimationFrame(tick);
    };
    tick();
  }
  function setPlaying(v) { P.playing = v; $("pp").innerHTML = v ? "&#10074;&#10074;" : "&#9654;"; }
  function finish() { stopSrc(); clearHl(); setPlaying(false); P.i = 0; say("Finished"); }
  function close() { P.gen++; stopSrc(); clearHl(); setPlaying(false); $("bar").classList.remove("on"); P.buf.clear(); }

  let lastUser = 0; ["wheel", "touchmove", "keydown"].forEach((e) => addEventListener(e, () => (lastUser = Date.now()), { passive: true }));
  P.userScrolledRecently = () => Date.now() - lastUser < 5000;

  /* ---------- start points ---------- */
  function selectionStart() {
    const s = getSelection(); if (!s || s.isCollapsed || !s.rangeCount) return null;
    const r = s.getRangeAt(0), a = r.startContainer;
    for (let i = 0; i < P.sents.length; i++) {
      const sent = P.sents[i], last = sent.map[sent.e - 1], first = sent.map[sent.s];
      if (!last || !first) continue;
      const end = document.createRange(); end.setStart(last[0], last[1] + 1); end.collapse(true);
      if (r.compareBoundaryPoints(Range.START_TO_START, end) < 0) { // selection starts before sentence end
        const st = document.createRange(); st.setStart(first[0], first[1]); st.collapse(true);
        if (r.compareBoundaryPoints(Range.START_TO_START, st) <= 0) return { i, skip: 0 };
        let off = 0; for (let k = sent.s; k < sent.e; k++) { const [n, o] = sent.map[k]; if (n === a && o >= r.startOffset) { off = k - sent.s; break; } if (k === sent.e - 1) off = 0; }
        const t = sent.text; const cut = t.lastIndexOf(" ", off); return { i, skip: cut > 0 ? cut + 1 : 0 };
      }
    }
    return null;
  }
  async function start(fromSel) {
    $("bar").classList.add("on"); say("Reading the page\u2026");
    P.sents = extract(); P.buf.clear();
    if (!P.sents.length) return say("Nothing readable found on this page.", true);
    ac(); let at = { i: 0, skip: 0 };
    if (fromSel) { const s = selectionStart(); if (s) at = s; }
    setPlaying(true); playIndex(at.i, at.skip);
  }
  function pauseResume() {
    if (!P.sents.length) return start(false);
    if (P.playing) { P.ctx.suspend(); setPlaying(false); } else { P.ctx.resume(); setPlaying(true); }
  }
  $("pp").onclick = pauseResume; $("x").onclick = close;
  $("prev").onclick = () => playIndex(Math.max(0, P.i - 1), 0); $("next").onclick = () => playIndex(P.i + 1, 0);
  $("rate").onchange = () => { P.buf.clear(); if (P.sents.length) playIndex(P.i, 0); };
  addEventListener("keydown", (e) => { if (e.key === "Escape" && $("bar").classList.contains("on")) close(); }, true);

  /* floating "Read from here" */
  let selT = 0;
  document.addEventListener("selectionchange", () => {
    clearTimeout(selT); selT = setTimeout(() => {
      const s = getSelection(), rf = $("rf");
      if (!s || s.isCollapsed || s.toString().trim().length < 3) return rf.classList.remove("on");
      const rc = s.getRangeAt(0).getBoundingClientRect(); if (!rc.width && !rc.height) return rf.classList.remove("on");
      rf.style.left = Math.max(8, Math.min(innerWidth - 150, rc.left)) + "px";
      rf.style.top = (rc.top > 50 ? rc.top - 42 : rc.bottom + 10) + "px"; rf.classList.add("on");
    }, 180);
  });
  $("rf").addEventListener("mousedown", (e) => e.preventDefault());
  $("rf").onclick = () => { start(true); $("rf").classList.remove("on"); };

  /* ---------- messages from popup / context menu ---------- */
  chrome.runtime.onMessage.addListener((m, _s, reply) => {
    if (m.type === "readPage") start(false);
    else if (m.type === "readFromSelection") start(true);
    else if (m.type === "sendPage") {
      const sents = extract(), blocks = []; let cur = "";
      sents.forEach((s, i) => { if (s.head) { if (cur) blocks.push({ kind: "p", text: cur }), (cur = ""); blocks.push({ kind: "h", text: s.text }); } else { cur += (cur ? " " : "") + s.text; if (!sents[i + 1] || sents[i + 1].el !== s.el) blocks.push({ kind: "p", text: cur }), (cur = ""); } });
      chrome.storage.local.get(["voice", "speed"]).then((c) => send({ type: "sendPage", payload: { title: document.title, url: location.href, blocks, options: { voice: c.voice, speed: c.speed } } })).then((r) => { $("bar").classList.add("on"); say(r.ok ? "Sent to the Studio. Open Web to Audio there." : (r.error || "Could not send"), !r.ok); setTimeout(() => !P.playing && $("bar").classList.remove("on"), 4000); });
    }
    reply && reply({ ok: true });
  });
})();
