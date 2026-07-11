#!/usr/bin/env python3
"""eroica — colorize and annotate LilyPond piano scores by pitch class and chord."""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

# 12 pitch classes, C=0 .. B=11 (semitones above C) — matches the
# `(modulo (ly:pitch-semitones pitch) 12)` lookup used in the generated Scheme.
PITCH_CLASSES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

# Accept flat spellings when editing config.json by hand; each maps to the
# same slot as its enharmonic sharp name.
FLAT_ALIASES = {"Db": "C#", "Eb": "D#", "Gb": "F#", "Ab": "G#", "Bb": "A#"}

# Display order/spellings for the color-legend markup: start at A, step
# through the wheel. Naturals are a single letter; black keys show both
# enharmonic spellings as (sharp_letter, flat_letter) — the accidental glyph
# itself is drawn by LilyPond's own music font (see legend-sharp-flat-markup),
# not typed as a plain Unicode character, so it's properly sized/shaped
# instead of reading like a hash mark.
LEGEND_ORDER = ["A", "A#", "B", "C", "C#", "D", "D#", "E", "F", "F#", "G", "G#"]
LEGEND_SPELLING = {
    "C": "C",
    "C#": ("C", "D"),
    "D": "D",
    "D#": ("D", "E"),
    "E": "E",
    "F": "F",
    "F#": ("F", "G"),
    "G": "G",
    "G#": ("G", "A"),
    "A": "A",
    "A#": ("A", "B"),
    "B": "B",
}

# Validated by hand against fur_elise_first_minute.ly: A=red, then stepping
# around the hue wheel (30 degrees/semitone) through ROYGBIV, with
# interstitial hues filling in the other 5 chromatic notes.
DEFAULT_COLORDICT = {
    "C": "#73cf17",
    "C#": "#17cf17",
    "D": "#17cf73",
    "D#": "#17cfcf",
    "E": "#1773cf",
    "F": "#1717cf",
    "F#": "#7317cf",
    "G": "#cf17cf",
    "G#": "#cf1773",
    "A": "#cf1717",
    "A#": "#cf7317",
    "B": "#cfcf17",
}

DEFAULT_CONFIG = {
    "colors": {
        "enabled": True,
        "colordict": dict(DEFAULT_COLORDICT),
    },
    "chordStagger": {"enabled": True, "step": 0.6},
    "chordQualityCircle": {"enabled": True},
    "chordNoteStack": {"enabled": True},
}

HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")

VOICES_REQUIRED_VARS = {
    "upMusic": re.compile(r"(?m)^\s*upMusic\s*="),
    "downMusic": re.compile(r"(?m)^\s*downMusic\s*="),
}


class ConfigError(Exception):
    """Raised for anything wrong with config.json — always a clear, specific message."""


# --------------------------------------------------------------------------
# Config loading
# --------------------------------------------------------------------------


def hex_to_rgb(hex_str, *, context=""):
    """'#rrggbb' -> (r, g, b) floats in 0..1. Raises ConfigError on anything else."""
    if not isinstance(hex_str, str) or not HEX_RE.match(hex_str):
        prefix = f"{context}: " if context else ""
        raise ConfigError(f"{prefix}expected a 6-digit hex color like '#ce1717', got {hex_str!r}")
    r = int(hex_str[1:3], 16) / 255.0
    g = int(hex_str[3:5], 16) / 255.0
    b = int(hex_str[5:7], 16) / 255.0
    return r, g, b


def _deep_merge(base, override):
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _normalize_colordict_keys(colordict):
    normalized = {}
    for key, value in colordict.items():
        canonical = FLAT_ALIASES.get(key, key)
        if canonical not in PITCH_CLASSES:
            raise ConfigError(
                f"colors.colordict: unknown note name {key!r} "
                f"(expected one of {', '.join(PITCH_CLASSES)}, or a flat alias like 'Bb')"
            )
        normalized[canonical] = value
    return normalized


