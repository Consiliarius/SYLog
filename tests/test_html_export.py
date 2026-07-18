"""Tests for the HTML review export (logbook/html_export.py) — the third tier.

The load-bearing rules, in the order §14.10.1 ranks them:

  - **Escaping.** Named there as "the single likeliest bug in the whole job":
    remarks, item labels, notes, place names and the vessel name are all free
    text, and one '<' breaks the page. Every free-text field is fed markup here.
  - **Self-containment.** No JS, no CDN, no web fonts, no linked stylesheet, no
    href off-box. The page opens offline, years later, from one file.
  - **Parity with the archive.** The page renders the row dicts the CSV writers
    use, so the two cannot disagree. Asserted both ways: the figures match, AND
    every populated entry column reaches the page — the second is what would
    have caught §16's soundings being silently dropped from the timeline.

Plus the rules §14.10.2 says the page must not lose, because dropping them would
make it say something the tool refuses to say: deleted rows shown struck through
(§6.10), an edited row marked (§6.10), a typed position distinguishable from an
observed fix (§4.1), and cumulative hours never without their provenance (§7).

Build order: §14.10.1 step 6. Fixtures generated here, never committed.
Run: ``python -m unittest discover -s tests -t .``
"""

import csv
import html as html_mod
import json
import re
import tempfile
import unittest
from datetime import timezone
from pathlib import Path

from logbook import db, export, html_export

UTC = timezone.utc
SAILS = [{"id": "main", "name": "Mainsail", "reefs": ["full", "1st reef"]},
         {"id": "genoa", "name": "Genoa", "reefs": ["full", "partly furled"]}]

# Fed into every free-text field. If any page renders this unescaped, a browser
# builds a <script> element out of the logbook — and §14.10.1 says this is the
# bug most likely to happen.
XSS = '<script>alert("x")</script> & 5 < 6 "quoted"'


def strip_tags(html: str) -> str:
    """Tags out, entities decoded — so a test compares against what a READER
    sees, not against the escaping (which its own tests cover)."""
    return html_mod.unescape(re.sub(r"<[^>]+>", " ", html))


class HtmlExportTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.out = self.dir / "out"
        self.d = db.open_db(self.dir / "logbook.db")
        self.addCleanup(self.d.close)
        self.d.set_meta("vessel_name", "Kingfisher")
        self.d.set_meta("engine_hours_baseline", "280")
        self.d.set_meta("engine_hours_baseline_note", "estimated")
        self.sid = self.d.create_session(
            opened_utc="2026-07-13T09:00:00Z", departed_from="Haslar",
            bound_for="Yarmouth", skipper="A. Skipper")

    # -- helpers --------------------------------------------------------------

    def _entry(self, **extra):
        base = dict(session_id=self.sid, timestamp_utc="2026-07-13T10:00:00Z",
                    time_source="gps", recorded_utc="2026-07-13T10:00:05Z",
                    entry_type="manual", category="observation",
                    position_source="gps")
        base.update(extra)
        return self.d.insert_entry(**base)

    def _engine_run(self, **extra):
        row = dict(session_id=self.sid, started_utc="2026-07-13T10:00:00Z",
                   stopped_utc="2026-07-13T10:45:00Z", duration_min=45.0,
                   method="paired", open=0, notes=None)
        row.update(extra)
        cur = self.d.conn.execute(
            "INSERT INTO engine_run(session_id, started_utc, stopped_utc, "
            "duration_min, method, open, notes) VALUES (?,?,?,?,?,?,?)",
            (row["session_id"], row["started_utc"], row["stopped_utc"],
             row["duration_min"], row["method"], row["open"], row["notes"]))
        self.d.conn.commit()
        return cur.lastrowid

    def _pages(self) -> dict[str, str]:
        """Every page, through the real export path, as {name: html}."""
        export.export_html(self.d, self.sid, self.out, sails=SAILS, tz=UTC)
        return {p.name: p.read_text(encoding="utf-8")
                for p in self.out.glob("*.html")}

    def _session_page(self) -> str:
        return self._pages()["session-001.html"]

    # -- escaping (§14.10.1: the likeliest bug in the job) --------------------

    def test_esc_escapes_markup_and_quotes(self):
        self.assertEqual(html_export._esc('<b>&"x"'), "&lt;b&gt;&amp;&quot;x&quot;")

    def test_esc_renders_none_blank_not_the_word_none(self):
        # An unset field is blank on the page, as it is blank in the CSV.
        self.assertEqual(html_export._esc(None), "")

    def test_esc_coerces_non_strings(self):
        # So a caller cannot sidestep the escape by passing a number.
        self.assertEqual(html_export._esc(3.5), "3.5")
        self.assertEqual(html_export._esc(0), "0")

    def test_free_text_never_reaches_any_page_as_markup(self):
        """Markup in EVERY free-text field the schema has, on every page."""
        self.d.set_meta("vessel_name", XSS)
        self.d.update_session(self.sid, departed_from=XSS, bound_for=XSS,
                              skipper=XSS, crew=XSS, notes=XSS)
        self._entry(remarks=XSS, location_name=XSS)
        self._engine_run(notes=XSS)
        run = self.d.insert_checklist_run(
            checklist_key="k", title=XSS, completed_utc="2026-07-13T10:05:00Z",
            items_json=json.dumps([{"label": XSS, "checked": False, "note": XSS}]),
            session_id=self.sid, remarks=XSS)
        self.assertTrue(run)
        ti = self.d.insert_task_issue(kind="issue", source="manual",
                                      description=XSS,
                                      raised_utc="2026-07-13T10:00:00Z",
                                      session_id=self.sid)
        self.d.mark_task_issue_done(ti, done_utc="2026-07-13T11:00:00Z",
                                    done_note=XSS)
        dead = self._entry(remarks="gone")
        self.d.soft_delete_entry(dead, XSS)

        for name, html in self._pages().items():
            with self.subTest(page=name):
                self.assertNotIn("<script", html.lower())
                self.assertNotIn("</script", html.lower())
                self.assertNotIn('alert("x")', html)
                self.assertIn("&lt;script&gt;", html)
                # The text is present and readable — escaped, not dropped.
                self.assertIn("alert(&quot;x&quot;)", html)

    def test_escaped_markup_does_not_close_an_attribute(self):
        """A '"' in free text must not break out of an attribute value."""
        self.d.update_session(self.sid, bound_for='" onload="evil()')
        self._entry(remarks='" onload="evil()')
        pages = self._pages()

        # Nowhere may it become syntax...
        for name, html in pages.items():
            with self.subTest(page=name):
                self.assertNotIn('onload="evil()"', html)
        # ...and where the text does appear, it appears inert and intact.
        self.assertIn("onload=&quot;evil()", pages["session-001.html"])

    # -- self-containment (§14.10.1) ------------------------------------------

    def test_pages_are_self_contained(self):
        self._entry(remarks="a remark")
        for name, html in self._pages().items():
            with self.subTest(page=name):
                self.assertNotIn("http://", html)
                self.assertNotIn("https://", html)
                self.assertNotIn("//fonts.", html)
                self.assertNotIn("<script", html.lower())
                self.assertNotIn("<link", html.lower())
                self.assertNotIn("src=", html.lower())
                self.assertNotIn("@import", html)
                # Exactly one inline stylesheet, and no external one.
                self.assertEqual(html.lower().count("<style>"), 1)

    def test_every_href_is_a_sibling_page(self):
        """Navigation depth <= 2, and nothing off-box (§14.10.2)."""
        pages = self._pages()
        for name, html in pages.items():
            for href in re.findall(r'href="([^"]*)"', html):
                with self.subTest(page=name, href=href):
                    self.assertIn(href, pages,
                                  f"{name} links to {href}, which is not a page "
                                  "written beside it")

    def test_index_reaches_every_page_and_others_link_home(self):
        pages = self._pages()
        index_hrefs = set(re.findall(r'href="([^"]*)"', pages["index.html"]))
        self.assertEqual(index_hrefs, set(pages) - {"index.html"})
        for name, html in pages.items():
            if name == "index.html":
                continue
            with self.subTest(page=name):
                self.assertIn('href="index.html"', html)

    # -- parity with the archive (§14.10.1) -----------------------------------

    def test_session_page_figures_match_the_csv(self):
        self._entry(remarks="under way")
        self.d.set_session_distance(self.sid, 18.4)
        self.d.close_session(self.sid, closed_utc="2026-07-13T14:00:00Z")

        export.export_session(self.d, self.sid, self.out, sails=SAILS, tz=UTC)
        page = self._session_page()
        with open(self.out / "session-001-summary.csv", encoding="utf-8",
                  newline="") as fh:
            summary = next(csv.DictReader(fh))

        self.assertIn(f'{float(summary["distance_og_nm"]):g} nm', page)
        for column in ("departed_from", "bound_for", "skipper"):
            with self.subTest(column=column):
                self.assertIn(summary[column], page)

    def test_session_page_shows_both_over_ground_and_through_water(self):
        """§6.8: DOG (GPS) and DTW (impeller end − start) both shown, each
        labelled so neither is read as the other. The impeller zeros each passage,
        so 0 → 17.6 is the normal case and DTW is the end reading itself."""
        self._entry(remarks="under way")
        self.d.set_session_distance(self.sid, 18.4)
        self.d.update_session(self.sid, log_start_nm=0.0)
        self.d.close_session(self.sid, closed_utc="2026-07-13T14:00:00Z",
                             log_end_nm=17.6)
        page = self._session_page()
        self.assertIn("18.4 nm", page)          # DOG, from GPS
        self.assertIn("DOG", page)
        self.assertIn("17.6 nm", page)          # DTW = 17.6 − 0
        self.assertIn("DTW", page)

    def test_through_water_absent_without_both_log_readings(self):
        """Only a start reading was taken — no DTW figure, DOG still shown. A
        half-recorded pair yields no through-water distance, never a bare number."""
        self.d.set_session_distance(self.sid, 18.4)
        self.d.update_session(self.sid, log_start_nm=0.0)
        self.d.close_session(self.sid, closed_utc="2026-07-13T14:00:00Z")
        page = self._session_page()
        self.assertIn("18.4 nm", page)          # DOG present
        self.assertNotIn("DTW", page)           # no through-water figure

    def test_distances_are_rounded_to_a_tenth_on_the_page(self):
        """DOG/DTW show to a tenth of a mile; the CSV keeps full precision (§8)."""
        self.d.set_session_distance(self.sid, 13.90203221356903)   # raw GPS total
        self.d.update_session(self.sid, log_start_nm=0.0)
        self.d.close_session(self.sid, closed_utc="2026-07-13T14:00:00Z",
                             log_end_nm=17.63)
        page = self._session_page()
        self.assertIn("13.9 nm", page)                 # DOG, rounded
        self.assertNotIn("13.902", page)               # not the raw figure
        self.assertIn("17.6 nm", page)                 # DTW, rounded

        export.export_session(self.d, self.sid, self.out, sails=SAILS, tz=UTC)
        with open(self.out / "session-001-summary.csv", encoding="utf-8",
                  newline="") as fh:
            summary = next(csv.DictReader(fh))
        self.assertEqual(summary["distance_og_nm"], "13.90203221356903")  # CSV: full

    def test_every_populated_entry_column_reaches_the_page(self):
        """The test §16's soundings needed and did not have.

        The CSV gains a column for free — the row dict drives its header. The
        page does NOT: ``_entry_facts`` names each fact explicitly, so a new
        column is silently missing from the timeline until someone adds it. This
        fails when that happens, naming the column.

        Asserts the label->value PAIR a reader sees, never the value alone: an
        earlier draft checked that "5.4" appeared for ``depth_m`` and passed with
        the sounding removed entirely, because the same row's ``sog_kn`` was
        also 5.4. Every value here is deliberately distinct for the same reason.
        """
        self._entry(latitude=50.79, longitude=-1.11, cog_deg=214.0, sog_kn=5.4,
                    heading_deg=210.0, heading_ref="M", log_nm=1204.2,
                    sail_state=json.dumps({"main": "1st reef"}),
                    wind_dir_deg=225.0, wind_force_bf=4, sea_state=3,
                    depth_m=7.2, cloud_oktas=5, precip_type="rain",
                    precip_intensity="moderate", visibility="good",
                    pressure_mb=1013.5, location_name="Ryde Middle",
                    radio_channel="Ch 11", radio_station="QHM",
                    remarks="everything at once")
        page = self._session_page()
        facts = {html_mod.unescape(k): html_mod.unescape(strip_tags(v)).strip()
                 for k, v in re.findall(r"<dt>([^<]*)</dt><dd>(.*?)</dd>", page)}

        # column -> (label, value) as rendered. Where a renderer transforms the
        # value (compass, sail names, precipitation), the TRANSFORMED form is
        # what a reader must see.
        expected = {
            "sog_kn + cog_deg": ("Course over ground", "5.4 kn · 214°"),
            "heading_deg + heading_ref": ("Heading", "210°M"),
            "log_nm": ("Log (nm)", "1204.2"),
            "wind_dir_deg + wind_force_bf": ("Wind", "SW F4"),
            "sea_state": ("Sea state", "3"),
            "depth_m": ("Sounded (m)", "7.2"),
            "cloud_oktas": ("Cloud (oktas)", "5/8"),
            "precip_type + precip_intensity": ("Precipitation", "moderate rain"),
            "visibility": ("Visibility", "good"),
            "pressure_mb": ("Pressure (mb)", "1013.5"),
            "sail_state": ("Sails", "Mainsail 1st reef"),
            "location_name": ("Place", "Ryde Middle"),
            "radio_channel + radio_station": ("Radio", "Ch 11 · QHM"),
        }
        for column, (label, value) in expected.items():
            with self.subTest(column=column):
                self.assertIn(label, facts,
                              f"{column} is in the CSV but has no fact on the "
                              "page — see _entry_facts in html_export.py")
                self.assertEqual(facts[label], value)

        # Not label/value pairs: the position line and the remark.
        text = strip_tags(page)
        self.assertIn("50°47.4'N", text)       # latitude/longitude
        self.assertIn("everything at once", text)   # remarks

    def test_units_are_in_the_labels(self):
        """§8: "a CSV whose columns require documentation is not archival" — a
        review page needing a key is worse (§14.10.2)."""
        self._entry(log_nm=1204.2, depth_m=7.2, pressure_mb=1013.5,
                    cloud_oktas=5)
        page = self._session_page()
        for label in ("Log (nm)", "Sounded (m)", "Pressure (mb)",
                      "Cloud (oktas)"):
            with self.subTest(label=label):
                self.assertIn(f"<dt>{label}</dt>", page)

    def test_a_sounding_is_never_labelled_a_depth(self):
        """§16: the row holds what the instrument displayed, uncorrected for
        datum. A seabed level needs the tide, the datum and the draught, none of
        which are here — so the label names the instrument, not the seabed."""
        self._entry(depth_m=7.2)
        page = self._session_page()
        self.assertIn("Sounded (m)", page)
        self.assertNotIn("<dt>Depth", page)

    def test_engine_page_total_matches_the_cumulative_csv(self):
        self._engine_run()
        export.export_session(self.d, self.sid, self.out, sails=SAILS, tz=UTC)
        page = self._pages()["engine.html"]
        with open(self.out / "engine-cumulative.csv", encoding="utf-8",
                  newline="") as fh:
            rows = list(csv.DictReader(fh))

        baseline = float(rows[0]["engine_hours_baseline"])
        logged = sum(float(r["duration_min"]) for r in rows
                     if not int(r["deleted"])) / 60.0
        self.assertIn(f"{baseline + logged:,.1f} h", page)

    # -- rules the page must not lose (§14.10.2) ------------------------------

    def test_deleted_entry_is_shown_struck_through_with_its_reason(self):
        """§6.10 / §8: the page cannot be less complete than the CSV."""
        dead = self._entry(remarks="a mistake")
        self.d.soft_delete_entry(dead, "logged twice")
        page = self._session_page()
        self.assertIn("a mistake", page)              # shown, not omitted
        self.assertIn("deleted", page.lower())
        self.assertIn("logged twice", page)           # with its reason
        self.assertRegex(page, r'class="[^"]*deleted[^"]*"')

    def test_edited_entry_is_marked(self):
        """§6.10: a correction is visible, never silent."""
        eid = self._entry(remarks="original", wind_force_bf=5)
        self.d.update_entry(eid, wind_force_bf=6)
        self.assertIn("edited", self._session_page().lower())

    def test_typed_position_is_distinguishable_from_an_observed_fix(self):
        """§4.1 / §8 / §14.10.2: never render a typed position as a measurement."""
        self._entry(latitude=50.70, longitude=-1.26, position_source="manual",
                    remarks="DR position")
        self.assertIn("typed", self._session_page().lower())

    def test_observed_fix_is_not_badged_as_typed(self):
        self._entry(latitude=50.70, longitude=-1.26, position_source="gps",
                    fix_mode=3, remarks="a real fix")
        self.assertNotIn("typed", self._session_page().lower())

    def test_cumulative_hours_never_appear_without_provenance(self):
        """§7: a bare number invites false confidence."""
        self._engine_run()
        pages = self._pages()
        for name in ("index.html", "engine.html"):
            with self.subTest(page=name):
                self.assertIn("280.8 h", pages[name])          # 280 + 0h45m
                self.assertIn("estimated", pages[name])
                self.assertIn("a guess, not a reading", pages[name])

    def test_a_running_run_is_not_counted_and_says_so(self):
        self._engine_run()
        self._engine_run(stopped_utc=None, duration_min=None, open=1)
        page = self._pages()["engine.html"]
        self.assertIn("280.8 h", page)                 # unchanged by the open run
        self.assertIn("running", page.lower())
        self.assertIn("not counted until it is stopped", page)

    def test_a_withdrawn_run_leaves_the_figure_but_stays_on_the_page(self):
        self._engine_run()
        bad = self._engine_run(duration_min=360.0, notes="typed 6h by mistake")
        self.d.soft_delete_engine_run(bad, "typed 360 instead of 36")
        page = self._pages()["engine.html"]
        self.assertIn("280.8 h", page)                 # out of the figure
        self.assertIn("typed 360 instead of 36", page)  # still in the record
        self.assertIn("1 run", page)                   # and not counted

    def test_engine_run_without_times_says_so_rather_than_inventing_them(self):
        """A manual_duration run records how long, never when (§4.1)."""
        self._engine_run(started_utc=None, stopped_utc=None, duration_min=25.0,
                         method="manual_duration")
        page = self._pages()["engine.html"]
        self.assertIn("entered, duration", page)
        self.assertIn("—", page)

    # -- tasks.html, the near-term wish (§14.10) ------------------------------

    def test_open_items_come_first_and_done_are_collapsed_but_present(self):
        self.d.insert_task_issue(kind="task", source="manual",
                                 description="an open task",
                                 raised_utc="2026-07-13T10:00:00Z")
        done = self.d.insert_task_issue(kind="issue", source="manual",
                                        description="a closed issue",
                                        raised_utc="2026-07-13T09:00:00Z")
        self.d.mark_task_issue_done(done, done_utc="2026-07-13T11:00:00Z")
        page = self._pages()["tasks.html"]

        self.assertLess(page.index("an open task"), page.index("a closed issue"))
        self.assertIn("a closed issue", page)          # present, not dropped
        self.assertIn("<details>", page)               # subordinate, no JS needed

    def test_issue_and_task_are_distinguishable(self):
        self.d.insert_task_issue(kind="issue", source="manual",
                                 description="an issue",
                                 raised_utc="2026-07-13T10:00:00Z")
        page = self._pages()["tasks.html"].lower()
        self.assertIn(">issue<", page)

    # -- generation (§14.10.1 step 5) -----------------------------------------

    def test_writes_the_four_pages_beside_the_csvs(self):
        self._entry()
        paths = export.export_html(self.d, self.sid, self.out, sails=SAILS, tz=UTC)
        self.assertEqual({p.name for p in paths},
                         {"index.html", "tasks.html", "engine.html",
                          "session-001.html"})

    def test_re_export_overwrites_and_leaves_no_temp_files(self):
        """§8: deterministic regeneration, into a directory rclone is watching."""
        self._entry()
        export.export_html(self.d, self.sid, self.out, sails=SAILS, tz=UTC)
        before = {p.name for p in self.out.iterdir()}
        export.export_html(self.d, self.sid, self.out, sails=SAILS, tz=UTC)
        self.assertEqual(before, {p.name for p in self.out.iterdir()})
        self.assertFalse([p for p in self.out.iterdir()
                          if p.suffix == ".tmp"])

    def test_a_session_with_no_entries_still_renders(self):
        page = self._session_page()
        self.assertIn("No entries logged", page)


