## MODIFIED Requirements

### Requirement: macOS Background Daemon LaunchAgent
The project SHALL provide a macOS LaunchAgent property list template that `install.sh` renders with absolute paths, allowing vox to be started, stopped, and managed via `launchctl` as a persistent background daemon for the user session. Its output SHALL go to a log file only the user can read.

#### Scenario: Daemon starts via launchd
- **WHEN** the LaunchAgent is loaded via `launchctl bootstrap` or `launchctl load`
- **THEN** the vox daemon starts in the background and restarts automatically after a crash, but not after Quit or when another instance is already running, which exit successfully

#### Scenario: Rendered agent
- **WHEN** `install.sh` installs the LaunchAgent
- **THEN** the agent runs `~/.local/bin/vox` by its absolute path, with a PATH that includes `~/.local/bin`, `/opt/homebrew/bin` and `/usr/local/bin`, and a umask of 077

#### Scenario: Shown as Vox Transfer in Login Items
- **WHEN** `install.sh` installs the LaunchAgent
- **THEN** the agent names the launcher's bundle id, `com.runsonmypc.vox.launcher`, in `AssociatedBundleIdentifiers`, so System Settings lists it under Login Items as the launcher app, Vox Transfer, while its label stays `com.runsonmypc.vox`

#### Scenario: Private log file
- **WHEN** the LaunchAgent runs Vox
- **THEN** its output goes to `~/Library/Logs/Vox/vox.log` (directory 0700, file 0600), and the installer deletes the old `/tmp/vox.stdout.log` and `/tmp/vox.stderr.log` files the user owns

#### Scenario: Log kept small
- **WHEN** Vox starts with its output going to a regular log file larger than 10 MiB
- **THEN** it empties the file and logs one INFO line saying so, while output that is not a regular file, such as the Linux journal, is left alone
