"""HTML review export — the third tier, never the archival record.

CSV is canonical (§8); the ``.db`` is the working convenience. This renders those
same rows into pages a skipper reads on a PHONE — ashore, one-handed, months
after the passage, possibly with no signal (§14.10.2). Not the netbook.

  - **Self-contained**: inline CSS, no JS, no CDN, no web fonts. One file opens
    offline, years later, and shares as a single attachment.
  - **Generated, not a viewer**: ``fetch()`` from a ``file://`` page is
    CORS-blocked, so a double-clicked page cannot read its sibling CSVs, and §11
    rules out a local server. The data is inlined (§14.10.1).
  - **Every interpolated value goes through ``_esc``.** Remarks, item labels,
    notes, place names and the vessel name are all free text: one '<' in a
    remark otherwise breaks the page. There are no exceptions to this.
  - ``theme.py`` is deliberately NOT imported. Its palette and 36 px touch
    targets are a 1024x600 resistive screen at a chart table; carrying them onto
    a phone would be cargo-culting the wrong constraints (§14.10.2).

Read-only. No forms, no links off-box, navigation depth <= 2: ``index.html``
reaches everything, every other page links home.

Build order: step 1 of §14.10.1's plan.
Spec: §14.10, §14.10.1, §14.10.2.
"""

from __future__ import annotations

import html
from string import Template

# One shared stylesheet, inlined into every page. Light only: a single theme is
# one less thing to be wrong on a device we cannot test against.
#
# Mobile-first — the reader is on ~380 px of phone, so the base layout IS the
# narrow one and the wider screen only relaxes it. No fixed pixel widths, ~16 px
# base, system fonts (a web font is a network dependency, and there is none).
STYLESHEET = """
:root {
  --ink: #17242f;
  --ink-soft: #55677a;
  --ink-faint: #8496a6;
  --rule: #dbe3ea;
  --page: #eef2f5;
  --card: #ffffff;
  --accent: #1f5c8b;
  --accent-soft: #e7f0f7;
  --flag: #9a4c14;
  --flag-soft: #fbeee3;
  --gone: #9aa7b2;
}

*, *::before, *::after { box-sizing: border-box; }

html { -webkit-text-size-adjust: 100%; }

body {
  margin: 0;
  padding: 0 1rem 4rem;
  background: var(--page);
  color: var(--ink);
  font: 16px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
        "Helvetica Neue", Arial, sans-serif;
  /* The body never scrolls sideways; a wide table scrolls inside .scroll. */
  overflow-x: hidden;
}

.wrap { max-width: 46rem; margin: 0 auto; }

/* -- header ------------------------------------------------------------- */

.page-head { padding: 1.5rem 0 1rem; }

.home {
  display: inline-block;
  margin-bottom: 0.75rem;
  color: var(--accent);
  font-size: 0.9rem;
  text-decoration: none;
}
.home:hover { text-decoration: underline; }

h1 {
  margin: 0;
  font-size: 1.5rem;
  line-height: 1.25;
  letter-spacing: -0.01em;
}

.subtitle {
  margin: 0.35rem 0 0;
  color: var(--ink-soft);
  font-size: 0.95rem;
}

h2 {
  margin: 2rem 0 0.75rem;
  font-size: 1.05rem;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  color: var(--ink-soft);
}

/* -- card --------------------------------------------------------------- */

.card {
  background: var(--card);
  border: 1px solid var(--rule);
  border-radius: 8px;
  padding: 1rem;
  margin: 0 0 0.75rem;
}

.card.lead { border-left: 3px solid var(--accent); }

/* -- key/value ---------------------------------------------------------- */

.kv { margin: 0; }
.kv div {
  display: flex;
  justify-content: space-between;
  gap: 1rem;
  padding: 0.4rem 0;
  border-bottom: 1px solid var(--rule);
}
.kv div:last-child { border-bottom: 0; }
.kv dt { color: var(--ink-soft); font-size: 0.9rem; }
.kv dd { margin: 0; text-align: right; font-variant-numeric: tabular-nums; }

/* A figure that leads a page: big, but never without its provenance (§7). */
.figure {
  font-size: 2rem;
  font-weight: 600;
  letter-spacing: -0.02em;
  font-variant-numeric: tabular-nums;
}
.provenance {
  margin: 0.35rem 0 0;
  color: var(--ink-soft);
  font-size: 0.85rem;
  line-height: 1.45;
}

/* -- tables ------------------------------------------------------------- */

/* The wide ones (the entries timeline) scroll in here, not in the page. */
.scroll {
  overflow-x: auto;
  -webkit-overflow-scrolling: touch;
  border: 1px solid var(--rule);
  border-radius: 8px;
  background: var(--card);
}

table { border-collapse: collapse; width: 100%; font-size: 0.9rem; }

th, td {
  padding: 0.5rem 0.6rem;
  text-align: left;
  vertical-align: top;
  white-space: nowrap;
  border-bottom: 1px solid var(--rule);
}

th {
  font-weight: 600;
  font-size: 0.75rem;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  color: var(--ink-soft);
  background: var(--accent-soft);
}

tr:last-child td { border-bottom: 0; }

td.wrap-cell { white-space: normal; min-width: 12rem; }
td.num { text-align: right; font-variant-numeric: tabular-nums; }

/* -- row state (§6.10) --------------------------------------------------- */

/* Soft-deleted rows are shown struck through, never omitted — the page cannot
   be less complete than the CSV. */
.deleted td, .deleted { color: var(--gone); text-decoration: line-through; }
.deleted .badge { text-decoration: none; }

/* Rows sharing a group_id are visibly grouped. */
.group-start td { border-top: 2px solid var(--accent-soft); }

.badge {
  display: inline-block;
  padding: 0.05rem 0.4rem;
  border-radius: 999px;
  font-size: 0.7rem;
  font-weight: 600;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  white-space: nowrap;
  background: var(--accent-soft);
  color: var(--accent);
}
.badge.flag { background: var(--flag-soft); color: var(--flag); }
.badge.quiet { background: var(--page); color: var(--ink-faint); }

/* -- lists -------------------------------------------------------------- */

.stack { list-style: none; margin: 0; padding: 0; }
.stack li { margin: 0 0 0.5rem; }

a.row-link { color: inherit; text-decoration: none; display: block; }
a.row-link:hover { border-color: var(--accent); }

.muted { color: var(--ink-soft); font-size: 0.9rem; }
.empty { color: var(--ink-faint); font-style: italic; padding: 0.5rem 0; }

details { margin-top: 0.5rem; }
summary {
  cursor: pointer;
  color: var(--accent);
  font-size: 0.9rem;
  padding: 0.5rem 0;
}

/* -- footer ------------------------------------------------------------- */

.foot {
  margin-top: 2.5rem;
  padding-top: 1rem;
  border-top: 1px solid var(--rule);
  color: var(--ink-faint);
  font-size: 0.8rem;
  line-height: 1.5;
}

@media (min-width: 40rem) {
  body { padding: 0 2rem 4rem; }
  h1 { font-size: 1.9rem; }
}
"""