def load_config(path):
    """Load config.json merged over DEFAULT_CONFIG. path=None -> pure defaults."""
    if path is None:
        user_config = {}
    else:
        p = Path(path)
        if not p.exists():
            raise ConfigError(f"config file not found: {p}")
        try:
            user_config = json.loads(p.read_text())
        except json.JSONDecodeError as e:
            raise ConfigError(f"{p}: invalid JSON ({e})") from e
        if not isinstance(user_config, dict):
            raise ConfigError(f"{p}: top-level JSON must be an object")

    if isinstance(user_config.get("colors"), dict) and isinstance(
        user_config["colors"].get("colordict"), dict
    ):
        user_config["colors"]["colordict"] = _normalize_colordict_keys(
            user_config["colors"]["colordict"]
        )

    config = _deep_merge(DEFAULT_CONFIG, user_config)

    if not isinstance(config["colors"]["enabled"], bool):
        raise ConfigError("colors.enabled must be true or false")
    for name in PITCH_CLASSES:
        hex_to_rgb(config["colors"]["colordict"].get(name), context=f"colors.colordict.{name}")

    for toggle in ("chordStagger", "chordQualityCircle", "chordNoteStack"):
        if not isinstance(config[toggle].get("enabled"), bool):
            raise ConfigError(f"{toggle}.enabled must be true or false")

    step = config["chordStagger"]["step"]
    if isinstance(step, bool) or not isinstance(step, (int, float)):
        raise ConfigError("chordStagger.step must be a number")

    return config


# --------------------------------------------------------------------------
# Scheme/LilyPond generation
# --------------------------------------------------------------------------
# Everything below is a direct, parameterized port of the functions written
# and visually validated by hand in fur_elise_first_minute.ly. Python's job
# is only to fill in the color vector from config and decide which blocks to
# include per toggle — the chord-matching/coloring/stacking logic itself
# stays in Scheme, unchanged, because that's what LilyPond acts on.

_COLOR_FUNCTIONS_TEMPLATE = r"""
% Pitch-class -> color, indexed by semitones above C (0=C ... 11=B).
#(define note-pitch-colors
   (vector
__COLOR_VECTOR_LINES__
     ))

#(define (get-event-pitch event)
   (and event
        (let ((p (ly:event-property event 'pitch #f)))
          (if (ly:pitch? p)
              p
              (let ((elts (ly:event-property event 'elements #f)))
                (and (pair? elts)
                     (get-event-pitch (car elts))))))))

#(define (pitch-class-color grob)
   (let ((pitch (get-event-pitch (event-cause grob))))
     (if (ly:pitch? pitch)
         (vector-ref note-pitch-colors (modulo (ly:pitch-semitones pitch) 12))
         (rgb-color 0 0 0))))

% For the legend's black-key entries ("C#/Db" etc): same eroica-accidental-glyph
% as pitch-root-name, rather than a plain Unicode character at full text size.
#(define (legend-sharp-flat-markup sharp-letter flat-letter)
   (make-concat-markup
     (list sharp-letter (eroica-accidental-glyph 1/2)
           "/" flat-letter (eroica-accidental-glyph -1/2))))
"""

_STAGGER_FUNCTIONS_TEMPLATE = r"""
% --- Accordion-stagger chords whose notes sit close together ---
#(define chord-stagger-step __STAGGER_STEP__)

#(define (chord-note-heads grob)
   (let ((column (ly:grob-parent grob X)))
     (if (ly:grob? column)
         (sort (ly:grob-array->list (ly:grob-object column 'note-heads))
               (lambda (a b) (< (ly:grob-property a 'staff-position 0)
                                 (ly:grob-property b 'staff-position 0))))
         (list grob))))

#(define (note-head-index grob heads)
   (list-index (lambda (h) (eq? h grob)) heads))

#(define (chord-stagger-amount grob)
   (let ((heads (chord-note-heads grob)))
     (if (< (length heads) 2)
         0
         (* (note-head-index grob heads) chord-stagger-step))))
"""

