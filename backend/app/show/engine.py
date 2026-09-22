"""Show engine: the live, in-memory state of one running session.

Ties together the room (fixture instances + safety zones), the fixture
profile library, and the DMX output. Everything the API/UI does --
set a color, drag a pan/tilt target, fire a group button -- goes through
here so there's one place that (a) resolves "which channels does this
role map to for this fixture", (b) checks safety zones before any
computed pan/tilt reaches hardware, and (c) keeps a readable snapshot of
current fixture/group state for the UI to render (sliders need to know
current values, not just be write-only).
"""

from __future__ import annotations

import copy
import dataclasses
from typing import Iterable, Optional, Union

from ..dmx.interface import DmxOutput
from ..fixtures.library import FixtureLibrary
from ..fixtures.schema import MAIN_ZONE_ID, FixtureProfile
from ..groups.model import ALL_GROUP_COLOR, ALL_GROUP_ID, ALL_GROUP_NAME, Group
from ..room.ik import compute_pan_tilt, target_violates_safety_zones
from ..room.model import Room, Vec3


@dataclasses.dataclass
class FixtureState:
    values: dict[str, int] = dataclasses.field(default_factory=dict)  # role/custom-label -> 0-255
    last_target: Optional[Vec3] = None
    blocked_by_safety_zone: Optional[str] = None  # zone id, if last aim was blocked
    shutter_closed: bool = False  # "Closed" button: dark regardless of dimmer/strobe
    # zone id -> {"color": [r, g, b, w] (logical 0-255), "dimmer": 0-255, "strobe": 0-255}, for fixtures
    # that declare zones (a light bar's spots and derbies)
    zones: dict[str, dict] = dataclasses.field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "shutter_closed": self.shutter_closed,
            "zones": {zid: {"color": list(z["color"]), "dimmer": z["dimmer"], "strobe": z.get("strobe", 0)}
                      for zid, z in self.zones.items()},
            "values": dict(self.values),
            "last_target": dataclasses.asdict(self.last_target) if self.last_target else None,
            "blocked_by_safety_zone": self.blocked_by_safety_zone,
        }


