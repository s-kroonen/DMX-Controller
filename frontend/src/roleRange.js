// Inverse of FixtureProfile.raw_value() (backend/app/fixtures/schema.py):
// given a role's raw DMX value, recover the logical 0-255 slider value that
// would have produced it. Shared by desktop (panels.js) and mobile
// (mobile/app.js) so both can sync their RGB/dimmer/strobe/pan-tilt
// controls to reflect a fixture's ACTUAL current state -- from another
// session, an animation, or a page reload -- not just write to it.
export function logicalFromRaw(profile, role, raw) {
  raw = Math.max(0, Math.min(255, Number(raw) || 0));
  const rng = profile?.role_ranges?.[role];
  if (!rng) return raw; // no shared-channel mapping -- raw value IS the logical value
  if (raw === rng.zero) return 0;
  const span = rng.max - rng.min;
  if (span <= 0) return 0;
  const frac = Math.max(0, Math.min(1, (raw - rng.min) / span));
  return Math.round(frac * 254) + 1;
}