class CrewHtmlTestCase(unittest.TestCase):
    """The per-crew page and its index links (§4 handoff, Q3).

    Same three rules as the rest of the export: escaping (a crew name is free
    text), self-containment and navigation depth (covered by the shared page
    tests, re-checked here now that crew pages exist), and parity — the per-crew
    figures are the session pages' DOG/DTW, summed through the one renderer.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.out = self.dir / "out"
        self.d = db.open_db(self.dir / "logbook.db")
        self.addCleanup(self.d.close)
        self.d.set_meta("vessel_name", "Kingfisher")

    def _passage(self, *, departed, bound, dog, log_start, log_end):
        sid = self.d.create_session(opened_utc="2026-07-13T09:00:00Z",
                                    departed_from=departed, bound_for=bound)
        # A departure and arrival five hours apart, so the passage has real time
        # under way (§5.6) for the per-crew hours total.
        ev = dict(session_id=sid, time_source="system", entry_type="event",
                  category="event", position_source="none")
        self.d.insert_entry(**ev, event_kind="departure",
                            timestamp_utc="2026-07-13T09:00:00Z",
                            recorded_utc="2026-07-13T09:00:00Z")
        self.d.insert_entry(**ev, event_kind="arrival",
                            timestamp_utc="2026-07-13T14:00:00Z",
                            recorded_utc="2026-07-13T14:00:00Z")
        self.d.set_session_distance(sid, dog)
        self.d.update_session(sid, log_start_nm=log_start)
        self.d.close_session(sid, closed_utc="2026-07-13T14:00:00Z", log_end_nm=log_end)
        return sid

    def _pages(self) -> dict[str, str]:
        latest = self.d.last_session()["id"]
        export.export_html(self.d, latest, self.out, tz=UTC)
        return {p.name: p.read_text(encoding="utf-8")
                for p in self.out.glob("*.html")}

    def test_per_crew_page_lists_passages_with_dog_dtw_and_totals(self):
        al = self.d.add_crew(name="Al")
        bo = self.d.add_crew(name="Bo")
        s1 = self._passage(departed="Haslar", bound="Yarmouth", dog=18.4,
                           log_start=0.0, log_end=17.6)
        self.d.set_session_crew(s1, [al, bo], skipper_id=al)
        s2 = self._passage(departed="Yarmouth", bound="Poole", dog=12.0,
                           log_start=0.0, log_end=11.2)
        self.d.set_session_crew(s2, [al], skipper_id=al)

        pages = self._pages()
        text = strip_tags(pages[html_export.crew_page_name(al)])
        self.assertIn("Al", text)
        self.assertIn("Haslar to Yarmouth", text)
        self.assertIn("Yarmouth to Poole", text)
        self.assertIn("30.4 nm", text)     # DOG total: 18.4 + 12.0
        self.assertIn("28.8 nm", text)     # DTW total: 17.6 + 11.2
        self.assertIn("Under way", text)   # the hours total row
        self.assertIn("10h 00m", text)     # 2 passages x 5h under way
        self.assertIn("skipper", pages[html_export.crew_page_name(al)].lower())

        # Bo was crew on the first passage only, and not skipper.
        bo_text = strip_tags(pages[html_export.crew_page_name(bo)])
        self.assertIn("Haslar to Yarmouth", bo_text)
        self.assertNotIn("Yarmouth to Poole", bo_text)

    def test_index_lists_and_links_every_crew_member_who_links_home(self):
        al = self.d.add_crew(name="Al")
        s1 = self._passage(departed="Haslar", bound="Yarmouth", dog=18.4,
                           log_start=0.0, log_end=17.6)
        self.d.set_session_crew(s1, [al], skipper_id=al)
        pages = self._pages()

        page_name = html_export.crew_page_name(al)
        self.assertIn(page_name, pages)                                  # written
        self.assertIn(f'href="{page_name}"', pages["index.html"])        # linked
        # The whole-export invariants, now with a crew page present.
        index_hrefs = set(re.findall(r'href="([^"]*)"', pages["index.html"]))
        self.assertEqual(index_hrefs, set(pages) - {"index.html"})
        self.assertIn('href="index.html"', pages[page_name])

    def test_crew_page_escapes_the_name(self):
        cid = self.d.add_crew(name=XSS)
        s1 = self._passage(departed="A", bound="B", dog=1.0,
                           log_start=0.0, log_end=1.0)
        self.d.set_session_crew(s1, [cid], skipper_id=cid)
        for name, html in self._pages().items():
            with self.subTest(page=name):
                self.assertNotIn("<script", html.lower())
                self.assertNotIn('alert("x")', html)

    def test_retired_member_still_gets_a_page_and_is_badged(self):
        cid = self.d.add_crew(name="Al")
        s1 = self._passage(departed="A", bound="B", dog=1.0,
                           log_start=0.0, log_end=1.0)
        self.d.set_session_crew(s1, [cid], skipper_id=cid)
        self.d.retire_crew(cid)                          # gone from the picker
        pages = self._pages()
        self.assertIn(html_export.crew_page_name(cid), pages)   # history still has a page
        self.assertIn("retired", pages["index.html"].lower())

    def test_no_crew_section_or_pages_without_a_roster(self):
        self._passage(departed="A", bound="B", dog=1.0, log_start=0.0, log_end=1.0)
        pages = self._pages()
        self.assertFalse([n for n in pages if n.startswith("crew-")])
        self.assertNotIn("<h2>Crew", pages["index.html"])


if __name__ == "__main__":
    unittest.main()