class ShowEngine:
    def __init__(self, room: Room, library: FixtureLibrary, dmx: DmxOutput):
        self.room = room
        self.library = library
        self.dmx = dmx
        self.groups: dict[str, Group] = {}
        # fixture id -> what it showed before the calibration beam took it over (see beam_on)
        self._beam_backup: dict[str, dict] = {}
        self.fixture_state: dict[str, FixtureState] = {
            fid: FixtureState() for fid in room.fixtures
        }
        self._apply_all_fixture_defaults()
        self.sync_all_group()

    def _apply_all_fixture_defaults(self) -> None:
        for fid in self.room.fixtures:
            profile = self.profile_for(fid)
            for role, value in profile.defaults.items():
                self.set_role_value(fid, role, value)

    # -- lookups ------------------------------------------------------

    def profile_for(self, fixture_id: str) -> FixtureProfile:
        instance = self.room.fixtures[fixture_id]
        profile = self.library.get(instance.profile_id)
        if profile is None:
            raise KeyError(f"unknown fixture profile {instance.profile_id!r}")
        return profile

    def state_for(self, fixture_id: str) -> FixtureState:
        return self.fixture_state.setdefault(fixture_id, FixtureState())

    def resolve_fixture_ids(self, target: Union[str, Iterable[str]]) -> list[str]:
        """target may be a fixture id, a group id, or a list of either (the
        union, de-duplicated, in order). Passing the whole selection in one
        call matters for dimmer/strobe: whether the fixtures share one
        dimmer/strobe channel is judged across the whole set."""
        if not isinstance(target, str):
            resolved: list[str] = []
            for one in target:
                for fid in self.resolve_fixture_ids(one):
                    if fid not in resolved:
                        resolved.append(fid)
            return resolved
        if target in self.room.fixtures:
            return [target]
        if target in self.groups:
            return list(self.groups[target].fixture_ids)
        raise KeyError(f"no fixture or group with id {target!r}")

    # -- low-level channel access --------------------------------------

    def set_role_value(self, fixture_id: str, role: str, value: int) -> None:
        """Set a well-known function channel (pan, red, strobe, ...)."""
        if role == "dimmer":
            self._set_light(fixture_id, dimmer=value)
            return
        if role == "strobe":
            self._set_light(fixture_id, strobe=value)
            return
        profile = self.profile_for(fixture_id)
        instance = self.room.fixtures[fixture_id]
        offset = profile.channel_for(role)
        if offset is None:
            return  # fixture doesn't have this function; silently no-op like most consoles
        dmx_channel = instance.channel_for(offset)
        # `value` is the logical 0-255 the UI/animations use; the profile may
        # squeeze it into a sub-range of the physical channel (shared
        # dimmer/strobe channels).
        self.dmx.set_channel(dmx_channel, profile.raw_value(role, value))
        self.state_for(fixture_id).values[role] = value

    def set_custom_channel(self, fixture_id: str, channel_offset: int, value: int) -> None:
        """Set a custom/unmapped channel by its 1-indexed offset within the fixture."""
        instance = self.room.fixtures[fixture_id]
        dmx_channel = instance.channel_for(channel_offset)
        self.dmx.set_channel(dmx_channel, value)
        self.state_for(fixture_id).values[f"custom_{channel_offset}"] = value

    # -- high-level control, usable on a single fixture or a group ------

    # -- zones ---------------------------------------------------------------
    #
    # A fixture can have several independently colored zones (a light bar's two
    # spots and two derbies). `zones` arguments below are a list of zone ids, or
    # None for "every zone". A fixture with no declared zones is one implicit
    # "main" zone, driven through the plain red/green/blue/white roles exactly as
    # before, so selecting zones never breaks a normal fixture: it is simply
    # skipped if none of the named zones exist on it.

    def _selected_zones(self, fixture_id: str, zones: Optional[Iterable[str]]):
        wanted = None if zones is None else set(zones)
        return [z for z in self.profile_for(fixture_id).zone_list() if wanted is None or z.id in wanted]

    def zones_for(self, target: Union[str, Iterable[str]]) -> list[dict]:
        """The declared zones across a target set, in first-seen order, de-duplicated
        by id: [{"id", "label", "kind"}]. Fixtures without declared zones add none."""
        seen: dict[str, dict] = {}
        for fid in self.resolve_fixture_ids(target):
            for z in self.profile_for(fid).zones:
                seen.setdefault(z.id, {"id": z.id, "label": z.label, "kind": z.kind})
        return list(seen.values())

    def _zone_state(self, fixture_id: str, zone_id: str) -> dict:
        return self.state_for(fixture_id).zones.setdefault(zone_id, {"color": [0, 0, 0, 0], "dimmer": 255, "strobe": 0})

    def _write_zone(self, fixture_id: str, zone) -> None:
        """Send one zone's color to the wire. A zone with its own dimmer channel gets
        the dimmer written separately; otherwise brightness is applied by scaling the
        color, so per-zone brightness works on fixtures that only have a master dimmer."""
        instance = self.room.fixtures[fixture_id]
        st = self._zone_state(fixture_id, zone.id)
        level = st["dimmer"]
        own_dimmer = "dimmer" in zone.channels
        for i, role in enumerate(("red", "green", "blue", "white")):
            offset = zone.channels.get(role)
            if offset is None:
                continue
            value = st["color"][i]
            raw = value if own_dimmer else round(value * level / 255)
            self.dmx.set_channel(instance.channel_for(offset), raw)
        if own_dimmer:
            self.dmx.set_channel(instance.channel_for(zone.channels["dimmer"]), level)

    def set_color(self, target_id: Union[str, Iterable[str]], red: int, green: int, blue: int,
                  white: Optional[int] = None, zones: Optional[Iterable[str]] = None) -> None:
        wanted = None if zones is None else list(zones)
        for fid in self.resolve_fixture_ids(target_id):
            profile = self.profile_for(fid)
            if profile.has_zones():
                for zone in self._selected_zones(fid, wanted):
                    st = self._zone_state(fid, zone.id)
                    old_white = st["color"][3]
                    st["color"] = [red, green, blue, old_white if white is None else white]
                    self._write_zone(fid, zone)
                continue
            if wanted is not None and MAIN_ZONE_ID not in wanted:
                continue  # zones were named and this plain fixture has none of them
            self.set_role_value(fid, "red", red)
            self.set_role_value(fid, "green", green)
            self.set_role_value(fid, "blue", blue)
            if white is not None:
                self.set_role_value(fid, "white", white)

    def set_zone_dimmer(self, target: Union[str, Iterable[str]], value: int,
                        zones: Optional[Iterable[str]] = None) -> None:
        """Per-zone brightness (logical 0-255) for fixtures that declare zones."""
        value = max(0, min(255, int(value)))
        wanted = None if zones is None else list(zones)
        for fid in self.resolve_fixture_ids(target):
            if not self.profile_for(fid).has_zones():
                continue
            for zone in self._selected_zones(fid, wanted):
                self._zone_state(fid, zone.id)["dimmer"] = value
                self._write_zone(fid, zone)

    # -- dimmer / strobe / shutter (freestyler-style) ---------------------
    #
    # The UI speaks a logical 0-100 % (sent as 0-255) for both dimmer and
    # strobe; the profile's role_ranges map that onto each fixture's real DMX
    # values, so different fixtures behave the same when mixed.
    #
    # Fixtures come in two shapes:
    #   separate  dimmer and strobe on their own channels (e.g. the Beamz)
    #   shared    ONE channel carries both (e.g. the 14ch head: dimmer band,
    #             strobe band). Only one can be in effect; strobe wins while
    #             it is above 0, otherwise the dimmer level is sent.
    #
    # Operator rules for shared fixtures (as in freestyler):
    #   * strobe touched  -> dimmer goes to 0   (the channel now carries strobe)
    #   * dimmer touched  -> strobe goes to 0   (the channel now carries dimmer)
    # ...but only when EVERY fixture in the target set is shared. In a mixed
    # selection (say a head + a Beamz) nothing is zeroed: the held dimmer value
    # stays for the fixtures that can honour it, and everything strobes
    # together, each mapped to its own values.

    def _light_channels(self, fixture_id: str) -> tuple[Optional[int], Optional[int]]:
        profile = self.profile_for(fixture_id)
        dimmer = profile.channel_for("dimmer")
        strobe = profile.channel_for("strobe") or profile.channel_for("shutter")
        return dimmer, strobe

    def _is_shared_light_channel(self, fixture_id: str) -> bool:
        dimmer, strobe = self._light_channels(fixture_id)
        return dimmer is not None and dimmer == strobe

    def _all_shared(self, fixture_ids: list[str]) -> bool:
        """True if every fixture that has a dimmer or strobe shares one channel
        for them (and there is at least one)."""
        relevant = [f for f in fixture_ids if any(c is not None for c in self._light_channels(f))]
        return bool(relevant) and all(self._is_shared_light_channel(f) for f in relevant)

    def _set_light(self, fixture_id: str, *, dimmer: Optional[int] = None,
                   strobe: Optional[int] = None, closed: Optional[bool] = None) -> None:
        dimmer_ch, strobe_ch = self._light_channels(fixture_id)
        state = self.state_for(fixture_id)
        if dimmer is not None and dimmer_ch is not None:
            state.values["dimmer"] = max(0, min(255, int(dimmer)))
        if strobe is not None and strobe_ch is not None:
            state.values["strobe"] = max(0, min(255, int(strobe)))
        if closed is not None:
            state.shutter_closed = closed
        self._write_light(fixture_id)

    def _write_light(self, fixture_id: str) -> None:
        """Compute and send the dimmer/strobe channel(s) from the light state."""
        profile = self.profile_for(fixture_id)
        instance = self.room.fixtures[fixture_id]
        dimmer_ch, strobe_ch = self._light_channels(fixture_id)
        state = self.state_for(fixture_id)
        dimmer = state.values.get("dimmer")
        strobe = state.values.get("strobe")

        def send(offset: int, raw: int) -> None:
            self.dmx.set_channel(instance.channel_for(offset), raw)

        if dimmer_ch is not None and dimmer_ch == strobe_ch:  # shared channel
            if state.shutter_closed:
                send(dimmer_ch, profile.raw_value("dimmer", 0))
            elif strobe:  # strobe wins the channel while active
                send(dimmer_ch, profile.raw_value("strobe", strobe))
            elif dimmer is not None:
                send(dimmer_ch, profile.raw_value("dimmer", dimmer))
            elif strobe is not None:
                send(dimmer_ch, profile.raw_value("strobe", 0))
            return

        if dimmer_ch is not None and (state.shutter_closed or dimmer is not None):
            send(dimmer_ch, profile.raw_value("dimmer", 0 if state.shutter_closed else dimmer))
        if strobe_ch is not None and (state.shutter_closed or strobe is not None):
            raw = profile.raw_value("strobe", 0 if state.shutter_closed else strobe)
            send(strobe_ch, raw)

    def set_dimmer(self, target: Union[str, Iterable[str]], value: int,
                   zones: Optional[Iterable[str]] = None) -> None:
        """Master dimmer, or -- when `zones` names zones -- per-zone brightness on
        fixtures that declare zones (and, with no master dimmer channel, on all of
        their zones). A plain fixture just uses its master dimmer."""
        wanted = None if zones is None else list(zones)
        master: list[str] = []
        for fid in self.resolve_fixture_ids(target):
            profile = self.profile_for(fid)
            if profile.has_zones() and (wanted is not None or profile.channel_for("dimmer") is None):
                self.set_zone_dimmer(fid, value, wanted)
            elif wanted is not None and not profile.has_zones() and MAIN_ZONE_ID not in wanted:
                continue  # zones were named and this plain fixture has none of them
            else:
                master.append(fid)
        all_shared = self._all_shared(master)
        for fid in master:
            shared = self._is_shared_light_channel(fid)
            self._set_light(fid, dimmer=value, closed=False,
                            strobe=0 if (shared and all_shared) else None)

    def set_strobe(self, target: Union[str, Iterable[str]], value: int,
                   zones: Optional[Iterable[str]] = None) -> dict[str, list[str]]:
        """Strobe speed, logical 0 (none) .. 255 (fastest). With `zones`, only those zones of
        a fixture that gives its zones strobe channels (a light bar's spots and derbies);
        without, everything on the fixture strobes.

        Zones can share a strobe channel (the bar's two spots are on one), in which case
        strobing one strobes the others too. Returns {fixture_id: [zone ids that strobed
        without being asked to]} so the UI can say so."""
        value = max(0, min(255, int(value)))
        wanted = None if zones is None else list(zones)
        also: dict[str, list[str]] = {}
        whole: list[str] = []          # fixtures driven through their one fixture-wide strobe channel
        for fid in self.resolve_fixture_ids(target):
            profile = self.profile_for(fid)
            if profile.has_zone_strobe():
                dragged = self._set_zone_strobe(fid, value, wanted)
                if dragged:
                    also[fid] = dragged
                self._set_light(fid, closed=False)
                if wanted is None and (profile.channel_for("strobe") or profile.channel_for("shutter")):
                    whole.append(fid)
                continue
            if wanted is not None and not profile.has_zones() and MAIN_ZONE_ID not in wanted:
                continue  # zones were named and this plain fixture has none of them
            whole.append(fid)
        all_shared = self._all_shared(whole)
        for fid in whole:
            shared = self._is_shared_light_channel(fid)
            self._set_light(fid, strobe=value, closed=False,
                            dimmer=0 if (shared and all_shared and value > 0) else None)
        return also

    def _silence_zone_strobe(self, fixture_id: str) -> None:
        profile = self.profile_for(fixture_id)
        instance = self.room.fixtures[fixture_id]
        for offset in {z.channels["strobe"] for z in profile.zones if "strobe" in z.channels}:
            self.dmx.set_channel(instance.channel_for(offset), profile.raw_value("strobe", 0))

    def _set_zone_strobe(self, fixture_id: str, value: int, wanted: Optional[list[str]]) -> list[str]:
        """Write the strobe channel of each wanted zone that has one; every zone on a shared
        channel takes the value, since it is one physical channel. Returns the zones that
        strobed although they were not asked for."""
        profile = self.profile_for(fixture_id)
        instance = self.room.fixtures[fixture_id]
        chosen = {z.id for z in self._selected_zones(fixture_id, wanted)}
        channels = {z.channels["strobe"] for z in profile.zones if z.id in chosen and "strobe" in z.channels}
        raw = profile.raw_value("strobe", value)
        for offset in channels:
            self.dmx.set_channel(instance.channel_for(offset), raw)
        dragged: list[str] = []
        for z in profile.zones:
            if z.channels.get("strobe") in channels:
                self._zone_state(fixture_id, z.id)["strobe"] = value
                if z.id not in chosen:
                    dragged.append(z.id)
        if wanted is None:
            self.state_for(fixture_id).values["strobe"] = value
        return dragged

    def set_shutter(self, target: Union[str, Iterable[str]], closed: bool) -> None:
        """Closed: dark, dimmer/strobe values are remembered. Open: light on,
        no strobe; a shared-channel fixture whose dimmer is at 0 (e.g. after
        strobing) is brought to full so Open is never dark."""
        for fid in self.resolve_fixture_ids(target):
            if closed:
                self._set_light(fid, closed=True)
                if self.profile_for(fid).has_zone_strobe():
                    self._silence_zone_strobe(fid)   # dark means dark; the strobe setting is remembered
                continue
            dimmer = None
            if self._is_shared_light_channel(fid) and not self.state_for(fid).values.get("dimmer"):
                dimmer = 255
            if self.profile_for(fid).has_zone_strobe():
                self._set_zone_strobe(fid, 0, None)
            self._set_light(fid, closed=False, strobe=0, dimmer=dimmer)

    def set_raw_pan_tilt(self, target_id: str, pan: int, tilt: int,
                         pan_fine: int = 0, tilt_fine: int = 0) -> None:
        """Direct pan/tilt slider control (no 3D targeting)."""
        for fid in self.resolve_fixture_ids(target_id):
            self.set_role_value(fid, "pan", pan)
            self.set_role_value(fid, "pan_fine", pan_fine)
            self.set_role_value(fid, "tilt", tilt)
            self.set_role_value(fid, "tilt_fine", tilt_fine)
            self.state_for(fid).last_target = None
            self.state_for(fid).blocked_by_safety_zone = None

    def set_custom(self, target_id: str, label: str, value: int) -> None:
        for fid in self.resolve_fixture_ids(target_id):
            profile = self.profile_for(fid)
            match = next((c for c in profile.custom_channels if c.label == label), None)
            if match is None:
                continue
            self.set_custom_channel(fid, match.channel, value)

    # -- calibration ----------------------------------------------------
    #
    # Checking a rig's calibration means pointing every head at the same spot and looking at
    # where the beams land. That needs each head lit (dim, so it is not blinding), and the lights
    # put back the way they were afterwards.

    BEAM_LEVEL = 51   # red at ~20 %: enough to see where a head lands

    def beam_on(self, fixture_ids: Iterable[str]) -> None:
        for fid in fixture_ids:
            if fid not in self.room.fixtures:
                continue
            if fid not in self._beam_backup:
                st = self.state_for(fid)
                self._beam_backup[fid] = {"values": dict(st.values), "zones": copy.deepcopy(st.zones)}
            profile = self.profile_for(fid)
            if profile.has_color():
                self.set_color(fid, self.BEAM_LEVEL, 0, 0, white=0)
            self.set_strobe(fid, 0)
            self.set_dimmer(fid, 255)
            self.set_shutter(fid, False)

    def beam_off(self, fixture_ids: Optional[Iterable[str]] = None) -> None:
        """Put the given fixtures (default: every one the beam took over) back as they were."""
        ids = list(self._beam_backup) if fixture_ids is None else [f for f in fixture_ids if f in self._beam_backup]
        for fid in ids:
            saved = self._beam_backup.pop(fid)
            if fid not in self.room.fixtures:
                continue
            profile = self.profile_for(fid)
            values = saved["values"]
            if profile.has_color():
                if profile.has_zones():
                    for zone in profile.zones:
                        before = saved["zones"].get(zone.id)
                        self.state_for(fid).zones[zone.id] = copy.deepcopy(before) if before else {
                            "color": [0, 0, 0, 0], "dimmer": 255, "strobe": 0}
                        self._write_zone(fid, zone)
                else:
                    for role in ("red", "green", "blue", "white"):
                        if role in profile.channels:
                            self.set_role_value(fid, role, values.get(role, 0))
            self.set_dimmer(fid, values.get("dimmer", 0))

    def beam_ids(self) -> list[str]:
        return list(self._beam_backup)

    def calibration_aim(self, fixture_ids: Iterable[str], point: Vec3) -> dict[str, dict]:
        """Aim each fixture at `point` and report where its pan/tilt ended up, so the operator
        can see the numbers behind what the beams do."""
        results: dict[str, dict] = {}
        for fid in fixture_ids:
            if fid not in self.room.fixtures:
                continue
            result = dict(self.aim_at_point(fid, point)[fid])
            values = self.state_for(fid).values
            result["pan_dmx"] = values.get("pan")
            result["tilt_dmx"] = values.get("tilt")
            result["pan_fine_dmx"] = values.get("pan_fine", 0)
            result["tilt_fine_dmx"] = values.get("tilt_fine", 0)
            results[fid] = result
        return results

    # -- 3D aiming ------------------------------------------------------

    def aim_at_point(self, target_id: str, point: Vec3, allow_unsafe: bool = False) -> dict:
        """Aim a fixture (or every fixture in a group) at a room-space
        point, running the IK core and checking safety zones first.

        Returns a per-fixture result dict so the UI can show which
        fixtures (if any) were blocked.
        """
        results: dict[str, dict] = {}
        for fid in self.resolve_fixture_ids(target_id):
            instance = self.room.fixtures[fid]
            profile = self.profile_for(fid)
            if not profile.has_pan_tilt():
                results[fid] = {"ok": False, "reason": "fixture has no pan/tilt"}
                continue

            zone_hit = target_violates_safety_zones(
                instance.position, instance.orientation, point,
                list(self.room.safety_zones.values()),
            )
            if zone_hit is not None and not allow_unsafe:
                self.state_for(fid).blocked_by_safety_zone = zone_hit.id
                results[fid] = {"ok": False, "reason": f"blocked by safety zone {zone_hit.name}"}
                continue

            ik_result = compute_pan_tilt(
                instance.position,
                instance.orientation,
                point,
                pan_range_deg=profile.pan_range_deg or 540.0,
                tilt_range_deg=profile.tilt_range_deg or 270.0,
                inverted_pan=instance.inverted_pan,
                inverted_tilt=instance.inverted_tilt,
                pan_offset_deg=instance.pan_offset_deg,
                tilt_offset_deg=instance.tilt_offset_deg,
            )
            self.set_role_value(fid, "pan", ik_result.pan_dmx)
            self.set_role_value(fid, "pan_fine", ik_result.pan_fine_dmx)
            self.set_role_value(fid, "tilt", ik_result.tilt_dmx)
            self.set_role_value(fid, "tilt_fine", ik_result.tilt_fine_dmx)

            state = self.state_for(fid)
            state.last_target = point
            state.blocked_by_safety_zone = None
            results[fid] = {
                "ok": True,
                "in_range": ik_result.in_range,
                "pan_angle_deg": ik_result.pan_angle_deg,
                "tilt_angle_deg": ik_result.tilt_angle_deg,
            }
        return results

    # -- groups -----------------------------------------------------

    def add_fixture(self, instance) -> None:  # room.model.FixtureInstance
        self.room.add_fixture(instance)
        self.fixture_state.setdefault(instance.id, FixtureState())
        profile = self.profile_for(instance.id)
        for role, value in profile.defaults.items():
            self.set_role_value(instance.id, role, value)
        self.sync_all_group()

    def remove_fixture(self, fixture_id: str) -> None:
        self.room.remove_fixture(fixture_id)
        self.fixture_state.pop(fixture_id, None)
        for group in self.groups.values():
            if fixture_id in group.fixture_ids:
                group.fixture_ids.remove(fixture_id)
        self.sync_all_group()

    def add_group(self, group: Group) -> None:
        self.groups[group.id] = group
        self.sync_all_group()

    def remove_group(self, group_id: str) -> None:
        if group_id == ALL_GROUP_ID:
            return  # the "All lights" group is built in
        self.groups.pop(group_id, None)

    def set_groups(self, groups: Iterable[Group]) -> None:
        """Replace every group (config import / startup)."""
        self.groups = {g.id: g for g in groups}
        self.sync_all_group()

    def sync_all_group(self) -> bool:
        """Make the built-in "All lights" group exist and hold every fixture in the room, in
        patch order. Called after anything that can change the fixtures or the groups, so a
        light can never be missing from it. Returns True if anything had to change."""
        everyone = list(self.room.fixtures)
        group = self.groups.get(ALL_GROUP_ID)
        if group is None:
            self.groups[ALL_GROUP_ID] = Group(id=ALL_GROUP_ID, name=ALL_GROUP_NAME, fixture_ids=everyone,
                                             color=ALL_GROUP_COLOR)
            return True
        # keep the order the group already has, then add newcomers
        ordered = [f for f in group.fixture_ids if f in self.room.fixtures]
        ordered += [f for f in everyone if f not in ordered]
        changed = ordered != group.fixture_ids
        group.fixture_ids = ordered
        return changed

    def load_room(self, room: Room) -> None:
        """Replace the live room wholesale (config import) -- old fixture ids
        may no longer exist and new ones carry no prior state, so
        fixture_state is rebuilt from scratch rather than merged, and each
        fixture's role defaults are reapplied so DMX output reflects the
        just-loaded room instead of holding stale channel values."""
        self.room = room
        self.fixture_state = {fid: FixtureState() for fid in room.fixtures}
        self._apply_all_fixture_defaults()
        self.sync_all_group()

    def blackout(self) -> None:
        self.dmx.blackout()
        for state in self.fixture_state.values():
            state.values.clear()
            state.zones.clear()
            state.last_target = None

    # -- snapshot for API/UI -----------------------------------------

    def snapshot(self) -> dict:
        return {
            "room": self.room.to_dict(),
            "groups": [g.to_dict() for g in self.groups.values()],
            "fixture_state": {fid: st.to_dict() for fid, st in self.fixture_state.items()},
            "dmx_status": self.dmx.status(),
        }
