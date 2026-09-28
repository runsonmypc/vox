# Privacy

[Back to the README](../README.md)

In short:

- Vox Transfer talks to one service, OpenAI, and only when you dictate in an OpenAI mode or save an
  API key (it checks a new key by listing OpenAI's models, which costs nothing).
- Local (whisper.cpp) mode sends nothing anywhere.
- No analytics, telemetry or update checks.
- Recordings are never saved. Your history stays on your computer, and you can delete it.

The details:

- [What leaves your computer](#what-leaves-your-computer)
- [Screen hints](#screen-hints-context-screen)
- [What stays on your computer](#what-stays-on-your-computer)

## What leaves your computer

| Mode | Sent to OpenAI |
| --- | --- |
| OpenAI (batch), the default | The recording (as 16 kHz mono audio), the model name, your `language` and `prompt` settings, and a list of spelling hints. |
| OpenAI (streaming) | The recording as you speak (as 24 kHz mono audio), the model name, your `language` and `prompt` settings, and spelling hints from your dictionary and the window title. If the live session fails, Vox Transfer sends the recording as in batch mode. |
| Local (whisper.cpp) | Nothing. Audio, hints and text stay on your computer. |

The spelling hints are your dictionary words plus, when screen hints are on, up to 10 words from the
focused window's title and up to 25 words from the text visible in it: at most 40 words in total.
Streaming mode sends its hints as it connects, before the window's text could be read, so it uses
only the dictionary and the title. On Linux, Local (whisper.cpp) mode uses only the dictionary:
whisper.cpp gets its hints and your `prompt` setting on its command line, which other accounts on
the computer can see, so Vox Transfer gives it no window or screen words there and does not capture
the screen for it. Those accounts can still see your dictionary and `prompt`, so keep secrets out of
them. Words that look like passwords, tokens or API keys are removed before anything is sent, and
before they reach whisper.cpp:

- keys and tokens such as `sk-…`, `ghp_…`, `AKIA…`, JSON web tokens, and long random strings;
- the value after a name such as `API_KEY=`, `token:`, `DB_PASS=`, `MYSQL_PWD=` or `password:`,
  and for a password the rest of its line when the value is not in quotes;
- the user name and password in a URL, as in `postgres://user:password@host` (the host stays);
- passwords on a command line, as in `--password VALUE`, `--pass=VALUE`, `--passphrase VALUE`,
  `-pVALUE` and `-u user:VALUE`.

The filter works from patterns and cannot catch every password, so turn screen hints off while
secrets are on screen. Your API key goes to OpenAI with each request, as any OpenAI client's does.
OpenAI's API terms and data usage policies apply to what it receives.

## Screen hints (`[context] screen`)

Screen hints help the transcriber spell names and terms that are on screen. With them on (the
default), Vox Transfer reads text from the focused window only, never the whole screen or other
windows:

- **macOS**: the text of the tmux pane when the focused app is a terminal running tmux in its only
  session (one window and one tab, not split by the terminal itself), so the pane is the one on
  screen; otherwise a screenshot of the focused window, read with Apple's on-device text recognition.
- **Linux** (not in Local mode, see above): the window's text through the accessibility interface
  (AT-SPI). If that finds little, a screenshot of the window read with `tesseract` when `maim` and
  `tesseract` are installed, or, in a terminal running tmux in its only session, the pane text.

Screenshots are never kept: on macOS the temporary file is deleted as soon as it is read, and on
Linux the image goes straight from `maim` to `tesseract`. Text recognition runs on your computer,
and only the filtered words listed above are sent.

With screen hints off (the **Screen hints** switch on the General page of Settings, or
`screen = false` under `[context]`), Vox Transfer sends no window-title words and no screen text
to any service and never captures the screen, so macOS never asks for Screen Recording. It still
looks up which app has focus, locally, to pick the right paste shortcut.

## What stays on your computer

- **History**: every dictation's text, with its time, length, the kind of app it went to (not the
  window title) and the transcription that produced it (batch, streaming or whisper.cpp; a streaming
  recording that fell back to batch counts as batch), in `~/.local/share/vox/history.db`, readable
  only by you. Recordings are never saved. Open **Search History…** to find past dictations,
  **Delete** one, or **Clear History** to erase them all; deleted text is overwritten, not just
  unlinked.
- **Recent dictations**: the last three appear in the Vox Transfer menu, so anyone who sees your
  screen, for example in a screen share, can read them.
- **Logs**: `~/Library/Logs/Vox/vox.log` on macOS (readable only by you, and emptied when
  Vox Transfer starts if it has grown past 10 MiB), the user journal on Linux
  (`journalctl --user -u vox`). They record events, lengths and errors, not what you said. Only
  `vox -v` (verbose) logs transcripts, snippet expansions and the titles of the windows you dictate
  into. Your API key is never logged.
- **Settings**: `~/.config/vox/config.toml`, which holds your dictionary and snippets.
- **Clipboard**: Vox Transfer pastes through the clipboard and then puts back what was there before.
  On macOS that includes images, files and rich text, and Vox Transfer marks its temporary copy so
  clipboard managers and Universal Clipboard ignore it. On Linux one form comes back: copied files,
  otherwise plain text, otherwise an image or HTML, so rich text copied from a browser returns as
  plain text. Vox Transfer cannot mark its temporary copy on Linux, so a clipboard manager there may
  keep each dictation in its history. A password that a password manager marks as one when you copy
  it is treated differently. On Linux (KeePassXC marks it this way) Vox Transfer does not put it
  back, and the clipboard is empty after the paste: the password could only come back without its
  mark, and a clipboard history would then keep it. On macOS it comes back for this Mac only, so
  Universal Clipboard does not offer it to your other devices.
