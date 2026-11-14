# Paper compatibility and evidence boundary

This document prevents the public repository from overstating its relationship to the VeilWave manuscript and its Option A pilot evaluation.

## Agreed Option A framing

The manuscript presents VeilWave as an early physical prototype with preliminary, mixed speech-quality results. Wind/fabric-noise recognition is not fully validated. The physical prototype is described as containing a Raspberry Pi 4, Arduino Nano, piezoelectric disc, and Jamary microphone. The pilot timing measurements must not be attributed to the Raspberry Pi 4 because their host has not been verified. Stethoscope mode is proposed and not experimentally validated. No direct experimental comparison with other systems has been performed. The complete software package required to reproduce the experiment is unavailable.

The repository documentation must preserve these statements.

## Pilot evaluation facts

The pilot results discussed with the manuscript comprise:

- 6 speech utterances from 2 speakers;
- 2 wind sources;
- SNR conditions of −5, 0, +5, and +10 dB;
- 48 noisy mixtures;
- 4 processing variants;
- 192 result rows;
- PESQ-WB, STOI, and SI-SDR metrics;
- detector activation reported as 56.4%;
- timing summaries reported as 0.128 ms/frame mean and 0.316 ms/frame 99.9th percentile, plus 0.106 ms/16-ms-hop mean and 0.714 ms/hop 99.9th percentile.

The objective speech-quality results are mixed and do not establish consistent improvement. These values are manuscript/pilot facts; this repository does not contain the complete source audio and exact end-to-end evaluation package needed to regenerate them.

## Code facts visible in this repository

| Item | Current source code |
|---|---|
| Audio rate | 16,000 Hz |
| Analysis frame | 512 samples (32 ms) |
| Hop | 256 samples (16 ms) |
| Input model | Mono / single channel |
| Detector features | Low-frequency energy fraction and normalized variance of eight sub-block energies |
| Detector rule | `R_LF > theta_lf` AND `var_energy < theta_var` |
| Default detector thresholds | `theta_lf=0.80`, `theta_var=2.0`, explicitly placeholders |
| Noise reduction | MCRA-style noise tracking, decision-directed SNR, ERB-band Wiener gain |
| Neural mask | Optional; requires user-supplied trained weights |
| Deep filter | Optional; requires user-supplied trained weights |
| AGC | Optional stage |
| Stethoscope | Software feature present, but proposed and not validated |
| Firmware | 16 kHz / 10-bit ADC / 250,000 baud packet design; not verified on hardware |

## Important differences and unresolved points

1. **Detector equation:** the current code implements the single-channel rule shown above. Earlier manuscript review identified a conflict between the manuscript's equation/algorithm and one or more figures. Do not state that the code resolves that manuscript inconsistency until the final manuscript equation, algorithm, and figures have been reconciled against the actual intended implementation.
2. **Thresholds:** the code defaults are placeholders. They are not the pilot threshold values. A user-generated `thresholds.json` is a new calibration result, not a reproduction of the pilot calibration.
3. **Pilot variants:** the repository does not provide a documented command that regenerates the pilot's four variants and all 192 result rows from the exact source files.
4. **Metrics:** the core `process` command does not calculate PESQ-WB, STOI, or SI-SDR. An optional paired-recording self-test is not equivalent to the full pilot evaluation.
5. **Timing:** `run` prints elapsed processing time around each input block. This is not automatically equivalent to per-frame and per-hop measurements in the paper. It must not be used to attribute the paper's timing results to a Raspberry Pi 4.
6. **Neural network:** no trained weights are included. Training code uses a loss that omits the manuscript's differentiable-PESQ term. Neural processing and ONNX/INT8 export are not part of the reported pilot evidence unless separately documented.
7. **Fabric noise:** no fabric-friction evaluation dataset or validated fabric-noise recognition result is included.
8. **Hardware:** the firmware is not a verified implementation of the complete physical prototype. In particular, this repository does not establish that the Jamary microphone and piezo disc were both integrated and tested through the supplied firmware path.
9. **Reproducibility:** making this repository public does not change the manuscript's reproducibility limitation. The complete software package and source data required to reproduce the pilot remain unavailable here.

## Safe wording for a paper or repository citation

Recommended:

> The public repository provides an experimental single-channel research implementation associated with VeilWave. It is not a complete reproduction package for the manuscript's pilot evaluation. Optional neural and stethoscope-related components are not validated, and the repository makes no claim of clinical effectiveness or verified Raspberry Pi 4 timing.

Avoid:

- "This repository reproduces all results in the paper."
- "The wind/fabric detector is validated."
- "The system improves speech quality in all conditions."
- "The reported latency was measured on Raspberry Pi 4."
- "The neural network was trained and evaluated in the reported pilot."
- "Stethoscope mode is clinically validated."
- "VeilWave outperforms existing systems."
