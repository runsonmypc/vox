## Why

On macOS, Vox plays the system alert sounds (Tink, Pop, Basso, Funk, Blow, Bottle and Glass). On Linux it played pairs of plain sine beeps, so the app sounded different on each system, and the Linux beeps were up to 12 dB louder than the macOS sounds. The owner decided that the Linux sounds should resemble the macOS ones. Apple's sound files are Apple's copyrighted assets, so nothing of them may enter the repository: no samples, no excerpts and nothing derived from them sample by sample. The macOS sounds are only measured, to choose the parameters of Vox's own synthesis, the way a sound designer imitates a sound by ear.

## What Changes

- On Linux, and wherever NSSound is not available, each built-in sound is synthesized when Vox starts from a short table of partials. A partial is a sine that rises, then fades at a set rate in dB per second, optionally damped quickly from a given time and optionally sliding down to its pitch at the start, or a band of noise for the click of a strike. Each table imitates its macOS counterpart's pitch, timbre, length, envelope and level:
  - start (like Tink): a 698 Hz tick with a click, 0.055 s.
  - stop (like Pop): a bubble that bounces, six ticks at about 680 and 500 Hz, each fainter, 0.22 s.
  - error (like Basso): a low honk, harmonics of 216 Hz over harmonics of 84 Hz, cut off at 0.16 s.
  - busy (like Funk): three plucked notes, 159, 319 and 401 Hz, each damped, then a faint echo, 1.0 s.
  - cancel (like Blow): a soft 391 Hz hum that swells in with a 48 Hz flutter, joined by 494 and 587 Hz, 1.37 s.
  - pause (like Bottle): three knocks falling in pitch, 494, 246 and 185 Hz, 0.86 s.
  - resume (like Glass): a 391 Hz body under bell partials from 2.3 to 14.7 kHz, struck again every 126 ms and softer each time, 1.04 s.
- Levels follow the macOS sounds, so the sounds keep their loudness relative to each other, and every sound peaks at least 6 dB below full scale.
- macOS is unchanged. Custom WAV files in `~/.config/vox/sounds` replace the built-in sounds as before, and the device rescan after a dictation still waits for a sound that is still playing.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `dictation-pipeline`: Audio Feedback Sounds now says that the Linux sounds resemble the macOS alert sounds and are Vox's own synthesis, and what every built-in Linux sound guarantees (silent start and end, headroom, length, the same sound at every start).
- `double-tap-cancel`: the built-in cancellation sound is Blow on macOS and Vox's sound resembling it on Linux, no longer "a synthetic descending tone".

## Impact

- Code: `vox/sounds.py` only. The two-tone table and its generator are replaced by `Partial`, the `_SYNTH_SOUNDS` table and `_synthesize`. numpy only, no new dependency.
- Startup: making the seven sounds takes about 11 ms (see tasks.md).
- The Linux sounds are longer than the beeps (0.055 to 1.37 s instead of 0.14 to 0.4 s), so after a cancel the device rescan waits about 1.5 s instead of 1 s.
- Tests: `tests/test_sounds.py`.
- Docs: the README's Sounds section and the CHANGELOG's 1.0.0 entry.
