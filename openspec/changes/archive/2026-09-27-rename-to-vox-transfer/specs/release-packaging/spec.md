## ADDED Requirements

### Requirement: Product Name
The system SHALL present itself to users as Vox Transfer: in its tray or menu bar menu, its windows, dialogs and messages, its log lines, `vox --help` and `vox --version`, its launchers, the Linux service's description, the Debian package's description, the release title and its documentation. The names that scripts, installs and permission grants depend on SHALL stay `vox`: the command and `/usr/bin/vox`, the Python package, `~/.config/vox`, `~/Library/Logs/Vox`, `~/.local/share/vox`, `/opt/vox`, `vox.service`, the launchd label and its plist, the bundle identifiers (`com.runsonmypc.vox…`), the `.desktop` file names, the `vox` Debian package and the release file names, the keychain item `vox`, environment variables, and the GitHub repository `runsonmypc/vox`.

#### Scenario: Names users see
- **WHEN** the user opens the menu, a window of the app, the applications list or Spotlight, reads the log, runs `vox --help`, or looks at the package or the release
- **THEN** the app is called Vox Transfer: the menu's first line begins "Vox Transfer ·", its last item is "Quit Vox Transfer", the launchers' `Name=` and the macOS launcher's bundle name are "Vox Transfer", the systemd unit's `Description=` is "Vox Transfer voice dictation", the `.deb` description begins "Vox Transfer", and the GitHub release is titled "Vox Transfer X.Y.Z"

#### Scenario: Names that stay vox
- **WHEN** an existing install is updated
- **THEN** the command, settings, history, logs, login service, keychain item and package keep the paths and names they had, so nothing moves and no permission or key is lost

## MODIFIED Requirements

### Requirement: Self-Contained Per-User Installation
The system SHALL provide one install script for macOS and Linux that installs Vox and its tray support for the current user, without keeping the source checkout and without a compiler. The installer SHALL NOT ask for, copy, or store the OpenAI API key, SHALL refuse to run as root, and on macOS SHALL never write into or delete an app bundle it did not create.

#### Scenario: Fresh install
- **WHEN** the user runs `./install.sh`
- **THEN** Vox is installed into its own virtualenv (`~/.local/share/vox/venv`) from exactly the hash-pinned packages in `requirements.lock`, with a `~/.local/bin/vox` link; missing system packages are installed (on Linux including `xdotool`, `xclip` and `xprop` from x11-utils); Vox starts at login whether or not an OpenAI API key is set; a Vox Transfer launcher is added to the system's applications; and it ends by naming the full path of the `vox` command (`~/.local/bin/vox`), warning when that folder is not on `PATH`

#### Scenario: Another app named Vox Transfer (macOS)
- **WHEN** `/Applications` already holds an app at `Vox Transfer.app` that is not this user's own launcher (its `Info.plist` is not owned by the user or lacks `com.runsonmypc.vox.launcher`)
- **THEN** the installer never writes into it: it puts the launcher at `~/Applications/Vox Transfer.app` under the same rule, or, when neither place is free, skips the launcher with a warning and still finishes; and `--uninstall` removes only this user's own launcher

#### Scenario: Another app named Vox (macOS)
- **WHEN** `/Applications` or `~/Applications` holds an app at `Vox.app` (`VOX.app` on the case-insensitive default volume, such as the VOX music player) that is not this user's own launcher
- **THEN** installing, updating and `--uninstall` never write into or delete it, and it does not stop the `Vox Transfer.app` launcher from going to `/Applications`

#### Scenario: Launcher from before the rename (macOS)
- **WHEN** an install or update adds the `Vox Transfer.app` launcher, and `/Applications` or `~/Applications` holds a `Vox.app` launcher that this user's installer added before the rename
- **THEN** that `Vox.app` is deleted, so the user is left with one launcher, Vox Transfer

#### Scenario: Start after Quit
- **WHEN** the user has quit Vox from the tray and opens the Vox Transfer launcher (the Applications folder or Spotlight on macOS, the applications list on Linux)
- **THEN** Vox starts again through its login service with the same permissions, and opening the launcher while Vox is running does nothing

#### Scenario: Already running
- **WHEN** Vox is started while another instance is running
- **THEN** it exits successfully before any permission prompt, so the service manager does not retry it

#### Scenario: GNOME tray host
- **WHEN** the installer runs on GNOME and no tray host is present
- **THEN** it installs and enables the AppIndicator extension through GNOME's own confirmation dialog, without sudo or logging out

