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

This puts an `eroica` command in your PATH (`~/.local/bin/`).

**For local development:**

```bash
uv tool install --editable .
```

The `--editable` flag means changes to the source are reflected immediately
without reinstalling.

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
dynamics, `\clef` changes mid-voice, grace notes, etc. See
`examples/fur-elise/voices.ly` for a full worked example.

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

## config.json

```json
{
  "colors": { "enabled": true, "colordict": { "C": "#73cf17", "...": "..." } },
  "chordStagger": { "enabled": true, "step": 0.6 },
  "chordQualityCircle": { "enabled": true },
  "chordNoteStack": { "enabled": true }
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
- **`chordStagger`** — fans out chord notes diagonally (bottom to top)
  instead of stacking them straight up; `step` is the horizontal offset per
  note, in staff-spaces.
- **`chordQualityCircle`** — when a chord's pitches form a recognizable
  major/minor/dim/aug/sus/7th shape, circles the guessed name (e.g. Ⓒ, Ⓐm)
  above the staff. Ambiguous chords (bare 5ths, 2nds, non-tertian clusters)
  are deliberately left unlabeled rather than guessing.
- **`chordNoteStack`** — replaces the note-name row's slash-joined chord
  spelling ("f/g/bb") with a circled top-down stack of the same letters, for
  any 2+ note chord regardless of whether it has a guessable quality.

## Scope

eroica is an annotator, not a transcription tool — it expects the notes to
already be written down as LilyPond. It also doesn't fetch sheet music or
figure out "the first N seconds" of a piece; both are piece-specific enough
that doing them by hand is more reliable than a general solution right now.