# The page shell. Values arrive pre-escaped or pre-rendered; the fragments named
# here ($home, $subtitle, $body) are HTML built by this module's own helpers.
_PAGE = Template("""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>$title</title>
<style>$stylesheet</style>
</head>
<body>
<div class="wrap">
<header class="page-head">
$home<h1>$heading</h1>
$subtitle
</header>
<main>
$body
</main>
<footer class="foot">$footer</footer>
</div>
</body>
</html>
""")

_HOME_LINK = '<a class="home" href="index.html">&larr; Dashboard</a>\n'

# Said on every page, because the page must not be mistaken for the record it
# renders (§8, §14.10). The reader may be holding this years later.
_FOOTER = ("Generated by the vessel logbook for review. "
           "The CSV export alongside it is the archival record.")


def _esc(value) -> str:
    """Escape ANY value bound for a page. The single gate — nothing reaches a
    template without passing through here (§14.10.1).

    ``None`` renders empty, not 'None': an unset field is blank on the page, as
    it is blank in the CSV. Non-strings are coerced rather than rejected, so a
    caller cannot sidestep the escape by passing a float.
    """
    if value is None:
        return ""
    return html.escape(str(value), quote=True)


def page(title: str, body: str, *, heading: str | None = None,
         subtitle: str | None = None, home: bool = True) -> str:
    """One complete, self-contained document.

    ``title`` is the tab/window title; ``heading`` the on-page ``h1`` when it
    should differ. ``body`` is HTML built by the caller from escaped values.
    ``home=False`` for index.html, which is home (navigation depth <= 2).
    """
    return _PAGE.substitute(
        title=_esc(title),
        heading=_esc(title if heading is None else heading),
        subtitle=f'<p class="subtitle">{_esc(subtitle)}</p>' if subtitle else "",
        home=_HOME_LINK if home else "",
        stylesheet=STYLESHEET,
        body=body,
        footer=_esc(_FOOTER),
    )
