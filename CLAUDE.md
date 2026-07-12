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
  one staff, e.g. Gnossienne No. 3's LH) needs explicit
  `\new Voice { \voiceOne ... }` / `\new Voice { \voiceTwo ... }` in the
  adapted body. This works out of the box — `_SCORE_BLOCK`'s
  `\new NoteNames \with { \accepts "Voice" }` plus the notehead-engraver
  transparency overrides in `build_preamble` already handle it (this was
  the fix in `fix/notenames-voice-polyphony`, validated against
  Gnossienne No. 3). Nothing extra needed from the adapter; just know why
  it renders correctly instead of spawning a stray staff.

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
