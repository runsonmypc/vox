## Purpose

Defines how Vox is installed, updated, removed and released on macOS and Linux: the per-user installer and its release bootstrap, the Debian package, the hash-locked dependency set, the Linux login service, and the free CI and release workflows that build and check all of it.

## ADDED Requirements

### Requirement: Self-Contained Per-User Installation
The system SHALL provide one install script for macOS and Linux that installs Vox and its tray support for the current user, without keeping the source checkout and without a compiler. The installer SHALL NOT ask for, copy, or store the OpenAI API key, SHALL refuse to run as root, and on macOS SHALL never write into or delete an app bundle it did not create.

#### Scenario: Fresh install
- **WHEN** the user runs `./install.sh`
- **THEN** Vox is installed into its own virtualenv (`~/.local/share/vox/venv`) from exactly the hash-pinned packages in `requirements.lock`, with a `~/.local/bin/vox` link; missing system packages are installed (on Linux including `xdotool`, `xclip` and `xprop` from x11-utils); Vox starts at login whether or not an OpenAI API key is set; a Vox launcher is added to the system's applications; and it ends by naming the full path of the `vox` command (`~/.local/bin/vox`), warning when that folder is not on `PATH`

#### Scenario: Another app named Vox (macOS)
- **WHEN** `/Applications` already holds an app at `Vox.app` (`VOX.app` on the case-insensitive default volume) that is not this user's own Vox launcher (its `Info.plist` is not owned by the user or lacks `com.runsonmypc.vox.launcher`)
- **THEN** the installer never writes into it: it puts the launcher at `~/Applications/Vox.app` under the same rule, or, when neither place is free, skips the launcher with a warning and still finishes; and `--uninstall` removes only this user's own launcher

#### Scenario: Start after Quit
- **WHEN** the user has quit Vox from the tray and opens the Vox launcher (the Applications folder or Spotlight on macOS, the applications list on Linux)
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

### Requirement: Install from a Release
When `install.sh` runs outside a source tree, as with `curl -fsSL https://github.com/runsonmypc/vox/releases/latest/download/install.sh | bash`, the system SHALL download the latest release's tarball, verify it against that release's `SHA256SUMS`, and run the tarball's own installer with the same options. The script SHALL do nothing until it has been read completely.

#### Scenario: Latest release installed
- **WHEN** the script runs from a pipe or as a lone download
- **THEN** it resolves the latest `vX.Y.Z` release, downloads `vox-X.Y.Z.tar.gz` and `SHA256SUMS`, checks the hash, unpacks the tarball into a temporary directory that is removed afterwards, and runs `install.sh` from it with the options it was given

#### Scenario: Damaged download
- **WHEN** the tarball's hash does not match its `SHA256SUMS` entry, or the entry is missing
- **THEN** the installer stops with an error and installs nothing

#### Scenario: No release published
- **WHEN** the repository has no published release
- **THEN** the installer stops with an error saying it could not find one

### Requirement: Uninstall
The system SHALL remove a per-user install with `install.sh --uninstall`, keeping the user's settings, history, logs and API key, and saying where they are.

#### Scenario: Uninstalling on Linux
- **WHEN** the user runs `install.sh --uninstall` on Linux
- **THEN** the login service is stopped and disabled, and the unit, autostart entry, launcher, icon, virtualenv and the `vox` link are removed, while `~/.config/vox` and `~/.local/share/vox` history are kept; the output explains how to delete the key from the login keyring and mentions an installed Vox `.deb`

#### Scenario: Uninstalling on macOS
- **WHEN** the user runs `install.sh --uninstall` on macOS
- **THEN** the LaunchAgent is booted out and deleted, only the `Vox.app` launcher this user's installer created is removed (in `/Applications` or `~/Applications`), the virtualenv and the `vox` link are removed, and settings, history, `~/Library/Logs/Vox` and the Keychain item are kept; a launcher that cannot be deleted is reported and does not stop the rest of the uninstall

### Requirement: Debian Package
The system SHALL publish a `vox_<version>_<arch>.deb` for Ubuntu 24.04 and its derivatives, for amd64 and arm64, that installs Vox into `/opt/vox` with `/usr/bin/vox`, depends on the system packages Vox needs (including `x11-utils` for `xprop`, as `install.sh` requires), and starts Vox at login for every user.

#### Scenario: First install
- **WHEN** an administrator installs the package
- **THEN** the systemd user unit is enabled globally (`systemctl --global enable`), so Vox starts at the next login of every user, and an application launcher and an XDG autostart entry are installed

#### Scenario: Upgrade
- **WHEN** the package is upgraded or reinstalled
- **THEN** whether Vox starts at login stays as it was, including an administrator's `systemctl --global disable`, and running user instances are restarted if active

