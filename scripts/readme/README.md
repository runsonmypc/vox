# README app animation

`demo.html` adapts the Terminal, Notes and Mail scenes from Alex's personal site
(`src/components/showcase/VoxShowcase.astro` and `src/data/vox.ts`). These app
windows are illustrations. The 224 × 44 overlay is rendered by Vox's production
AppKit code, with controlled microphone levels and the real waveform smoothing,
recording/transcribing icons, glowing bead and cancel glyph.

The demo keeps spoken words outside the app, pastes the entire batch result at
once, hides the overlay 50 ms before paste, and leaves the terminal command
unexecuted. Screen context is explained in the caption; it does not add controls,
OCR panels or colored formatting to the target app. There is one cloud sequence
across three examples, without repeating a local transcription animation.

To regenerate on macOS with the repo's Python environment, Chrome, and a local
installation of `puppeteer-core`:

```sh
.venv/bin/python scripts/readme/render-overlay.py /tmp/vox-demo-native
node scripts/readme/capture-demo.mjs /path/to/puppeteer-core/lib/puppeteer/puppeteer-core.js /tmp/vox-demo-native /tmp/vox-demo-frames
.venv/bin/python scripts/readme/encode-demo.py /tmp/vox-demo-frames docs/images/app-demo.png
```

`CHROME_PATH` can override Chrome's executable. Capturing uses a fixed clock at
20 fps, then holds each complete result. The 21.2-second APNG loops with native
RGBA transparency, without color-key removal or palette conversion. Capture
checks enforce text bounds, complete-result pasting and overlay dismissal;
encoding checks every decoded pixel and frame duration against the originals.
`app-demo-still.png` is the reduced-motion alternative used by the README.

The Snippets screenshots use the real macOS Settings window with an isolated
fixture: `"dev server" = "npm run dev -- --host 127.0.0.1 --port 4321"` replaces
`sign off` in both appearances. No personal config, microphone or provider is
used when generating these documentation images.