_NOTE_NAME_LETTERS_TEMPLATE = r"""
% Capital note letters (matching the chord-quality labels' style, e.g. "Dm"
% rather than "d minor"), with a properly small, raised (superscript-style)
% accidental. LilyPond's own accidental->text-markup (make-smaller-markup
% under the hood) scales *relative to the ambient font-size at
% markup-interpretation time* — which turned out to differ noticeably
% between a single note-name moment and a multi-voice-merged one, so the
% "smaller" glyph came out looking the same size as the letter (or bigger)
% in exactly the cases this whole thing was meant to fix. Force an absolute
% font-size directly on just the glyph instead, so it's the same small size
% everywhere regardless of context, then raise it — without this it sits at
% the glyph's own baseline, which reads as subscript next to a capital
% letter rather than the superscript style real chord-symbol accidentals use.
#(define (eroica-accidental-glyph alt)
   (make-raise-markup 1.6 (make-fontsize-markup -6 (make-accidental-markup alt))))

#(define chord-root-letters (vector "C" "D" "E" "F" "G" "A" "B"))

#(define (pitch-root-name pitch)
   (let* ((nn (ly:pitch-notename pitch))
          (alt (ly:pitch-alteration pitch))
          (letter (vector-ref chord-root-letters nn)))
     (if (= alt 0)
         letter
         (make-concat-markup (list letter (eroica-accidental-glyph alt))))))
"""

_QUALITY_CIRCLE_TEMPLATE = r"""
% --- Guess a lead-sheet chord name for genuine chords, and circle it ---

% Recognized shapes, as sorted interval sets above a candidate root. Only
% clear major/minor (and simple 7th) shapes get a name; anything else (bare
% 5ths, 2nds, non-tertian clusters) is left unnamed on purpose rather than
% guessing a quality that isn't really there.
#(define chord-shapes
   (list (cons '(0 4 7 11) "maj7")
         (cons '(0 4 7 10) "7")
         (cons '(0 3 7 10) "m7")
         (cons '(0 3 6 9)  "dim7")
         (cons '(0 3 6 10) "m7b5")
         (cons '(0 4 7)    "")
         (cons '(0 3 7)    "m")
         (cons '(0 3 6)    "dim")
         (cons '(0 4 8)    "aug")
         (cons '(0 5 7)    "sus4")
         (cons '(0 2 7)    "sus2")
         (cons '(0 4)      "")
         (cons '(0 3)      "m")))

#(define (interval-set-from pitches root-pc)
   (sort (delete-duplicates
          (map (lambda (p) (modulo (- (ly:pitch-semitones p) root-pc) 12)) pitches))
         <))

#(define (try-root pitches root-pitch)
   (let* ((root-pc (modulo (ly:pitch-semitones root-pitch) 12))
          (iset (interval-set-from pitches root-pc))
          (hit (assoc iset chord-shapes)))
     (if hit
         (make-concat-markup (list (pitch-root-name root-pitch) (cdr hit)))
         #f)))

#(define (guess-chord-name pitches)
   (if (< (length pitches) 2)
       #f
       (any (lambda (p) (try-root pitches p)) pitches)))

#(define (add-chord-name-if-any m)
   (if (music-is-of-type? m 'event-chord)
       (let* ((notes (filter (lambda (e) (music-is-of-type? e 'note-event))
                              (ly:music-property m 'elements)))
              (pitches (map (lambda (e) (ly:music-property e 'pitch)) notes)))
         (if (>= (length pitches) 2)
             (let ((name (guess-chord-name pitches)))
               (if name
                   (let* ((existing (ly:music-property m 'articulations '()))
                          (txt (make-music 'TextScriptEvent
                                           'direction UP
                                           'text (make-circle-markup (make-bold-markup name)))))
                     (ly:music-set-property! m 'articulations (cons txt existing))
                     m)
                   m))
             m))
       m))

chordNames = #(define-music-function (music) (ly:music?)
   (music-map add-chord-name-if-any music))
"""

_NOOP_QUALITY_CIRCLE = r"""
chordNames = #(define-music-function (music) (ly:music?) music)
"""

