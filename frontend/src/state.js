// Shared client-side state: cached room/groups/profiles + current selection.
// Selection can hold multiple fixture ids and/or group ids at once; every
// control panel action resolves against the whole selection so you can
// e.g. select a fixture and a group together and set color on both.

export const state = {
  room: {
    name: "", dimensions: { width: 10, depth: 10, height: 4 },
    floor_points: [], fixtures: [], safety_zones: [], objects: [], animation_points: [],
  },
  groups: [],
  profiles: [],
  fixtureState: {},
  dmxStatus: {},
  selection: new Set(), // fixture ids and/or group ids
  zoneFilter: new Set(), // zone ids picked in the Zones window; empty = every zone

  fixtureById(id) {
    return this.room.fixtures.find((f) => f.id === id);
  },
  profileById(id) {
    return this.profiles.find((p) => p.id === id);
  },
  groupById(id) {
    return this.groups.find((g) => g.id === id);
  },

  isSelected(id) {
    return this.selection.has(id);
  },

  toggleSelection(id, exclusive) {
    if (exclusive) {
      const wasOnly = this.selection.size === 1 && this.selection.has(id);
      this.selection.clear();
      if (!wasOnly) this.selection.add(id);
    } else if (this.selection.has(id)) {
      this.selection.delete(id);
    } else {
      this.selection.add(id);
    }
  },

  zoneFilterList() {
    return [...this.zoneFilter];
  },

  // Expand any mix of fixture and group ids to concrete fixture ids.
  expandIds(ids) {
    const out = new Set();
    for (const id of ids) {
      const group = this.groupById(id);
      if (group) group.fixture_ids.forEach((fid) => out.add(fid));
      else out.add(id);
    }
    return [...out];
  },

  // The zones the given fixtures declare ({id, label, kind}), first-seen order, de-duplicated.
  // Fixtures without declared zones (moving heads, PARs) contribute none.
  zonesForFixtures(fixtureIds) {
    const seen = new Map();
    for (const fid of fixtureIds) {
      const fixture = this.fixtureById(fid);
      const profile = fixture && this.profileById(fixture.profile_id);
      for (const z of (profile && profile.zones) || []) {
        if (!seen.has(z.id)) seen.set(z.id, { id: z.id, label: z.label, kind: z.kind });
      }
    }
    return [...seen.values()];
  },

  // A selection may reference groups; expand to the concrete fixture ids
  // for anything that needs to inspect real fixture data (e.g. custom
  // channel sliders, profile lookups).
  expandedFixtureIds() {
    const ids = new Set();
    for (const sel of this.selection) {
      const group = this.groupById(sel);
      if (group) {
        group.fixture_ids.forEach((fid) => ids.add(fid));
      } else {
        ids.add(sel);
      }
    }
    return [...ids];
  },
};

export const listeners = [];
export function onStateChange(fn) {
  listeners.push(fn);
}
export function notifyStateChange() {
  listeners.forEach((fn) => fn());
}
