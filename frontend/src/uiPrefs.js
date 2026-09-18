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
