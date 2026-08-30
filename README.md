# eroica

Colorize and annotate LilyPond piano scores by pitch class and chord: notes
colored around a chromatic wheel, close chord notes fanned out instead of
stacked, recognized chord qualities circled above the staff, and chord note
letters circled below it instead of slash-joined.

## Requirements

- Python 3.10+
- [uv](https://github.com/astral-sh/uv)
- [LilyPond](https://lilypond.org/) (`brew install lilypond`) — eroica shells
  out to the `lilypond` binary to produce the PDF.

## Install

```bash
uv tool install git+https://github.com/und1sk0/eroica
```

This puts an `eroica` command on your PATH (`~/.local/bin/` by default). If
`eroica: command not found` right after installing, your shell hasn't picked
up that directory yet — run `uv tool update-shell` and open a new terminal,
or add `~/.local/bin` to `PATH` yourself.

Check it worked:

```bash
eroica --help
```

**For local development**, clone the repo and install from the working copy
instead of the git URL:

```bash
git clone git@github.com:und1sk0/eroica.git
cd eroica
uv tool install --editable .
```

The `--editable` flag means changes to the source are reflected immediately
without reinstalling.

To upgrade an existing install to the latest commit/tag:

```bash
uv tool upgrade eroica
```

To uninstall:

```bash
uv tool uninstall eroica
```

## Usage

### 1. Start a new piece

```bash
eroica init my-piece
```

Writes `my-piece/voices.ly` (a starter treble/bass template) and
`my-piece/config.json` (the default colors and toggles, copy for editing).

### 2. Write the notes

Edit `voices.ly`. eroica doesn't parse or transcribe music — it looks for
exactly two variables and splices the rest in as plain LilyPond:

```
upMusic = {
  \clef treble
  ...
}

downMusic = {
  \clef bass
  ...
}
```

Anything valid inside those braces works — single notes, chords, repeats,
dynamics, `\clef` changes mid-voice, grace notes, etc. Worked examples:

- `examples/fur-elise/voices.ly` — the first-minute excerpt this project
  started with.
- `examples/fur-elise-full/voices.ly` — the complete piece: repeats,
  tuplets, an ottava run, grace notes/appoggiaturas.
- `examples/gnossienne-no-3/voices.ly` — genuine two-voice-per-hand
  polyphony (`\new Voice { \voiceOne ... }` / `\new Voice { \voiceTwo ... }`
  inside one staff). Both the `NoteNames` row and the noteheads have to be
  taught to reach into those explicitly-created voices — see `CLAUDE.md`.

### 3. Render

```bash
eroica render my-piece/voices.ly -o my-piece/score.pdf
```

If `-o` is omitted, output defaults to `<input>.pdf` next to the voices file.
If `-c/--config` is omitted, eroica looks for `config.json` next to where you
run the command, falling back to built-in defaults if there isn't one. The
combined `.ly` that was actually compiled is written alongside the PDF as
`<output-stem>.eroica.ly`, so you can inspect or hand-tweak it — named that
way (not just `.ly`) specifically so it never collides with your input file
when input and output share a stem (e.g. `voices.ly` -> `voices.pdf`).

```bash
eroica render examples/fur-elise/voices.ly -o /tmp/fur-elise.pdf --title "Für Elise" --composer "Ludwig van Beethoven"
```

#### Page layout

Out of the box a render is large print: a landscape letter page at staff size
24, five measures to a row and two rows to a page, with every page numbered.
Those are the defaults because the point of an eroica score is to be read off
a music stand — the annotations (note names under every note, chord circles
above and below) need room, and a portrait page at LilyPond's normal staff
size packs 5-6 dense measures into every row.

Every part of that is adjustable, per render or in config.json:

```bash
eroica render my-piece/voices.ly -o my-piece/score.pdf \
  --measures-per-line 3 --staff-size 20 --systems-per-page 0
```

- `--landscape` / `--portrait` — page orientation. Landscape is the default;
  `--portrait` gives LilyPond's usual page shape.
- `--paper-size` — LilyPond paper size name (`letter`, `a4`, ...).
- `--staff-size` — the one knob that actually changes how big things look;
  everything on the page is sized relative to it. Default 24; LilyPond's own
  default is 20, and 30+ is very large print.
- `--measures-per-line` — exactly N measures per row (default 5) instead of
  as many as fit. A `\partial` pickup rides along on the first row rather
  than being stranded on a row of its own.
- `--systems-per-page` — exactly N rows per page (default 2).
- `--page-numbers` / `--no-page-numbers` — page numbers are on by default,
  on every page including the first, always in the same top corner (LilyPond
  itself skips page 1 and alternates corners for facing pages, which is the
  convention for a bound book rather than a stack of loose practice sheets).

`0` means "let LilyPond decide" for the two count flags — it's how you switch
a config.json setting back off from the command line.

**The right numbers depend on the piece.** The defaults are tuned on music
written like Satie's Gymnopédie No. 1 — a handful of events per bar. A
densely written piece (Debussy's Clair de Lune, with a chord circle over most
beats in both hands) makes much taller rows, and two of them will not fit a
landscape page. `--systems-per-page` *forces* the count: when N rows don't
fit, LilyPond packs them in anyway and the bottom row's note names run off
the page edge, silently — no warning, and it renders successfully. If your
score looks cut off at the bottom, pass `--systems-per-page 0` to let
LilyPond fit what it can, or drop `--staff-size`.

### 4. Excerpt by duration (optional)

```bash
eroica excerpt my-piece/voices.ly --start-bar 40 --seconds 20 -o my-piece/excerpt.ly
```

Cuts `upMusic`/`downMusic` down to at least the given number of seconds,
starting at the given 1-based bar number (both default: bar 1, 60 seconds —
i.e. `eroica excerpt my-piece/voices.ly` alone gives you the first minute).
Repeats are unfolded in the process (a partial cut can't be represented with
repeat brackets anymore). Requires a single, constant `\tempo` marking (e.g.
`\tempo 4 = 72`) and, if `--start-bar` is more than 1, a `\time` signature —
pieces with tempo changes aren't supported yet and will error out rather
than guess. Render the result normally with `eroica render`.

## config.json

```json
{
  "colors": { "enabled": true, "colordict": { "C": "#4f8e10", "...": "..." } },
  "chordStagger": { "enabled": true, "step": 0.6 },
  "chordQualityCircle": { "enabled": true },
  "chordNoteStack": { "enabled": true },
  "page": {
    "orientation": "landscape",
    "paperSize": "letter",
    "staffSize": 24,
    "systemsPerPage": 2,
    "measuresPerLine": 5,
    "pageNumbers": true
  }
}
```

- **`colors.enabled`** — master on/off switch for all coloring (noteheads,
  stems, accidentals, and the note-name row). `false` gives a plain black
  score.
- **`colors.colordict`** — one hex color per pitch *class* (`C` through `B`,
  the 12 semitones — enharmonic spelling in the music doesn't matter, `C#`
  and `Db` are the same slot). Flat spellings (`Db`, `Eb`, `Gb`, `Ab`, `Bb`)
  are accepted as aliases when editing by hand. You only need to include the
  notes you want to override — anything you omit keeps the default color.
  For picking replacement colors by hex/RGB, the
  [Wikipedia ANSI escape code 8-bit color table](https://en.wikipedia.org/wiki/ANSI_escape_code#8-bit)
  is a handy reference (256-color swatches with their RGB and hex values
  side by side) — watch legibility on white paper the way B, C, and D's
  defaults had to be darkened from their raw wheel-step values.
- **`chordStagger`** — fans out chord notes diagonally (bottom to top)
  instead of stacking them straight up; `step` is the horizontal offset per
  note, in staff-spaces.
- **`chordQualityCircle`** — when a chord's pitches form a recognizable
  major/minor/dim/aug/sus/7th shape, circles the guessed name (e.g. Ⓒ, Ⓐm)
  above the staff. Ambiguous chords (bare 5ths, 2nds, non-tertian clusters)
  are deliberately left unlabeled rather than guessing.
- **`page`** — page geometry and visual scale, the config.json form of the
  render flags above. `orientation` is `"portrait"` or `"landscape"`;
  `paperSize` is any LilyPond paper size name; `staffSize` is the global
  staff size everything else scales with (LilyPond's default is 20);
  `systemsPerPage` and `measuresPerLine` pin the number of staff rows per
  page and measures per row (2 and 5), or are `null` to let LilyPond fit as
  many as it can; `pageNumbers` numbers every page (including the first) in a
  fixed corner, and is on by default. Defaults: landscape letter, staff size
  24 — see the layout notes above, including why a densely written piece
  wants different numbers.
- **`chordNoteStack`** — replaces the note-name row's slash-joined chord
  spelling ("f/g/bb") with a circled top-down stack of the same letters, for
  any 2+ note chord regardless of whether it has a guessable quality.

## Scope

eroica is an annotator, not a transcription tool — it expects the notes to
already be written down as LilyPond. It also doesn't fetch sheet music —
that's piece-specific enough that doing it by hand is more reliable than a
general solution right now.
