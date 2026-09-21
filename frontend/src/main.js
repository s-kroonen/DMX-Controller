import { api, connectWebSocket } from "./api.js";
import { state, onStateChange, notifyStateChange } from "./state.js";
import { renderSidebar, initSidebar } from "./sidebar.js";
import { initGroupModal } from "./groupModal.js";
import { initPanels } from "./panels.js";
import { initPatchModal } from "./patch.js";
import { initFixtureCreator } from "./fixtureCreator.js";
import { initSafetyModal } from "./safety.js";
import { initAnimationsModal } from "./animations.js";
import { initRoomShapeModal } from "./roomShape.js";
import { initRoomObjectsModal } from "./roomObjects.js";
import { initDmxSetupModal, reconnectDmx } from "./dmxSetup.js";
import { initConfigModal } from "./configTransfer.js";
import { initWindowsMenu } from "./windowsMenu.js";
import { initSoundPanel } from "./sound.js";
import { initZonesPanel } from "./zones.js";
import { initScene3D } from "./scene3d.js";
import { initFixtureDetailsPanel, renderFixtureDetailsPanel } from "./fixtureDetails.js";
import { loadInitialData } from "./main_data.js";
import { applyMode, initModeSwitch } from "./mode.js";
import { initCalibrateWindow } from "./calibrateWindow.js";

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
  pill.onclick = null;
  pill.style.cursor = "";
  if (status.link_lost) {
    pill.textContent = "DMX: LINK LOST -- click to reconnect";
    pill.className = "status-pill error";
    pill.style.cursor = "pointer";
    pill.onclick = () => reconnectDmx(false);
  } else if (status.running) {
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
    applyMode();
    renderSidebar();
    updateDmxStatusPill();
    renderFixtureDetailsPanel();
  });

  await loadInitialData();

  initPanels();
  initZonesPanel();
  initWindowsMenu();
  initPatchModal();
  initGroupModal();
  initSidebar();
  initFixtureDetailsPanel();
  initFixtureCreator();
  initRoomShapeModal();
  initRoomObjectsModal();
  initDmxSetupModal();
  initConfigModal();
  initSoundPanel();
  initSafetyModal();
  initAnimationsModal();
  initBlackout();
  initModalCloseOnBackdrop();
  initModeSwitch();
  initCalibrateWindow();

  initScene3D(document.getElementById("scene-container"));

  connectWebSocket((snapshot) => {
    state.room = snapshot.room;
    state.groups = snapshot.groups;
    state.fixtureState = snapshot.fixture_state;
    state.dmxStatus = snapshot.dmx_status;
    state.mode = snapshot.mode || state.mode;
    state.calibration = snapshot.calibration || state.calibration;
    notifyStateChange();
  });

  notifyStateChange();
}

bootstrap().catch((err) => {
  console.error("Failed to start DMX Controller UI", err);
  alert("Failed to start UI -- check console. Is the backend running?");
});