_NOTE_STACK_TEMPLATE = r"""
% --- Replace the note-name row's slash-joined chord text ("F/G/Bb") with a
% circled, top-down stack of the same letters (no slashes). Order is
% preserved exactly as written in the chord. Single notes are untouched.
% Reuses pitch-root-name directly — same capitalized letter + properly
% small accidental as everywhere else note names appear.
#(define (stacked-chord-markup pitches)
   (make-circle-markup
     (make-fontsize-markup -3
       (make-center-column-markup (map pitch-root-name pitches)))))

#(define (add-stacked-chord-label m)
   (if (music-is-of-type? m 'event-chord)
       (let ((notes (filter (lambda (e) (music-is-of-type? e 'note-event))
                             (ly:music-property m 'elements))))
         (if (>= (length notes) 2)
             (let* ((pitches (map (lambda (e) (ly:music-property e 'pitch)) notes))
                    (stacked (stacked-chord-markup pitches))
                    (first-note (car notes)))
               (ly:music-set-property! first-note 'tweaks
                 (acons (cons 'NoteName 'text) stacked
                        (ly:music-property first-note 'tweaks '())))
               m)
             m))
       m))

chordNoteNameStack = #(define-music-function (music) (ly:music?)
   (music-map add-stacked-chord-label music))
"""

_NOOP_NOTE_STACK = r"""
chordNoteNameStack = #(define-music-function (music) (ly:music?) music)
"""

_SCORE_BLOCK = r"""
\paper {
  top-margin = 8\mm
  bottom-margin = 9\mm
  ragged-last-bottom = ##f
}

\score {
  \new PianoStaff
  <<
    \new Staff = "up" { \colorNotes \chordNames \unfoldRepeats \upMusic }
    \new NoteNames \with { \accepts "Voice" } {
      \colorNoteNames \chordNoteNameStack \unfoldRepeats \upMusic
    }
    \new Staff = "down" { \colorNotes \chordNames \unfoldRepeats \downMusic }
    \new NoteNames \with { \accepts "Voice" } {
      \colorNoteNames \chordNoteNameStack \unfoldRepeats \downMusic
    }
  >>
  \layout { }
}
"""

_LEGEND_TEMPLATE = r"""
colorLegend = \markup \fill-line {
  \column {
    \line { \bold "Color key (chromatic, A = red -> around the wheel):" }
    \line {
__LEGEND_LINES__
    }
  }
}

\markup \vspace #1
\colorLegend
"""


def _scheme_rgb_vector_lines(colordict):
    lines = []
    for name in PITCH_CLASSES:
        r, g, b = hex_to_rgb(colordict[name], context=f"colors.colordict.{name}")
        lines.append(f"     (rgb-color {r:.4f} {g:.4f} {b:.4f})  ; {name}")
    return "\n".join(lines)


def _scheme_legend_lines(colordict):
    lines = []
    for name in LEGEND_ORDER:
        r, g, b = hex_to_rgb(colordict[name], context=f"colors.colordict.{name}")
        spelling = LEGEND_SPELLING[name]
        if isinstance(spelling, tuple):
            sharp_letter, flat_letter = spelling
            label_markup = f'#(legend-sharp-flat-markup "{sharp_letter}" "{flat_letter}")'
        else:
            label_markup = f'"{spelling}"'
        lines.append(f"      \\with-color #(rgb-color {r:.4f} {g:.4f} {b:.4f}) {label_markup}")
    return "\n".join(lines)


