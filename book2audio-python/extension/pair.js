// Runs on the Studio page only: lets the "Connect extension" button hand over the address and token.
(() => {
  if (!document.querySelector('meta[name="b2a-studio"]')) return;
  window.addEventListener("message", (e) => {
    if (e.source !== window || e.origin !== location.origin) return;
    const d = e.data;
    if (!d || d.b2a !== "pair" || d.server !== location.origin || typeof d.token !== "string") return;
    chrome.storage.local.set({ server: d.server, token: d.token }, () => window.postMessage({ b2a: "paired" }, location.origin));
  });
})();
