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
from datetime import timezone, tzinfo
from string import Template

from logbook import db, engine
from logbook.ui.render import (  # pure; imports no Tk, as export.py's do
    engine_baseline_note, vessel_bar,
)

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

/* Badges left, state right — the two things scanned first (§14.10.2). */
.task-head {
  display: flex;
  align-items: center;
  gap: 0.35rem;
  margin-bottom: 0.4rem;
}
.task-head > :last-child { margin-left: auto; }

.task-desc { margin: 0 0 0.25rem; line-height: 1.4; }

a.row-link { color: inherit; text-decoration: none; display: block; }
a.row-link:hover .task-desc { color: var(--accent); }

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


# -- small shared bits ---------------------------------------------------------

def _when(iso: str | None, *, tz: tzinfo, fmt: str = "%d %b %Y %H:%M") -> str:
    """A stored UTC timestamp in the reader's clock. Blank stays blank — an
    absent time is absent, and must not become an invented one (§4.1)."""
    if not iso:
        return ""
    return db.parse_iso_utc(iso).astimezone(tz).strftime(fmt)


def _badge(text: str, *, kind: str = "") -> str:
    cls = f"badge {kind}".strip()
    return f'<span class="{_esc(cls)}">{_esc(text)}</span>'


def _empty(text: str) -> str:
    return f'<p class="empty">{_esc(text)}</p>'


def _vessel_title(d) -> str:
    """The boat's name from ``meta`` — never config (§15.4). Falls back to a
    generic title rather than an empty one, so an unnamed boat still has a page
    with a heading."""
    return d.get_meta("vessel_name", "") or "Vessel logbook"


def _vessel_reference(d) -> dict:
    """Identity from ``meta`` for ``render.vessel_bar``. Dimensions are NOT here:
    they are specification, not identity, and ``meta`` does not carry them (§8)."""
    return {
        "name": d.get_meta("vessel_name", ""),
        "ssr": d.get_meta("vessel_ssr", ""),
        "callsign": d.get_meta("vessel_callsign", ""),
        "mmsi": d.get_meta("vessel_mmsi", ""),
    }


# -- tasks.html ----------------------------------------------------------------

def _task_card(row, *, tz: tzinfo) -> str:
    """One task or issue, laid out from its fields rather than from a joined
    string — so the kind reads as a badge and the dates as their own column
    (§14.10.2: issues must be distinguishable from tasks at a glance).

    The row dict is ``export._task_issue_row``'s, so the card cannot disagree
    with tasks-and-issues.csv (§14.10.1).
    """
    kind = (row["kind"] or "").lower()
    done = row["status"] == "done"
    deleted = bool(row["deleted"])

    marks = [_badge(kind or "item", kind="flag" if kind == "issue" else "")]
    if deleted:
        marks.append(_badge("deleted", kind="flag"))
    elif row["edited"]:
        # §6.10: an edited row is visibly marked, here as everywhere.
        marks.append(_badge("edited", kind="quiet"))

    meta = [f"raised {_esc(_when(row['raised_utc'], tz=tz, fmt='%d %b %Y'))}"]
    if done:
        when = _when(row["done_utc"], tz=tz, fmt="%d %b %Y")
        meta.append(f"done {_esc(when)}" if when else "done")
        if row["done_note"]:
            meta.append(_esc(row["done_note"]))
    if deleted and row["deleted_reason"]:
        meta.append(f"deleted: {_esc(row['deleted_reason'])}")

    state = _badge("done", kind="quiet") if done else _badge("open")
    cls = "card deleted" if deleted else "card"
    return (
        f'<li class="{cls}">'
        f'<div class="task-head">{"".join(marks)}{state}</div>'
        f'<p class="task-desc">{_esc(row["description"])}</p>'
        f'<p class="muted">{" &middot; ".join(meta)}</p>'
        f'</li>'
    )