#### Scenario: Removal
- **WHEN** the package is removed
- **THEN** Vox is stopped for logged-in users, it no longer starts at login, `/opt/vox` is deleted, and each user's settings and history stay in their home folders

#### Scenario: Reinstall after removal
- **WHEN** a removed package that started Vox at login is installed again
- **THEN** it starts Vox at login again, and a purge forgets that setting and deletes the autostart entry

### Requirement: Hash-Locked Dependencies
Every install path SHALL install exactly the dependency versions in `requirements.lock`, generated from `uv.lock`, verified by hash, and then build Vox itself with the locked build backend and no dependency resolution. Every locked package SHALL have a wheel for each supported platform, so installing never needs a compiler.

#### Scenario: Installing dependencies
- **WHEN** `install.sh` or the `.deb` build installs Vox's dependencies
- **THEN** it installs `requirements.lock` with hash checking and without resolving further dependencies, then installs Vox without build isolation using the locked setuptools

#### Scenario: Lock out of date
- **WHEN** `requirements.lock` does not match `uv.lock`, or a locked package lacks a wheel for Linux x86_64 or aarch64 (glibc 2.39) or macOS on Python 3.12 or 3.13
- **THEN** the lock check fails in CI

#### Scenario: Packages that need a compiler
- **WHEN** a transitive dependency ships no wheels and Vox never imports it (evdev, for pynput's unused uinput backend)
- **THEN** it is left out of `requirements.lock`

### Requirement: Linux Login Service
On Linux, the system SHALL run Vox as a systemd user service tied to the graphical session. The service SHALL restart Vox after a crash but not after a permanent startup failure, and files Vox creates SHALL be readable only by the user.

#### Scenario: Crash
- **WHEN** Vox exits with a failure status other than 78
- **THEN** systemd restarts it after 3 seconds

#### Scenario: Permanent startup failure
- **WHEN** Vox exits with status 78 (a missing system dependency or an unusable lock directory)
- **THEN** systemd does not restart it, and the user starts it again from the launcher or with `systemctl --user start vox` once the problem is fixed

#### Scenario: Private files
- **WHEN** the service runs Vox
- **THEN** it runs with umask 0077

### Requirement: Version Reporting
The system SHALL report its installed version.

#### Scenario: Version flag
- **WHEN** the user runs `vox --version`
- **THEN** it prints `vox` and the installed package version, or `unknown` when run from a checkout that was never installed

### Requirement: Continuous Integration
The project SHALL check every push to `main` and every pull request with GitHub Actions on standard GitHub-hosted runners only, which are free for public repositories, and never on larger or paid runners.

#### Scenario: Checks on a pull request
- **WHEN** a pull request is opened or updated
- **THEN** CI runs ruff, the lock check, a syntax check and shellcheck of every shell script (with maintainer scripts checked as POSIX sh), the Linux tests under Xvfb with the GTK 4 window tests in their own process and required rather than skipped, the macOS tests, `install.sh` in a clean Ubuntu 24.04 container without a compiler, and an amd64 `.deb` build installed, upgraded and removed in a clean container, whose smoke test checks that `xdotool`, `xclip` and `xprop` are present and imports the packaged Vox from `/opt/vox` (with `python -P` from `/`), never the checkout it runs from

#### Scenario: Supply chain
- **WHEN** a workflow uses a third-party action
- **THEN** it is pinned to a full commit SHA, checkouts do not keep credentials, workflows default to read-only permissions, and uv is pinned to the version that wrote `uv.lock`

### Requirement: Release Workflow
The project SHALL publish a release when a `vX.Y.Z` tag is pushed. It SHALL publish only after the checks pass, and SHALL publish the release tarball, the Debian packages, `install.sh` and their checksums.

#### Scenario: Tagging a release
- **WHEN** a tag `vX.Y.Z` is pushed
- **THEN** the workflow checks that the tag matches the version in `pyproject.toml` and that `CHANGELOG.md` has a section for it, runs CI, builds the wheel with the locked setuptools and a `git archive` tarball of the tagged commit, builds and smoke-tests amd64 and arm64 packages on their own runners, writes `SHA256SUMS`, and creates the GitHub release with the changelog section as its notes

#### Scenario: Mismatched tag
- **WHEN** the tag does not match `pyproject.toml`'s version, or `CHANGELOG.md` has no section for it
- **THEN** the workflow fails before building anything

#### Scenario: Pre-release version
- **WHEN** the version contains letters, such as `1.1.0rc1`
- **THEN** the release is marked as a pre-release, so it never becomes the latest release that `install.sh` installs

#### Scenario: Write access
- **WHEN** the release workflow runs
- **THEN** only the job that creates the release can write to the repository
