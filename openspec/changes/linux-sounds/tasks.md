The work is on branch `vt/sounds`, which starts from `vt/final`. Every test named here exists on that branch. Task 5.4 is left open: it needs the owner to listen.

## 1. Analysis

- [x] 1.1 Convert the seven macOS alert sounds to WAV in a scratch folder outside the repository (`afconvert`), and measure each one: duration, attack, envelope and decay, main partials (frequency, level, decay rate), pitch glides, noise bands, and peak and RMS level; look at their spectrograms. Verified by the measurements recorded in design.md and in each table's comments in `vox/sounds.py`

## 2. Synthesis

- [x] 2.1 Replace the two-tone table and its generator with `Partial`, a table of partials per sound (`_SYNTH_SOUNDS`) and `_synthesize`: decaying sines with an attack, optional damping and pitch bend, and seeded bands of noise; verified by `test_a_partial_rises_to_its_level_then_fades_at_its_decay_rate`, `test_a_damped_partial_dies_quickly_from_then_on` and `test_a_band_of_noise_stays_in_its_band`
- [x] 2.2 Give each cue a sound that resembles its macOS counterpart, at its level; verified by `test_every_cue_has_a_built_in_sound`, `test_the_built_in_sounds_are_all_different`, `test_the_built_in_sounds_are_audible_and_never_clip`, `test_the_built_in_sounds_start_and_end_on_silence`, `test_the_built_in_sounds_are_short` and `test_the_built_in_sounds_are_the_same_every_time`
- [x] 2.3 Keep custom WAV files, the macOS path and the end time of a Linux sound working; verified by `test_a_custom_wav_replaces_that_cue_on_linux`, `test_an_unreadable_custom_wav_falls_back_to_the_built_in_sound`, `test_sound_player_macos_alert_mapping`, `test_a_custom_wav_replaces_that_cue_on_macos` and `test_a_linux_sound_notes_when_it_ends`

## 3. Tuning

- [x] 3.1 Define a similarity score (spectrogram distance after time alignment, and envelope correlation) and tune each table against it, keeping each sound's peak and RMS level within 0.5 dB of the macOS sound's; compare side-by-side spectrograms of each pair. Scores (0 to 100, higher is closer), before (the beeps) and after: start (Tink) 39.9 and 90.6, stop (Pop) 30.7 and 93.9, error (Basso) 31.6 and 91.3, busy (Funk) 53.5 and 91.9, cancel (Blow) 60.9 and 94.5, pause (Bottle) 60.6 and 93.2, resume (Glass) 62.3 and 92.1; mean 48.5 and 92.5. Each new sound's RMS level is within 0.6 dB of its macOS sound's, and its peak within 1.4 dB
- [x] 3.2 Measure the time to make the seven sounds, the fastest of 40 runs: 10 ms on the owner's Mac (while it was heavily loaded) and 12 ms on the Linux test PC

## 4. Docs and specs

- [x] 4.1 Describe the Linux sounds in the README's Sounds section, and add a line to the CHANGELOG's 1.0.0 entry
- [x] 4.2 Write the delta specs for `dictation-pipeline` (Audio Feedback Sounds) and `double-tap-cancel` (Cancellation Audio Feedback), and sync them into `openspec/specs`; verified by `openspec validate --all --strict`, and by a trial archive in a scratch copy that found the synced specs already matching ("Specs already in sync"), so archiving this change later leaves them as they are

## 5. Verification

- [x] 5.1 `uv run --frozen pytest -q -p no:cacheprovider` passes on macOS (1028 passed, 40 skipped; 1019 passed before this change), and so does `uv run --frozen ruff check vox tests`
- [x] 5.2 The Linux suite, with the GTK window tests, passes under Xvfb on the Linux test PC: 954 passed and 77 skipped without GTK, and 37 GTK tests passed
- [x] 5.3 Write listening files outside the repository: each new sound alone, and each macOS sound, 0.6 s of silence, then the new sound
- [ ] 5.4 The owner listens to the listening files, and on the Linux PC with Vox installed, and accepts the sounds or asks for changes
