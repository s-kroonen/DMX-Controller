import { mountCalibrationView } from "./calibrationView.js";
import { setPoint, finishCalibration } from "./calibration.js";
import { startPickPoint } from "./scene3d.js";
import { isPanelOpen, openPanel, closePanel } from "./panelWindows.js";
import { notify } from "./mode.js";

// The desktop Calibrate window (edit mode): opened from the Calibrate button in the header. It is
// the shared calibration view (see calibrationView.js) in a floating window, plus "Pick in 3D".
// Closing it puts the lights back (beam off, sweep stopped).

export function initCalibrateWindow() {
  const view = mountCalibrationView(document.getElementById("cal-body"), {
    onPick: () => {
      notify("Click a surface in the 3D view to place the point.");
      startPickPoint((point) => setPoint(point, true).catch(console.error));
    },
  });
  document.getElementById("btn-calibrate").onclick = () => {
    if (isPanelOpen("calibrate")) closePanel("calibrate");
    else openPanel("calibrate");
    document.getElementById("btn-calibrate").classList.toggle("active", isPanelOpen("calibrate"));
    view.refresh();
  };
  document.addEventListener("panel-closed", (evt) => {
    if (evt.detail !== "calibrate") return;
    document.getElementById("btn-calibrate").classList.remove("active");
    finishCalibration().catch(console.error);
  });
}
