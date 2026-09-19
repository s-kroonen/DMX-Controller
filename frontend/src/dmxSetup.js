import { api } from "./api.js";

// Hardware testing happens from this panel: pick the port, connect (the
// driver does the DMX4ALL "C?"/"G" handshake), then use the raw-channel
// test -- all from the browser instead of editing env vars and
// restarting the backend between attempts. A failed connect here is
// designed to never crash the backend (see app/context.py); it just
// reports the error and leaves whatever was running before untouched.

export function initDmxSetupModal() {
  document.getElementById("btn-dmx-setup").onclick = () => openModal();
  document.querySelector("#modal-dmx-setup .modal-close").onclick = () => closeModal();
  document.getElementById("ds-refresh-ports").onclick = refreshPorts;
  document.getElementById("ds-connect").onclick = connect;
  document.getElementById("ds-disconnect").onclick = disconnect;
  document.getElementById("ds-reconnect").onclick = () => reconnectDmx(false);
  document.getElementById("ds-kill-holders").onclick = killHolders;
  document.getElementById("ds-raw-send").onclick = sendRaw;
  document.getElementById("ds-raw-blackout").onclick = () =>
    api.dmxRawBlackout().catch((e) => alert(e.message));
}

async function openModal() {
  document.getElementById("modal-dmx-setup").classList.remove("hidden");
  await Promise.all([refreshPorts(), refreshStatus(), refreshHolders()]);
}

function closeModal() {
  document.getElementById("modal-dmx-setup").classList.add("hidden");
}

async function refreshPorts() {
  const select = document.getElementById("ds-port-select");
  select.innerHTML = '<option value="">-- pick a detected port --</option>';
  try {
    const ports = await api.dmxPorts();
    for (const p of ports) {
      const opt = document.createElement("option");
      opt.value = p.device;
      opt.textContent = `${p.device} -- ${p.description || "unknown device"}`;
      select.appendChild(opt);
    }
    if (ports.length === 0) {
      const opt = document.createElement("option");
      opt.value = "";
      opt.textContent = "(none detected -- type the port manually below)";
      select.appendChild(opt);
    }
  } catch (e) {
    console.error("failed to list serial ports", e);
  }
}

async function refreshStatus() {
  const box = document.getElementById("ds-status-box");
  try {
    const status = await api.dmxStatus();
    const lines = [
      `Mode: ${status.mode}${status.mode === "dmx4all" ? ` (${status.port}, ${status.baud_rate} baud)` : ""}`,
      `Running: ${status.running}`,
      `Frames sent: ${status.frames_sent}`,
    ];
    if (status.link_lost) lines.push("LINK LOST -- press Reconnect");
    if (status.last_frame_error) lines.push(`Last frame error: ${status.last_frame_error}`);
    if (status.connect_error) lines.push(`Last connect error: ${status.connect_error}`);
    box.textContent = lines.join(" | ");
    box.style.color = (status.link_lost || status.last_frame_error || status.connect_error) ? "#ff8080" : "";
  } catch (e) {
    box.textContent = `Failed to fetch DMX status: ${e.message}`;
  }
}

function currentPort() {
  const manual = document.getElementById("ds-port-manual").value.trim();
  if (manual) return manual;
  return document.getElementById("ds-port-select").value;
}

async function connect() {
  const port = currentPort();
  if (!port) {
    alert("Pick a detected port or type one manually.");
    return;
  }
  const baud = Number(document.getElementById("ds-baud").value);
  try {
    await api.dmxConnect(port, baud);
    await refreshStatus();
  } catch (e) {
    alert(`Connect failed (backend is still running fine): ${e.message}`);
    await refreshStatus();
  }
}

async function disconnect() {
  await api.dmxDisconnect().catch((e) => alert(e.message));
  await refreshStatus();
}

async function sendRaw() {
  const channel = Number(document.getElementById("ds-raw-channel").value);
  const value = Number(document.getElementById("ds-raw-value").value);
  try {
    await api.dmxRaw(channel, value);
  } catch (e) {
    alert(e.message);
  }
}

async function refreshHolders() {
  const box = document.getElementById("ds-holders");
  try {
    const holders = await api.dmxHolders();
    box.textContent = holders.length
      ? `Would be killed: ${holders.map((h) => `${h.name} (pid ${h.pid})`).join(", ")}`
      : "No other DMX/USB processes found (FreeStyler, DMX-Configurator, other copies of this backend).";
  } catch (e) {
    box.textContent = `Failed to list processes: ${e.message}`;
  }
}

// Exported so the top-bar status pill can trigger a reconnect in one click.
export async function reconnectDmx(killOtherHolders = false) {
  try {
    const status = await api.dmxReconnect(killOtherHolders);
    if (status.killed && status.killed.length) {
      alert(`Killed: ${status.killed.map((k) => `${k.name} (pid ${k.pid})`).join(", ")}`);
    }
  } catch (e) {
    alert(`Reconnect failed (backend is still running on the simulator): ${e.message}`);
  }
  await Promise.all([refreshStatus(), refreshHolders()]);
}

async function killHolders() {
  const holders = await api.dmxHolders().catch(() => []);
  const names = holders.map((h) => `${h.name} (pid ${h.pid})`).join("\n") || "(none found)";
  if (!confirm(`Kill these processes and release the DMX port?\n\n${names}`)) return;
  try {
    await api.dmxKillHolders();
  } catch (e) {
    alert(e.message);
  }
  await Promise.all([refreshStatus(), refreshHolders()]);
  alert("Done. Press Reconnect to re-open the dongle.");
}