def build_preamble(config):
    colors_on = config["colors"]["enabled"]
    stagger_on = config["chordStagger"]["enabled"]
    quality_on = config["chordQualityCircle"]["enabled"]
    stack_on = config["chordNoteStack"]["enabled"]

    parts = [_NOTE_NAME_LETTERS_TEMPLATE]

    if colors_on:
        block = _COLOR_FUNCTIONS_TEMPLATE.replace(
            "__COLOR_VECTOR_LINES__", _scheme_rgb_vector_lines(config["colors"]["colordict"])
        )
        parts.append(block)

    if stagger_on:
        parts.append(
            _STAGGER_FUNCTIONS_TEMPLATE.replace(
                "__STAGGER_STEP__", repr(float(config["chordStagger"]["step"]))
            )
        )

    notehead_overrides = []
    # Voice contexts nested inside NoteNames (needed for polyphonic voices.ly
    # content, see the \accepts "Voice" below) bring their own normal
    # note-drawing engravers along with them, so real noteheads/stems/etc
    # would otherwise get drawn right alongside the note-name text. Hide
    # everything except the text and its accidental — this has no effect on
    # single-voice content (nothing here is drawn in that case anyway).
    notename_overrides = [
        r"  \override NoteHead.transparent = ##t",
        r"  \override Stem.transparent = ##t",
        r"  \override Flag.transparent = ##t",
        r"  \override Beam.transparent = ##t",
        r"  \override Slur.transparent = ##t",
        r"  \override Tie.transparent = ##t",
        r"  \override Rest.transparent = ##t",
        r"  \override MultiMeasureRest.transparent = ##t",
        r"  \override Dots.transparent = ##t",
        # Multi-voice moments (two voices merged into one note-name column,
        # e.g. a melody note + a chord in the other voice) otherwise inherit
        # a noticeably larger ambient font-size than single-voice moments do,
        # making their accidental glyphs look oversized/hash-like by
        # comparison even though it's the same accidental->text-markup glyph
        # either way. Pin an explicit size so it's consistent regardless.
        r"  \override NoteName.font-size = #-2",
        # Replace NoteNames' own (lowercase) default with pitch-root-name, so
        # plain note-name text is capitalized like the chord-quality labels
        # ("D" not "d"), and every note name — single or merged — goes
        # through the exact same markup-building code path.
        r"  \set NoteNames.noteNameFunction = #(lambda (pitch context) (pitch-root-name pitch))",
    ]
    if colors_on:
        notehead_overrides += [
            r"  \override NoteHead.color = #pitch-class-color",
            r"  \override Accidental.color = #pitch-class-color",
            r"  \override Stem.color = #pitch-class-color",
        ]
        notename_overrides.append(r"  \override NoteName.color = #pitch-class-color")
    if stagger_on:
        notehead_overrides.append(
            "  \\override NoteHead.X-offset = #(grob-transformer 'X-offset\n"
            "     (lambda (grob orig) (+ orig (chord-stagger-amount grob))))"
        )

    parts.append("colorNotes = {\n" + "\n".join(notehead_overrides) + "\n}\n")
    parts.append("colorNoteNames = {\n" + "\n".join(notename_overrides) + "\n}\n")

    parts.append(_QUALITY_CIRCLE_TEMPLATE if quality_on else _NOOP_QUALITY_CIRCLE)
    parts.append(_NOTE_STACK_TEMPLATE if stack_on else _NOOP_NOTE_STACK)

    return "\n".join(parts)


def _ly_escape(s):
    return s.replace("\\", "\\\\").replace('"', '\\"')


def build_header(title, composer):
    lines = ["\\header {"]
    if title:
        lines.append(f'  title = "{_ly_escape(title)}"')
    if composer:
        lines.append(f'  composer = "{_ly_escape(composer)}"')
    lines.append("  tagline = ##f")
    lines.append("}")
    return "\n".join(lines)


def build_color_legend(config):
    return _LEGEND_TEMPLATE.replace(
        "__LEGEND_LINES__", _scheme_legend_lines(config["colors"]["colordict"])
    )


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------


