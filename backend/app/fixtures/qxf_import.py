"""Importer for QLC+ .qxf fixture definitions.

QLC+ has a large community fixture library (including moving heads like
the MHL108 family). Rather than hand-authoring every fixture profile,
this lets you drop a .qxf in and get one FixtureProfile per <Mode>
(a QXF fixture can have several channel-count modes, same as ours).

QXF channel roles are inferred from the channel's <Group> element and
name -- QXF doesn't have a single canonical "this is Pan" flag the way
our schema does, so this uses the same heuristics DMX software commonly
uses (group name + capability text).
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

from .schema import CustomChannel, FixtureProfile

_GROUP_TO_ROLE = {
    "pan": "pan",
    "tilt": "tilt",
    "intensity": "dimmer",
    "colour": None,  # resolved per-channel by name below
    "color": None,
    "gobo": "gobo_wheel",
    "shutter": "shutter",
    "speed": None,
    "prism": "prism",
    "beam": None,
    "nothing": None,
    "maintenance": None,
    "effect": None,
}

_NAME_TO_ROLE = [
    (re.compile(r"pan.*fine", re.I), "pan_fine"),
    (re.compile(r"^pan\b", re.I), "pan"),
    (re.compile(r"tilt.*fine", re.I), "tilt_fine"),
    (re.compile(r"^tilt\b", re.I), "tilt"),
    (re.compile(r"^red\b", re.I), "red"),
    (re.compile(r"^green\b", re.I), "green"),
    (re.compile(r"^blue\b", re.I), "blue"),
    (re.compile(r"^white\b", re.I), "white"),
    (re.compile(r"^amber\b", re.I), "amber"),
    (re.compile(r"^uv\b|ultra ?violet", re.I), "uv"),
    (re.compile(r"dimmer|intensity|master", re.I), "dimmer"),
    (re.compile(r"strobe|shutter", re.I), "strobe"),
    (re.compile(r"zoom", re.I), "zoom"),
    (re.compile(r"focus", re.I), "focus"),
    (re.compile(r"colou?r wheel", re.I), "color_wheel"),
    (re.compile(r"gobo.*rotation|gobo.*index", re.I), "gobo_rotation"),
    (re.compile(r"gobo", re.I), "gobo_wheel"),
    (re.compile(r"prism", re.I), "prism"),
]


def _infer_role(channel_name: str, group: str | None) -> str | None:
    for pattern, role in _NAME_TO_ROLE:
        if pattern.search(channel_name):
            return role
    if group:
        return _GROUP_TO_ROLE.get(group.strip().lower())
    return None


def parse_qxf(path: str | Path) -> list[FixtureProfile]:
    """Return one FixtureProfile per <Mode> defined in the .qxf file."""
    tree = ET.parse(path)
    root = tree.getroot()

    def tag(elem: ET.Element) -> str:
        # strip namespace, QXF sometimes has xmlns
        return elem.tag.rsplit("}", 1)[-1]

    manufacturer = ""
    model = ""
    fixture_type = "generic"
    channel_groups: dict[str, str | None] = {}  # channel name -> group name

    for child in root.iter():
        t = tag(child)
        if t == "Manufacturer":
            manufacturer = (child.text or "").strip()
        elif t == "Model":
            model = (child.text or "").strip()
        elif t == "Type":
            qxf_type = (child.text or "").strip().lower()
            if "moving" in qxf_type or "head" in qxf_type or "scanner" in qxf_type:
                fixture_type = "moving_head"
            elif "laser" in qxf_type:
                fixture_type = "laser"
            elif "led" in qxf_type or "par" in qxf_type or "dimmer" in qxf_type:
                fixture_type = "par"
        elif t == "Channel":
            name = child.attrib.get("Name", "")
            group_elem = next((c for c in child if tag(c) == "Group"), None)
            channel_groups[name] = group_elem.text if group_elem is not None else None

    profiles: list[FixtureProfile] = []
    for mode_elem in root.iter():
        if tag(mode_elem) != "Mode":
            continue
        mode_name = mode_elem.attrib.get("Name", "mode")
        channel_map: dict[str, int] = {}
        custom: list[CustomChannel] = []
        max_channel = 0

        for ch_elem in mode_elem:
            if tag(ch_elem) != "Channel":
                continue
            number = int(ch_elem.attrib.get("Number", "0")) + 1  # QXF is 0-indexed
            name = (ch_elem.text or "").strip()
            max_channel = max(max_channel, number)
            role = _infer_role(name, channel_groups.get(name))
            if role and role not in channel_map:
                channel_map[role] = number
            else:
                custom.append(CustomChannel(channel=number, label=name or f"Ch {number}"))

        slug_source = f"{manufacturer}-{model}-{mode_name}".lower()
        slug = re.sub(r"[^a-z0-9]+", "-", slug_source).strip("-")

        profiles.append(
            FixtureProfile(
                id=slug,
                name=f"{manufacturer} {model}".strip() or slug,
                manufacturer=manufacturer,
                mode=mode_name,
                channel_count=max_channel,
                channels=channel_map,
                custom_channels=custom,
                fixture_type=fixture_type,
            )
        )

    return profiles
