# VeilWave

**VeilWave** is an early-stage, single-microphone audio-processing research prototype associated with the manuscript:

*VeilWave: An Adaptive Audio-Processing Prototype for Hostile Acoustic Environments.*

This repository is shared for code inspection, educational use, and further research. It is **not a validated hearing aid, medical device, or reproducible release of the manuscript's complete pilot-evaluation package**.

> [!IMPORTANT]
> The manuscript reports preliminary, mixed speech-quality results. Wind/fabric-noise recognition has not been fully validated, fabric-noise performance has not been established, and no direct experimental comparison with other systems has been performed. The stethoscope feature is proposed and has not been experimentally validated. Do not interpret this code as evidence of clinical or real-world effectiveness.

## Repository status

| Area | Status |
|---|---|
| Single-microphone Python processing prototype | Research implementation; inspect and test before use |
| Offline WAV processing | Available; not a reproduction of the complete paper evaluation |
| Live sound-card input/output | Experimental; depends on the host audio system |
| Arduino UART parser | Unit-tested in software |
| Arduino firmware | Included for review; **not verified on physical hardware** |
| Raspberry Pi 4 performance | Not established by the supplied timing results |
| Wind/fabric detector | Not validated on a labelled dataset |
| Neural mask and deep-filter stages | Optional experimental code; no trained weights are included |
| Stethoscope mode | Proposed feature only; not clinically or experimentally validated |
| Full reproduction of manuscript pilot | Not supported by this repository alone |

## What is included

- `veilwave.py` — single-file Python research implementation.
- `firmware/veilwave_firmware_16k_v2.ino` — experimental Arduino Nano sampler firmware.
- `tests/` — automated software tests.
- `.github/workflows/ci.yml` — GitHub Actions test workflow.
- `PAPER_COMPATIBILITY.md` — explicit mapping between this repository and the manuscript.
- `HARDWARE_NOTES.md` — hardware and firmware caveats.

## Processing outline

The current code is configured for 16 kHz mono audio, with a 512-sample analysis frame and a 256-sample hop (32 ms frame, 16 ms hop).

```text
Mono audio input
    |
    v
512-sample Hann-window analysis / 256-sample hop
    |
    v
Single-channel low-frequency energy and sub-block-energy features
    |
    v
Detector decision
    |
    +---- not flagged ----> bypass spectral enhancement
    |
    +---- flagged --------> MCRA-style noise tracking
                             + decision-directed SNR estimate
                             + ERB-band Wiener gain
                             + optional neural mask (weights required)
                             + optional deep-filter network (weights required)
    |
    v
Overlap-add and optional AGC
    |
    v
Mono output
```

This diagram describes the current source code's intended processing path. It must not be read as proof that every optional stage was trained, used in the pilot, or validated.

## Requirements

- Python 3.10 or newer
- NumPy
- SciPy

Install the core dependencies:

```bash
python -m venv .venv
# Linux/macOS:
source .venv/bin/activate
# Windows PowerShell:
# .venv\Scripts\Activate.ps1

python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Optional dependencies:

```bash
# WAV file input/output and calibration
python -m pip install -r requirements-audio.txt

# Live sound-card audio and Arduino serial input
python -m pip install -r requirements-live.txt

# Experimental neural-network training / model loading
python -m pip install -r requirements-training.txt

# Tests
python -m pip install -r requirements-test.txt
```

Live audio also requires a working PortAudio installation supported by `sounddevice`. Serial operation requires a compatible USB-serial adapter and `pyserial`.

## Quick start

Run the software tests:

```bash
python -m pytest -q
python veilwave.py selftest
```

List available audio devices (requires `sounddevice`):

```bash
python veilwave.py devices
```

Process a WAV file offline (requires `soundfile`):

```bash
python veilwave.py process input.wav -o output.wav
```

The loader converts input audio to 16 kHz mono. Keep the original input file; the output is a processed research sample, not a validated hearing-aid signal.

Calibrate detector thresholds using separate training/calibration recordings:

```bash
python veilwave.py calibrate \
  --wind wind_calibration.wav \
  --speech clean_speech_calibration.wav \
  --out thresholds.json
