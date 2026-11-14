# Hardware and firmware notes

## Physical prototype described in the manuscript

The Option A manuscript describes the assembled prototype as containing:

- Raspberry Pi 4;
- Arduino Nano;
- Jamary microphone;
- piezoelectric disc.

This list describes the prototype assembly. It does not imply that every input path, firmware function, or operating mode in this repository has been verified on that assembly.

## Firmware status

`firmware/veilwave_firmware_16k_v2.ino` is experimental and explicitly marked **not tested on hardware**.

The source is written for an ATmega328P-style Arduino Nano and configures:

- Timer1-triggered ADC sampling;
- nominal 16 kHz sample rate;
- 10-bit ADC samples;
- 32 samples per packet;
- 42-byte packets;
- nominal 250,000 baud serial transport.

These are source-code design settings, not independently measured hardware results.

Before using this firmware on a physical board, a qualified developer should review the timer/ADC configuration, interrupt and ring-buffer concurrency, serial throughput, ADC accuracy, analog front-end, input protection, grounding, power supply, and packet-loss behaviour. Compile success alone is not hardware validation.

The source comments note that the ADC clock is 250 kHz, above the commonly recommended 200 kHz range for full 10-bit accuracy. Actual effective resolution must be measured in the intended circuit.

## Power and logic levels

Do not assume the Nano is a 3.3 V, 16 MHz board. Verify the exact board variant and its regulator/USB-serial circuitry. For a classic 5 V / 16 MHz ATmega328P Nano, use a supply and logic-level arrangement compatible with that board and the Raspberry Pi. Raspberry Pi GPIO is not 5 V tolerant. Do not connect a 5 V UART signal directly to a Raspberry Pi GPIO input.

The exact wiring must be checked against the actual board revisions and circuit. This repository does not provide a verified complete wiring diagram for the Jamary microphone and piezoelectric disc.

## Audio input

The Python sound-card path requests 16 kHz mono input. The UART path expects the repository's 42-byte packet format. The code does not establish that a particular microphone amplifier, bias circuit, or piezo preamplifier is suitable. Check gain, clipping, DC bias, anti-alias filtering, and electrical protection before connecting hardware.

## Raspberry Pi claims

The manuscript's pilot timing host is not verified as a Raspberry Pi 4. Do not use the Python live timing printout as evidence of Raspberry Pi 4 processing performance. A Pi-specific claim requires a documented benchmark run on the actual Pi 4, including operating system, Python and library versions, audio device, block size, CPU load, and repeated timing statistics.
