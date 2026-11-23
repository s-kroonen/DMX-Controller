// The LAN access PIN prompt (backend: app/auth.py). A device that isn't this machine gets a 401 from
// /api and /ws once a PIN is set; this overlay asks for it, the backend answers with a cookie, and
// the page reloads. The machine running the server never sees it.

let overlay = null;

export function showPinPrompt() {
  if (overlay) return;
  overlay = document.createElement("div");
  overlay.style.cssText = "position:fixed;inset:0;z-index:99999;background:#0f1115;display:flex;" +
    "align-items:center;justify-content:center;font-family:system-ui,sans-serif;color:#e8eaed";
  overlay.innerHTML = `
    <form style="display:grid;gap:12px;width:min(300px,86vw);text-align:center">
      <h2 style="margin:0">DMX Controller</h2>
      <div style="opacity:.7;font-size:.9em">Enter the access PIN</div>
      <input type="password" inputmode="numeric" autocomplete="current-password" maxlength="8"
        style="font-size:1.6em;text-align:center;padding:10px;border-radius:8px;border:1px solid #3a4150;
        background:#161a21;color:inherit" autofocus>
      <button type="submit" style="font-size:1.1em;padding:10px;border-radius:8px;border:0;
        background:#3a7bd5;color:#fff">Unlock</button>
      <div class="pin-error" style="color:#ff8080;min-height:1.2em"></div>
    </form>`;
  const form = overlay.querySelector("form");
  const input = overlay.querySelector("input");
  const error = overlay.querySelector(".pin-error");
  form.onsubmit = async (evt) => {
    evt.preventDefault();
    const resp = await fetch("/api/auth/login", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pin: input.value }),
    });
    if (resp.ok) { location.reload(); return; }
    let detail = "wrong PIN";
    try { detail = (await resp.json()).detail || detail; } catch { /* keep the default */ }
    error.textContent = detail;
    input.value = "";
    input.focus();
  };
  document.body.appendChild(overlay);
  input.focus();
}

// Used when the websocket drops: was it the PIN (not a network blip)?
export async function promptIfPinRequired() {
  try {
    const status = await (await fetch("/api/auth/status")).json();
    if (status.pin_set && !status.authorized) { showPinPrompt(); return true; }
  } catch { /* server unreachable: ordinary reconnect handles it */ }
  return false;
}
