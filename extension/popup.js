const api = globalThis.browser ?? chrome;
const $ = (id) => document.getElementById(id);
const FIELDS = ["enabled", "captureAllTypes", "minSizeKB", "skipExts"];

api.runtime.sendMessage({ type: "status" }, (s) => {
  const st = $("state");
  st.textContent = s.running ? "Connected to SwiftGet" : "SwiftGet is not running";
  st.className = s.running ? "ok" : "bad";
  for (const f of FIELDS) {
    const el = $(f);
    if (el.type === "checkbox") el.checked = !!s[f]; else el.value = s[f];
  }
});

for (const f of FIELDS) {
  $(f).addEventListener("change", (e) => {
    const el = e.target;
    const v = el.type === "checkbox" ? el.checked : el.type === "number" ? Math.max(0, +el.value || 0) : el.value;
    api.storage.local.set({ [f]: v });
  });
}

$("grab").addEventListener("click", async () => {
  const [tab] = await api.tabs.query({ active: true, currentWindow: true });
  api.runtime.sendMessage({ type: "grabAll", tab }, (r) => {
    if (r && r.ok) window.close();
    else $("state").textContent = (r && r.error) || "Failed";
  });
});

$("open").addEventListener("click", () => {
  api.runtime.sendMessage({ type: "open" }, () => window.close());
});
