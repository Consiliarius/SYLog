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
import json
from datetime import timezone, tzinfo
from string import Template

from logbook import db, engine
from logbook.ui.render import (  # pure; imports no Tk, as export.py's do
    distance_through_water, engine_baseline_note, engine_method_text,
    engine_run_when, format_hm, format_nm, precip_text, split_label, vessel_bar,
    wind_text,
)

# One shared stylesheet, inlined into every page. Light only: a single theme is
# one less thing to be wrong on a device we cannot test against.
#
# Mobile-first — the reader is on ~380 px of phone, so the base layout IS the
# narrow one and the wider screen only relaxes it. No fixed pixel widths, ~16 px
# base, system fonts (a web font is a network dependency, and there is none).
STYLESHEET = """
:root {
  /* Light only, and declared: without this a phone set to dark can have the
     user agent restyle form controls and scrollbars over a page that has no
     dark palette to meet them (§14.10.2). */
  color-scheme: light;

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
  /* The body never scrolls sideways (§14.10.2). */
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

/* The baseline's provenance sits UNDER its label, never beside the figure —
   §7's caveat is part of the row, not a footnote to it. */
.kv .sub {
  display: block;
  color: var(--ink-faint);
  font-size: 0.8rem;
  line-height: 1.35;
  max-width: 22rem;
}

/* The row the sum lands on. */
.kv .total { border-top: 2px solid var(--rule); border-bottom: 0; }
.kv .total dt { color: var(--ink); font-weight: 600; }
.kv .total dd { font-weight: 600; }

/* A figure that leads a page: big, but never without its provenance (§7). */
.figure {
  font-size: 2rem;
  font-weight: 600;
  letter-spacing: -0.02em;
  font-variant-numeric: tabular-nums;
}
/* Two lead figures side by side — distance over ground and through water, each
   labelled so neither is read as the other (§6.8). */
.figures { display: flex; gap: 2rem; flex-wrap: wrap; margin-bottom: 0.25rem; }
.dfig { display: flex; flex-direction: column; gap: 0.1rem; }
.fig-label {
  color: var(--ink-soft);
  font-size: 0.8rem;
  letter-spacing: 0.06em;
}
.provenance {
  margin: 0.35rem 0 0;
  color: var(--ink-soft);
  font-size: 0.85rem;
  line-height: 1.45;
}

/* -- row state (§6.10) --------------------------------------------------- */

/* Soft-deleted rows are shown struck through, never omitted — the page cannot
   be less complete than the CSV. The badges keep their own decoration, or a
   struck-through 'DELETED' would be the least legible word on the card. */
.deleted { color: var(--gone); text-decoration: line-through; }
.deleted .badge, .deleted .muted { text-decoration: none; }

/* No table styles: nothing renders a table. §14.10.2's open question resolved
   to stacked cards, so a `.scroll` container and column rules would be dead
   weight inlined into every page. Reinstate them with a table, not before. */

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
a.row-link:hover .task-desc, a.row-link:hover .more { color: var(--accent); }
.more { color: var(--accent); white-space: nowrap; }

.muted { color: var(--ink-soft); font-size: 0.9rem; }
.empty { color: var(--ink-faint); font-style: italic; padding: 0.5rem 0; }

/* -- timeline ------------------------------------------------------------ */

.clock {
  font-variant-numeric: tabular-nums;
  font-weight: 600;
  font-size: 0.95rem;
}

.entry .kv { margin-top: 0.5rem; }

.pos {
  margin: 0.1rem 0 0;
  font-variant-numeric: tabular-nums;
  font-size: 0.95rem;
}

.remark { margin: 0.5rem 0 0; line-height: 1.45; }

.ticks { list-style: none; margin: 0.6rem 0 0; padding: 0; font-size: 0.9rem; }
.ticks li { padding: 0.15rem 0; }
.tick { color: var(--accent); font-weight: 700; }
.tick.untick { color: var(--flag); }

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
        facts.append(f"{format_nm(row['distance_og_nm'])} nm")
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
                 tz: tzinfo = timezone.utc, crew=None) -> str:
    """The dashboard — "what state is the boat in?"

    Vessel identity, cumulative engine hours WITH their provenance, how many
    items are open, then the sessions newest-first (§14.10.2). Home: every other
    page links back here, and nothing is more than one tap away.

    ``crew`` (the roster, each with its passages) adds a Crew section linking each
    member's own page (§4 handoff); ``None`` or empty omits it entirely, so a boat
    with no roster shows no section rather than an empty heading.
    """
    rec = engine.reconciliation(d)
    bar = vessel_bar(_vessel_reference(d))

    # §7: the figure NEVER appears without its provenance — a bare number invites
    # false confidence, on a phone as much as on the bar (§14.10.2). engine.html
    # has the full reconciliation; this is the caveat in one line.
    parts = [
        '<div class="card lead">',
        '<a class="row-link" href="engine.html">',
        f'<p class="figure">{rec.total_h:,.1f} h</p>',
        '<p class="provenance">Engine hours &mdash; baseline '
        f'{rec.baseline_h:,.1f} h ({_esc(engine_baseline_note(rec.note))})'
        f' + {rec.logged_h:,.1f} h logged since. '
        "<span class=\"more\">See every run &rarr;</span></p>",
        "</a></div>",
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

    if crew:
        parts.append(f"<h2>Crew &middot; {len(crew)}</h2>")
        parts.append('<ul class="stack">'
                     + "".join(_crew_index_card(c) for c in crew) + "</ul>")

    return page(_vessel_title(d), "".join(parts),
                subtitle=bar or None, home=False)


# -- session-NNN.html ----------------------------------------------------------

# The words an event row reads as. Mirrors render._EVENT_TEXT, but titled for a
# page rather than a dense log line; the tag itself carries the rest.
_EVENT_TEXT = {
    "session_open": "Log opened", "departure": "Departed", "arrival": "Arrived",
    "engine_on": "Engine started", "engine_off": "Engine stopped",
    "engine_duration": "Engine run logged", "engine_issue": "Engine issue",
    "autolog_on": "Auto-log started", "autolog_off": "Auto-log stopped",
    "checklist_complete": "Checklist completed",
    "task_raised": "Task added", "task_done": "Task completed",
    "issue_raised": "Issue raised", "issue_closed": "Issue closed",
}
_TAG = {
    "departure": ("Depart", ""), "arrival": ("Arrive", ""),
    "engine_on": ("Engine", ""), "engine_off": ("Engine", ""),
    "engine_duration": ("Engine", ""), "engine_issue": ("Engine", "flag"),
    "session_open": ("Log", "quiet"),
    "autolog_on": ("Auto", "quiet"), "autolog_off": ("Auto", "quiet"),
    "checklist_complete": ("Check", ""),
    "task_raised": ("Task", ""), "task_done": ("Task", ""),
    "issue_raised": ("Issue", "flag"), "issue_closed": ("Issue", "flag"),
}
_TAG_BY_CATEGORY = {
    "auto": ("Auto", "quiet"), "observation": ("Obs", ""), "sail": ("Sail", ""),
    "radio": ("Radio", ""), "crew": ("Crew", ""), "event": ("Event", ""),
}


def _entry_tag(row) -> str:
    text, kind = (_TAG.get(row["event_kind"])
                  or _TAG_BY_CATEGORY.get(row["category"], ("Entry", "")))
    return _badge(text, kind=kind)


def _position_html(row) -> str:
    """A position, with its provenance attached (§4.1, §8, §14.10.2).

    ``position_dm`` is the CSV's own reading column, already formatted by
    ``export._entry_row``; taking it rather than re-deriving it is what makes
    "parity by construction" literal rather than coincidental (§14.10.1).

    A TYPED position is badged. §14.10.2: *"do not render a back-dated typed
    position as though the boat was measured there"* — the CSV keeps the two
    apart in ``position_source`` precisely so this cannot be lost, and a page
    that dropped the distinction would claim the boat was measured somewhere it
    was only reckoned to be.
    """
    if not row["position_dm"]:
        return ""
    fix = _esc(row["position_dm"])
    if row["position_source"] == "manual":
        return f'{fix} {_badge("typed", kind="flag")}'
    return fix


def _entry_facts(row) -> list[tuple[str, str]]:
    """What this entry actually recorded, as (label, html) — only what IS there.

    Units live in the labels, per §8: *"a CSV whose columns require documentation
    is not archival"*, and a review page needing a key is worse.
    """
    facts: list[tuple[str, str]] = []
    if row["sog_kn"] is not None or row["cog_deg"] is not None:
        bits = []
        if row["sog_kn"] is not None:
            bits.append(f"{row['sog_kn']:.1f} kn")
        if row["cog_deg"] is not None:
            bits.append(f"{round(row['cog_deg'])}°")
        facts.append(("Course over ground", _esc(" · ".join(bits))))
    if row["heading_deg"] is not None:
        facts.append(("Heading", _esc(f"{round(row['heading_deg'])}°"
                                      f"{row['heading_ref'] or ''}")))
    if row["log_nm"] is not None:
        facts.append(("Log (nm)", _esc(f"{row['log_nm']:g}")))

    wind = wind_text(row)
    if wind:
        facts.append(("Wind", _esc(wind)))
    if row["sea_state"] is not None:
        facts.append(("Sea state", _esc(row["sea_state"])))
    if row["depth_m"] is not None:
        # "Sounded", never "Depth" (§16): the row holds what the instrument
        # displayed, uncorrected for datum. A seabed level needs the tide, the
        # datum and the draught, none of which are here — so the label names the
        # instrument, exactly as render.one_line's "sounded 5.4 m" does.
        facts.append(("Sounded (m)", _esc(f"{row['depth_m']:g}")))
    if row["cloud_oktas"] is not None:
        facts.append(("Cloud (oktas)", _esc(f"{row['cloud_oktas']}/8")))
    precip = precip_text(row["precip_type"], row["precip_intensity"])
    if precip:
        facts.append(("Precipitation", _esc(precip)))
    if row["visibility"]:
        facts.append(("Visibility", _esc(row["visibility"])))
    if row["pressure_mb"] is not None:
        facts.append(("Pressure (mb)", _esc(f"{row['pressure_mb']:g}")))

    # sail_plan is the CSV's legible column, with the display names already
    # resolved at export time — readable forever without config.json (§8).
    if row["sail_plan"]:
        facts.append(("Sails", _esc(row["sail_plan"])))
    if row["radio_channel"] or row["radio_station"]:
        facts.append(("Radio", _esc(" · ".join(
            x for x in (row["radio_channel"], row["radio_station"]) if x))))
    if row["location_name"]:
        facts.append(("Place", _esc(row["location_name"])))
    return facts


def _entry_card(row, *, tz: tzinfo, grouped: bool) -> str:
    """One entry as a stacked card."""
    marks = [_entry_tag(row)]
    if row["deleted"]:
        marks.append(_badge("deleted", kind="flag"))
    elif row["edited"]:
        marks.append(_badge("edited", kind="quiet"))
    if grouped:
        marks.append(_badge("same action", kind="quiet"))

    facts = _entry_facts(row)
    event = _EVENT_TEXT.get(row["event_kind"], "")
    lines = []
    if event:
        lines.append(f'<p class="task-desc">{_esc(event)}</p>')
    pos = _position_html(row)
    if pos:
        lines.append(f'<p class="pos">{pos}</p>')
    if facts:
        lines.append('<dl class="kv">' + "".join(
            f"<div><dt>{_esc(label)}</dt><dd>{value}</dd></div>"
            for label, value in facts) + "</dl>")
    if row["remarks"]:
        lines.append(f'<p class="remark">{_esc(row["remarks"])}</p>')
    if row["deleted"] and row["deleted_reason"]:
        lines.append(f'<p class="muted">deleted: {_esc(row["deleted_reason"])}</p>')

    cls = "card entry deleted" if row["deleted"] else "card entry"
    return (f'<li class="{cls}">'
            f'<div class="task-head"><span class="clock">'
            f'{_esc(_when(row["timestamp_utc"], tz=tz, fmt="%H:%M"))}</span>'
            f'{"".join(marks)}</div>'
            + "".join(lines) + "</li>")


def _timeline(rows, *, tz: tzinfo) -> str:
    """The entries as stacked cards, in ``id`` order — written order (§14.10.2).

    **Cards, not a table** — §14.10.2 left this open, to be decided "against a
    real session's data rather than in the abstract". Measured against a real
    21-entry passage at 375 px, the table's natural width was 950 px: Time, Tag
    and Position were fully visible, COG/SOG 5%, and **Wind, Sails and Remarks
    0%** — 64% of it behind a horizontal scroll. Its supposed advantage, keeping
    the tabular reading, does not survive the device: columns you cannot see
    cannot be compared, and the remark — the most valuable thing in the log —
    was the column furthest off-screen. Cards cost length (5,300 px vs 2,950 px
    for that session), which is the cheap axis on a phone: vertical scroll is
    the one-handed gesture.
    """
    if not rows:
        return _empty("No entries logged for this session.")
    # A group_id shared by ONE row is not a group — only mark a real one (§6.7).
    seen: dict = {}
    for r in rows:
        if r["group_id"]:
            seen[r["group_id"]] = seen.get(r["group_id"], 0) + 1
    return '<ul class="stack">' + "".join(
        _entry_card(r, tz=tz,
                    grouped=bool(r["group_id"]) and seen[r["group_id"]] > 1)
        for r in rows) + "</ul>"


def _engine_card(row, *, tz: tzinfo, show_session: bool = False) -> str:
    """One engine run.

    ``When`` is '—' for a ``manual_duration`` run, which genuinely has no times:
    a duration typed in afterwards records how long, never when. ``Method`` is
    always shown — a timed run and one typed from memory are worth different
    amounts of trust, and §7 is precisely about not hiding that.

    ``show_session`` on engine.html, where runs from every session (and the
    mooring runs belonging to none) share one list; redundant on a session page.
    """
    marks = [_badge("Engine")]
    if row["deleted"]:
        marks.append(_badge("deleted", kind="flag"))
    elif row["open"]:
        marks.append(_badge("running", kind="flag"))

    facts = [("When", _esc(engine_run_when(row, tz=tz))),
             # An open run reads 'running', never an elapsed time: its
             # duration_min is still NULL and it is NOT in the cumulative
             # figure. A number here would disagree with the total above it.
             ("Duration", _esc("running" if row["open"]
                               else format_hm(row["duration_min"] or 0))),
             ("Method", _esc(engine_method_text(row["method"])))]
    if show_session:
        facts.append(("Session", _esc(f"{int(row['session_id']):03d}")
                      if row["session_id"] else "no session"))
    if row["notes"]:
        facts.append(("Notes", _esc(row["notes"])))
    if row["deleted"] and row["deleted_reason"]:
        facts.append(("Withdrawn", _esc(row["deleted_reason"])))

    cls = "card deleted" if row["deleted"] else "card"
    return (f'<li class="{cls}"><div class="task-head">{"".join(marks)}</div>'
            + '<dl class="kv">' + "".join(
                f"<div><dt>{_esc(k)}</dt><dd>{v}</dd></div>" for k, v in facts)
            + "</dl></li>")


def _engine_rows_html(runs, *, tz: tzinfo) -> str:
    if not runs:
        return _empty("No engine runs logged for this session.")
    return '<ul class="stack">' + "".join(
        _engine_card(r, tz=tz) for r in runs) + "</ul>"


def _checklist_html(runs, *, tz: tzinfo) -> str:
    """Checklist runs, from each run's OWN snapshot — so it reads the same
    forever without config.json (§8, §14.2's snapshot principle)."""
    if not runs:
        return ""
    out = []
    for r in runs:
        try:
            items = json.loads(r["items_json"] or "[]")
        except (TypeError, ValueError):
            items = []
        ticks = []
        for it in items:
            title, descriptor = split_label(it.get("label", ""))
            mark = "&#10003;" if it.get("checked") else "&times;"
            cls = "tick" if it.get("checked") else "tick untick"
            note = (f' <span class="muted">{_esc(it["note"])}</span>'
                    if it.get("note") else "")
            ticks.append(f'<li><span class="{cls}">{mark}</span> '
                         f'{_esc(title)}{note}</li>')
        marks = _badge("deleted", kind="flag") if r["deleted"] else ""
        cls = ' class="card deleted"' if r["deleted"] else ' class="card"'
        out.append(
            f"<li{cls}><div class=\"task-head\">{_badge('Check')}{marks}"
            f'<span class="muted">'
            f'{_esc(_when(r["completed_utc"], tz=tz, fmt="%H:%M"))}</span></div>'
            # 'result' is the CSV's own legible column, composed by
            # _checklist_row from the run's snapshot — taken, not recomputed.
            f'<p class="task-desc">{_esc(r["result"])}</p>'
            f'<ul class="ticks">{"".join(ticks)}</ul>'
            + (f'<p class="remark">{_esc(r["remarks"])}</p>' if r["remarks"] else "")
            + "</li>")
    return "<h2>Checklists</h2><ul class=\"stack\">" + "".join(out) + "</ul>"


def render_session(summary, entries, engine_runs, checklist_runs, *,
                   tz: tzinfo = timezone.utc) -> str:
    """The logbook page — "what happened on that passage?"

    The summary FIRST — departure and arrival, distance, time under way vs
    stationary (§5.6) — then the timeline beneath it. Not a CSV dump with a
    header (§14.10.2).

    Every argument is ``export_session``'s own row dicts, so the page cannot
    disagree with the archive (§14.10.1).
    """
    number = int(summary["id"])
    passage = " to ".join(x for x in (summary["departed_from"],
                                      summary["bound_for"]) if x)

    lead = ["<div class=\"card lead\">"]
    # DOG (GPS-accumulated) and DTW (impeller end − start) side by side, each
    # labelled. Both are kept; their difference is the tidal set (§6.8).
    figures = []
    if summary["distance_og_nm"] is not None:
        figures.append(("DOG", summary["distance_og_nm"]))
    dtw = distance_through_water(summary)
    if dtw is not None:
        figures.append(("DTW", dtw))
    if figures:
        cells = "".join(
            f'<div class="dfig"><span class="figure">{format_nm(value)} nm</span>'
            f'<span class="fig-label">{label}</span></div>' for label, value in figures)
        lead.append(f'<div class="figures">{cells}</div>')
    lead.append('<dl class="kv">')
    facts = [
        ("Departed", _esc(summary["departed_from"]) or "&mdash;"),
        ("Bound for", _esc(summary["bound_for"]) or "&mdash;"),
        ("Under way", _esc(format_hm(summary["time_under_way_min"] or 0))),
        ("Stationary", _esc(format_hm(summary["time_stationary_min"] or 0))),
    ]
    if summary["log_start_nm"] is not None and summary["log_end_nm"] is not None:
        facts.append(("Log (nm)", _esc(f'{summary["log_start_nm"]:g} '
                                       f'→ {summary["log_end_nm"]:g}')))
    if summary["skipper"]:
        facts.append(("Skipper", _esc(summary["skipper"])))
    if summary["crew"]:
        facts.append(("Crew", _esc(summary["crew"])))
    if not summary["closed"]:
        facts.append(("Status", _badge("still open", kind="flag")))
    lead.extend(f"<div><dt>{_esc(k)}</dt><dd>{v}</dd></div>" for k, v in facts)
    lead.append("</dl></div>")

    parts = ["".join(lead), "<h2>Timeline</h2>",
             _timeline(entries, tz=tz),
             "<h2>Engine</h2>", _engine_rows_html(engine_runs, tz=tz),
             _checklist_html(checklist_runs, tz=tz)]

    when = _when(summary["opened_utc"], tz=tz, fmt="%d %B %Y")
    return page(f"Session {number:03d}", "".join(parts),
                subtitle=" · ".join(x for x in (passage, when) if x) or None)


# -- engine.html ---------------------------------------------------------------

def _reconciliation_html(rec, *, counted: int, running: bool) -> str:
    """The §7 figure, itemised — baseline with its provenance, runs logged since,
    then the sum. Mirrors ``engine_log.EngineHoursView``'s header (§14.11).

    **Never a bare total.** §7: *"47.3 hours that are all true is a better figure
    than 1,847 of which 1,800 are a guess, because in the latter the error is
    invisible."* A total alone re-hides exactly what §7 wants visible — on a
    phone as much as at the chart table (§14.10.2).
    """
    rows = [
        ("Baseline", engine_baseline_note(rec.note), f"{rec.baseline_h:,.1f} h"),
        ("Logged since", f"{counted} run{'' if counted == 1 else 's'}",
         f"{rec.logged_h:,.1f} h"),
    ]
    out = ['<div class="card lead">',
           f'<p class="figure">{rec.total_h:,.1f} h</p>',
           # Says what the figure IS made of, and does not promise the rounded
           # decimals below sum to the rounded total — each is rounded to 1dp
           # independently, exactly as the engine-hours view does it (§14.11).
           '<p class="provenance">Cumulative engine hours: no hour meter is '
           'fitted, so this is the baseline plus every run logged since.</p>',
           '<dl class="kv">']
    for label, note, value in rows:
        out.append(f"<div><dt>{_esc(label)}"
                   f'<span class="sub">{_esc(note)}</span></dt>'
                   f"<dd>{_esc(value)}</dd></div>")
    out.append(f'<div class="total"><dt>Total</dt>'
               f'<dd>{_esc(f"{rec.total_h:,.1f} h")}</dd></div>')
    out.append("</dl>")
    if running:
        # engine_log says this for the same reason: the figure looks stale to
        # someone watching the engine run unless it says why.
        out.append('<p class="provenance">'
                   + _badge("running", kind="flag")
                   + " A run is in progress — not counted until it is stopped."
                     "</p>")
    out.append("</div>")
    return "".join(out)


def render_engine(d, rows, *, tz: tzinfo = timezone.utc) -> str:
    """Cumulative hours — "how many hours, and how honest is that number?"

    ``rows`` are ``export_engine_cumulative``'s own row dicts, every run across
    every session, so the page and engine-cumulative.csv cannot disagree. They
    arrive in ``id`` order and are shown NEWEST FIRST, mirroring the tool
    (§14.10.2) — by ``id``, not by time, because a ``manual_duration`` run has no
    start at all and time is therefore not a total order over these rows.

    Deleted runs are shown, struck through and flagged: they are out of the
    figure but not out of the record (§8). They are excluded from ``counted`` for
    the same reason they are excluded from the sum.
    """
    rec = engine.reconciliation(d)
    live = [r for r in rows if not r["deleted"]]
    counted = sum(1 for r in live if not r["open"])
    running = any(r["open"] for r in live)

    parts = [_reconciliation_html(rec, counted=counted, running=running),
             f"<h2>Runs &middot; {len(rows)}</h2>"]
    if rows:
        parts.append('<ul class="stack">' + "".join(
            _engine_card(r, tz=tz, show_session=True)
            for r in reversed(rows)) + "</ul>")
    else:
        parts.append(_empty("No engine runs logged."))
    return page("Engine hours", "".join(parts),
                subtitle=_vessel_title(d) or None)


# -- crew-NNN.html -------------------------------------------------------------

def crew_page_name(member_id) -> str:
    """The stable filename for a crew member's page. The single source for the
    name, used both to write the page and to link it from the index, so the two
    cannot drift (§14.10.1)."""
    return f"crew-{int(member_id):03d}.html"


def _crew_totals(passages) -> dict:
    """A crew member's mileage over their passages — total DOG and DTW, the count,
    and how many they skippered (§4 handoff, Q3).

    DTW goes through ``render.distance_through_water``, the one renderer the
    session page and CSV also use, so the total cannot diverge from its parts. A
    total is ``None`` — shown as '—', never 0 — when NO passage recorded that
    figure: zero miles and no reading are different facts (§10.1)."""
    dog_vals = [p["distance_og_nm"] for p in passages
                if p["distance_og_nm"] is not None]
    dtw_vals = [dtw for p in passages
                if (dtw := distance_through_water(p)) is not None]
    return {
        "passages": len(passages),
        "skippered": sum(1 for p in passages if p["is_skipper"]),
        "dog": sum(dog_vals) if dog_vals else None,
        "dtw": sum(dtw_vals) if dtw_vals else None,
    }


def _crew_index_card(c) -> str:
    """One roster member on the dashboard — name, passage count and totals, linking
    to their own page. The whole card is the tap target, one-handed (§14.10.2)."""
    totals = _crew_totals(c["passages"])
    n = totals["passages"]
    facts = [f"{n} passage" if n == 1 else f"{n} passages"]
    if totals["dog"] is not None:
        facts.append(f"{format_nm(totals['dog'])} nm DOG")
    if totals["dtw"] is not None:
        facts.append(f"{format_nm(totals['dtw'])} nm DTW")
    trail = " &middot; ".join(_esc(f) for f in facts)

    marks = [_badge("crew", kind="quiet")]
    if not c["active"]:
        marks.append(_badge("retired", kind="quiet"))
    return (
        f'<li class="card"><a class="row-link" href="{_esc(crew_page_name(c["id"]))}">'
        f'<div class="task-head">{"".join(marks)}</div>'
        f'<p class="task-desc">{_esc(c["name"])}</p>'
        + (f'<p class="muted">{trail}</p>' if trail else "")
        + "</a></li>"
    )


def _crew_passage_card(row, *, tz: tzinfo) -> str:
    """One passage on a crew member's page — DOG and DTW, both labelled (§6.8),
    linking to the full session page. ``row`` is ``export._summary_row``'s own
    dict (+ is_skipper), so the figures match the session page's (§14.10.1)."""
    number = int(row["id"])
    passage = " to ".join(x for x in (row["departed_from"], row["bound_for"]) if x)

    facts = []
    if row["distance_og_nm"] is not None:
        facts.append(f"DOG {format_nm(row['distance_og_nm'])} nm")
    dtw = distance_through_water(row)
    if dtw is not None:
        facts.append(f"DTW {format_nm(dtw)} nm")
    trail = " &middot; ".join(_esc(f) for f in facts)

    marks = [_badge(f"Session {number:03d}", kind="quiet")]
    if row["is_skipper"]:
        marks.append(_badge("skipper"))
    return (
        f'<li class="card"><a class="row-link" href="session-{number:03d}.html">'
        f'<div class="task-head">{"".join(marks)}'
        f'<span class="muted">{_esc(_when(row["opened_utc"], tz=tz, fmt="%d %b %Y"))}</span>'
        "</div>"
        f'<p class="task-desc">{_esc(passage) or "&mdash;"}</p>'
        + (f'<p class="muted">{trail}</p>' if trail else "")
        + "</a></li>"
    )


def render_crew(member, *, tz: tzinfo = timezone.utc, vessel: str = "") -> str:
    """One crew member's page — "how far has this person sailed with the boat?"

    The totals lead (DOG and DTW, each labelled so neither is read as the other,
    §6.8), then every passage they were aboard, linking to the session page. Both
    figures reported, deliberately: their difference over time is the tidal set,
    and the skipper wants to see which reference tracks better (§4 handoff, Q3).

    ``member`` is ``{id, name, active, passages}``; each passage is an
    ``export._summary_row`` dict + ``is_skipper``, so this page cannot disagree
    with the session pages beside it (§14.10.1).
    """
    passages = member["passages"]
    totals = _crew_totals(passages)

    lead = ['<div class="card lead">']
    figures = []
    if totals["dog"] is not None:
        figures.append(("DOG total", totals["dog"]))
    if totals["dtw"] is not None:
        figures.append(("DTW total", totals["dtw"]))
    if figures:
        cells = "".join(
            f'<div class="dfig"><span class="figure">{format_nm(value)} nm</span>'
            f'<span class="fig-label">{label}</span></div>' for label, value in figures)
        lead.append(f'<div class="figures">{cells}</div>')
    lead.append('<dl class="kv">')
    facts = [("Passages", _esc(totals["passages"])),
             ("As skipper", _esc(totals["skippered"]))]
    if not member["active"]:
        facts.append(("Status", _badge("retired", kind="quiet")))
    lead.extend(f"<div><dt>{_esc(k)}</dt><dd>{v}</dd></div>" for k, v in facts)
    lead.append("</dl></div>")

    parts = ["".join(lead), "<h2>Passages</h2>"]
    if passages:
        parts.append('<ul class="stack">' + "".join(
            _crew_passage_card(p, tz=tz) for p in passages) + "</ul>")
    else:
        parts.append(_empty("No passages logged for this crew member yet."))

    return page(member["name"], "".join(parts), subtitle=vessel or None)
