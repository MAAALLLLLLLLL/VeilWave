#!/usr/bin/env python3
"""VeilWave: single-microphone audio-processing research prototype.

One file, no package needed. This is an experimental implementation associated with the
VeilWave manuscript; it is not a verified reproduction of the manuscript's pilot pipeline:

    microphone (Arduino over UART, or sound card; 16 kHz, mono)
      -> STFT (N = 512, H = 256, Hann)
      -> Stage 1  single-channel wind / fabric detector d(t)
      -> if d(t) = 1:  Stage 2  MCRA-2 noise tracking + decision-directed SNR + ERB-domain Wiener filter
                       Stage 3  neural-network mask   (needs trained weights, otherwise off)
                       Stage 4  deep filtering        (identity unless trained weights are given)
                       Stage 5  SNR-conditioned fusion
      -> overlap-add -> AGC -> output (speaker or Bluetooth)

Commands (python veilwave.py <command> -h for the options):
    run         live processing (UART or microphone) with keyboard control
    process     process a WAV file offline (no audio hardware needed)
    calibrate   detector thresholds from a wind-only and a clean-speech recording
    train       train the mask network (needs PyTorch and your own data)
    export-onnx export the mask network to ONNX and quantize it to INT8 (experimental)
    selftest    run the built-in tests
    devices     list the audio devices

STATUS (honest): research code.  No trained network is included; without weights the neural stage is off.
The wind detector is not validated on labelled data, the stethoscope mode is a proposed feature, and no
performance claim is made by this repository.  See README.md.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import queue
import random
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass
from math import gcd

import numpy as np

try:                                   # PyTorch is optional: only the neural stages and training need it
    import torch
    import torch.nn as nn
    import torch.nn.functional as F
    HAVE_TORCH = True
except ImportError:                    # pragma: no cover
    HAVE_TORCH = False

__version__ = "0.1.0"


# =============================================================================================
# 1. Parameters (research defaults; verify against the final manuscript before citing)
# =============================================================================================
@dataclass
class Config:
    fs: int = 16000
    N: int = 512                # 32 ms frame
    H: int = 256                # 16 ms hop (50 % overlap), Hann window
    # Stage 1: single-channel wind / fabric detector
    f_c: float = 250.0          # energy fraction below f_c  (k_c = 8 at 31.25 Hz resolution)
    theta_lf: float = 0.80      # [PLACEHOLDER] calibrate with `calibrate` on your own training data
    theta_var: float = 2.0      # [PLACEHOLDER] idem
    hangover: int = 0           # [ASSUMED] frames the decision stays 1 after the last detection
    # Stage 2: MCRA-2 and ERB Wiener
    alpha_s: float = 0.92
    alpha_d: float = 0.85
    L_min: int = 150            # frames (150 x 16 ms = 2.4 s)
    alpha_dd: float = 0.98
    min_bias: float = 1.0       # [ASSUMED] bias compensation of the minimum (none in the manuscript)
    n_erb: int = 32
    alpha_G: float = 0.92
    g_floor: float = 1e-3       # [ASSUMED] numerical floor of the Wiener gain
    # Stage 4: deep filtering
    L_f: int = 5
    f_df_max: float = 4000.0
    # Stage 5 and AGC
    eta0_db: float = 5.0
    g_max: float = 4.0
    alpha_E: float = 0.95
    E_target_db: float = -18.0
    attack_ms: float = 30.0
    release_ms: float = 150.0

    @property
    def K(self):
        return self.N // 2 + 1

    @property
    def k_c(self):
        return int(self.f_c // (self.fs / self.N))

    @property
    def k_df(self):
        return int(self.f_df_max // (self.fs / self.N))


# =============================================================================================
# 2. Stage 1: single-channel wind / fabric detector
#    d(t) = 1 if R_LF(t) > theta_lf and var_energy(t) < theta_var
# =============================================================================================
def detector_features(frame, X, cfg):
    """R_LF: fraction of the frame energy at or below f_c.  var_energy: variance of 8 sub-block energies,
    normalised by the squared mean."""
    P = np.abs(X) ** 2
    r_lf = P[: cfg.k_c + 1].sum() / (P.sum() + 1e-12)
    e = (frame.reshape(8, -1) ** 2).mean(1)
    return float(r_lf), float(e.var() / (e.mean() ** 2 + 1e-12))


class WindDetector:
    def __init__(self, cfg):
        self.cfg, self.hang = cfg, 0

    def update(self, frame, X):
        r, v = detector_features(frame, X, self.cfg)
        d = (r > self.cfg.theta_lf) and (v < self.cfg.theta_var)
        if d:
            self.hang = self.cfg.hangover
        elif self.hang > 0:
            self.hang -= 1
            d = True
        return int(d), r, v


def feature_track(x, cfg):
    """(R_LF, var_energy) for every frame of a signal (for calibration and plots)."""
    w = np.hanning(cfg.N + 1)[:-1]
    out = []
    for i in range(0, len(x) - cfg.N + 1, cfg.H):
        fr = x[i:i + cfg.N]
        out.append(detector_features(fr, np.fft.rfft(fr * w), cfg))
    return np.array(out)


def calibrate_thresholds(wind_only, clean_speech, cfg):
    """theta_lf halfway between the 10th percentile of R_LF on wind and the 90th percentile on clean speech;
    theta_var = 95th percentile of var_energy on wind.  Use TRAINING material only."""
    fw, fs_ = feature_track(wind_only, cfg), feature_track(clean_speech, cfg)
    return float(0.5 * (np.percentile(fw[:, 0], 10) + np.percentile(fs_[:, 0], 90))), float(np.percentile(fw[:, 1], 95))


# =============================================================================================
# 3. Stage 2a: MCRA-2 noise tracking (Eqs. 5-7) and decision-directed a priori SNR (Eq. 8)
# =============================================================================================
class MCRA2:
    def __init__(self, K, cfg):
        self.cfg, self.S, self.lam, self.i = cfg, None, None, 0
        self.buf = np.full((cfg.L_min + 1, K), np.inf)

    def update(self, P):
        c = self.cfg
        if self.S is None:
            self.S, self.lam = P.copy(), P.copy()
        else:
            self.S = c.alpha_s * self.S + (1 - c.alpha_s) * P
        self.buf[self.i % self.buf.shape[0]] = self.S
        self.i += 1
        self.lam = c.alpha_d * self.lam + (1 - c.alpha_d) * c.min_bias * self.buf.min(axis=0)
        return self.lam


def a_priori_snr(P, lam_now, lam_prev, prev_clean_power, alpha_dd):
    xi = P / (lam_now + 1e-12)
    return xi, (1 - alpha_dd) * np.maximum(xi - 1, 0) + alpha_dd * prev_clean_power / (lam_prev + 1e-12)


# =============================================================================================
# 4. Stage 2b: ERB-domain Wiener filter (32 bands equally spaced on the ERB-rate scale)
# =============================================================================================
def erb_rate(f):
    return 21.4 * np.log10(4.37 * np.asarray(f) / 1000.0 + 1.0)


def inv_erb_rate(e):
    return (10 ** (np.asarray(e) / 21.4) - 1.0) * 1000.0 / 4.37


def band_edges(K, fs, n_bands):
    hz = inv_erb_rate(np.linspace(0, erb_rate(fs / 2), n_bands + 1))
    idx = np.round(hz / (fs / 2) * (K - 1)).astype(int)
    idx[0], idx[-1] = 0, K
    for i in range(n_bands - 1, 0, -1):
        idx[i] = min(idx[i], idx[i + 1] - 1)
    for i in range(1, n_bands + 1):
        idx[i] = max(idx[i], idx[i - 1] + 1)
    assert idx[-1] == K and np.all(np.diff(idx) >= 1)
    return idx


class ERBWiener:
    def __init__(self, cfg):
        self.cfg, self.Gs = cfg, None
        self.edges = band_edges(cfg.K, cfg.fs, cfg.n_erb)
        self.map = np.zeros(cfg.K, dtype=int)
        for b in range(cfg.n_erb):
            self.map[self.edges[b]:self.edges[b + 1]] = b
        self.counts = np.diff(self.edges).astype(float)

    def gain(self, eta):
        eb = np.add.reduceat(eta, self.edges[:-1]) / self.counts
        G = eb / (eb + 1.0)
        self.Gs = G if self.Gs is None else self.cfg.alpha_G * self.Gs + (1 - self.cfg.alpha_G) * G
        return np.maximum(self.Gs[self.map], self.cfg.g_floor)


# =============================================================================================
# 5. AGC with asymmetric attack / release, applied per hop
# =============================================================================================
class AGC:
    def __init__(self, cfg):
        self.c, self.E_hat, self.g = cfg, None, 1.0
        self.a_att = np.exp(-cfg.H / (cfg.fs * cfg.attack_ms / 1000.0))
        self.a_rel = np.exp(-cfg.H / (cfg.fs * cfg.release_ms / 1000.0))

    def process(self, seg):
        c = self.c
        E = float(np.mean(seg.astype(np.float64) ** 2)) + 1e-12
        self.E_hat = E if self.E_hat is None else c.alpha_E * self.E_hat + (1 - c.alpha_E) * E
        g_t = min(10 ** ((c.E_target_db - 10 * np.log10(self.E_hat)) / 20.0), c.g_max)
        a = self.a_att if g_t < self.g else self.a_rel
        g_new = a * self.g + (1 - a) * g_t
        ramp = np.linspace(self.g, g_new, len(seg), endpoint=False)
        self.g = g_new
        return (seg * ramp).astype(np.float32)


# =============================================================================================
# 6. Networks (PyTorch, optional).  Nothing is trained here; weights come from `train`.
#    NOTE: two unidirectional LSTM layers with 64 units already hold 66,560 parameters, so a total of
#    37,312 parameters is not possible for that size.  Default hidden = 32 (about 35.9k in total).
#    The LSTM is unidirectional (causal) by default; bidirectional over time needs future frames.
# =============================================================================================
def make_features(P, eta):
    """Network input per frame (..., 2, K): [log power, a priori SNR in dB], scaled to about [-1, 1]."""
    f1 = np.log10(P + 1e-8) / 4.0
    f2 = np.clip(10 * np.log10(eta + 1e-3), -30, 30) / 30.0
    return np.stack([f1, f2], axis=-2).astype(np.float32)


if HAVE_TORCH:
    class MaskNet(nn.Module):
        """Encoder (4x downsampling over frequency) -> LSTM over time -> decoder (4x upsampling) -> sigmoid mask."""

        def __init__(self, K=257, hidden=32, layers=2, ch=16, bidirectional=False):
            super().__init__()
            self.K, self.Kp = K, int(np.ceil(K / 4) * 4)
            self.enc1 = nn.Conv1d(2, ch, 5, stride=2, padding=2)
            self.enc2 = nn.Conv1d(ch, 4, 5, stride=2, padding=2)
            self.to_rnn = nn.Linear(4 * (self.Kp // 4), hidden)
            self.rnn = nn.LSTM(hidden, hidden, layers, batch_first=True, bidirectional=bidirectional)
            self.from_rnn = nn.Linear(hidden * (2 if bidirectional else 1), 4 * (self.Kp // 4))
            self.dec1 = nn.ConvTranspose1d(4, ch, 4, stride=2, padding=1)
            self.dec2 = nn.ConvTranspose1d(ch, 1, 4, stride=2, padding=1)

        def forward(self, feat, state=None):               # feat (B, T, 2, K) -> mask (B, T, K)
            B, T, C, K = feat.shape
            x = F.pad(feat.reshape(B * T, C, K), (0, self.Kp - K))
            x = F.relu(self.enc2(F.relu(self.enc1(x))))
            x, state = self.rnn(self.to_rnn(x.reshape(B * T, -1)).reshape(B, T, -1), state)
            x = F.relu(self.from_rnn(x.reshape(B * T, -1))).reshape(B * T, 4, self.Kp // 4)
            x = torch.sigmoid(self.dec2(F.relu(self.dec1(x))))[:, 0, :K]
            return x.reshape(B, T, K), state

        @torch.no_grad()
        def step(self, feat_np, state):                    # one frame (2, K) -> mask (K,), new state
            m, state = self.forward(torch.from_numpy(feat_np)[None, None], state)
            return m[0, 0].numpy(), state

    class DFNet(nn.Module):
        """Two Conv1D layers over frequency (kernel 3, tanh, 32 hidden) -> complex taps h_l(t,k); identity at
        initialisation (h_0 = 1); ||h||_2 <= 1 by rescaling."""

        def __init__(self, L_f=5, hidden=32):
            super().__init__()
            self.L_f = L_f
            self.c1 = nn.Conv1d(2, hidden, 3, padding=1)
            self.c2 = nn.Conv1d(hidden, 2 * L_f, 3, padding=1)
            nn.init.normal_(self.c2.weight, std=1e-3)
            nn.init.zeros_(self.c2.bias)
            with torch.no_grad():
                self.c2.bias[0] = 1.0

        def forward(self, feat):                           # (B, 2, k_df) -> complex (B, L_f, k_df)
            y = self.c2(torch.tanh(self.c1(feat)))
            h = torch.complex(y[:, : self.L_f], y[:, self.L_f:])
            return h / torch.clamp(torch.sqrt((h.abs() ** 2).sum(1, keepdim=True)), min=1.0)

        @torch.no_grad()
        def coeffs(self, feat_np):
            return self.forward(torch.from_numpy(feat_np)[None])[0].numpy()

    def count_params(m):
        return sum(p.numel() for p in m.parameters())


# =============================================================================================
# 7. The cascade (streaming and offline), with switches for ablation
# =============================================================================================
DEFAULT_STAGES = dict(wiener=True, mask="identity", df=True, fusion=True, agc=True, force_wind=False)


class VeilWave:
    def __init__(self, cfg=None, mask_net=None, df_net=None, stages=None, log_maxlen=None):
        self.cfg, self.log_maxlen = cfg or Config(), log_maxlen
        self.stages = dict(DEFAULT_STAGES, **(stages or {}))
        if self.stages["mask"] == "dnn" and mask_net is None:
            raise ValueError("stages['mask']='dnn' needs a trained mask network (see `train`)")
        self.mask_net, self.df_net = mask_net, df_net
        self.reset()

    def reset(self):
        c = self.cfg
        self.win = np.hanning(c.N + 1)[:-1]
        self.det, self.noise, self.erb, self.agc = WindDetector(c), MCRA2(c.K, c), ERBWiener(c), AGC(c)
        self.lam_prev, self.prev_S2 = None, np.zeros(c.K)
        self.hist = np.zeros((c.L_f, c.K), dtype=np.complex128)
        self.ola, self.inbuf, self.state = np.zeros(c.N), np.zeros(c.H), None
        self.log = deque(maxlen=self.log_maxlen) if self.log_maxlen else []       # per frame (d, R_LF, var)

    def _frame(self, fr):
        c, s = self.cfg, self.stages
        X = np.fft.rfft(fr * self.win)
        d, r, v = self.det.update(fr, X)
        d = 1 if s["force_wind"] else d
        self.log.append((d, r, v))
        S = Xt = X
        if d and s["wiener"]:
            P = np.abs(X) ** 2
            lam = self.noise.update(P)
            lam_prev = lam if self.lam_prev is None else self.lam_prev
            _, eta = a_priori_snr(P, lam, lam_prev, self.prev_S2, c.alpha_dd)
            self.lam_prev = lam
            Xt = self.erb.gain(eta) * X                                           # Stage 2
            M = np.ones(c.K)
            if s["mask"] == "dnn":                                                # Stage 3
                M, self.state = self.mask_net.step(make_features(P, eta), self.state)
            self.hist = np.roll(self.hist, 1, axis=0)
            self.hist[0] = Xt
            Y = Xt.copy()
            if s["df"] and self.df_net is not None:                               # Stage 4
                kd = c.k_df
                Y[:kd] = (self.df_net.coeffs(make_features(np.abs(Xt) ** 2, eta)[:, :kd]) * self.hist[:, :kd]).sum(0)
            if s["fusion"]:                                                       # Stage 5
                gam = 1.0 / (1.0 + np.exp(-(10 * np.log10(eta + 1e-3) - c.eta0_db)))
                S = gam * M * Xt + (1 - gam) * Y
            else:
                S = M * Xt
        else:
            self.hist = np.roll(self.hist, 1, axis=0)
            self.hist[0] = Xt
        self.prev_S2 = np.abs(S) ** 2
        self.ola += np.fft.irfft(S, c.N)
        out = self.ola[: c.H].copy()
        self.ola = np.concatenate([self.ola[c.H:], np.zeros(c.H)])
        return self.agc.process(out) if s["agc"] else out.astype(np.float32)

    def push(self, x):
        """Streaming: feed any number of samples; returns the output samples that became available."""
        c = self.cfg
        self.inbuf = np.concatenate([self.inbuf, np.asarray(x, dtype=np.float64)])
        outs = []
        while len(self.inbuf) >= c.N:
            outs.append(self._frame(self.inbuf[: c.N]))
            self.inbuf = self.inbuf[c.H:]
        return np.concatenate(outs) if outs else np.zeros(0, dtype=np.float32)

    def process(self, x):
        """Offline: whole signal in, aligned signal of the same length out."""
        c = self.cfg
        self.reset()
        y = np.concatenate([self.push(x), self.push(np.zeros(c.N))])
        return y[c.H: c.H + len(x)]


# =============================================================================================
# 8. Arduino link: 16 kHz, 10 bit, 250,000 baud, 42-byte packets with re-synchronisation
#    packet = 0xA5 | seq | 40 payload bytes (32 samples x 10 bit, 4 samples in 5 bytes)
# =============================================================================================
SYNC, PKT_SAMPLES, PAYLOAD, PKT_LEN = 0xA5, 32, 40, 42


def pack_samples(samples, seq):
    """Reference packer (mirrors firmware/veilwave_firmware_16k_v2.ino)."""
    out = bytearray([SYNC, seq & 0xFF])
    for i in range(0, PKT_SAMPLES, 4):
        a, b, c, d = (int(v) & 0x3FF for v in samples[i:i + 4])
        out += bytes([a >> 2, ((a & 3) << 6) | (b >> 4), ((b & 15) << 4) | (c >> 6), ((c & 63) << 2) | (d >> 8), d & 255])
    return bytes(out)


def unpack_payload(p):
    s = []
    for i in range(0, PAYLOAD, 5):
        b0, b1, b2, b3, b4 = p[i:i + 5]
        s += [(b0 << 2) | (b1 >> 6), ((b1 & 63) << 4) | (b2 >> 4), ((b2 & 15) << 6) | (b3 >> 2), ((b3 & 3) << 8) | b4]
    return s


class PacketParser:
    """A header is accepted only when the next header (0xA5, seq + 1) follows exactly 42 bytes later."""

    def __init__(self):
        self.buf, self.last_seq, self.lost_packets, self.resyncs, self.synced = bytearray(), None, 0, 0, False

    def _find_sync(self):
        i = self.buf.find(bytes([SYNC]))
        while i != -1:
            if len(self.buf) < i + PKT_LEN + 2:
                del self.buf[:i]
                return None
            if self.buf[i + PKT_LEN] == SYNC and self.buf[i + PKT_LEN + 1] == ((self.buf[i + 1] + 1) & 0xFF):
                return i
            i = self.buf.find(bytes([SYNC]), i + 1)
        del self.buf[: max(0, len(self.buf) - PKT_LEN - 1)]
        return None

    def feed(self, data):
        self.buf += data
        out = []
        while True:
            if not self.synced:
                i = self._find_sync()
                if i is None:
                    return out
                del self.buf[:i]
                self.synced = True
                self.resyncs += 1
            if len(self.buf) < PKT_LEN + 2:
                return out
            if self.buf[0] != SYNC or self.buf[PKT_LEN] != SYNC or self.buf[PKT_LEN + 1] != ((self.buf[1] + 1) & 0xFF):
                self.synced = False
                del self.buf[:1]
                continue
            seq = self.buf[1]
            if self.last_seq is not None:
                gap = (seq - self.last_seq - 1) & 0xFF
                if gap:
                    self.lost_packets += gap
                    out += [512] * (gap * PKT_SAMPLES)
            self.last_seq = seq
            out += unpack_payload(self.buf[2:PKT_LEN])
            del self.buf[:PKT_LEN]


def uart_blocks(port, baud=250000):
    """Generator of float32 blocks from the Arduino (needs pyserial)."""
    import serial
    ser, parser, dc = serial.Serial(port, baud, timeout=0.05), PacketParser(), 0.0
    while True:
        smp = parser.feed(ser.read(ser.in_waiting or 1))
        if smp:
            x = (np.asarray(smp, dtype=np.float64) - 512.0) / 512.0
            dc += 0.001 * (x.mean() - dc)
            yield (x - dc).astype(np.float32)


# =============================================================================================
# 9. Stethoscope mode (piezoelectric disc): PROPOSED FEATURE, not validated
# =============================================================================================
class _SpectralGate:
    def __init__(self, fft_size=512, threshold_db=-15.0, smoothing=0.80):
        self.fft_size, self.threshold, self.smoothing, self._noise = fft_size, 10 ** (threshold_db / 20.0), smoothing, None

    def process(self, chunk):
        n = len(chunk)
        pad = np.zeros(self.fft_size)
        pad[:n] = chunk
        sp = np.fft.rfft(pad)
        mag, ph = np.abs(sp), np.angle(sp)
        self._noise = mag.copy() if self._noise is None else self.smoothing * self._noise + (1 - self.smoothing) * mag
        ratio = mag / (self._noise * self.threshold + 1e-10)
        return np.fft.irfft(np.clip(ratio / (1 + ratio), 0, 1) * mag * np.exp(1j * ph))[:n].astype(np.float32)


class StethoscopeMode:
    """Band-pass 20-800 Hz, 50/100 Hz notches, spectral gate, gain.  Blocks must be at most 512 samples."""

    def __init__(self, fs=16000, gain=15.0, region_hz=50):
        from scipy.signal import butter, sosfilt_zi, iirnotch, lfilter_zi
        self.fs, self.gain = fs, gain
        self._pre = butter(2, 300.0, btype="low", fs=fs, output="sos")
        self._bp = butter(4, [20.0, 800.0], btype="band", fs=fs, output="sos")
        self._n50, self._n100 = iirnotch(region_hz, Q=50, fs=fs), iirnotch(region_hz * 2, Q=50, fs=fs)
        self._pre_zi, self._bp_zi = sosfilt_zi(self._pre), sosfilt_zi(self._bp)
        self._n50_zi, self._n100_zi = lfilter_zi(*self._n50), lfilter_zi(*self._n100)
        self._gate = _SpectralGate(512)

    def process(self, chunk):
        from scipy.signal import sosfilt, lfilter
        x = np.asarray(chunk, dtype=np.float64)
        low, self._pre_zi = sosfilt(self._pre, x, zi=self._pre_zi)
        x, self._bp_zi = sosfilt(self._bp, x + 2.0 * low, zi=self._bp_zi)
        x, self._n50_zi = lfilter(*self._n50, x, zi=self._n50_zi)
        x, self._n100_zi = lfilter(*self._n100, x, zi=self._n100_zi)
        x = self._gate.process(x.astype(np.float32)).astype(np.float64)
        return np.clip(x * self.gain, -1.0, 1.0).astype(np.float32)


# =============================================================================================
# 10. Audio files, audio devices and the live program
# =============================================================================================
def load_audio_16k(path):
    import soundfile as sf
    from scipy.signal import resample_poly
    x, sr = sf.read(path, dtype="float64")
    x = x if x.ndim == 1 else x.mean(1)
    if sr != 16000:
        g = gcd(16000, sr)
        x = resample_poly(x, 16000 // g, sr // g)
    return x


def build_pipeline(thresholds=None, mask_weights=None, df_weights=None, log_maxlen=None, cfg=None):
    """Build the optional research pipeline. Neural weights are never loaded implicitly."""
    cfg = cfg or Config()
    if thresholds:
        with open(thresholds, encoding="utf-8") as f:
            t = json.load(f)
        required = {"theta_lf", "theta_var"}
        if not required.issubset(t):
            raise ValueError(f"threshold file must contain {sorted(required)}")
        cfg.theta_lf, cfg.theta_var = float(t["theta_lf"]), float(t["theta_var"])

    if (mask_weights or df_weights) and not HAVE_TORCH:
        raise SystemExit("PyTorch is required when loading neural-network weights")

    stages, mask_net, df_net = {}, None, None
    if mask_weights:
        mask_net = MaskNet()
        mask_net.load_state_dict(torch.load(mask_weights, map_location="cpu"))
        mask_net.eval()
        stages["mask"] = "dnn"
    if df_weights:
        df_net = DFNet()
        df_net.load_state_dict(torch.load(df_weights, map_location="cpu"))
        df_net.eval()
    return VeilWave(cfg, mask_net, df_net, stages, log_maxlen)


class _Output:
    def __init__(self, fs, block, device=None, bluetooth=False):
        import sounddevice as sd
        self.sd, self.fs, self.block, self.device, self.bt = sd, fs, block, device, bluetooth
        self._q, self._stream = queue.Queue(maxsize=8), None
        if bluetooth:
            self._route_bluetooth()

    @staticmethod
    def _route_bluetooth():
        try:
            for line in subprocess.run(["pactl", "list", "sinks", "short"], capture_output=True, text=True, timeout=3).stdout.splitlines():
                p = line.split()
                if len(p) >= 2 and any(k in p[1].lower() for k in ("bluez", "bluetooth", "a2dp", "hsp", "hfp")):
                    subprocess.run(["pactl", "set-default-sink", p[1]], timeout=3, check=True)
                    print(f"[VeilWave] Bluetooth sink: {p[1]}")
                    return
            print("[VeilWave] no Bluetooth sink found, using the default output")
        except Exception as e:
            print(f"[VeilWave] could not set the Bluetooth sink: {e}")

    def _callback(self, outdata, frames, t, status):
        try:
            blk = self._q.get_nowait()
        except queue.Empty:
            blk = np.zeros(frames, dtype=np.float32)
        outdata[:, 0] = np.resize(blk, frames)

    def start(self):
        self._stream = self.sd.OutputStream(samplerate=self.fs, blocksize=self.block, dtype="float32", channels=1,
                                            device=self.device, callback=self._callback)
        self._stream.start()

    def stop(self):
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    def write(self, blk):
        try:
            self._q.put_nowait(blk.astype(np.float32))
        except queue.Full:
            pass

    def toggle_bluetooth(self):
        self.stop()
        self.bt = not self.bt
        if self.bt:
            self._route_bluetooth()
        self.start()
        print("[VeilWave] output ->", "Bluetooth" if self.bt else "local speaker")


def mic_blocks(cfg, device=None):
    import sounddevice as sd
    q = queue.Queue(maxsize=16)

    def cb(indata, frames, t, status):
        try:
            q.put_nowait(indata[:, 0].copy())
        except queue.Full:
            try:
                q.get_nowait()
            except queue.Empty:
                pass
            q.put_nowait(indata[:, 0].copy())

    stream = sd.InputStream(samplerate=cfg.fs, blocksize=cfg.H, channels=1, dtype="float32", device=device, callback=cb)
    stream.start()
    try:
        while True:
            try:
                yield q.get(timeout=0.2)
            except queue.Empty:
                yield None
    finally:
        stream.stop()
        stream.close()


class _Keys:
    def __init__(self):
        self.pending, self.lock, self.run = [], threading.Lock(), True
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while self.run:
            try:
                c = input().strip().lower()
                if c:
                    with self.lock:
                        self.pending.append(c)
            except EOFError:
                break

    def poll(self):
        with self.lock:
            return self.pending.pop(0) if self.pending else None


def _vu(rms):
    n = int(np.clip((20 * np.log10(max(rms, 1e-9)) + 60) / 60, 0, 1) * 24)
    return "#" * n + "-" * (24 - n)


def cmd_run(a):
    cfg = Config()
    vw = build_pipeline(a.thresholds, a.mask_weights, a.df_weights, log_maxlen=2000, cfg=cfg)
    steth = StethoscopeMode(cfg.fs)
    mode, gain, show = ("stethoscope" if a.start_mode == "s" else "normal"), a.gain, not a.no_vis
    out = _Output(cfg.fs, cfg.H, a.out_device, a.bluetooth)
    out.start()
    keys = _Keys()
    src = uart_blocks(a.port) if a.source == "uart" else mic_blocks(cfg, a.mic_device)
    times, frames, t0 = [], 0, time.time()
    print("[VeilWave] running. Keys (then Enter): n normal | s stethoscope | b Bluetooth/speaker | g+ g- gain | v VU | c reset | q quit")
    try:
        for blk in src:
            c = keys.poll()
            if c == "q":
                break
            elif c in ("n", "1"):
                mode = "normal"
            elif c in ("s", "2"):
                mode = "stethoscope"
            elif c == "b":
                out.toggle_bluetooth()
            elif c in ("g+", "+"):
                gain = min(gain + 0.1, 4.0)
            elif c in ("g-", "-"):
                gain = max(gain - 0.1, 0.1)
            elif c == "v":
                show = not show
            elif c == "c":
                vw.reset()
            if blk is None:
                continue
            t1 = time.perf_counter()
            y = vw.push(blk) if mode == "normal" else steth.process(blk)
            times.append(time.perf_counter() - t1)
            if len(y):
                y = np.clip(y * gain, -1.0, 1.0)
                for i in range(0, len(y), cfg.H):
                    out.write(y[i:i + cfg.H])
            frames += 1
            if show and frames % 8 == 0 and len(y):
                d = vw.log[-1][0] if len(vw.log) else 0
                sys.stdout.write(f"\r  {mode:11s} IN [{_vu(float(np.sqrt(np.mean(blk ** 2))))}] OUT [{_vu(float(np.sqrt(np.mean(y ** 2))))}] wind={'YES' if d else 'no '}  ")
                sys.stdout.flush()
    except KeyboardInterrupt:
        pass
    finally:
        out.stop()
        keys.run = False
        if times:
            ms = 1000 * np.array(times)
            fl = float(np.mean([d for d, _, _ in vw.log])) if len(vw.log) else float("nan")
            print(f"\n[VeilWave] {frames} blocks in {time.time() - t0:.1f} s | per call: mean {ms.mean():.2f} ms, 99.9th pct {np.percentile(ms, 99.9):.2f} ms | frames flagged (last {len(vw.log)}): {fl:.2f}")


def cmd_process(a):
    import soundfile as sf
    cfg = Config()
    vw = build_pipeline(a.thresholds, a.mask_weights, a.df_weights, cfg=cfg)
    stages = {k: v for k, v in dict(wiener=not a.no_wiener, agc=not a.no_agc, force_wind=a.force_wind).items()}
    vw.stages.update(stages)
    x = load_audio_16k(a.input)
    if a.mode == "stethoscope":
        st = StethoscopeMode(cfg.fs)
        y = np.concatenate([st.process(x[i:i + cfg.H]) for i in range(0, len(x), cfg.H)])
    else:
        y = vw.process(x)
    y = np.clip(y * a.gain, -1.0, 1.0)
    if a.output:
        sf.write(a.output, y, cfg.fs, subtype="FLOAT")
    fl = float(np.mean([d for d, _, _ in vw.log])) if len(vw.log) and a.mode == "normal" else float("nan")
    print(f"processed {len(x) / cfg.fs:.1f} s; frames flagged as wind/fabric: {fl:.3f}; output written: {bool(a.output)}")


def cmd_calibrate(a):
    cfg = Config()
    w, s = load_audio_16k(a.wind), load_audio_16k(a.speech)
    t_lf, t_var = calibrate_thresholds(w, s, cfg)
    fw, fs_ = feature_track(w, cfg), feature_track(s, cfg)
    det = lambda f: float(((f[:, 0] > t_lf) & (f[:, 1] < t_var)).mean())
    json.dump(dict(theta_lf=t_lf, theta_var=t_var), open(a.out, "w"))
    print(f"theta_lf = {t_lf:.3f}, theta_var = {t_var:.3f}; flagged on the wind file {det(fw):.2f}, on the speech file {det(fs_):.2f} (same material: optimistic)")


# =============================================================================================
# 11. Training and export (need PyTorch and your own data; not tested in the repository's CI)
# =============================================================================================
def _stft(x, cfg):
    w = np.hanning(cfg.N + 1)[:-1]
    n = 1 + (len(x) - cfg.N) // cfg.H
    return np.stack([np.fft.rfft(x[i * cfg.H:i * cfg.H + cfg.N] * w) for i in range(n)])


def _front_end(X, cfg):
    """a priori SNR per frame with the pipeline's own MCRA-2 + decision-directed tracker (all frames as wind)."""
    noise, prev, lam_prev, etas = MCRA2(cfg.K, cfg), np.zeros(cfg.K), None, []
    for t in range(len(X)):
        P = np.abs(X[t]) ** 2
        lam = noise.update(P)
        _, eta = a_priori_snr(P, lam, lam if lam_prev is None else lam_prev, prev, cfg.alpha_dd)
        lam_prev = lam
        prev = (eta / (eta + 1)) ** 2 * P
        etas.append(eta)
    return np.array(etas)


