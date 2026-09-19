"""Audio analysis: levels, frequency bands, beat detection and BPM.

Pure numpy, no audio hardware, no wall clock -- time is the number of samples
fed in, so tests can run minutes of synthetic audio in milliseconds and the
capture thread can feed silence when a loopback device delivers nothing.

Pipeline (one step per `hop` samples, ~86 steps/s at 44.1 kHz / 512):
  * level / bass / mid / high: RMS and band energies, each auto-gain
    normalised to 0..1 (a slow peak follower), so the result doesn't depend
    on how loud the music is; `gain` scales the normalised value.
  * onset strength: half-wave rectified spectral flux of log-compressed
    magnitudes, weighted towards the kick region.
  * beat: local peak of the onset strength above an adaptive threshold
    (mean + k*std of the last ~2 s), with a minimum spacing. If a tempo is
    locked and a beat goes missing, one is filled in on the beat grid so
    chases keep running through a breakdown.
  * BPM: autocorrelation of the onset envelope over the last ~6 s, with a
    harmonic comb (prefers the beat over its sub-divisions) and a mild prior
    around 125 BPM, then smoothed (a jump needs two agreeing estimates).
"""

from __future__ import annotations

import collections
import dataclasses
import math
import threading
from typing import Optional

import numpy as np

SAMPLE_RATE = 44100
HOP = 512
FFT_SIZE = 1024

MIN_BPM = 70.0
MAX_BPM = 190.0
SILENCE_RMS = 0.004        # below this (about -48 dBFS) nothing counts as a beat
MIN_BEAT_GAP_S = 0.27      # ~222 BPM ceiling before a tempo is known


@dataclasses.dataclass
class AudioFeatures:
    time: float = 0.0             # seconds of audio processed so far
    rms: float = 0.0              # raw RMS of the latest hop (0..1 full scale)
    level: float = 0.0            # 0..1, auto-gain normalised
    bass: float = 0.0
    mid: float = 0.0
    high: float = 0.0
    onset: float = 0.0            # 0..1 onset strength (normalised)
    beat_count: int = 0           # increments on every beat (audio or filled-in)
    last_beat_time: float = 0.0   # analyzer clock at the last beat
    beat_pulse: float = 0.0       # 1.0 at a beat, decays quickly
    bpm: float = 0.0              # 0 = no tempo locked yet
    bpm_confidence: float = 0.0   # 0..1

    def to_dict(self) -> dict:
        return {k: (round(v, 4) if isinstance(v, float) else v)
                for k, v in dataclasses.asdict(self).items()}


class _PeakFollower:
    """x / (recent peak): instant attack, slow decay, never below `floor`."""

    def __init__(self, decay_seconds: float, floor: float, rate_hz: float):
        self.decay = 0.5 ** (1.0 / (decay_seconds * rate_hz))
        self.floor = floor
        self.peak = floor

    def normalise(self, x: float) -> float:
        self.peak = max(x, self.peak * self.decay, self.floor)
        return min(1.0, x / self.peak)


