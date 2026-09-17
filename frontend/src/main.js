import { api, connectWebSocket } from "./api.js";
import { state, onStateChange, notifyStateChange } from "./state.js";
import { renderSidebar } from "./sidebar.js";
import { initPanels } from "./panels.js";
import { initPatchModal } from "./patch.js";
import { initFixtureCreator } from "./fixtureCreator.js";
import { initSafetyModal } from "./safety.js";
import { initAnimationsModal } from "./animations.js";
import { initRoomShapeModal } from "./roomShape.js";
import { initRoomObjectsModal } from "./roomObjects.js";
import { initDmxSetupModal } from "./dmxSetup.js";
import { initScene3D } from "./scene3d.js";
import { loadInitialData } from "./main_data.js";

function initModalCloseOnBackdrop() {
  document.querySelectorAll(".modal").forEach((modal) => {
    modal.addEventListener("click", (evt) => {
      if (evt.target === modal) modal.classList.add("hidden");
    });
  });
}

function updateDmxStatusPill() {
  const pill = document.getElementById("dmx-status");
  const status = state.dmxStatus || {};
  if (status.running) {
    pill.textContent = `DMX: running (${status.frames_sent || 0} frames${status.last_frame_error ? ", ERROR" : ""})`;
    pill.className = "status-pill " + (status.last_frame_error ? "error" : "ok");
  } else {
    pill.textContent = "DMX: stopped";
    pill.className = "status-pill error";
  }
}

function initBlackout() {
  document.getElementById("btn-blackout").onclick = async () => {
    if (confirm("Blackout all fixtures?")) {
      await api.blackout();
    }
  };
}

async function bootstrap() {
  onStateChange(() => {
    renderSidebar();
    updateDmxStatusPill();
  });

  await loadInitialData();

  initPanels();
  initPatchModal();
  initFixtureCreator();
  initRoomShapeModal();
  initRoomObjectsModal();
  initDmxSetupModal();
  initSafetyModal();
  initAnimationsModal();
  initBlackout();
  initModalCloseOnBackdrop();

  initScene3D(document.getElementById("scene-container"));

  connectWebSocket((snapshot) => {
    state.room = snapshot.room;
    state.groups = snapshot.groups;
    state.fixtureState = snapshot.fixture_state;
    state.dmxStatus = snapshot.dmx_status;
    notifyStateChange();
  });

  notifyStateChange();
}

bootstrap().catch((err) => {
  console.error("Failed to start DMX Controller UI", err);
  alert("Failed to start UI -- check console. Is the backend running?");
});
