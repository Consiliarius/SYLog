"""One-line summary renderer for the rolling log and the viewer.

Renders from WHICH FIELDS ARE POPULATED, not from the category label. Produces
human strings AT DISPLAY TIME from structured storage — precipitation
("moderate rain") from type + intensity, cloud from oktas, sail plan from JSON +
config display names. Nothing is concatenated at storage (invariant 5).

Sail state is shown only where it was stated; the viewer may carry the last
known state forward at display time, marked as carried, never presented as
observed.

Build order: step 3 (with the UI).
Spec: §6.1, §6.9.
"""
