# Using Vox Transfer

[Back to the README](../README.md)

- [The hotkey](#the-hotkey)
- [The menu](#the-menu)
- [Choosing another hotkey](#choosing-another-hotkey)
- [Long recordings](#long-recordings)

## The hotkey

| Action | What happens |
| --- | --- |
| Tap **right Shift** on its own | Start recording (Vox Transfer lowers the volume and plays a sound). |
| Tap it again | Stop, transcribe, and paste the text into the focused app. |
| Double-tap it | Cancel: discard the recording, or stop a transcription in progress. Nothing is pasted. |

Pressing the hotkey together with another key, as when typing a capital letter with Shift, does
nothing. To use another key, see [Choosing another hotkey](#choosing-another-hotkey).

A short sound tells you what Vox Transfer is doing: when dictation starts, stops, is cancelled or
fails, when you press the hotkey while it is busy or paused, and when you pause or resume.
[Sounds](settings.md#sounds) describes them and how to use your own.

## The menu

Click the Vox Transfer icon in the menu bar (macOS) or the tray (Linux):

- **Status**: idle, recording, processing, paused, or while idle a problem to fix (see
  [Menu messages](troubleshooting.md#menu-messages)).
- **Pause Dictation**: ignore the hotkey until you resume.
- **Input Device**: the microphone to record from, or the system default. A microphone connected
  while Vox Transfer runs appears after your next dictation, and on macOS also within 30 seconds
  while idle.
- **Transcription**: OpenAI (batch), OpenAI (streaming) or Local (whisper.cpp). A mode that is not
  set up (no API key, or no whisper.cpp model) is greyed out.
- **Recording Limit**: 5, 10, 15, 30 or 60 minutes.
- **Recent dictations**: the last three. Click one to copy it.
- **Search History…**: find past dictations, copy or delete one, or clear them all.
- **Vocabulary & Snippets…**: words to spell exactly as written, and snippets: say a trigger phrase
  on its own and Vox Transfer types the expansion instead.
- **Set API Key…**: paste, replace or remove your OpenAI API key.
- **Set Hotkey…** (while idle): see [Choosing another hotkey](#choosing-another-hotkey).
- **Quit Vox Transfer**. To start it again, open Vox Transfer from Applications or Spotlight (macOS)
  or your applications list (Linux). Vox Transfer quits the same way when its login service stops
  (for example `systemctl --user stop vox` on Linux), when you log out, or during an update, so a
  volume lowered for a recording is restored.

The hotkey, transcription mode and recording limit you choose are saved in
`~/.config/vox/config.toml`. The input device you choose lasts until Vox Transfer quits; set
`[audio] device` to keep one (see [Settings](settings.md)).

## Choosing another hotkey

Choose **Set Hotkey…** from the menu while Vox Transfer is idle.

- Click **Hotkey** and tap the key you want: Shift, Control, Option or Command on either side (on
  Linux Ctrl, Alt or Super, or AltGr), a function key from F1 to F20, fn (Globe) on macOS, or Pause
  or Scroll Lock on Linux. Keys you type with are refused.
- Click **Key combination** and press an optional combination such as Control + Space, which also
  starts and stops dictation (press it twice quickly to cancel; the app you're in receives it too).
- **Use Default** goes back to right Shift.

The new hotkey works as soon as you click Save, and while the window is open the hotkey does not
dictate.

macOS acts on fn too: to use it, set **Press 🌐 key to** (or **Press fn key to**) to **Do Nothing**
in System Settings > Keyboard, and if the Dictation shortcut there is to press 🌐 or fn twice,
choose another.

## Long recordings

A recording that reaches the recording limit (15 minutes unless you change it) stops and is
transcribed as if you had tapped the key. In batch mode a recording longer than about 13 minutes is
uploaded in parts, split at pauses; a part with nothing audible in it is not uploaded, and if a part
fails, the text of the parts before it is kept in history (or pasted, if history cannot store it).
