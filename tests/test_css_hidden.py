"""Guard against this project's most-repeated bug.

`[hidden] { display: none }` in the browser's own stylesheet is specificity
(0,1,0). A bare author rule like `.card { display: flex }` ties it -- and on a
tie, author CSS wins. An id rule outranks it outright. So an unscoped
`display:` silently cancels the hidden attribute, and an element the JS
believes it hid renders anyway.

This has shipped five separate times here: `.screen` (every screen visible at
once), `#settings-host-controls`, `.card`, `#details-image`, and
`.skip-vote-btn`. The fix each time is `:not([hidden])` on the selector. This
test enforces it by parsing the stylesheet, so the sixth one fails in CI
instead of in a playtest.
"""

import re
from pathlib import Path

CSS_PATH = Path(__file__).resolve().parent.parent / "static" / "style.css"

# Selectors that structurally cannot carry [hidden]: element/pseudo selectors,
# and descendant rules whose subject is a generated or always-present child.
# Anything id- or class-addressable that JS could hand a `hidden` attribute is
# deliberately NOT exempt.
ALLOWED_UNSCOPED = {
    "*",
    "body",
    "h1::after",
    ".avatar-choice img",
    ".avatar-choice .fallback-emoji",
    ".vote-avatar.fallback-emoji",
    ".score-avatar.fallback-emoji",
    ".score-row",
    ".player-avatar-emoji",
    ".player-text",
    ".player-card",
    ".vote-option",
    ".char-info-toggle",
    ".anime-controls",
    ".history-hint",
    ".reaction-emoji-row",
    ".reaction-phrases",
    ".reaction-free",
    ".waiting-dots",
    "#settings-host-controls label",
}

RULE = re.compile(r"([^{}]+)\{([^{}]*)\}")
DISPLAY = re.compile(r"(?:^|[;{\s])display\s*:\s*([^;]+)")


def _rules():
    css = CSS_PATH.read_text()
    for match in RULE.finditer(css):
        selector = match.group(1).strip().splitlines()[-1].strip()
        yield selector, match.group(2)


def test_every_display_rule_is_scoped_against_the_hidden_attribute():
    offenders = []
    for selector, body in _rules():
        for declaration in DISPLAY.finditer(body):
            if declaration.group(1).strip() == "none":
                continue  # `display: none` is the one value that can't break it
            if ":not([hidden])" in selector:
                continue
            if selector in ALLOWED_UNSCOPED:
                continue
            offenders.append(selector)

    assert not offenders, (
        "Unscoped `display:` rules will cancel the hidden attribute. Add "
        f":not([hidden]) to: {sorted(set(offenders))}"
    )


def test_grouped_selectors_are_each_scoped_individually():
    """`#a, #b:not([hidden]) { display: flex }` scopes only the second one --
    a real bug this project shipped, and invisible at a glance."""
    offenders = []
    for selector, body in _rules():
        if not any(d.group(1).strip() != "none" for d in DISPLAY.finditer(body)):
            continue
        parts = [p.strip() for p in selector.split(",") if p.strip()]
        if len(parts) < 2:
            continue
        for part in parts:
            if ":not([hidden])" not in part and part not in ALLOWED_UNSCOPED:
                offenders.append(f"{part}  (in: {selector})")

    assert not offenders, offenders


def test_the_allowlist_has_not_gone_stale():
    """Every exemption must still correspond to a real rule, so the list can't
    quietly accumulate entries that no longer mean anything."""
    selectors = {selector for selector, _ in _rules()}
    unused = {s for s in ALLOWED_UNSCOPED if s not in selectors}
    assert not unused, f"allowlist entries no longer in style.css: {sorted(unused)}"