def _make_example(speech, wind, rng, cfg, seg=3.0):
    L = int(seg * cfg.fs)
    s = np.pad(speech[rng.integers(0, max(1, len(speech) - L)):][:L], (0, max(0, L - len(speech))))[:L]
    w = np.pad(wind[rng.integers(0, max(1, len(wind) - L)):][:L], (0, max(0, L - len(wind))))[:L]
    snr = rng.uniform(-5, 10)
    n = np.sqrt((s ** 2).mean() / ((w ** 2).mean() + 1e-12) / 10 ** (snr / 10)) * w
    X, S, Nn = _stft(s + n, cfg), _stft(s, cfg), _stft(n, cfg)
    irm = np.abs(S) ** 2 / (np.abs(S) ** 2 + np.abs(Nn) ** 2 + 1e-12)
    return make_features(np.abs(X) ** 2, _front_end(X, cfg)), irm.astype(np.float32), X, s


def _ola_torch(frames, cfg):
    B, T, N = frames.shape
    return F.fold(frames.transpose(1, 2), (1, (T - 1) * cfg.H + N), (1, N), stride=(1, cfg.H))[:, 0, 0]


def _si_snr(est, ref):
    est, ref = est - est.mean(-1, keepdim=True), ref - ref.mean(-1, keepdim=True)
    t = (est * ref).sum(-1, keepdim=True) / (ref.pow(2).sum(-1, keepdim=True) + 1e-8) * ref
    return 10 * torch.log10(t.pow(2).sum(-1) / ((est - t).pow(2).sum(-1) + 1e-8) + 1e-8)


