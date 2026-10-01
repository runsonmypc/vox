# Using Vox Transfer

[Back to the README](../README.md)

- [The hotkey](#the-hotkey)
- [The menu](#the-menu)
- [Settings](#settings)
- [Choosing another hotkey](#choosing-another-hotkey)
- [Long recordings](#long-recordings)

## The hotkey

| Action | What happens |
| --- | --- |
| Tap **right Shift** on its own | Start recording (Vox Transfer lowers the volume and plays a sound). |
| Tap it again | Stop, transcribe, and paste the text into the focused app. |
| Click × in the overlay | Cancel: discard the recording, or stop a transcription in progress. Nothing is pasted. |

The overlay is on by default on macOS and Linux/X11; turn it off in General Settings. Double-tap cancellation is off by default; opt in with `[hotkey] double_tap_cancel = true` in the configuration file.

Pressing the hotkey together with another key, as when typing a capital letter with Shift, does
nothing. To use another key, see [Choosing another hotkey](#choosing-another-hotkey).

A short sound tells you what Vox Transfer is doing: when dictation starts, stops, is cancelled or
fails, when you press the hotkey while it is busy or paused, and when you pause or resume.
[Sounds](settings.md#sounds) describes them and how to use your own.

## The menu

Click the Vox Transfer icon in the menu bar (macOS) or the tray (Linux):

- **Status**: idle, recording, processing, paused, or while idle a problem to fix (see
  [Menu messages](troubleshooting.md#menu-messages)).
- **Set API Key…**, only while the key is missing or can't be read: opens Settings on its
  Transcription page.
- **Pause Dictation**: ignore the hotkey until you resume.
- **Recent dictations**: the last three. Click one to copy it.
- **Search History…**: find past dictations, copy or delete one, or clear them all.
- **Transcription** (while idle, with Settings closed): switch between OpenAI (batch), OpenAI
  (streaming) and Local (whisper.cpp). Another mode that can't run yet, for want of an API key or
  a whisper.cpp model, is greyed out; set it up on the Transcription page of Settings. The current
  mode stays clickable, so picking it again retries its setup. While the settings file has an
  error, every mode is greyed out until you fix it.
- **Settings…** (while idle): see [Settings](#settings).
- **Quit Vox Transfer**. To start it again, open Vox Transfer from Applications or Spotlight (macOS)
  or your applications list (Linux). Vox Transfer quits the same way when its login service stops
  (for example `systemctl --user stop vox` on Linux), when you log out, or during an update, so a
  volume lowered for a recording is restored.

## Settings

Choose **Settings…** from the menu while Vox Transfer is idle. It has five pages:

- **General**: the microphone (the refresh button next to it looks for one you just connected), the
  recording limit (5, 10, 15, 30 or 60 minutes), sounds, the optional
  [recording overlay](settings.md#recording-overlay) (macOS and Linux/X11, on by default), how far to lower other audio while
  recording, screen hints, and whether to keep failed recordings for retry.
- **Hotkey**: see [Choosing another hotkey](#choosing-another-hotkey).
- **Transcription**: OpenAI (batch), OpenAI (streaming) or Local (whisper.cpp), your OpenAI API key,
  the spoken language, a prompt, and the whisper.cpp program and model. A mode that is not set up
  (no API key, or no whisper.cpp model) can't be chosen, and says why.
- **Vocabulary**: words to spell exactly as written.
- **Snippets**: say a trigger phrase on its own and Vox Transfer types the expansion instead.

Each change is saved to `~/.config/vox/config.toml` as you make it, and a text field is saved when
you press Return or leave it. There is no Save button. The hotkey does not dictate while Settings is
open, and your changes apply as soon as you close it. [The Settings window](settings.md#the-settings-window)
shows every page.

Vox Transfer also finds a microphone connected while it runs after your next dictation, and on
macOS within 30 seconds while idle.

## Choosing another hotkey

Open **Settings…** from the menu and choose the **Hotkey** page.

- Click **Hotkey** and tap the key you want: Shift, Control, Option or Command on either side (on
  Linux Ctrl, Alt or Super, or AltGr), a function key from F1 to F20, fn (Globe) on macOS, or Pause
  or Scroll Lock on Linux. Keys you type with are refused.
- Click **Key combination** and press an optional combination such as Control + Space, which also
  starts and stops dictation (press it twice quickly to cancel; the app you're in receives it too).
- **Use Default** goes back to right Shift.

Each key is saved as soon as you press it, and the new hotkey works once you close Settings.

macOS acts on fn too: to use it, set **Press 🌐 key to** (or **Press fn key to**) to **Do Nothing**
in System Settings > Keyboard, and if the Dictation shortcut there is to press 🌐 or fn twice,
choose another.

## Long recordings

A recording that reaches the recording limit (15 minutes unless you change it) stops and is
transcribed as if you had tapped the key. In batch mode a recording longer than about 13 minutes is
uploaded in parts, split at pauses; a part with nothing audible in it is not uploaded, and if a part
fails, the text of the parts before it is kept in history (or pasted, if history cannot store it).

## Recover a failed recording

Open **Search History…** and select a failed recording or partial transcript. Choose
**Retry with Local** to use whisper.cpp, or **Retry with OpenAI Batch** to upload the complete
saved recording to OpenAI. Batch may charge again for the whole recording, including parts
already transcribed. Both methods use current transcription settings without changing the
default mode; streaming recordings can be recovered through either method.

History hides or minimizes before transcription starts. A complete result replaces the
original entry and automatically pastes into the external app focused when retry finishes.
If a Vox window has focus, the result stays ready to copy in History. The original time and
duration are retained, and audio is removed after the complete transcript is saved, even if
paste fails. If another attempt fails, the audio remains and distinct partial attempts can
be copied separately. Retry never starts or pastes automatically after restart.

Use **Cancel Retry** in History even when hotkeys are paused. Cancellation before completion
keeps the audio and earlier text. After the full transcript is saved, cancellation preserves
that text and suppresses paste that has not begun. A paste already sent finishes restoring
the clipboard. **Delete** and **Clear History** cancel affected retries and remove retained
audio, including recordings not yet indexed. Vox must be running to retry; offline copying
and deletion remain available.

Recovery is enabled by default. Turn **Keep failed recordings for retry** off in General
Settings to stop new saves and retries. Existing recordings stay until you delete them;
re-enabling restores their eligibility. Audio normally lives in `~/.local/share/vox/audio/`.
