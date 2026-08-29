# eroica — notes for Claude

Project-specific knowledge that isn't in README.md (user-facing docs) or
obvious from the code. Read this before adapting a new piece or touching
the excerpt/duration logic.

## Adapting a piece from Mutopia (or similar public-domain sources)

eroica only understands exactly two variables, `upMusic`/`downMusic` — a
Mutopia `.ly` file is never usable as-is. Adapting one means:

- Strip everything outside the actual note content: Mutopia files bundle a
  `\header`, `\paper`, sometimes a `\midi` block, page-break hints, etc.
  None of that survives into `voices.ly` — eroica supplies its own
  `\header`/`\paper`/`\score`.
- Drop sustain pedal markings (`\sustainOn`/`\sustainOff` and similar).
  eroica doesn't do anything meaningful with them and they add visual
  noise to an already-busy annotated score.
  Für Elise's `voices_full.ly` follows this.
- Drop cross-staff visual tricks — places where the original engraving
  temporarily notates a note on the "wrong" staff for voice-leading
  clarity (`\change Staff = ...` and similar). eroica's up/down split
  assumes each staff's notes really belong to that hand; cross-staff notes
  confuse both the coloring and the NoteNames row. Simplify to the staff
  the note actually belongs to. Gnossienne No. 3's adaptation
  (`Erik Satie/Gnossienne No 3/voices.ly`, not in this repo — see the
  Sheet Music working directory) is the worked example for this.
- DO preserve custom slur/tie shape overrides (explicit control-point
  tweaks) from the original engraving — those reflect real engraving
  decisions, not incidental Mutopia-file structure.
- Genuinely polyphonic content within one hand (two independent voices in
  one staff, e.g. Gnossienne No. 3's LH, or Gymnopédie No. 1's RH melody
  over its own accompaniment chords) needs explicit
  `\new Voice { \voiceOne ... }` / `\new Voice { \voiceTwo ... }` in the
  adapted body. Nothing extra is needed from the adapter, but two separate
  pieces of machinery have to be in place for it to render correctly, and
  it's worth knowing they are *different* mechanisms:
  - The **note-name row** works because `_SCORE_BLOCK` declares
    `\new NoteNames \with { \accepts "Voice" }`, plus the notehead-engraver
    transparency overrides in `build_preamble` (the
    `fix/notenames-voice-polyphony` change).
  - The **noteheads themselves** work because `colorNotes` qualifies its
    overrides to `Staff.` (`\override Staff.NoteHead.color`, not a bare
    `\override NoteHead.color`). An unqualified override binds to the
    *Voice* context — specifically the implicit Voice LilyPond opens for a
    staff's contents — and an explicit `\new Voice` is a **fresh** context
    that inherits none of it. Setting the property on `Staff` propagates it
    down to every Voice in the staff, implicit or explicit.

  Historical note, since it's an easy trap to fall back into: this file used
  to claim polyphony "works out of the box, validated against Gnossienne
  No. 3." That was only ever true of the note-name row. Notehead coloring was
  silently broken for *every* polyphonic piece — including the shipped
  Gnossienne No. 3 example, whose noteheads rendered plain black while the
  names beside them were correctly colored. Single-voice pieces (Für Elise)
  were unaffected, which is why it went unnoticed. Fixed in
  `fix/color-polyphonic-voices`.

## Page layout (landscape / large print, `page` config section)

Three separate LilyPond mechanisms, each with a trap:

- **Landscape** is emitted as the paper-size *name*
  (`#(set-default-paper-size "letterlandscape")`), never as the symbol form
  `#(set-default-paper-size "letter" 'landscape)`. The symbol form keeps a
  portrait MediaBox (612x792) and rotates the content inside it, so viewers
  show the music sideways on a portrait page; the name form emits a real
  landscape page (792x612) with upright content. Verified by reading the
  MediaBox out of both PDFs.
- **Scale** is `#(set-global-staff-size N)`, and it is the only knob that
  makes things bigger — noteheads, the NoteNames row, chord-quality circles,
  and the legend are all sized relative to it, so nothing needs to be scaled
  separately. Both settings are top-level and must precede the `\paper`
  block (see `build_page_setup`).