def cmd_train(a):
    """Mask network, speaker-independent split (70/10/20) written to split.json; test speakers are never used.
    Loss = beta * (-SI-SNR) + (1 - beta) * IRM-MSE.  The manuscript's differentiable-PESQ term is NOT included."""
    if not HAVE_TORCH:
        raise SystemExit("PyTorch is required for training")
    import soundfile as sf
    cfg = Config()
    os.makedirs(a.out_dir, exist_ok=True)
    rng = np.random.default_rng(a.seed)
    random.seed(a.seed)
    torch.manual_seed(a.seed)
    files = sorted(glob.glob(os.path.join(a.speech_dir, "**", "*.wav"), recursive=True) + glob.glob(os.path.join(a.speech_dir, "**", "*.flac"), recursive=True))
    spk = sorted({os.path.basename(f).split("_")[0] for f in files})
    if len(spk) < 4:
        raise SystemExit(
            "speaker-independent training requires at least 4 distinct speaker IDs "
            "in filenames (the text before the first underscore)"
        )
    random.shuffle(spk)
    ntr = max(1, int(round(.7 * len(spk))))
    nva = max(1, int(round(.1 * len(spk))))
    while ntr + nva >= len(spk):
        if ntr > 1:
            ntr -= 1
        else:
            nva -= 1
    split = dict(train=spk[:ntr], val=spk[ntr:ntr + nva], test=spk[ntr + nva:])
    json.dump(split, open(os.path.join(a.out_dir, "split.json"), "w"), indent=1)
    by = lambda names: [f for f in files if os.path.basename(f).split("_")[0] in set(names)]
    tr_f, va_f = by(split["train"]), by(split["val"])
    winds = [load_audio_16k(f) for f in sorted(glob.glob(os.path.join(a.wind_dir, "**", "*.wav"), recursive=True))]
    if not tr_f or not va_f or not winds:
        raise SystemExit("training requires speech files for non-empty train and validation splits, plus wind WAV files")
    net = MaskNet()
    print("mask network parameters:", count_params(net))
    opt, best, bad = torch.optim.Adam(net.parameters(), lr=a.lr), 1e9, 0

    def batch(fl):
        ex = [_make_example(load_audio_16k(fl[rng.integers(len(fl))]), winds[rng.integers(len(winds))], rng, cfg) for _ in range(a.batch)]
        return (torch.from_numpy(np.stack([e[0] for e in ex])), torch.from_numpy(np.stack([e[1] for e in ex])),
                torch.from_numpy(np.stack([e[2] for e in ex])), torch.from_numpy(np.stack([e[3] for e in ex])).float())

    def loss_fn(mask, irm, X, s):
        est = _ola_torch(torch.fft.irfft(mask * X.to(torch.complex64), cfg.N), cfg)
        return a.beta * (-_si_snr(est, s[:, : est.shape[-1]]).mean()) + (1 - a.beta) * F.mse_loss(mask, irm)

    for ep in range(a.epochs):
        net.train()
        for _ in range(a.steps):
            f, irm, X, s = batch(tr_f)
            l = loss_fn(net(f)[0], irm, X, s)
            opt.zero_grad()
            l.backward()
            opt.step()
        net.eval()
        with torch.no_grad():
            vl = float(np.mean([loss_fn(net(b[0])[0], b[1], b[2], b[3]).item() for b in (batch(va_f) for _ in range(10))]))
        print(f"epoch {ep + 1}: train {l.item():.3f}  val {vl:.3f}", flush=True)
        if vl < best - 1e-4:
            best, bad = vl, 0
            torch.save(net.state_dict(), os.path.join(a.out_dir, "mask_net.pt"))
        else:
            bad += 1
            if bad >= a.patience:
                print("early stopping")
                break
    json.dump(dict(vars(a), best_val=best, params=count_params(net)), open(os.path.join(a.out_dir, "train_log.json"), "w"), indent=1)


