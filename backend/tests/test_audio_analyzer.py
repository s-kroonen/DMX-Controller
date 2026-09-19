"""Beat/BPM/level analysis against synthetic audio (no hardware, faster than real time)."""

import numpy as np
import pytest

from app.audio.analyzer import SAMPLE_RATE, AudioAnalyzer

RNG = np.random.default_rng(7)


def kick_track(bpm: float, seconds: float, amp: float = 0.6, hats: bool = True,
               noise: float = 0.004) -> np.ndarray:
    n = int(seconds * SAMPLE_RATE)
    out = RNG.normal(0, noise, n).astype(np.float32)
    period = 60.0 / bpm
    t_kick = np.arange(int(0.09 * SAMPLE_RATE)) / SAMPLE_RATE
    kick = np.sin(2 * np.pi * (55 + 90 * np.exp(-t_kick * 40)) * t_kick) * np.exp(-t_kick * 35)
    hat = RNG.normal(0, 1, int(0.02 * SAMPLE_RATE)) * np.exp(-np.arange(int(0.02 * SAMPLE_RATE)) / 200)
    beat = 0
    while beat * period < seconds:
        i = int(beat * period * SAMPLE_RATE)
        end = min(n, i + len(kick))
        out[i:end] += amp * kick[:end - i]
        if hats:
            j = int((beat + 0.5) * period * SAMPLE_RATE)
            if j + len(hat) < n:
                out[j:j + len(hat)] += 0.15 * amp * hat
        beat += 1
    return out


def run(analyzer: AudioAnalyzer, audio: np.ndarray, chunk: int = 700):
    """Feed in odd-sized chunks (capture blocks never line up with the hop)."""
    f = None
    for i in range(0, len(audio), chunk):
        f = analyzer.process(audio[i:i + chunk])
    return f


@pytest.mark.parametrize("bpm", [90, 120, 128, 150])
def test_locks_onto_the_tempo_of_a_kick_track(bpm):
    f = run(AudioAnalyzer(), kick_track(bpm, 14))
    assert f.bpm == pytest.approx(bpm, abs=2.5)
    assert f.bpm_confidence > 0.3


def test_counts_about_one_beat_per_kick():
    a = AudioAnalyzer()
    f = run(a, kick_track(120, 12))
    assert 20 <= f.beat_count <= 26   # 24 kicks (a couple lost while locking is fine)


def test_beats_land_close_to_the_kicks():
    a = AudioAnalyzer()
    audio = kick_track(120, 10)
    beat_times = []
    last = 0
    for i in range(0, len(audio), 512):
        f = a.process(audio[i:i + 512])
        if f.beat_count != last:
            beat_times.append(f.last_beat_time)
            last = f.beat_count
    beat_times = [t for t in beat_times if t > 4]   # after lock-in
    offsets = [(t % 0.5) for t in beat_times]
    # every beat within ~60 ms of a kick (kicks are every 0.5 s)
    assert all(o < 0.06 or o > 0.44 for o in offsets), offsets


def test_follows_a_tempo_change():
    a = AudioAnalyzer()
    run(a, kick_track(100, 12))
    f = run(a, kick_track(140, 14))
    assert f.bpm == pytest.approx(140, abs=3)


def test_silence_gives_no_beats_and_no_tempo():
    f = run(AudioAnalyzer(), np.zeros(SAMPLE_RATE * 6, dtype=np.float32))
    assert f.beat_count == 0
    assert f.bpm == 0.0
    assert f.level == 0.0


def test_background_noise_alone_is_not_a_beat():
    noise = RNG.normal(0, 0.02, SAMPLE_RATE * 8).astype(np.float32)
    f = run(AudioAnalyzer(), noise)
    assert f.beat_count <= 2


def test_levels_are_normalised_regardless_of_volume():
    quiet = run(AudioAnalyzer(), kick_track(120, 10, amp=0.08))
    loud = run(AudioAnalyzer(), kick_track(120, 10, amp=0.9))
    assert quiet.bpm == pytest.approx(loud.bpm, abs=3)
    assert abs(quiet.beat_count - loud.beat_count) <= 4


def test_band_energies_separate_bass_from_treble():
    t = np.arange(SAMPLE_RATE * 2) / SAMPLE_RATE
    bass = run(AudioAnalyzer(), (0.5 * np.sin(2 * np.pi * 80 * t)).astype(np.float32))
    treble = run(AudioAnalyzer(), (0.5 * np.sin(2 * np.pi * 6000 * t)).astype(np.float32))
    assert bass.bass > 0.8 and bass.high < 0.05
    assert treble.high > 0.8 and treble.bass < 0.05


def test_gain_scales_the_normalised_levels():
    t = np.arange(SAMPLE_RATE) / SAMPLE_RATE
    tone = (0.5 * np.sin(2 * np.pi * 80 * t)).astype(np.float32)
    half = run(AudioAnalyzer(gain=0.5), tone)
    assert 0.35 < half.bass < 0.65


def test_higher_sensitivity_finds_more_beats_in_a_weak_signal():
    weak = kick_track(120, 10, amp=0.15, noise=0.02)
    strict = run(AudioAnalyzer(sensitivity=0.4), weak).beat_count
    eager = run(AudioAnalyzer(sensitivity=2.0), weak).beat_count
    assert eager >= strict


def test_beat_pulse_peaks_at_a_beat_and_decays():
    a = AudioAnalyzer()
    audio = kick_track(120, 8)
    pulses = []
    last = 0
    for i in range(0, len(audio), 512):
        f = a.process(audio[i:i + 512])
        if f.beat_count != last:
            last = f.beat_count
            pulses.append(f.beat_pulse)
    assert pulses and min(pulses) > 0.7
    f = a.feed_silence(1.0)
    assert f.beat_pulse < 0.05


def test_reset_forgets_the_tempo():
    a = AudioAnalyzer()
    run(a, kick_track(120, 10))
    assert a.features().bpm > 0
    a.reset()
    assert a.features().bpm == 0.0


def test_bpm_never_leaves_the_search_range_on_irregular_material():
    """Real music (swing, fills, dropouts) must not produce runaway tempo readings."""
    a = AudioAnalyzer()
    audio = RNG.normal(0, 0.01, SAMPLE_RATE * 20).astype(np.float32)
    t = 0.0
    while t < 19.5:                       # kicks at irregular intervals
        i = int(t * SAMPLE_RATE)
        k = np.sin(2 * np.pi * 60 * np.arange(3000) / SAMPLE_RATE) * np.exp(-np.arange(3000) / 500)
        audio[i:i + 3000] += 0.5 * k[:len(audio[i:i + 3000])]
        t += float(RNG.uniform(0.25, 0.9))
    seen = []
    for i in range(0, len(audio), 512):
        f = a.process(audio[i:i + 512])
        seen.append(f.bpm)
    assert all(b == 0 or 70 <= b <= 190 for b in seen), sorted(set(round(b) for b in seen))


def test_a_locked_tempo_is_not_dislodged_by_a_short_glitch():
    a = AudioAnalyzer()
    run(a, kick_track(128, 12))
    locked = a.features().bpm
    run(a, RNG.normal(0, 0.01, SAMPLE_RATE * 1).astype(np.float32))   # 1 s of near-silence
    run(a, kick_track(128, 6))
    assert a.features().bpm == pytest.approx(locked, abs=3)
