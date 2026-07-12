#!/usr/bin/env python3
"""eroica — colorize and annotate LilyPond piano scores by pitch class and chord."""

import argparse
import json
import re
import subprocess
import sys
from fractions import Fraction
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
# interstitial hues filling in the other 5 chromatic notes. C, D, and B are
# darkened relative to the raw wheel step (same hue, scaled down) — at full
# brightness their high green/yellow content read as nearly invisible on
# white paper, B worst of all since it's pure bright yellow.
DEFAULT_COLORDICT = {
    "C": "#4f8e10",
    "C#": "#17cf17",
    "D": "#119a56",
    "D#": "#17cfcf",
    "E": "#1773cf",
    "F": "#1717cf",
    "F#": "#7317cf",
    "G": "#cf17cf",
    "G#": "#cf1773",
    "A": "#cf1717",
    "A#": "#cf7317",
    "B": "#80800e",
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
        # Staff-qualified, NOT bare `\override NoteHead.color`. An unqualified
        # override binds to the *Voice* context — specifically the implicit Voice
        # LilyPond opens for the staff's contents. That's fine for single-voice
        # music, but polyphonic voices.ly content (`\new Voice { \voiceOne ... }`,
        # e.g. Gymnopédie No. 1's melody-over-accompaniment right hand, or
        # Gnossienne No. 3's left hand) creates *fresh* Voice contexts that
        # inherit none of it — so every notehead in them rendered plain black
        # while the NoteNames row beside them was correctly colored. Setting the
        # property on Staff instead propagates it down to every Voice in the
        # staff, implicit or explicit.
        notehead_overrides += [
            r"  \override Staff.NoteHead.color = #pitch-class-color",
            r"  \override Staff.Accidental.color = #pitch-class-color",
            r"  \override Staff.Stem.color = #pitch-class-color",
        ]
        notename_overrides.append(r"  \override NoteName.color = #pitch-class-color")
    if stagger_on:
        # Same reasoning as above — must reach explicit Voice contexts too, or
        # chords in a polyphonic staff stack instead of fanning out.
        notehead_overrides.append(
            "  \\override Staff.NoteHead.X-offset = #(grob-transformer 'X-offset\n"
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
# Auto-excerpt by duration
# --------------------------------------------------------------------------
# Cuts upMusic/downMusic down to "at least N seconds" of music. Repeats are
# unfolded textually first (a partial cut can't be represented with repeat
# brackets any more than the hand-built fur_elise_first_minute.ly excerpt
# could), then both voices are walked as a flat stream of duration-bearing
# events (notes/chords/rests) and cut at the same elapsed musical time —
# not the same measure *index* — since that's the one thing guaranteed to
# line up between two independently-written voices.
#
# Scope, deliberately: a single constant \tempo and no per-voice tempo
# changes. Anything fancier (accelerandos, multiple tempo marks) errors out
# clearly rather than silently picking one, matching the "guess nothing
# that isn't really there" philosophy already used for chord-quality
# detection.


class ExcerptError(Exception):
    """Raised for anything that prevents computing/cutting an excerpt."""


def _find_matching_brace(text, open_pos):
    """text[open_pos] must be '{'. Returns the index of its matching '}'."""
    depth = 0
    i = open_pos
    n = len(text)
    while i < n:
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise ExcerptError(f"unbalanced braces starting at position {open_pos}")


def _find_variable_body_span(text, name):
    """Returns (body_start, body_end) — indices strictly inside the braces
    of `name = { ... }` — or raises ExcerptError if not found."""
    m = re.search(rf"(?m)^\s*{re.escape(name)}\s*=\s*\{{", text)
    if not m:
        raise ExcerptError(f"could not find `{name} = {{ ... }}` in input")
    open_pos = m.end() - 1
    close_pos = _find_matching_brace(text, open_pos)
    return open_pos + 1, close_pos


def _split_top_level_blocks(text):
    """'{A} {B} {C}' (only top-level {...} blocks; whitespace between them
    is ignored) -> ['A', 'B', 'C']. Used for \\alternative's branches."""
    blocks = []
    i, n = 0, len(text)
    while i < n:
        if text[i].isspace():
            i += 1
            continue
        if text[i] != "{":
            raise ExcerptError(f"expected '{{' at position {i} inside \\alternative")
        close = _find_matching_brace(text, i)
        blocks.append(text[i + 1 : close])
        i = close + 1
    return blocks


_REPEAT_OPEN_RE = re.compile(r"\\repeat\s+volta\s+(\d+)\s*\{")
_ALTERNATIVE_OPEN_RE = re.compile(r"\s*\\alternative\s*\{")


def unfold_repeats_text(text):
    """Replace every `\\repeat volta N { BODY } [\\alternative { {A1} {A2} ... }]`
    with N concatenated copies of BODY, substituting alternative endings in
    order (reusing the last alternative for any extra passes) — the same
    semantics as LilyPond's own \\unfoldRepeats, done at the text level so
    the result can be sliced anywhere, not just at a repeat boundary."""
    while True:
        m = _REPEAT_OPEN_RE.search(text)
        if not m:
            return text
        n_repeats = int(m.group(1))
        body_open = m.end() - 1
        body_close = _find_matching_brace(text, body_open)
        body = text[body_open + 1 : body_close]

        consumed_end = body_close + 1
        alternatives = []
        alt_m = _ALTERNATIVE_OPEN_RE.match(text, body_close + 1)
        if alt_m:
            alt_open = alt_m.end() - 1
            alt_close = _find_matching_brace(text, alt_open)
            alternatives = _split_top_level_blocks(text[alt_open + 1 : alt_close])
            consumed_end = alt_close + 1

        passes = []
        for i in range(n_repeats):
            piece = body
            if alternatives:
                piece += alternatives[i] if i < len(alternatives) else alternatives[-1]
            passes.append(piece)

        text = text[: m.start()] + " ".join(passes) + text[consumed_end:]


_QUOTED_STRING_RE = re.compile(r'"(?:[^"\\]|\\.)*"')
_CLEF_ARG_RE = re.compile(r"(?<=\\clef\s)[a-zA-Z]+")
_KEY_ARG_RE = re.compile(r"(?<=\\key\s)[a-g](?:is|es)*")


def _mask_non_musical(text):
    """Blank out (space-fill, same length/positions so indices still line
    up with the original text) spans that would otherwise look like note
    names to the tokenizer below but aren't: quoted strings (markup text),
    \\clef's clef-name argument (e.g. "bass" starts with a pitch letter),
    and \\key's tonic-pitch argument. Actual \\command names are already
    safe without masking — the tokenizer's own lookbehind excludes any
    letter preceded by another letter or a backslash, which covers a whole
    command word once its first letter is protected."""
    chars = list(text)
    for pat in (_QUOTED_STRING_RE, _CLEF_ARG_RE, _KEY_ARG_RE):
        for m in pat.finditer(text):
            for i in range(m.start(), m.end()):
                chars[i] = " "
    return "".join(chars)


_TUPLET_OPEN_RE = re.compile(r"\\tuplet\s+(\d+)\s*/\s*(\d+)\s*\{")
_GRACE_OPEN_RE = re.compile(r"\\grace\s*\{")
_APPOGGIATURA_RE = re.compile(r"\\appoggiatura\s+")

_DURATION_RE = r"\d+\.*(?:\*\d+(?:/\d+)?)?"
_NOTE_OR_REST = r"(?:[a-g](?:is|es)*[',]*[!?]*|[rRs])"
_EVENT_RE = re.compile(
    rf"<[^<>]*>(?P<chord_dur>{_DURATION_RE})?"
    rf"|(?<![A-Za-z\\]){_NOTE_OR_REST}(?P<note_dur>{_DURATION_RE})?"
)


def _duration_value(token):
    """'4' -> 1/4, '4.' -> 3/8, '4..' -> 7/16, '1*2/3' -> 2/3, etc — as a
    fraction of a whole note."""
    m = re.match(r"(\d+)(\.*)(?:\*(\d+)(?:/(\d+))?)?", token)
    base = Fraction(1, int(m.group(1)))
    dots = len(m.group(2))
    value = base * (2 - Fraction(1, 2**dots)) if dots else base
    if m.group(3):
        value *= Fraction(int(m.group(3)), int(m.group(4)) if m.group(4) else 1)
    return value


def _find_scale_and_skip_spans(text):
    """scale_spans: (start, end, Fraction) for \\tuplet N/M {...} bodies —
    events starting in [start, end) get their duration multiplied by the
    Fraction (M/N; nested tuplets multiply). skip_spans: (start, end) for
    \\grace {...} bodies and single-token \\appoggiatura arguments — events
    starting there contribute zero duration (they're ornaments, not part
    of the notated beat) but stay in the text."""
    scale_spans = []
    skip_spans = []

    for m in _TUPLET_OPEN_RE.finditer(text):
        n, d = int(m.group(1)), int(m.group(2))
        open_pos = m.end() - 1
        close_pos = _find_matching_brace(text, open_pos)
        scale_spans.append((open_pos + 1, close_pos, Fraction(d, n)))

    for m in _GRACE_OPEN_RE.finditer(text):
        open_pos = m.end() - 1
        close_pos = _find_matching_brace(text, open_pos)
        skip_spans.append((open_pos + 1, close_pos))

    for m in _APPOGGIATURA_RE.finditer(text):
        nm = _EVENT_RE.match(text, m.end())
        if nm:
            skip_spans.append((nm.start(), nm.end()))

    return scale_spans, skip_spans


def compute_event_stream(body_text):
    """body_text should already be repeat-unfolded. Returns a list of
    (start, end, duration_in_whole_notes) for every note/chord/rest event,
    in order, with tuplet scaling applied and grace-note content zeroed."""
    masked = _mask_non_musical(body_text)
    scale_spans, skip_spans = _find_scale_and_skip_spans(masked)
    events = []
    last_duration = None
    pos, n = 0, len(masked)
    while pos < n:
        m = _EVENT_RE.search(masked, pos)
        if not m:
            break
        start, end = m.start(), m.end()
        dur_str = m.group("chord_dur") or m.group("note_dur")
        if dur_str:
            last_duration = _duration_value(dur_str)
            duration = last_duration
        else:
            if last_duration is None:
                raise ExcerptError(
                    f"note/chord/rest with no explicit duration before any duration "
                    f"was established (near {body_text[max(0, start - 20) : start + 5]!r})"
                )
            duration = last_duration

        if any(s <= start < e for s, e in skip_spans):
            duration = Fraction(0)
        else:
            for s, e, scale in scale_spans:
                if s <= start < e:
                    duration *= scale

        events.append((start, end, duration))
        pos = end
    return events


_TEMPO_RE = re.compile(r"\\tempo\s+(\d+)(\.*)\s*=\s*(\d+)")


def parse_seconds_per_whole_note(text):
    matches = _TEMPO_RE.findall(text)
    if not matches:
        raise ExcerptError(
            "no \\tempo marking found (e.g. \\tempo 4 = 72) — auto-excerpt needs "
            "exactly one constant tempo to convert measures to seconds"
        )
    distinct = {(beat, dots, bpm) for beat, dots, bpm in matches}
    if len(distinct) > 1:
        raise ExcerptError(
            "multiple different \\tempo markings found — auto-excerpt only supports "
            "a single constant tempo for now"
        )
    beat_unit, dots, bpm = next(iter(distinct))
    beat_fraction = _duration_value(beat_unit + dots)
    seconds_per_beat = 60.0 / int(bpm)
    return seconds_per_beat / float(beat_fraction)


_PARTIAL_RE = re.compile(rf"\\partial\s+({_DURATION_RE})")
_TIME_RE = re.compile(r"\\time\s+(\d+)\s*/\s*(\d+)")
_MEASURE_POS_RE = re.compile(
    r"\\set\s+Timing\.measurePosition\s*=\s*#\(ly:make-moment\s+(-?\d+)(?:/(\d+))?\)"
)


def find_bar_start_pos(body_text, target_bar):
    """Returns the index into body_text where bar `target_bar` (1-based)
    begins, by walking the same event stream used for duration-cutting and
    counting measure boundaries. Honors a leading \\partial (pickup measure
    — the first measure is shorter than the time signature) and any \\set
    Timing.measurePosition overrides, which is LilyPond's own device for
    correcting bar-number bookkeeping around an odd \\alternative ending
    (e.g. Für Elise's first one) — since we're now doing our own
    independent bar count instead of relying on LilyPond's engraver, we
    have to honor the same override or our bar numbers would drift from
    what LilyPond itself would print."""
    if target_bar <= 1:
        return 0

    time_m = _TIME_RE.search(body_text)
    if not time_m:
        raise ExcerptError("no \\time signature found — needed to count bars")
    measure_length = Fraction(int(time_m.group(1)), int(time_m.group(2)))

    masked = _mask_non_musical(body_text)
    events = compute_event_stream(body_text)

    items = [(e[0], "event", e) for e in events]
    items += [
        (m.start(), "partial", _duration_value(m.group(1))) for m in _PARTIAL_RE.finditer(masked)
    ]
    for m in _MEASURE_POS_RE.finditer(masked):
        value = Fraction(int(m.group(1)), int(m.group(2)) if m.group(2) else 1)
        items.append((m.start(), "position", value))
    items.sort(key=lambda x: x[0])

    current_bar = 1
    expected_length = measure_length
    elapsed = Fraction(0)
    bar_start_pos = 0

    for _pos, kind, payload in items:
        if kind == "partial":
            expected_length = payload
            continue
        if kind == "position":
            elapsed = payload if payload >= 0 else measure_length + payload
            continue
        _start, end, duration = payload
        elapsed += duration
        while elapsed >= expected_length:
            elapsed -= expected_length
            current_bar += 1
            expected_length = measure_length
            bar_start_pos = end
            if current_bar == target_bar:
                return bar_start_pos

    raise ExcerptError(f"requested bar {target_bar} but this voice only has {current_bar} bar(s)")


def _extend_past_open_spans(cut_pos, scale_spans, skip_spans):
    """If cut_pos lands inside a \\tuplet or \\grace span, push it out past
    the end of that span instead — cutting the text there would otherwise
    leave a dangling unclosed \\tuplet/\\grace construct. `e` is the index
    *of* the span's closing brace (see _find_scale_and_skip_spans), so the
    push-out target is e + 1 — just past it — not e itself."""
    changed = True
    while changed:
        changed = False
        for s, e, *_ in list(scale_spans) + [(s, e) for s, e in skip_spans]:
            if s <= cut_pos < e:
                cut_pos = e + 1
                changed = True
    return cut_pos


def find_excerpt_cut(body_text, seconds_per_whole_note, target_seconds):
    """Returns (cut_pos, actual_seconds): the index into body_text just past
    the last event needed to cover at least target_seconds, and how many
    seconds that actually is (may run slightly past target_seconds if the
    boundary had to be pushed past an open tuplet/grace span)."""
    events = compute_event_stream(body_text)
    masked = _mask_non_musical(body_text)
    scale_spans, skip_spans = _find_scale_and_skip_spans(masked)

    elapsed = Fraction(0)
    for start, end, duration in events:
        elapsed += duration
        seconds = float(elapsed * seconds_per_whole_note)
        if seconds >= target_seconds:
            cut_pos = _extend_past_open_spans(end, scale_spans, skip_spans)
            return cut_pos, seconds
    total_seconds = float(elapsed * seconds_per_whole_note)
    raise ExcerptError(
        f"requested {target_seconds}s but this voice is only {total_seconds:.2f}s long"
    )


def excerpt_voices_text(voices_text, target_seconds, start_bar=1):
    """Cut upMusic and downMusic down to (at least) target_seconds of music
    starting at bar `start_bar` (1-based, default the very beginning), at
    the same elapsed musical time/bar in both voices. Returns the rewritten
    voices text plus the actual seconds covered (>= target_seconds)."""
    if start_bar < 1:
        raise SystemExit("error: excerpt: --start-bar must be 1 or greater")
    try:
        seconds_per_whole_note = parse_seconds_per_whole_note(voices_text)

        new_text = voices_text
        actual_seconds = None
        for name in ("upMusic", "downMusic"):
            body_start, body_end = _find_variable_body_span(new_text, name)
            body = new_text[body_start:body_end]
            flat_body = unfold_repeats_text(body)

            events = compute_event_stream(flat_body)
            if not events:
                raise ExcerptError(f"{name} has no notes/chords/rests to excerpt")
            header_end = events[0][0]
            header = flat_body[:header_end]

            if start_bar > 1:
                masked = _mask_non_musical(flat_body)
                scale_spans, skip_spans = _find_scale_and_skip_spans(masked)
                start_pos = find_bar_start_pos(flat_body, start_bar)
                start_pos = _extend_past_open_spans(start_pos, scale_spans, skip_spans)
            else:
                start_pos = 0
            excerpt_start = max(start_pos, header_end)

            cut_pos, seconds = find_excerpt_cut(
                flat_body[excerpt_start:], seconds_per_whole_note, target_seconds
            )
            actual_seconds = max(actual_seconds or 0, seconds)

            excerpt_body = flat_body[excerpt_start : excerpt_start + cut_pos]
            new_body = f'\n {header.strip()}\n {excerpt_body.strip()}\n \\bar "|."\n'
            new_text = new_text[:body_start] + new_body + new_text[body_end:]
    except ExcerptError as e:
        raise SystemExit(f"error: excerpt: {e}") from e

    return new_text, actual_seconds


def excerpt(input_path, output_path, seconds, *, start_bar=1):
    voices_path = Path(input_path)
    if not voices_path.exists():
        raise SystemExit(f"error: input file not found: {voices_path}")
    voices_text = voices_path.read_text()

    missing = [name for name, pat in VOICES_REQUIRED_VARS.items() if not pat.search(voices_text)]
    if missing:
        raise SystemExit(
            f"error: {voices_path} is missing required variable(s): {', '.join(missing)}"
        )

    new_text, actual_seconds = excerpt_voices_text(voices_text, seconds, start_bar=start_bar)

    out_path = Path(output_path)
    if out_path.resolve() == voices_path.resolve():
        raise SystemExit("error: refusing to overwrite input file — choose a different -o/--output")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(new_text)
    bar_note = f", starting at bar {start_bar}" if start_bar > 1 else ""
    print(f"wrote {out_path} ({actual_seconds:.1f}s{bar_note}, requested >= {seconds}s)")
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


def cmd_excerpt(args):
    excerpt(
        args.input,
        args.output or str(Path(args.input).with_name(Path(args.input).stem + ".excerpt.ly")),
        args.seconds,
        start_bar=args.start_bar,
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

    p_excerpt = sub.add_parser(
        "excerpt", help="cut a voices.ly file down to (at least) N seconds of music"
    )
    p_excerpt.add_argument("input", help="path to a voices .ly file (defines upMusic/downMusic)")
    p_excerpt.add_argument(
        "--seconds",
        type=float,
        default=60.0,
        help="minimum duration of the excerpt, in seconds (default: 60)",
    )
    p_excerpt.add_argument(
        "--start-bar",
        type=int,
        default=1,
        help="1-based bar number to start the excerpt at (default: 1, the beginning)",
    )
    p_excerpt.add_argument(
        "-o",
        "--output",
        default=None,
        help="output .ly path (default: <input-stem>.excerpt.ly)",
    )
    p_excerpt.set_defaults(func=cmd_excerpt)

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
