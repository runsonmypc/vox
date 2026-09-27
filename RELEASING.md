# Releasing Vox

A release is a GitHub release made by `.github/workflows/release.yml` when a `vX.Y.Z` tag is
pushed. This file is for the maintainer. Users install from the README.

## Before the first release

- **Make the repository public.** CI and the release run only on standard GitHub-hosted runners
  (`ubuntu-24.04`, `ubuntu-24.04-arm` and `macos-latest`), which are free on public repositories.
  In a private repository they use the account's Actions minutes, so check the plan first.
- **Allow the actions the workflows use** if the repository restricts them (Settings > Actions >
  General): `actions/checkout`, `actions/upload-artifact`, `actions/download-artifact` and
  `astral-sh/setup-uv`. The workflows pin each one to a commit, with its version in a comment.
- **Turn on private vulnerability reporting** (Settings > Security), which `SECURITY.md` points to.
- The workflows need no secrets. Only the `publish` job can write to the repository, through the
  `contents: write` permission it asks for itself.

## Try the release workflow without publishing

Run the release workflow by hand, from the Actions tab (Release > Run workflow, then pick a branch)
or with the GitHub CLI:

```sh
gh workflow run release.yml --ref main
gh run watch
```

A manual run is a dry run. It does everything a release does except publish:

1. Checks that `CHANGELOG.md` has a section for the version in `pyproject.toml`. The tag check is
   skipped, since a branch has no tag.
2. Runs the lint and test jobs of `ci.yml` (Linux and macOS).
3. Builds the wheel and the source tarball.
4. Installs from the unpacked tarball with its `install.sh`: in clean Ubuntu 24.04 containers on
   amd64 and arm64 (install, update, uninstall, with no C compiler present), and on macOS (install
   without the login service, `vox --version`, uninstall).
5. Builds the `.deb` for amd64 and arm64 from that wheel, then installs, upgrades, removes,
   reinstalls and purges it in a clean Ubuntu 24.04 container.
6. Collects the release files with their `SHA256SUMS`, shows the release notes in the run's
   summary, and uploads the files as the workflow artifact `release`.

The `publish` job is skipped. To look at the files it would have published:

```sh
gh run download <run-id> --name release
```

## Cut a release

