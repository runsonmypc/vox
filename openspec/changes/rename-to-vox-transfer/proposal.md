## Why

The owner renamed the app from Vox to Vox Transfer before its first public release, 1.0.0 (a v1.0.0 release was published and withdrawn). The owner decided that only what users see changes. Renaming the `vox` command, the package, the paths, the services or the identifiers would break existing installs, the macOS permission grants, the keychain item and users' scripts, and users would gain nothing from it.

## What Changes

- Every place where users see the app's name now says "Vox Transfer": the tray and menu bar status line ("Vox Transfer · Idle") and its Quit item ("Quit Vox Transfer"), the app menu of the macOS windows, the GTK application name, the text of the API key and vocabulary windows, error, dialog and log messages, and `vox --help`.
- `vox --version` prints `Vox Transfer X.Y.Z`. **BREAKING** for anything that parses the output as `vox X.Y.Z`. The release workflow and the `.deb` smoke test only look for the version number, so they are unaffected.
- macOS launcher: the installer adds `Vox Transfer.app`, whose `CFBundleName` and `CFBundleDisplayName` are "Vox Transfer", with the same placement rules and ownership guard as before. An install or update deletes the `Vox.app` launcher that this user's installer added before, and never another app of that name, such as the VOX music player. `--uninstall` removes the user's own launcher under either name. The LaunchAgent names the launcher's bundle id in `AssociatedBundleIdentifiers`, so the Login Items list in System Settings shows the agent as Vox Transfer.
- Linux: the `Name=` of the application and autostart entries, the systemd unit's `Description=`, and the `.deb`'s description and `Upstream-Name` now say Vox Transfer. The file names stay the same.
- Release: the GitHub release is titled "Vox Transfer X.Y.Z", and installer messages say Vox Transfer.
- Docs: the README, CHANGELOG, RELEASING.md and `config.example.toml` call the app Vox Transfer. The README notes near Install that the command is still `vox`.
- These keep their names: the `vox` command and `/usr/bin/vox`, the Python package and modules, the `vox` project in `pyproject.toml`, `~/.config/vox`, `~/Library/Logs/Vox`, `~/.local/share/vox`, `/opt/vox`, `vox.service`, the launchd label and plist name, the bundle identifiers (`com.runsonmypc.vox…`), the `.desktop` file names, the `.deb` package `vox` and the release file names, the keychain item `vox`, environment variables, and the GitHub repository `runsonmypc/vox`.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `release-packaging`: a new requirement for the name users see. The per-user installer adds the launcher under its new name and replaces the old one. Uninstall removes the launcher under either name. Version output names Vox Transfer, and so does the release title.
- `desktop-frontend`: the status line now begins "Vox Transfer ·".
- `dictation-pipeline`: stopping on SIGTERM now points to the renamed Quit item.
- `macos-support`: the LaunchAgent is linked to the launcher, so Login Items shows Vox Transfer.

## Impact

- Code: user-visible strings in `vox/`, including the tray, the windows, the key and vocabulary models, and messages in `__main__.py`, `daemon.py`, `keystore.py`, `hotkey.py`, `injector.py`, `sounds.py` and `transcribe.py`. `install.sh` gets its launcher constants and a `remove_launchers` helper. Also changed: the files under `packaging/`, and the release title in `.github/workflows/release.yml`.
- Tests: assertions on renamed strings are updated. New tests cover the launcher migration and the names in the packaging files.
- User data: nothing moves. On macOS, the next `install.sh` run replaces the user's `Vox.app` launcher with `Vox Transfer.app`, so a Dock item that pointed at `Vox.app` no longer works.
- Code comments and spec prose still use "Vox" as the app's short internal name.