def render_tasks(rows, *, tz: tzinfo = timezone.utc, vessel: str = "") -> str:
    """The Tasks & Issues worklist — "what still needs doing on the boat?"

    The near-term wish behind the whole idea (§14.10). Open items first and
    newest first; done items are subordinate but PRESENT — they are the evidence
    something was dealt with — inside a collapsed ``<details>``, which is the
    no-JS way to keep them without burying the open ones (§14.10.2).
    """
    live = [r for r in rows if not r["deleted"]]
    gone = [r for r in rows if r["deleted"]]
    open_rows = [r for r in live if r["status"] != "done"]
    done_rows = [r for r in live if r["status"] == "done"]

    parts = [f'<h2>Open &middot; {len(open_rows)}</h2>']
    if open_rows:
        parts.append('<ul class="stack">'
                     + "".join(_task_card(r, tz=tz) for r in open_rows) + "</ul>")
    else:
        parts.append(_empty("Nothing open. The boat's list is clear."))

    for label, group in (("Done", done_rows), ("Deleted", gone)):
        if not group:
            continue
        parts.append(
            f"<details><summary>{_esc(label)} &middot; {len(group)}</summary>"
            '<ul class="stack">'
            + "".join(_task_card(r, tz=tz) for r in group)
            + "</ul></details>")

    return page("Tasks & Issues", "".join(parts),
                subtitle=vessel or None)


# -- index.html ----------------------------------------------------------------

def _session_card(row, *, tz: tzinfo) -> str:
    """One session in the list. Links to its own page; the whole card is the tap
    target, because the reader is one-handed on a phone (§14.10.2)."""
    number = int(row["id"])
    tag = f"session-{number:03d}"
    passage = " to ".join(x for x in (row["departed_from"], row["bound_for"]) if x)

    facts = []
    if row["distance_og_nm"] is not None:
        facts.append(f"{row['distance_og_nm']:g} nm")
    if not row["closed"]:
        facts.append("still open")
    # Each fact escaped, THEN joined with the entity — joining first would send
    # the '&' of '&middot;' through _esc and print a literal '&middot;'.
    trail = " &middot; ".join(_esc(f) for f in facts)

    return (
        f'<li class="card"><a class="row-link" href="{_esc(tag)}.html">'
        f'<div class="task-head">{_badge(f"Session {number:03d}", kind="quiet")}'
        f'<span class="muted">{_esc(_when(row["opened_utc"], tz=tz, fmt="%d %b %Y"))}</span>'
        "</div>"
        f'<p class="task-desc">{_esc(passage) or "&mdash;"}</p>'
        + (f'<p class="muted">{trail}</p>' if trail else "")
        + "</a></li>"
    )


def render_index(d, sessions, open_count: int, *,
                 tz: tzinfo = timezone.utc) -> str:
    """The dashboard — "what state is the boat in?"

    Vessel identity, cumulative engine hours WITH their provenance, how many
    items are open, then the sessions newest-first (§14.10.2). Home: every other
    page links back here, and nothing is more than one tap away.
    """
    rec = engine.reconciliation(d)
    bar = vessel_bar(_vessel_reference(d))

    parts = [
        '<div class="card lead">',
        f'<p class="figure">{rec.total_h:,.1f} h</p>',
        '<p class="provenance">Engine hours: baseline '
        f'{rec.baseline_h:,.1f} h ({_esc(engine_baseline_note(rec.note))})'
        f' + {rec.logged_h:,.1f} h logged since.</p>',
        "</div>",
    ]

    parts.append('<ul class="stack"><li class="card">'
                 f'<div class="task-head">{_badge("Worklist")}'
                 f'<span class="muted">{open_count} open</span></div>'
                 '<a class="row-link" href="tasks.html">'
                 '<p class="task-desc">Tasks &amp; Issues &rarr;</p></a>'
                 "</li></ul>")

    parts.append(f"<h2>Sessions &middot; {len(sessions)}</h2>")
    if sessions:
        parts.append('<ul class="stack">'
                     + "".join(_session_card(s, tz=tz) for s in sessions)
                     + "</ul>")
    else:
        parts.append(_empty("No sessions logged yet."))

    return page(_vessel_title(d), "".join(parts),
                subtitle=bar or None, home=False)
