"""A small list of everyday English words, used only to warn about risky rules.

This is not a spell-checker and must not become one. Its single job: when you
add a dictionary entry, decide whether the thing you are matching on is a word
that already appears in ordinary sentences — so "cloud" earns a warning and
"Supabase" does not.

Frequency, not validity, is the right test here. `/usr/share/dict/words` would
be the obvious source and is exactly wrong: it contains "anthropic", so every
sensible entry would be flagged. What matters is whether a word is common
enough that rewriting every occurrence would damage normal text.
"""

from __future__ import annotations

#: Roughly the most frequent English words, plus the technology words that are
#: also ordinary words — which are precisely the dangerous single-word entries
#: ("cloud", "code", "swift", "go", "rust", "stream", "flow", "port", "key").
COMMON_WORDS = frozenset(
    """
    a about above across act add after again against age ago agree air all allow
    almost alone along already also although always am among amount an and another
    answer any anyone anything appear apply are area around arrive as ask at
    attention away back bad bag base be because become been before begin behind
    being believe below best better between big bit block board body book both
    bottom box boy break bring build business but buy by call can car card care
    carry case catch cause cell center certain chance change charge check child
    choose city claim class clean clear close cloud code cold color come common
    community company complete computer condition consider contain continue control
    cost could count country couple course cover create cross current cut data day
    deal decide deep degree describe design detail develop die difference different
    direct do doctor does dog door down draw drive drop during each early east easy
    eat edge effect effort eight either else end enough enter entire even evening
    ever every example expect experience explain eye face fact fall family far fast
    father fear feel few field fight figure fill film final find fine finish fire
    first fish fit five fix floor flow fly focus follow food foot for force form
    forward four free friend from front full function game general get girl give
    glass go good got government great green ground group grow guess hair half hand
    hang happen happy hard have he head hear heart heat heavy help her here herself
    high him himself his history hit hold home hope hot hour house how however
    human hundred idea if image imagine impact important in include increase indeed
    industry information inside instead interest into is issue it item its itself
    job join just keep key kid kill kind know land language large last late later
    laugh law lay lead learn least leave left leg less let letter level lie life
    light like line list listen little live load local lock long look lose lot love
    low machine main major make man many mark market match matter may maybe me mean
    measure media meet member memory mention message method middle might mind
    minute miss model money month more morning most mother move much music must my
    name nation natural nature near need network never new news next nice night
    nine no none nor north not note nothing notice now number object of off offer
    office often oh oil ok old on once one only open operation opportunity option
    or order other our out outside over own page pain paper parent part particular
    party pass past pattern pay people per perform perhaps period person phone
    physical pick picture piece place plan plant play please point policy political
    poor popular population port position possible power practice prepare present
    president press pressure pretty prevent price probably problem process produce
    product program project property protect prove provide public pull purpose push
    put quality question quick quickly quiet quite race radio raise range rate
    rather reach read ready real reason receive recent record red reduce reflect
    region relate remain remember remove report represent require research resource
    respond response rest result return reveal rich right rise risk road rock role
    room root round rule run rust safe same save say scale school science score sea
    season seat second section see seek seem sell send sense series serious serve
    service set seven several shape share she shoot short shot should show side
    sign significant similar simple simply since sing single sister sit site
    situation six size skill skin small smile so social society some someone
    something sometimes son song soon sort sound source south space speak special
    specific speed spend sport spring staff stage stand standard star start state
    station stay step stick still stock stop store story strategy stream street
    strong structure student study stuff style subject success such suddenly suffer
    suggest summer support sure surface swift system table take talk task tax teach
    team technology tell ten term test text than thank that the their them then
    theory there these they thing think third this those though thought three
    through throw thus time to today together tone too top total touch toward town
    trade traffic train travel treat tree trial trip trouble true trust truth try
    turn two type under understand union unit until up upon us use usually value
    various very view visit voice vote wait walk wall want war watch water wave way
    we wear week weight well west what when where whether which while white who
    whole why wide wife will win wind window wish with within without woman wonder
    word work world worry would write wrong yard yeah year yes yet you young your
    yourself
    """.split()
) | frozenset(
    # Common *compounds*, which matter for a different reason: a two-word
    # pattern is allowed to match with no separator at all, so an entry like
    # "in put" would otherwise silently rewrite every "input".
    """
    anyone anything backup birthday cannot database download everyone everything
    feedback firewall framework hardware however input inside internet into keyboard
    laptop login logout meanwhile myself network nobody nothing offline online
    otherwise output outside overall password playback rollback runtime setup
    software somebody someone something sometimes somewhere therefore throughout
    timeline update upgrade upload username webpage website weekend workflow
    workspace yourself
    """.split()
)


def is_common(word: str) -> bool:
    """True when a word is frequent enough that rewriting it would hurt."""
    return word.strip().lower() in COMMON_WORDS
