"""Groups: a named set of fixtures with one button in the UI.

Freestyler-style: clicking a group button selects all its fixtures, and
any subsequent RGB/strobe/pan-tilt/custom-slider change applies to all of
them at once, in addition to being able to select+control a single
fixture the same way.
"""

from __future__ import annotations

import dataclasses


@dataclasses.dataclass
class Group:
    id: str
    name: str
    fixture_ids: list[str] = dataclasses.field(default_factory=list)
    color: str = "#3a7bd5"  # button color in the UI

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "Group":
        return Group(
            id=d["id"],
            name=d["name"],
            fixture_ids=list(d.get("fixture_ids", [])),
            color=d.get("color", "#3a7bd5"),
        )