1. **Pick the version** `X.Y.Z` ([semantic versioning](https://semver.org/)) and set it as
   `version` in `pyproject.toml`. `tests/test_release.py` accepts only `X.Y.Z`.

2. <a id="relock"></a>**Relock.** `uv.lock` records Vox's own version, so it must be regenerated,
   with the uv version that CI pins (`UV_VERSION` in `.github/workflows/ci.yml` and `release.yml`,
   now 0.12.19); another uv may write a different lock. `scripts/lock.sh` calls `uv` itself, so
   put the pinned uv first on `PATH`:

   ```sh
   mkdir -p /tmp/uv-pinned
   printf '#!/bin/sh\nexec uvx uv@0.12.19 "$@"\n' >/tmp/uv-pinned/uv
   chmod +x /tmp/uv-pinned/uv
   PATH="/tmp/uv-pinned:$PATH" uv lock
   PATH="/tmp/uv-pinned:$PATH" scripts/lock.sh
   PATH="/tmp/uv-pinned:$PATH" scripts/lock.sh --check
   ```

   To move to a newer uv, change `UV_VERSION` in both workflows and relock with it.

3. **Describe it in `CHANGELOG.md`.** Add a `## [X.Y.Z] - YYYY-MM-DD` section above the previous
   one, dated the day you tag, and a link line at the bottom:

   ```
   [X.Y.Z]: https://github.com/runsonmypc/vox/releases/tag/vX.Y.Z
   ```

   That section becomes the release notes word for word. `scripts/changelog-notes.sh X.Y.Z` prints
   what they will be.

4. **Check and commit.** Run `uv run --frozen pytest -q` and `uv run --frozen ruff check vox tests`,
   commit, push to `main`, and wait for CI to pass. A dry run (above) on `main` also tests the
   installers and packages.

5. **Tag the tested commit and push the tag:**

   ```sh
   git tag -a vX.Y.Z -m "Vox X.Y.Z"
   git push origin vX.Y.Z
   ```

   The workflow stops before building anything if the tag does not name the version in
   `pyproject.toml` or `CHANGELOG.md` has no section for it. It publishes only after every build
   and test job has passed.

If a job fails, nothing has been published. Fix the problem on `main`, then move the tag to the
fixed commit:

```sh
git push --delete origin vX.Y.Z
git tag -d vX.Y.Z
```

and tag again (step 5). If only the `publish` job failed after it created the release, delete the
release with `gh release delete vX.Y.Z` (the tag stays) and re-run the failed job with
`gh run rerun <run-id> --failed`.

## What a release publishes

A GitHub release named "Vox X.Y.Z" on the tag `vX.Y.Z`, with the `CHANGELOG.md` section as its
notes and these files:

| File | What it is |
| --- | --- |
| `vox-X.Y.Z.tar.gz` | The tagged source tree (`git archive`), with `install.sh` for macOS and Linux. |
| `vox_X.Y.Z_amd64.deb`, `vox_X.Y.Z_arm64.deb` | Packages for Ubuntu 24.04 and distributions based on it. Vox and its locked dependencies go to `/opt/vox`, and Vox starts at login for every user. |
| `install.sh` | The installer on its own. Run without a source tree, it downloads the latest release's tarball, checks it against `SHA256SUMS` and runs the `install.sh` inside. |
| `SHA256SUMS` | SHA-256 checksums of the four files above. |

`https://github.com/runsonmypc/vox/releases/latest/download/install.sh` then points at the new
installer, and the README's one-line install installs the new version. Nothing is uploaded to PyPI
or a package repository; the wheel is built only to make the `.deb` packages.

## Check the published release

Installing starts Vox at login, so use a spare Mac, a spare account or a virtual machine rather
than the computer you work on. Each check ends by removing Vox again.

### macOS

Needs [uv](https://docs.astral.sh/uv/) (`brew install uv`).

```sh
curl -fsSL https://github.com/runsonmypc/vox/releases/latest/download/install.sh | bash
~/.local/bin/vox --version                                   # vox X.Y.Z
launchctl print "gui/$(id -u)/com.runsonmypc.vox" | grep state   # state = running
grep "Starting vox" ~/Library/Logs/Vox/vox.log
```

The Vox icon appears in the menu bar and Vox.app in Applications. Allow Microphone, Accessibility
and Input Monitoring when macOS asks, then dictate a sentence into any text field. Remove it with:

```sh
curl -fsSL https://github.com/runsonmypc/vox/releases/latest/download/install.sh | bash -s -- --uninstall
```

### Ubuntu 24.04, with the tarball installer

```sh
curl -fsSL https://github.com/runsonmypc/vox/releases/latest/download/install.sh | bash
~/.local/bin/vox --version        # vox X.Y.Z
systemctl --user status vox       # active (running)
journalctl --user -u vox -e
```

On GNOME, click Install if a dialog offers the AppIndicator extension; the Vox icon then appears in
the top bar. Dictate a sentence, then remove Vox with the same one-liner followed by
`bash -s -- --uninstall`.

### Ubuntu 24.04, with the .deb

Use `arm64` in place of `amd64` on an arm64 machine (`dpkg --print-architecture` says which).

```sh
curl -fsSLO https://github.com/runsonmypc/vox/releases/download/vX.Y.Z/vox_X.Y.Z_amd64.deb
curl -fsSLO https://github.com/runsonmypc/vox/releases/download/vX.Y.Z/SHA256SUMS
sha256sum --check --ignore-missing SHA256SUMS
sudo apt install ./vox_X.Y.Z_amd64.deb
vox --version                                 # vox X.Y.Z
systemctl --global is-enabled vox.service     # enabled
systemctl --user start vox                    # or log out and back in
systemctl --user status vox                   # active (running)
```

Dictate a sentence, then remove the package with `sudo apt remove vox` and check that
`systemctl --user status vox` no longer finds the unit.