def cmd_export_onnx(a):
    """EXPERIMENTAL and untested: ONNX export + dynamic INT8 quantization."""
    if not HAVE_TORCH:
        raise SystemExit("PyTorch is required for export-onnx")
    os.makedirs(a.out_dir, exist_ok=True)
    net = MaskNet()
    net.load_state_dict(torch.load(a.weights, map_location="cpu"))
    net.eval()

    class Seq(nn.Module):
        def __init__(s, n):
            super().__init__()
            s.n = n

        def forward(s, f):
            return s.n(f)[0]

    path = os.path.join(a.out_dir, "mask_net_fp32.onnx")
    torch.onnx.export(Seq(net), torch.zeros(1, 100, 2, 257), path, input_names=["features"], output_names=["mask"],
                      dynamic_axes={"features": {1: "T"}, "mask": {1: "T"}}, opset_version=17)
    from onnxruntime.quantization import quantize_dynamic, QuantType
    quantize_dynamic(path, os.path.join(a.out_dir, "mask_net_int8.onnx"), weight_type=QuantType.QInt8)
    print("written", path, "and mask_net_int8.onnx")


# =============================================================================================
# 12. Self-tests (python veilwave.py selftest; set PAIR_DIR for the optional real-recording test)
# =============================================================================================
def selftest(pair_dir=None):
    rng = np.random.default_rng(0)
    results = []

    def check(name, cond, info=""):
        results.append(bool(cond))
        print(("PASS " if cond else "FAIL ") + name, info)

    x = rng.standard_normal(32000) * 0.1
    y = VeilWave(stages=dict(wiener=False, agc=False)).process(x)
    check("perfect reconstruction with no processing", np.max(np.abs(y - x)) < 1e-6, f"max error {np.max(np.abs(y - x)):.1e}")
    e = band_edges(257, 16000, 32)
    check("32 ERB bands cover the 257 bins", len(e) == 33 and e[0] == 0 and e[-1] == 257 and np.all(np.diff(e) >= 1))
    cfg = Config()
    m, w = MCRA2(257, cfg), np.hanning(513)[:-1]
    for _ in range(400):
        lam = m.update(np.abs(np.fft.rfft(rng.standard_normal(512) * w)) ** 2)
    ratio = float(np.median(lam[10:]) / (w ** 2).sum())
    check("MCRA-2 tracks white-noise PSD within a factor 3", 1 / 3 < ratio < 3, f"ratio {ratio:.2f} (the minimum is biased low; see min_bias)")
    orig = rng.integers(0, 1024, 100 * 32)
    stream = b"".join(pack_samples(orig[i * 32:(i + 1) * 32], i) for i in range(100))
    p = PacketParser()
    got = p.feed(stream)
    check("UART packets round-trip", np.array_equal(got, orig[:len(got)]) and len(got) >= 99 * 32)
    p = PacketParser()
    got = p.feed(b"\x13\xa5" + stream[:1500] + stream[1507:])
    check("UART parser recovers after 7 lost bytes", p.lost_packets <= 2 and len(got) > 90 * 32, f"lost {p.lost_packets}, resyncs {p.resyncs}")
    v1, v2 = VeilWave(), VeilWave()
    off = v1.process(x)
    st = np.concatenate([v2.push(x[i:i + 300]) for i in range(0, len(x), 300)] + [v2.push(np.zeros(512))])[256:256 + len(x)]
    check("streaming output equals offline output", np.max(np.abs(st - off)) < 1e-5)
    s = StethoscopeMode(16000)
    tt = np.arange(16000) / 16000
    hb = 0.1 * np.sin(2 * np.pi * 40 * tt) * (np.sin(2 * np.pi * 1.2 * tt) > 0.7)
    ys = np.concatenate([s.process(hb[i:i + 256]) for i in range(0, len(hb), 256)])
    check("stethoscope module runs", np.isfinite(ys).all() and len(ys) == len(hb))
    vw = VeilWave(log_maxlen=50)
    for i in range(0, len(x), 256):
        vw.push(x[i:i + 256])
    check("live log stays bounded", len(vw.log) <= 50)
    cp, wp = (os.path.join(pair_dir, "clear_speech.wav"), os.path.join(pair_dir, "speech_in_wind.wav")) if pair_dir else (None, None)
    if cp and os.path.exists(cp) and os.path.exists(wp):
        try:
            from pesq import pesq
            C, W = load_audio_16k(cp), load_audio_16k(wp)
            Nn, h = W - C, len(C) // 2
            c2 = Config()
            c2.theta_lf, c2.theta_var = calibrate_thresholds(Nn[:h], C[:h], c2)

            def sisdr(a, b):
                k = min(len(a), len(b))
                a, b = a[:k] - a[:k].mean(), b[:k] - b[:k].mean()
                t = (b @ a) / (a @ a) * a
                return 10 * np.log10((t @ t) / ((b - t) @ (b - t)))
            yo = VeilWave(c2, stages=dict(agc=False)).process(W)
            check("optional paired recording: SI-SDR and PESQ improve", sisdr(C, yo) > sisdr(C, W) and pesq(16000, C, yo[:len(C)], "wb") > pesq(16000, C, W, "wb"),
                  f"SI-SDR {sisdr(C, W):.2f} -> {sisdr(C, yo):.2f} dB (one utterance; thresholds tuned on its first halves)")
        except ImportError:
            print("SKIP paired-recording test (needs pesq and soundfile)")
    else:
        print("SKIP paired-recording test (set PAIR_DIR or use --pair-dir)")
    if HAVE_TORCH:
        net, df = MaskNet(), DFNet()
        mk, _ = net(torch.randn(2, 20, 2, 257))
        check("MaskNet output shape and range", mk.shape == (2, 20, 257) and float(mk.min().detach()) >= 0 and float(mk.max().detach()) <= 1, f"{count_params(net)} parameters")
        h = df(torch.randn(3, 2, 129))
        check("DFNet identity at initialisation", float((h[:, 0] - 1).abs().max().detach()) < 0.05)
        yy = VeilWave(mask_net=net.eval(), df_net=df.eval(), stages=dict(mask="dnn")).process(rng.standard_normal(16000) * 0.1)
        check("pipeline with the networks runs", np.isfinite(yy).all())
    else:
        print("SKIP network tests (PyTorch not installed)")
    print("\nALL PASSED" if all(results) else "\nSOME TESTS FAILED")
    return all(results)


