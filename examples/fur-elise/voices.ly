% Für Elise (WoO 59), Ludwig van Beethoven — first ~60 seconds.
% Notes taken verbatim from the Mutopia Project's public-domain edition
% (https://www.mutopiaproject.org/ftp/BeethovenLv/WoO59/fur_Elise_WoO59/),
% trimmed to the point where written repeats resolve into a natural phrase
% break, with repeats unfolded by `eroica` at render time.
%
% This file defines exactly the two variables `eroica render` looks for:
% upMusic (treble) and downMusic (bass). Nothing else in here is special —
% it's plain LilyPond, spliced as-is between eroica's generated preamble
% and score block.

upMusic = {
 \clef treble
 \key a \minor
 \time 3/8
 \tempo 4 = 72
 \repeat volta 2 {
 \partial 8 e''16\pp^\markup { \bold "Poco moto." }
 dis'' e'' dis'' e'' b' d'' c'' a'8 r16 c' e' a' b'8 r16 e' gis' b'
 c''8 r16 e'_[ e'' dis''] e'' dis'' e'' b' d'' c'' a'8 r16 c' e' a' b'8 r16 e' c'' b' }
 \alternative { { a'4 } { a'8 \bar "" r16 b' \set Timing.measurePosition = #(ly:make-moment -1/8) c''16 d'' }
 }
 \repeat volta 2 {
 e''8. g'16[ f'' e''] d''8. f'16[ e'' d''] c''8. e'16[ d'' c''] b'8 r16 e'_[ e''] r r e''[ e'''] r r dis''
 e''8 r16 dis'' e'' dis'' e''16 dis'' e'' b' d'' c''
 a'8 r16 c' e' a' b'8 r16 e' gis' b' c''8 r16 e'_[ e'' dis''] e'' dis'' e'' b' d'' c'' a'8 r16 c' e' a' b'8 r16 e' c'' b'}
 \alternative { { a'8 r16 b'[ c'' d''] } { a'8 r16 <e' c''>[ <f' c''> <e' g' c''>] } }

 \grace { f'16[ a'] } c''4 f''16. e''32 e''8([ d'']) bes''16. a''32 a''16( g'' f'' e'' d'' c'')
 bes'8[ a'] \appoggiatura bes'32 a'32[ g' a' bes'] c''4 d''16[ dis''] e''8. e''16[ f'' a'] c''4 d''16. b'32
 \bar "|."
}

downMusic = {
 \clef bass
 \key a \minor
 \time 3/8
 \repeat volta 2 {
 \partial 8 r8\pp R4. a,16 e a r16 r8 e,16 e gis r r8
 a,16 e a r r8 R4. a,16 e a r r8
 e,16 e gis r r8 }
 \alternative { { a,16 e a r } { a,16[ e \bar "" a16] r \set Timing.measurePosition = #(ly:make-moment -1/8) r8 } }
 \repeat volta 2 {
 c16 g c' r r8 g,16 g b r r8
 a,16 e a r r8 e,16 e e' r r \clef treble e'16_[( e'')] r r dis''[ e''] r r16 dis''[ e''] r r8 R4.
 \clef bass a,16 e a r16 r8 e,16 e gis r r8
 a,16 e a r r8 R4. a,16 e a r r8
 e,16 e gis r r8 }
 \alternative { { a,16 e a r r8 } { a,16[ e a] <bes c'>[ <a c'> <g bes c'>] } }

 f16 a c' a c' a f bes d' bes d' bes f e' <f g bes> e' <f g bes> e' f a c' a c' a
 \bar "|."
}