def render(
    input_path, config_path, output_path, *, title=None, composer=None, lilypond_bin="lilypond"
):
    voices_path = Path(input_path)
    if not voices_path.exists():
        raise SystemExit(f"error: input file not found: {voices_path}")
    voices_text = voices_path.read_text()

    missing = [name for name, pat in VOICES_REQUIRED_VARS.items() if not pat.search(voices_text)]
    if missing:
        raise SystemExit(
            f"error: {voices_path} is missing required variable(s): {', '.join(missing)}\n"
            f"       eroica expects exactly `upMusic = {{ ... }}` and `downMusic = {{ ... }}`."
        )

    if config_path is None and Path("config.json").exists():
        config_path = "config.json"
    config = load_config(config_path)

    out_path = Path(output_path).with_suffix(".pdf")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # NOT out_path.with_suffix(".ly") — that collides with (and would silently
    # overwrite) the input file whenever output shares the input's stem, which
    # is the common case (e.g. `eroica render voices.ly` with no -o defaults
    # to voices.pdf, and a naive same-stem .ly would be voices.ly again).
    ly_path = out_path.with_name(out_path.stem + ".eroica.ly")
    if ly_path.resolve() == voices_path.resolve():
        raise SystemExit(
            f"error: refusing to overwrite input file — the generated score would be "
            f"written to {ly_path}, which is the same as the input. Choose a different "
            f"-o/--output."
        )

    ly_text = "\n\n".join(
        part
        for part in [
            '\\version "2.26.0"',
            build_header(title, composer),
            build_preamble(config),
            voices_text,
            _SCORE_BLOCK,
            build_color_legend(config) if config["colors"]["enabled"] else "",
        ]
        if part
    )
    ly_path.write_text(ly_text)

    lilypond_out_stem = str(out_path.with_suffix(""))
    result = subprocess.run(
        [lilypond_bin, "-o", lilypond_out_stem, str(ly_path)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        sys.stderr.write(result.stdout)
        sys.stderr.write(result.stderr)
        raise SystemExit(f"error: lilypond failed (exit {result.returncode}) — see output above")

    if not out_path.exists():
        raise SystemExit(f"error: lilypond exited 0 but no PDF was produced at {out_path}")

    print(f"wrote {out_path}")
    print(f"wrote {ly_path}")
    return out_path


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

_STARTER_VOICES = r"""% Replace this with your own piece. eroica looks for exactly two
% variables: upMusic (treble) and downMusic (bass).

upMusic = {
  \clef treble
  \key c \major
  \time 4/4
  c'4 d' e' f'
  <e' g' c''>4 c'4 c'2
}

downMusic = {
  \clef bass
  \key c \major
  \time 4/4
  c4 g, c g,
  <c e g>4 c,4 c2
}
"""


def cmd_render(args):
    render(
        args.input,
        args.config,
        args.output or str(Path(args.input).with_suffix(".pdf")),
        title=args.title,
        composer=args.composer,
        lilypond_bin=args.lilypond,
    )


def cmd_init(args):
    target = Path(args.directory)
    target.mkdir(parents=True, exist_ok=True)
    voices_path = target / "voices.ly"
    config_path = target / "config.json"

    for p in (voices_path, config_path):
        if p.exists() and not args.force:
            raise SystemExit(f"error: {p} already exists (use --force to overwrite)")

    voices_path.write_text(_STARTER_VOICES)
    config_path.write_text(json.dumps(DEFAULT_CONFIG, indent=2) + "\n")
    print(f"wrote {voices_path}")
    print(f"wrote {config_path}")


def main():
    parser = argparse.ArgumentParser(prog="eroica", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_render = sub.add_parser("render", help="render a voices.ly file to an annotated PDF")
    p_render.add_argument("input", help="path to a voices .ly file (defines upMusic/downMusic)")
    p_render.add_argument(
        "-o", "--output", default=None, help="output PDF path (default: <input>.pdf)"
    )
    p_render.add_argument(
        "-c",
        "--config",
        default=None,
        help="path to config.json (default: ./config.json if present, else built-in defaults)",
    )
    p_render.add_argument("--title", default=None, help="score title")
    p_render.add_argument("--composer", default=None, help="score composer credit")
    p_render.add_argument("--lilypond", default="lilypond", help="lilypond binary to invoke")
    p_render.set_defaults(func=cmd_render)

    p_init = sub.add_parser("init", help="scaffold a starter voices.ly + config.json")
    p_init.add_argument("directory", nargs="?", default=".", help="target directory (default: .)")
    p_init.add_argument("--force", action="store_true", help="overwrite existing files")
    p_init.set_defaults(func=cmd_init)

    args = parser.parse_args()
    try:
        args.func(args)
    except ConfigError as e:
        raise SystemExit(f"error: {e}") from e


if __name__ == "__main__":
    main()