- **Fixed measures per line** needs *two* cooperating pieces, and is silently
  wrong with only one: the `break-every-n-bars` engraver forces a break at
  every Nth measure start, and `\override
  NonMusicalPaperColumn.line-break-permission = ##f` forbids breaks anywhere
  else. Without the override, LilyPond still breaks wherever it likes and "3
  per line" comes out as "at most 3" (Für Elise rendered a lone 1-measure
  row). The engraver counts *bar starts* it has seen rather than testing
  `currentBarNumber mod n`, because a piece can renumber mid-measure via
  `\set Timing.measurePosition` (Für Elise does, around its first
  alternative ending), and it deliberately never forces at the first bar
  start — otherwise a `\partial` pickup gets stranded alone on row one.

`systems-per-page` is a hard override, not a hint: LilyPond will put N
systems on a page whether or not they fit, and the overflow is silent — no
warning, exit 0, and the bottom NoteNames row simply gets cut off by the page
edge. Landscape makes this easy to hit, since the page is ~200pt shorter
while an annotated system (staff + note-name row per hand, plus chord circles
above and stacked-chord circles below) is exactly as tall as before. Clair de
Lune at staff size 26 needed 2 systems/page to look right and clipped 8 of 18
pages doing it; staff size 20 fits 2/page with ~15-30pt to spare. Check for
this by rasterizing pages and measuring where the ink stops (a bottom margin
of 0 means clipped) — eyeballing page 1 won't catch it, since the pages that
clip are the annotation-dense ones in the middle.

Page numbers (`page.pageNumbers`, on by default) depart from LilyPond twice,
both times because these print as loose single-sided sheets rather than a
bound book: `print-first-page-number` numbers page 1, and `evenHeaderMarkup`
is redefined as a copy of the default `oddHeaderMarkup` so the number stays in
the same corner instead of alternating for facing pages. That copy keeps
`\if \should-print-page-number`, so `print-page-number` still governs it —
drop that and `--no-page-numbers` would silently stop working on even pages.

Geometry defaults reproduce the old output exactly (portrait letter, staff
size 20, automatic breaks) — a no-flag render emits a byte-identical score
body. The `\paper` block is the one intended difference: it carries the
page-number settings.

## Excerpt/duration engine (eroica.py, "Auto-excerpt by duration" section)

- Everything is computed on **repeat-unfolded, text-sliced** music — the
  tokenizer never touches LilyPond's own timing engine. Positions/lengths
  are tracked in whole-note `Fraction`s and converted to seconds via a
  single required `\tempo` marking (`parse_seconds_per_whole_note`).
  Multiple/changing tempos are refused outright rather than guessed at.
- `--start-bar` requires actually counting measures (`find_bar_start_pos`),
  which means honoring `\partial` (pickup measures) and
  `\set Timing.measurePosition = #(ly:make-moment ...)` — the latter is
  LilyPond's own device for correcting bar-number bookkeeping around an
  odd `\alternative` ending (Für Elise's first one uses it). Since this
  tool keeps its own independent bar count instead of relying on
  LilyPond's engraver, it has to replay the same override or the count
  drifts from what LilyPond itself would print.
- A cut (either the start, for `--start-bar`, or the end, for `--seconds`)
  that would land inside an open `\tuplet`/`\grace` construct is pushed
  past the end of that construct (`_extend_past_open_spans`) rather than
  slicing mid-construct, which would leave a dangling unclosed brace.
- **Verification discipline**: before trusting any change here, check it
  against a real piece with a known-good answer, not just "it ran without
  erroring." Für Elise (`fur-elise/voices_full.ly` in the Sheet Music
  working directory, not this repo) is the standing oracle — a 60s cut
  from bar 1 should land at exactly 60.0s (hand-verified), and a
  `--start-bar 40` cut should land visibly in the piece's B section, not
  mid-phrase. Render the result and look at it; a clean `lilypond` exit
  with no warnings is necessary but not sufficient (a wrong cut point can
  still render warning-free).

## General

- Piece source files, adaptations, and rendered output for actual music
  (Für Elise, Erik Satie's Gymnopédies/Gnossiennes) live outside this repo,
  in the user's `~/Music/Sheet Music` working directory — `examples/` here
  only has the small Für Elise first-minute excerpt. Don't expect to find
  the full adaptations here; ask for the path if you need one.
