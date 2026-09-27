"""The README and the pages in docs/ link only to files and headings that exist."""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PAGES = [REPO / "README.md", *sorted((REPO / "docs").glob("*.md"))]


def slug(heading: str) -> str:
    """GitHub's anchor for a heading: lowercase, punctuation dropped, spaces as hyphens."""
    return re.sub(r"[^\w\- ]", "", heading.strip().lower()).replace(" ", "-")


def anchors(page: Path) -> set[str]:
    text = re.sub(r"^```.*?^```", "", page.read_text(), flags=re.MULTILINE | re.DOTALL)
    headings = {slug(h) for h in re.findall(r"^#+ (.+)$", text, flags=re.MULTILINE)}
    return headings | set(re.findall(r'<a id="([^"]+)"', text))


def links(page: Path) -> list[str]:
    text = page.read_text()
    return re.findall(r"\]\(([^)\s]+)\)", text) + re.findall(r'(?:src|srcset|href)="([^"]+)"', text)


def test_slug_matches_github():
    assert slug("Screen hints (`[context] screen`)") == "screen-hints-context-screen"
    assert slug("Local transcription with whisper.cpp") == "local-transcription-with-whispercpp"
    assert slug("First start: your API key") == "first-start-your-api-key"


def test_relative_links_and_anchors_resolve():
    broken = []
    for page in PAGES:
        for link in links(page):
            if re.match(r"[a-z]+:", link):
                continue  # https: and mailto: links are not checked here
            path, _, anchor = link.partition("#")
            target = (page.parent / path).resolve() if path else page
            if not target.exists():
                broken.append(f"{page.relative_to(REPO)}: {link} (no such file)")
            elif anchor and target.suffix == ".md" and anchor not in anchors(target):
                broken.append(f"{page.relative_to(REPO)}: {link} (no such heading)")
    assert not broken, "\n".join(broken)


def test_every_docs_page_is_linked_from_the_readme():
    readme = (REPO / "README.md").read_text()
    for page in (REPO / "docs").glob("*.md"):
        assert f"docs/{page.name}" in readme, page.name