class AudioAnalyzer:
    def __init__(self, sample_rate: int = SAMPLE_RATE, hop: int = HOP,
                 fft_size: int = FFT_SIZE, gain: float = 1.0, sensitivity: float = 1.0):
        self.sample_rate = sample_rate
        self.hop = hop
        self.fft_size = fft_size
        self.fps = sample_rate / hop
        self.gain = gain
        self.sensitivity = sensitivity  # >1 = more (and weaker) beats, <1 = fewer

        self._lock = threading.Lock()
        self._pending = np.zeros(0, dtype=np.float32)
        self._buf = np.zeros(fft_size, dtype=np.float32)
        self._window = np.hanning(fft_size).astype(np.float32)
        self._scale = 2.0 / float(self._window.sum())  # full-scale sine -> magnitude ~1
        freqs = np.fft.rfftfreq(fft_size, 1.0 / sample_rate)
        self._bass_bins = (freqs >= 30) & (freqs < 150)
        self._mid_bins = (freqs >= 150) & (freqs < 2000)
        self._high_bins = (freqs >= 2000) & (freqs < 12000)
        self._kick_bins = (freqs >= 30) & (freqs < 200)
        self._flux_bins = (freqs >= 30) & (freqs < 5000)
        self._prev_comp: Optional[np.ndarray] = None

        self._followers = {
            "level": _PeakFollower(4.0, 0.02, self.fps),
            "bass": _PeakFollower(4.0, 0.02, self.fps),
            "mid": _PeakFollower(4.0, 0.01, self.fps),
            "high": _PeakFollower(4.0, 0.005, self.fps),
            "onset": _PeakFollower(4.0, 0.02, self.fps),
        }
        self._onsets: collections.deque[float] = collections.deque(maxlen=int(self.fps * 6))
        self._recent: collections.deque[float] = collections.deque(maxlen=3)
        self._stats: collections.deque[float] = collections.deque(maxlen=int(self.fps * 2))

        self._t = 0.0
        self._last_beat_t = -10.0
        self._beat_count = 0
        self._bpm = 0.0
        self._bpm_conf = 0.0
        self._candidate = 0.0
        self._steps_since_bpm = 0
        self._features = AudioFeatures()

    # -- public ---------------------------------------------------------

    def process(self, samples: np.ndarray) -> AudioFeatures:
        """Feed mono float samples (any length); returns the latest features."""
        samples = np.asarray(samples, dtype=np.float32).reshape(-1)
        with self._lock:
            self._pending = np.concatenate([self._pending, samples])
            while len(self._pending) >= self.hop:
                hop_samples, self._pending = self._pending[:self.hop], self._pending[self.hop:]
                self._step(hop_samples)
            return self._snapshot_locked()

    def feed_silence(self, seconds: float) -> AudioFeatures:
        return self.process(np.zeros(int(seconds * self.sample_rate), dtype=np.float32))

    def features(self) -> AudioFeatures:
        with self._lock:
            return self._snapshot_locked()

    def reset(self) -> None:
        """Forget tempo and history (e.g. after switching input)."""
        with self._lock:
            self._pending = np.zeros(0, dtype=np.float32)
            self._buf[:] = 0
            self._prev_comp = None
            self._onsets.clear()
            self._recent.clear()
            self._stats.clear()
            self._bpm = self._bpm_conf = self._candidate = 0.0
            self._features.bpm = self._features.bpm_confidence = 0.0
            self._last_beat_t = self._t - 10.0

    # -- internals ------------------------------------------------------

    def _snapshot_locked(self) -> AudioFeatures:
        f = dataclasses.replace(self._features)
        f.time = self._t
        f.beat_pulse = math.exp(-(self._t - self._last_beat_t) / 0.15) if self._beat_count else 0.0
        return f

    def _step(self, hop_samples: np.ndarray) -> None:
        self._t += self.hop / self.sample_rate
        self._buf = np.concatenate([self._buf[self.hop:], hop_samples])

        rms = float(np.sqrt(np.mean(hop_samples.astype(np.float64) ** 2)))
        mag = np.abs(np.fft.rfft(self._buf * self._window)) * self._scale

        def band(mask: np.ndarray) -> float:
            return float(np.sqrt(np.mean(mag[mask] ** 2)))

        comp = np.log1p(100.0 * mag)
        if self._prev_comp is None:
            flux = kick = 0.0
        else:
            diff = np.maximum(comp - self._prev_comp, 0.0)
            flux = float(diff[self._flux_bins].mean())
            kick = float(diff[self._kick_bins].mean())
        self._prev_comp = comp
        onset_raw = 0.6 * kick + 0.4 * flux

        f = self._features
        g = self.gain
        f.rms = rms
        f.level = min(1.0, self._followers["level"].normalise(rms) * g)
        f.bass = min(1.0, self._followers["bass"].normalise(band(self._bass_bins)) * g)
        f.mid = min(1.0, self._followers["mid"].normalise(band(self._mid_bins)) * g)
        f.high = min(1.0, self._followers["high"].normalise(band(self._high_bins)) * g)
        f.onset = self._followers["onset"].normalise(onset_raw)

        self._detect_beat(onset_raw, rms)
        self._steps_since_bpm += 1
        if self._steps_since_bpm >= int(self.fps * 0.5):
            self._steps_since_bpm = 0
            self._estimate_bpm()
        f.beat_count = self._beat_count
        f.last_beat_time = self._last_beat_t
        f.bpm = self._bpm
        f.bpm_confidence = self._bpm_conf

    def _detect_beat(self, onset: float, rms: float) -> None:
        self._onsets.append(onset)
        self._recent.append(onset)
        if len(self._recent) < 3:
            self._stats.append(onset)
            return
        before, peak, latest = self._recent
        stats = np.fromiter(self._stats, dtype=np.float64) if self._stats else np.zeros(1)
        self._stats.append(onset)

        mean, std = float(stats.mean()), float(stats.std())
        # a beat must clear both the statistical bar and a multiple of the running mean,
        # otherwise stationary noise (flat spectrum, random flux) triggers constantly
        k = 1.6 / max(self.sensitivity, 0.1)
        threshold = mean + max(k * std, 3.5 * mean / max(self.sensitivity, 0.1))
        period = 60.0 / self._bpm if self._bpm else 0.0
        min_gap = max(MIN_BEAT_GAP_S, 0.6 * period) if self._bpm_conf >= 0.3 else MIN_BEAT_GAP_S
        loud_enough = rms >= SILENCE_RMS

        is_peak = peak > before and peak >= latest and peak > threshold and peak > 0.02
        if loud_enough and is_peak and self._t - self._last_beat_t >= min_gap:
            self._last_beat_t = self._t
            self._beat_count += 1
            return

        # fill a missed beat on the grid while a confident tempo is locked
        if (loud_enough and period and self._bpm_conf >= 0.3
                and self._t - self._last_beat_t >= 1.3 * period
                and self._t - self._last_beat_t < 4.0 * period):
            self._last_beat_t = max(self._last_beat_t + period, self._t - 0.5 * period)
            self._beat_count += 1

    def _estimate_bpm(self) -> None:
        env = np.fromiter(self._onsets, dtype=np.float64)
        if len(env) < self.fps * 3 or self._features.rms < SILENCE_RMS * 0.5:
            if self._features.rms < SILENCE_RMS * 0.5 and len(env) and env[-int(self.fps):].max(initial=0) < 0.01:
                self._bpm_conf *= 0.9
            return
        # widen the onset spikes a little: beats are quantised to whole hops, and a
        # razor-thin autocorrelation peak misses at fractional-hop periods
        env = np.convolve(env, np.array([1, 2, 3, 2, 1], dtype=np.float64) / 9.0, mode="same")
        env = env - env.mean()
        n = len(env)
        fft_len = 1 << (2 * n - 1).bit_length()
        spec = np.fft.rfft(env, fft_len)
        ac = np.fft.irfft(spec * np.conj(spec))[:n]
        if ac[0] <= 1e-12:
            return
        ac = ac / ac[0]

        lag_min = int(self.fps * 60.0 / MAX_BPM)
        lag_max = min(int(self.fps * 60.0 / MIN_BPM), n // 2 - 1)
        best_lag, best_score = 0, 0.0
        def near(lag: int) -> float:  # local max: tolerate +-1 hop of jitter
            return float(ac[max(lag - 1, 0):min(lag + 2, n)].max())

        for lag in range(lag_min, lag_max + 1):
            score = near(lag)
            for mult, weight in ((2, 0.5), (4, 0.25)):
                if lag * mult < n - 1:
                    score += weight * near(lag * mult)
            bpm = self.fps * 60.0 / lag
            score *= math.exp(-0.5 * (math.log2(bpm / 125.0) / 0.7) ** 2)  # mild prior
            if self._bpm and self._bpm_conf >= 0.25:
                # once locked, favour staying near the current tempo; a real change still
                # wins because the old tempo's autocorrelation collapses
                score *= 1.0 + math.exp(-0.5 * (math.log2(bpm / self._bpm) / 0.06) ** 2)
            if score > best_score:
                best_lag, best_score = lag, score
        if best_lag == 0:
            return

        # parabolic interpolation for a fractional lag
        lag = float(best_lag)
        if 0 < best_lag < n - 1:
            a, b, c = ac[best_lag - 1], ac[best_lag], ac[best_lag + 1]
            denom = a - 2 * b + c
            if abs(denom) > 1e-12:
                # a flat/noisy peak makes this shift blow up: never move more than one lag
                lag += max(-1.0, min(1.0, 0.5 * (a - c) / denom))
        bpm = max(MIN_BPM, min(MAX_BPM, self.fps * 60.0 / lag))
        confidence = float(max(0.0, min(1.0, ac[best_lag])))
        self._bpm_conf = confidence
        if confidence < 0.12:
            return

        if self._bpm == 0.0:
            self._bpm = bpm
        elif abs(bpm - self._bpm) / self._bpm <= 0.06:
            self._bpm += 0.3 * (bpm - self._bpm)
            self._candidate = 0.0
        elif self._candidate and abs(bpm - self._candidate) / self._candidate <= 0.06:
            self._bpm, self._candidate = bpm, 0.0   # two agreeing estimates: accept the jump
        else:
            self._candidate = bpm
