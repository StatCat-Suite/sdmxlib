"""MkDocs hook: link the tracker references the docs cite (GH-85).

The design docs cite the GitLab tracker as a bare `#N` (an issue), `!N` (a
merge request) or `%N` (a milestone), and GitHub's as `GH-N`. The source keeps
them bare, as CLAUDE.md's "Issue references" asks; on the docs site this hook
links each one: a GitLab reference to its export under `history/gitlab/`, a
GitHub one to the issue.

## What is left alone

Fenced and inline code, an existing Markdown link (its text and its target, so
a page anchor such as `(#9-full-example)` keeps working), an autolink or bare
URL, an HTML tag, a heading's `#` markers, `50%`, and a `#12` inside a word or
a path such as `page.md#12`.
"""

import re

#: Where the history and the issues live, from `repo_url`.
_ARCHIVE = "{repo}/blob/main/history/gitlab/{kind}/{number}.md"
_GITHUB = "{repo}/issues/{number}"
_KINDS = {"#": "issues", "!": "merge_requests", "%": "milestones"}

#: A reference: `GH-N`, or one of `# ! %` and a number, not inside a word, a
#: path or a longer number, and not followed by a hyphen (an anchor slug).
REFERENCE = re.compile(r"(?<![\w/#&!%.\[-])(GH-|[#!%])(\d+)(?![\w-])")
#: Spans never rewritten: inline code, a link or image, an autolink, a URL, an HTML tag.
PROTECTED = re.compile(r"`[^`\n]*`|!?\[[^\]\n]*\]\([^)\n]*\)|<https?://[^>\n]*>|https?://\S+|<[^>\n]+>")
FENCE = re.compile(r"^\s*(```|~~~)")


def link_references(markdown: str, repo: str) -> str:
    """`markdown` with every tracker reference outside code and links made a link.

    Args:
        markdown: A page's Markdown source.
        repo: The repository URL, `repo_url` in `mkdocs.yml`.

    Returns:
        The Markdown with the references linked.
    """

    def link(match: re.Match[str]) -> str:
        sigil, number = match.group(1), match.group(2)
        if sigil == "GH-":
            return f"[GH-{number}]({_GITHUB.format(repo=repo, number=number)})"
        return f"[{sigil}{number}]({_ARCHIVE.format(repo=repo, kind=_KINDS[sigil], number=number)})"

    out: list[str] = []
    fenced = False
    for line in markdown.split("\n"):
        if FENCE.match(line):
            fenced = not fenced
            out.append(line)
            continue
        if fenced:
            out.append(line)
            continue
        pieces: list[str] = []
        last = 0
        for span in PROTECTED.finditer(line):
            pieces.append(REFERENCE.sub(link, line[last : span.start()]))
            pieces.append(span.group(0))
            last = span.end()
        pieces.append(REFERENCE.sub(link, line[last:]))
        out.append("".join(pieces))
    return "\n".join(out)


def on_page_markdown(markdown: str, **event: object) -> str:
    """The MkDocs `on_page_markdown` event: link the page's references.

    MkDocs passes ``page``, ``config`` and ``files`` by keyword; only
    ``config`` is read, for `repo_url`.
    """
    config = event["config"]
    repo = str(getattr(config, "repo_url", None) or "").rstrip("/")
    return link_references(markdown, repo) if repo else markdown
