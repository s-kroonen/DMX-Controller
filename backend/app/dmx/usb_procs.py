"""Find and kill other processes that may be holding the DMX interface.

On Windows a COM port is exclusive: FreeStyler, DMX-Configurator, a second
copy of this backend, etc. leave the dongle "Access is denied" for everyone
else. Windows gives us no cheap way to ask "who has COM7 open" without
extra tools, so we match by what is known to talk to DMX4ALL dongles:
lighting apps by process name, and other instances of this backend by
command line. This process and its ancestors are always excluded (on
Windows a venv's python.exe is a launcher that parents the real one).
"""

from __future__ import annotations

import os
from typing import Optional

import psutil

DMX_APP_NAMES = (
    "freestyler", "dmx-configurator", "dmxconfigurator", "dmxcontrol",
    "nanodmx", "dmx4all", "qlcplus", "qlc+", "lightkey", "dmxis",
)
BACKEND_ARG = "app.main:app"


def is_holder_candidate(name: str, argv: list[str]) -> bool:
    """Pure matching rule (unit-tested): would this process plausibly hold a DMX port?

    Lighting apps match by process name. Another copy of this backend must
    be an actual python process whose argv *elements* include uvicorn and
    `app.main:app` -- matching the text anywhere in the command line would
    also hit shells/editors that merely have it in their arguments."""
    lname = (name or "").lower()
    if any(app in lname for app in DMX_APP_NAMES):
        return True
    if not lname.startswith("python"):
        return False
    argv = argv or []
    return BACKEND_ARG in argv and any("uvicorn" in a for a in argv)


def _protected_pids() -> set[int]:
    me = psutil.Process(os.getpid())
    pids = {me.pid}
    try:
        pids |= {p.pid for p in me.parents()}
    except psutil.Error:
        pass
    return pids


def find_holders() -> list[dict]:
    protected = _protected_pids()
    found = []
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            info = proc.info
            if info["pid"] in protected:
                continue
            argv = info["cmdline"] or []
            if is_holder_candidate(info["name"], argv):
                found.append({"pid": info["pid"], "name": info["name"], "cmdline": " ".join(argv)})
        except psutil.Error:
            continue
    return found


def kill_holders(grace_s: float = 1.5) -> list[dict]:
    """Terminate (then force-kill) every candidate from find_holders().
    Returns one dict per process with a `killed` flag / `error`."""
    results = []
    procs: list[tuple[dict, psutil.Process]] = []
    for holder in find_holders():
        try:
            proc = psutil.Process(holder["pid"])
            proc.terminate()
            procs.append((holder, proc))
        except psutil.Error as exc:
            results.append({**holder, "killed": False, "error": str(exc)})
    _, alive = psutil.wait_procs([p for _, p in procs], timeout=grace_s)
    alive_pids = {p.pid for p in alive}
    for holder, proc in procs:
        error: Optional[str] = None
        if proc.pid in alive_pids:
            try:
                proc.kill()
                proc.wait(timeout=grace_s)
            except psutil.Error as exc:
                error = str(exc)
        results.append({**holder, "killed": error is None, **({"error": error} if error else {})})
    return results
