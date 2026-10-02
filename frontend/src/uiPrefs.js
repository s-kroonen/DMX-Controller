// Small localStorage wrapper for UI-only preferences (panel positions/
// visibility, 3D camera pose). This is purely a per-browser convenience --
// never used for anything the server needs to know, and every call is
// guarded so a private window / blocked storage degrades to "just use
// the defaults" instead of breaking the page.

const PREFIX = "dmx-controller:";

export function loadPref(key, fallback) {
  try {
    const raw = localStorage.getItem(PREFIX + key);
    if (raw === null) return fallback;
    return JSON.parse(raw);
  } catch {
    return fallback;
  }
}

export function savePref(key, value) {
  try {
    localStorage.setItem(PREFIX + key, JSON.stringify(value));
  } catch {
    // ignore -- private window, storage disabled, quota exceeded, etc.
  }
}

// Removes every saved pref whose key starts with `prefix` (e.g. "panel:" to drop every window's
// remembered open/closed state). Used to reset window layout on an actual backend restart, see
// panelWindows.js -- boot_id changing is how a restart is told apart from an ordinary reload.
export function clearPrefsWithPrefix(prefix) {
  try {
    const doomed = [];
    for (let i = 0; i < localStorage.length; i++) {
      const key = localStorage.key(i);
      if (key && key.startsWith(PREFIX + prefix)) doomed.push(key);
    }
    doomed.forEach((key) => localStorage.removeItem(key));
  } catch {
    // ignore -- private window, storage disabled, etc.
  }
}
