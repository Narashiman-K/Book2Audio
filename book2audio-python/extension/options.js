const $ = (i) => document.getElementById(i);
chrome.storage.local.get(["server", "token"], (c) => { $("server").value = c.server || "http://127.0.0.1:8080"; $("token").value = c.token || ""; });
$("save").onclick = () => { let s = $("server").value.trim().replace(/\/+$/, ""); if (!/^http:\/\/(127\.0\.0\.1|localhost)(:\d+)?$/.test(s)) { $("msg").textContent = "Address must be http://127.0.0.1:PORT"; return; } chrome.storage.local.set({ server: s, token: $("token").value.trim() }, () => $("msg").textContent = "Saved."); };
$("find").onclick = () => chrome.runtime.sendMessage({ type: "probe" }, (r) => { if (r && r.server) { $("server").value = r.server; $("msg").textContent = "Found " + r.server; } else $("msg").textContent = "Studio not found. Start studio.bat."; });