#### Scenario: Update
- **WHEN** the user runs `./install.sh` again from a newer checkout or release
- **THEN** the new virtualenv is built where the old one was while the old one is kept aside, the new one must import Vox (with `python -P` from `/`, so the check tests the installed code and not a source tree) and run `vox --help` before it replaces the old one, existing configuration and history are kept, and the running service is restarted

#### Scenario: Failed or interrupted update
- **WHEN** building the new virtualenv, its smoke test, or the installer itself fails or is interrupted
- **THEN** the previous virtualenv is restored and keeps working, and a later run first recovers one left aside by an interrupted run

#### Scenario: Running as root
- **WHEN** `install.sh` is run as root
- **THEN** it exits with an error explaining that it must run as the user and asks for sudo itself when it needs system packages

#### Scenario: Linux system packages
- **WHEN** the installer on Linux finds required system packages missing
- **THEN** it installs them with `apt-get`, refreshing the package lists and retrying once if the first attempt fails, and on a system without `apt-get` it lists what is needed with a description and the Debian package name and exits

#### Scenario: Unsupported Linux Python
- **WHEN** `/usr/bin/python3` on Linux is not Python 3.12 or 3.13
- **THEN** the installer exits with a message naming the version it found, since some dependencies publish wheels only for those versions

#### Scenario: Desktop without graphical-session.target
- **WHEN** Vox is installed on a Linux desktop whose session never reaches `graphical-session.target` (Xfce, MATE, most window managers)
- **THEN** an XDG autostart entry starts the login service at login, and on desktops that do reach the target the entry does nothing, because it only starts a service that is already running

#### Scenario: macOS interpreter change
- **WHEN** an update on macOS moves Vox to a different Python build
- **THEN** the installer warns that macOS will ask again for Microphone, Accessibility, Input Monitoring and Screen Recording, and tells the user to remove the old python3.12 entries

#### Scenario: Wayland session
- **WHEN** the installer runs in a Linux Wayland session
- **THEN** it warns that the hotkey and paste only work in X11 (XWayland) apps

### Requirement: Uninstall
The system SHALL remove a per-user install with `install.sh --uninstall`, keeping the user's settings, history, logs and API key, and saying where they are.

#### Scenario: Uninstalling on Linux
- **WHEN** the user runs `install.sh --uninstall` on Linux
- **THEN** the login service is stopped and disabled, and the unit, autostart entry, launcher, icon, virtualenv and the `vox` link are removed, while `~/.config/vox` and `~/.local/share/vox` history are kept; the output explains how to delete the key from the login keyring and mentions an installed Vox Transfer `.deb`

#### Scenario: Uninstalling on macOS
- **WHEN** the user runs `install.sh --uninstall` on macOS
- **THEN** the LaunchAgent is booted out and deleted, only the launchers this user's installer created are removed (`Vox Transfer.app`, or `Vox.app` from before the rename, in `/Applications` or `~/Applications`), the virtualenv and the `vox` link are removed, and settings, history, `~/Library/Logs/Vox` and the Keychain item are kept; a launcher that cannot be deleted is reported and does not stop the rest of the uninstall

### Requirement: Version Reporting
The system SHALL report its installed version.

#### Scenario: Version flag
- **WHEN** the user runs `vox --version`
- **THEN** it prints `Vox Transfer` and the installed package version, or `unknown` when run from a checkout that was never installed

### Requirement: Release Workflow
The project SHALL publish a release when a `vX.Y.Z` tag is pushed. It SHALL publish only after the checks pass, and SHALL publish the release tarball, the Debian packages, `install.sh` and their checksums.

#### Scenario: Tagging a release
- **WHEN** a tag `vX.Y.Z` is pushed
- **THEN** the workflow checks that the tag matches the version in `pyproject.toml` and that `CHANGELOG.md` has a section for it, runs CI, builds the wheel with the locked setuptools and a `git archive` tarball of the tagged commit, builds and smoke-tests amd64 and arm64 packages on their own runners, writes `SHA256SUMS`, and creates the GitHub release titled "Vox Transfer X.Y.Z" with the changelog section as its notes

#### Scenario: Mismatched tag
- **WHEN** the tag does not match `pyproject.toml`'s version, or `CHANGELOG.md` has no section for it
- **THEN** the workflow fails before building anything

#### Scenario: Pre-release version
- **WHEN** the version contains letters, such as `1.1.0rc1`
- **THEN** the release is marked as a pre-release, so it never becomes the latest release that `install.sh` installs

#### Scenario: Write access
- **WHEN** the release workflow runs
- **THEN** only the job that creates the release can write to the repository
