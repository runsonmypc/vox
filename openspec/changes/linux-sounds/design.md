## Context

See proposal.md for why the Linux sounds change. The work is on branch `vt/sounds`, which starts from `vt/final` (1019 tests pass on macOS there, 40 skipped). The macOS sounds are 48 kHz, 24-bit AIFF files in `/System/Library/Sounds`. They were converted to WAV in a scratch folder outside the repository, only to be measured. Nobody could listen to the results during the work, so similarity was judged by measurement, and the owner gets listening files to judge by ear.

## Goals / Non-Goals

**Goals:**
- Each Linux sound is recognizably its macOS counterpart: the same pitches, the same kind of timbre (a tick, a pop, a honk, plucked notes, a hum, knocks, a chime), about the same length and envelope, and the same level relative to the other sounds.
- Every value in the synthesis is a readable parameter: a frequency, a level, a decay rate, a time. A reader can see what each sound is made of.
- Making the sounds at startup stays cheap.

**Non-Goals:**
- Copying the macOS sounds, or any part of them, into the repository, in any form: samples, excerpts, wavetables, arrays or WAV files.
- Changing anything on macOS, or how custom WAV files work.
- Stereo. The macOS sounds are stereo, but their channels are nearly the same, and the Linux sounds stay mono like the beeps they replace.

## Decisions

1. **Measure, then synthesize.** For each macOS sound the analysis measured the duration, the attack, the envelope and decay, the main partials (frequency, level, decay rate, pitch glides) and the noise bands of its clicks, from spectrograms and by demodulating one partial at a time. Those measurements chose the parameters of a sum of decaying sines and noise bands, the way a sound designer imitates a sound by ear. Only these parameters are in the code; the measuring scripts and the converted WAV files stay outside the repository.
2. **One small model for every sound.** A `Partial` is a sine at a frequency that rises to a level over `attack` seconds (a quarter sine, so it starts from silence without a click), then fades at `decay` dB a second. Three options cover what the macOS sounds do: `damp` stops it quickly (1500 dB a second more) from a given time, like a hand on a string, which gives Basso its abrupt end and Funk its plucked notes; `bend` starts it sharp and slides it down to its pitch within a few milliseconds (a 5 ms time constant), which gives the last knock of Bottle its drop; `width` turns it into a band of noise, for the click of a strike. A sound is a list of 2 to 15 partials with start times (58 in all), written as a table in `vox/sounds.py`.
3. **Each partial runs until it has faded to -80 dBFS.** Its length follows from its level, decay and damping, so nothing is computed after it is inaudible and the sound's length, which `sound_playing_until` reports, is where it really ends. A 5 ms fade-out ends every sound on zero.
4. **Tuned against a similarity score, within the measurements.** Since nobody could listen, a score compared each new sound with its macOS counterpart after time alignment: the mean dB difference of two band spectrograms (half-ERB bands with 93 ms frames for pitch, one-ERB bands with 12 ms frames for timing), and the correlation of the two amplitude envelopes. An optimizer then adjusted levels (within a factor of 4) and decay rates (within a factor of 2), and timings only a little around the measured values, while keeping each sound's peak and RMS level within 0.5 dB of the macOS sound's. The results were rounded to readable values, and partials that the score did not miss were dropped. Detuned pairs of partials, tried for the shimmer of Glass, did not raise its score and were left out. The scores before and after are in tasks.md.
5. **Levels are the macOS sounds' levels.** Each sound's RMS level is within 0.6 dB of its macOS counterpart's as NSSound plays it, and its peak within 1.4 dB, so the sounds keep their loudness relative to each other. The loudest peak (the start sound) is -8.2 dBFS, well below clipping.
6. **The noise is seeded.** Each sound draws its noise from `numpy.random.default_rng(0)`, so the sounds are the same at every start and in tests.

## Risks / Trade-offs

- [Nobody listened while tuning] The score measures spectra and envelopes, not what a listener hears. → Listening files (each new sound alone, and the macOS sound, 0.6 s of silence, then the new one) go to the owner, and task 5.4 stays open until the owner has listened.
- [Longer sounds] The cancel sound lasts about 1.4 s, and the device rescan after a cancel waits for it. → The rescan is not urgent; it already waited for long custom sounds.
- [Close to the originals] The sounds are meant to resemble the macOS ones, but they are made from 58 partials of a few numbers each and share no audio with them. → The code, the specs and the README say that the sounds are Vox's own synthesis.