# =============================================================================================
# 13. Command line
# =============================================================================================
def main(argv=None):
    ap = argparse.ArgumentParser(prog="veilwave.py", description="VeilWave research prototype (see the docstring and README)")
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="live processing")
    r.add_argument("--source", choices=["uart", "mic"], default="mic")
    r.add_argument("--port", default="/dev/ttyUSB0")
    r.add_argument("--thresholds")
    r.add_argument("--mask-weights")
    r.add_argument("--df-weights")
    r.add_argument("--bluetooth", action="store_true")
    r.add_argument("--gain", type=float, default=1.0)
    r.add_argument("--no-vis", action="store_true")
    r.add_argument("--start-mode", choices=["n", "s"], default="n")
    r.add_argument("--mic-device", type=int)
    r.add_argument("--out-device", type=int)
    pr = sub.add_parser("process", help="process a WAV file offline")
    pr.add_argument("input")
    pr.add_argument("-o", "--output")
    pr.add_argument("--mode", choices=["normal", "stethoscope"], default="normal")
    pr.add_argument("--thresholds")
    pr.add_argument("--mask-weights")
    pr.add_argument("--df-weights")
    pr.add_argument("--gain", type=float, default=1.0)
    pr.add_argument("--no-wiener", action="store_true")
    pr.add_argument("--no-agc", action="store_true")
    pr.add_argument("--force-wind", action="store_true", help="treat every frame as wind (ablation without the detector)")
    ca = sub.add_parser("calibrate", help="detector thresholds from wind-only and clean-speech recordings")
    ca.add_argument("--wind", required=True)
    ca.add_argument("--speech", required=True)
    ca.add_argument("--out", default="thresholds.json")
    tr = sub.add_parser("train", help="train the mask network")
    tr.add_argument("--speech-dir", required=True)
    tr.add_argument("--wind-dir", required=True)
    tr.add_argument("--out-dir", required=True)
    tr.add_argument("--epochs", type=int, default=100)
    tr.add_argument("--steps", type=int, default=200)
    tr.add_argument("--batch", type=int, default=16)
    tr.add_argument("--lr", type=float, default=1e-3)
    tr.add_argument("--patience", type=int, default=10)
    tr.add_argument("--beta", type=float, default=0.7)
    tr.add_argument("--seed", type=int, default=2026)
    ex = sub.add_parser("export-onnx", help="export the mask network (experimental)")
    ex.add_argument("--weights", required=True)
    ex.add_argument("--out-dir", default=".")
    st = sub.add_parser("selftest", help="run the built-in tests")
    st.add_argument("--pair-dir", default=os.environ.get("PAIR_DIR"))
    sub.add_parser("devices", help="list the audio devices")
    a = ap.parse_args(argv)
    if a.cmd == "run":
        cmd_run(a)
    elif a.cmd == "process":
        cmd_process(a)
    elif a.cmd == "calibrate":
        cmd_calibrate(a)
    elif a.cmd == "train":
        cmd_train(a)
    elif a.cmd == "export-onnx":
        cmd_export_onnx(a)
    elif a.cmd == "selftest":
        sys.exit(0 if selftest(a.pair_dir) else 1)
    elif a.cmd == "devices":
        import sounddevice as sd
        print(sd.query_devices())


if __name__ == "__main__":
    main()
