import { api } from "./api.js";
import { state, notifyStateChange } from "./state.js";

export async function reloadRoomAndGroups() {
  const [room, groups, profiles] = await Promise.all([
    api.getRoom(),
    api.listGroups(),
    api.listProfiles(),
  ]);
  state.room = room;
  state.groups = groups;
  state.profiles = profiles;
  notifyStateChange();
}

export async function loadInitialData() {
  await reloadRoomAndGroups();
  const snap = await api.snapshot();
  state.fixtureState = snap.fixture_state;
  state.dmxStatus = snap.dmx_status;
  notifyStateChange();
}
