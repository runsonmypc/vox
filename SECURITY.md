# Security policy

## Supported versions

Security fixes go into the latest release. To get them, run the installer again or install the new
`.deb` (see the README).

## Reporting a vulnerability

Please report security problems privately, not in a public issue:

1. Open the repository's **Security** tab on GitHub and choose **Report a vulnerability**
   ([direct link](https://github.com/runsonmypc/vox/security/advisories/new)).
2. Describe the problem, the version you use (`vox --version`), your operating system, and how to
   reproduce it.

You should get a reply within a week. Once a fix is released, the advisory is published with
credit to you, unless you prefer otherwise.

Please do not include real API keys, recordings or dictated text in a report.

## Scope

Vox records audio, reads text from the focused window for screen hints, pastes into other apps,
stores the OpenAI API key in the system keychain, and keeps a dictation history. Problems in any of
these, in the installer or in the release packages are in scope. Problems in OpenAI's service, in
whisper.cpp or in the operating system belong with their maintainers.
