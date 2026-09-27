## Context

See proposal.md for why the app is renamed. The rename starts from main at ae10a09, where 955 tests pass on macOS (30 skipped), and is done on branch `vt/rename`. A LaunchAgent (`com.runsonmypc.vox`) runs the installed app on the owner's Mac, and earlier installs there and on test machines added a `Vox.app` launcher.

## Goals / Non-Goals

**Goals:**
- Wherever users see the app's name, it says Vox Transfer: menus, windows, messages, logs, launchers, the service description, the package description, the release title and the docs.
- An existing macOS install ends up with a single launcher, named Vox Transfer, once it is updated.

**Non-Goals:**
- Renaming the command, the package, paths, services, identifiers, the keychain item, environment variables or the repository.
- Renaming internal identifiers such as `VoxError`, `VoxIcon`, `VoxHistory` or the GTK application ids, or rewording code comments that use "Vox" as the program's short name.

## Decisions

1. **Only what users see changes.** A file under a new path would orphan settings, history and logs. A new launchd label would leave the old agent running. New bundle identifiers would lose the macOS permission grants, and a new keychain service would lose the saved key. The `.deb` keeps the package name `vox`, so upgrades still apply.
2. **The launcher changes its file name, not its bundle id.** The new launcher, `Vox Transfer.app`, keeps `com.runsonmypc.vox.launcher`. So the existing ownership guard (the user owns `Info.plist`, which names that bundle id) recognizes launchers under both names, and nothing else is needed to find old ones. The executable and icon inside the bundle keep their internal names, `Vox` and `Vox.icns`.
3. **Migration happens in `mac_launcher`, after the new launcher is written.** `remove_launchers Vox.app` deletes only this user's own `Vox.app`, in `/Applications` and in `~/Applications`. On the default case-insensitive volume that path is the same as the VOX music player's `VOX.app`, which the guard leaves alone. When neither place is free for the new launcher, the old one stays, so the user still has a way to start the app. `--uninstall` calls the same helper with both names.
4. **Login Items shows Vox Transfer through `AssociatedBundleIdentifiers`.** The launchd label cannot change (Decision 1). `man launchd.plist` documents `AssociatedBundleIdentifiers` for a legacy plist installed by an app: it names the bundles that System Settings' Login Items associates with the job. The agent lists the launcher's bundle id there. Spotlight and Finder show the name from the bundle's name and `CFBundleDisplayName`.
5. **`vox --version` prints `Vox Transfer X.Y.Z`.** The usage line still says `vox`, since that is the command. Nothing parses the output beyond finding the version number: `release.yml`, `packaging/deb/smoke-test.sh` and the install smoke test use `grep -F` or only the exit status.
6. **Specs change where they quote user-visible text.** Spec prose keeps "Vox" as the app's short internal name. A new `release-packaging` requirement records which names change and which stay.

## Risks / Trade-offs

- [`AssociatedBundleIdentifiers` with an unsigned launcher] Apple documents the key but not how it treats unsigned bundles. If Login Items does not pick it up, the agent is listed as before, and nothing else changes. → Check System Settings > General > Login Items after the first install on a spare account.
- [Dock or Finder sidebar items for `Vox.app`] They stop working after the update removes the old launcher. → The CHANGELOG and README say that the launcher is replaced.
- [Logs and scripts that match the old wording] Log lines now say Vox Transfer, so a user's `grep "Starting vox"` stops matching. RELEASING.md is updated to match. → Nothing in the repository parses these lines.