```

Then:

```bash
python veilwave.py process input.wav -o output.wav --thresholds thresholds.json
```

Do not calibrate on evaluation/test recordings. Calibration on the same recordings used for reporting performance would produce optimistic results.

## Experimental live operation

Sound-card input:

```bash
python veilwave.py run --source mic
```

Arduino serial input (only after reviewing and validating the firmware and hardware):

```bash
python veilwave.py run --source uart --port /dev/ttyUSB0
```

The UART firmware is experimental and has not been verified on the physical prototype. The serial protocol and firmware details are documented in `HARDWARE_NOTES.md`.

The interactive stethoscope mode in the program is a software demonstration of a proposed feature. It is not a validated medical or auscultation tool and must not be used for diagnosis or clinical decisions.

## Optional neural experiments

The repository contains optional PyTorch model and training code. No trained weights or training datasets are distributed.

The training command expects WAV files organised under a speech directory and speaker IDs at the beginning of filenames, before the first underscore. It also expects wind WAV files:

```bash
python veilwave.py train \
  --speech-dir path/to/speech \
  --wind-dir path/to/wind \
  --out-dir experiment_01
```

The training implementation is experimental. Its loss does **not** implement the manuscript's differentiable-PESQ term. The held-out speaker split is recorded, but the code does not provide a complete independent test-set evaluation workflow. Do not report training or test performance without implementing and documenting that evaluation.

Only load model checkpoint files from sources you trust. PyTorch checkpoint loading can deserialize data and should not be treated as safe for arbitrary untrusted files.

ONNX export and INT8 quantization are experimental and have not been validated end to end:

```bash
python veilwave.py export-onnx --weights experiment_01/mask_net.pt --out-dir onnx_export
```

## Relationship to the manuscript

Read [`PAPER_COMPATIBILITY.md`](PAPER_COMPATIBILITY.md) before citing this repository or describing it as the paper's implementation.

In particular:

- The manuscript's pilot used 6 utterances from 2 speakers, 2 wind sources, and SNR conditions of −5, 0, +5, and +10 dB: 48 mixtures and 192 rows across four processing variants.
- The supplied pilot results are not regenerated by a documented end-to-end command in this repository.
- The pilot's timing host is not verified as a Raspberry Pi 4. The code's live timing printout is not interchangeable with the manuscript's timing measurements.
- The repository's detector defaults (`theta_lf=0.80`, `theta_var=2.0`) are placeholders and do not encode the pilot thresholds.
- No direct experimental comparison with another system is included.
- The complete software and audio package needed to reproduce the manuscript's pilot is not included here.

## Limitations and responsible use

1. **Early prototype:** this is research code, not a finished wearable hearing system.
2. **Detector validation:** no labelled wind/fabric dataset, ROC analysis, or false-alarm evaluation is supplied. Fabric-friction recognition is not established.
3. **Speech quality:** preliminary pilot results are mixed; processing may reduce objective speech-quality measures under some conditions.
4. **Hardware:** the manuscript's assembled prototype includes a Raspberry Pi 4, Arduino Nano, piezoelectric disc, and Jamary microphone. This repository does not establish that every software path or firmware function was tested on that complete assembly.
5. **Timing:** do not attribute the pilot timing values to a Raspberry Pi 4. The measurement host has not been verified.
6. **Neural models:** no trained model weights are included. Optional network, training, and quantization paths are experimental.
7. **Stethoscope mode:** proposed only; not experimentally validated and not for clinical use.
8. **Reproducibility:** source recordings, complete pilot scripts, and the full software package needed to reproduce the reported evaluation are not all included.
9. **No comparative claim:** no direct experimental comparison against competing systems has been performed.

## Tests and continuous integration

Run:

```bash
python -m pytest -q
```

GitHub Actions runs the software test suite on the configured Python versions. A passing CI run checks only the automated software tests. It does not validate audio quality, the physical circuit, firmware timing, Raspberry Pi performance, or clinical suitability.

## License

This project is distributed under the MIT License. See [`LICENSE`](LICENSE).

## Citation

A draft citation file is provided in [`CITATION.cff`](CITATION.cff). Update it when the manuscript is accepted or published, including the final bibliographic details and DOI if one is assigned.

## Contact and contributions

Please use GitHub Issues to report reproducible software bugs. Include the Python version, operating system, command used, and a minimal example. Do not upload private recordings, identifiable speech, medical information, API keys, passwords, or other sensitive material.

Contributions should preserve the distinction between implemented code, experimental features, and experimentally validated results. Do not add performance or hardware claims without corresponding documented tests.

For the local-to-GitHub publishing procedure, see [`GITHUB_PUBLISHING.md`](GITHUB_PUBLISHING.md).
