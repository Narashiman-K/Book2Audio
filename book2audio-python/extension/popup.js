const $ = (i) => document.getElementById(i);
const send = (m) => new Promise((r) => chrome.runtime.sendMessage(m, r));
(async () => {
  const cfg = await chrome.storage.local.get(["voice", "speed", "token"]);
  $("speed").value = cfg.speed || 1; $("pv").textContent = Number($("speed").value).toFixed(2) + "x";
  const s = await send({ type: "status" });
  const st = $("st");
  if (!s || !s.up) { st.textContent = "Studio not found. Start studio.bat first."; st.className = "st bad"; }
  else if (!s.meta || !s.meta.ok) { st.textContent = "Studio found, but not connected. Open Studio > Web to Audio > Connect extension."; st.className = "st bad"; }
  else {
    st.textContent = s.meta.engine_ready ? "Connected. Kokoro is ready." : "Connected, but the voice engine is not ready.";
    st.className = "st " + (s.meta.engine_ready ? "ok" : "bad");
    $("voice").innerHTML = ""; s.meta.voices.forEach((v) => { const o = document.createElement("option"); o.value = v.id; o.textContent = v.label; $("voice").append(o); });
    $("voice").value = cfg.voice || "af_heart";
  }
  $("voice").onchange = () => chrome.storage.local.set({ voice: $("voice").value });
  $("speed").oninput = () => { $("pv").textContent = Number($("speed").value).toFixed(2) + "x"; chrome.storage.local.set({ speed: parseFloat($("speed").value) }); };
  const tab = async () => (await chrome.tabs.query({ active: true, currentWindow: true }))[0];
  const tell = async (type) => {
    const t = await tab();
    try {
      try { await chrome.tabs.sendMessage(t.id, { type }); }
      catch (_) { // page was open before the extension: add the reader now, then retry
        await chrome.scripting.executeScript({ target: { tabId: t.id }, files: ["content.js"] });
        await chrome.tabs.sendMessage(t.id, { type });
      }
      window.close();
    } catch (e) { st.textContent = "This page does not allow reading (browser or store pages are blocked)."; st.className = "st bad"; }
  };
  $("read").onclick = () => tell("readPage");
  $("send").onclick = () => tell("sendPage");
  $("opt").onclick = (e) => { e.preventDefault(); chrome.runtime.openOptionsPage(); };
})();
