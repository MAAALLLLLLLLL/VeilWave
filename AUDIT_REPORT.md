# Repository audit report

Review basis: uploaded `veilwave-repo.zip`, including the Python source, README, dependency list, tests, CI workflow, MIT licence, and Arduino firmware.

## Checks completed

- Python source compiled successfully with `py_compile`.
- CLI top-level help and offline-process help returned successfully.
- Automated test suite: **8 passed** in the review environment after the included updates.
- The repository has a GitHub Actions workflow for Python 3.10, 3.11, and 3.12.
- Arduino firmware was inspected but not compiled or tested; `arduino-cli` was not available in the review environment.

## Changes made for the public repository

1. Rewrote the README to distinguish the early research prototype from validated results and hardware.
2. Added a paper-compatibility note preserving the Option A claims and listing the gaps between the pilot and this repository.
3. Added hardware/firmware caveats, including the unverified Nano firmware and Raspberry Pi timing restrictions.
4. Added a step-by-step GitHub publishing guide.
5. Added citation metadata, contribution guidance, a security policy, and a more useful `.gitignore`.
6. Split required and optional Python dependencies into separate requirement files.
7. Fixed `build_pipeline` so deep-filter weights can be loaded independently of mask-network weights.
8. Added checks for incomplete threshold files and empty training/validation splits.
9. Added creation of the ONNX output directory and a clear PyTorch dependency check.
10. Added tests for fragmented UART input, threshold loading, invalid threshold files, and independent deep-filter weight loading.
11. Removed a PyTorch test warning caused by converting gradient-tracked tensors directly to Python floats.

## Remaining blockers to scientific or hardware claims

- The repository does not reproduce the complete pilot evaluation or regenerate all 192 result rows from the original data.
- The manuscript/code detector equation and figure inconsistency must be reconciled against the intended algorithm.
- The code's detector defaults are placeholders and are not the pilot thresholds.
- No complete source audio set or trained weights are included.
- The optional neural training code does not implement the manuscript's differentiable-PESQ loss and does not provide a complete independent held-out test evaluation.
- The firmware remains uncompiled and untested on the physical Arduino Nano.
- No complete microphone/piezo wiring implementation is verified by this repository.
- No Raspberry Pi 4 benchmark was performed during this review.
- The stethoscope mode remains proposed and unvalidated.

## Publication assessment

**Suitable as a clearly labelled experimental research-code repository after the author reviews the included files and confirms redistribution rights. Not suitable to describe as a validated product, a complete reproducibility archive, or the exact implementation that generated the manuscript's pilot results.**
