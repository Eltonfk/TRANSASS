"""Small, deterministic quality gates for high-confidence translation risks.

These checks do not attempt to prove that a subtitle is perfect. They flag a
few objectively risky French-to-pt-BR outcomes and protect repeated French
proper names; uncertain language quality remains a human-review concern.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import re
from collections.abc import Iterable
import unicodedata

from ass_engine import (
    prose_delimiter_flags,
    prose_delimiter_ownership_sequence,
    prose_delimiter_token_sequence,
    visible_text,
)


_FRENCH_ALIASES = {"fr", "fra", "fre", "french", "francais", "français", "francês"}
_ENGLISH_ALIASES = {"en", "eng", "english", "ingles", "inglês"}
_PT_BR_ALIASES = {"pt-br", "pt_br", "português do brasil", "portugues do brasil", "brazilian portuguese"}
_TRAILING_ASCII_QUOTE_RUN = re.compile(r'(?P<quotes>"+)(?P<suffix>[.!?…,:;]*\s*)\Z')


def remove_unowned_terminal_ascii_quote_closures(source: str, translated: str) -> str:
    """Remove only model-added terminal quotes for a source-open quote.

    This narrowly restores delimiter ownership when the translated delimiter
    sequence starts with the source sequence, the source has an odd number of
    straight double quotes, and every extra token is a terminal straight quote.
    Any ambiguity is left untouched for the regular quality gate to reject.
    """
    value = str(translated or "")
    source_tokens = prose_delimiter_token_sequence(str(source or ""))
    source_ownership = prose_delimiter_ownership_sequence(source)
    translated_tokens = prose_delimiter_token_sequence(value)
    if (
        not source_tokens
        or source_ownership[-1][1] != "open"
        or source_tokens.count('"') % 2 == 0
        or len(translated_tokens) <= len(source_tokens)
        or translated_tokens[:len(source_tokens)] != source_tokens
    ):
        return value

    extra_tokens = translated_tokens[len(source_tokens):]
    if not extra_tokens or any(token != '"' for token in extra_tokens):
        return value

    trailing = _TRAILING_ASCII_QUOTE_RUN.search(value)
    if trailing is None or len(trailing.group("quotes")) < len(extra_tokens):
        return value

    remaining_quotes = trailing.group("quotes")[:-len(extra_tokens)]
    corrected = (
        value[:trailing.start("quotes")]
        + remaining_quotes
        + trailing.group("suffix")
    )
    return corrected if prose_delimiter_ownership_sequence(corrected) == source_ownership else value


def restore_source_enclosing_ascii_quotes(source: str, translated: str) -> str:
    """Restore enclosing double quotes when source has them and output stripped them.

    LLMs often strip enclosing double quotes because their JSON output schema wraps
    strings in quotes. When the source dialogue begins and ends with straight double
    quotes (e.g. '"Citation."' with exactly 2 quotes) and the translated string
    contains zero double quotes, restore the enclosing quote pair.
    """
    src = str(source or "").strip()
    val = str(translated or "").strip()
    if not src or not val:
        return str(translated or "")

    if src.startswith('"') and src.endswith('"') and src.count('"') == 2 and len(src) >= 2:
        if '"' not in val:
            prefix_len = len(translated) - len(translated.lstrip())
            suffix_len = len(translated) - len(translated.rstrip())
            prefix = translated[:prefix_len] if prefix_len else ""
            suffix = translated[len(translated) - suffix_len:] if suffix_len else ""
            return f'{prefix}"{val}"{suffix}'
    return str(translated or "")


_FRENCH_NAME_STOPWORDS = {
    "à", "a", "ah", "au", "aux", "avec", "bonjour", "ça", "car", "ce",
    "ceci", "cela", "celle", "celui", "ces", "cette", "ceux", "chez",
    "comme", "comment", "contre", "d", "dans", "de", "dedans", "dehors",
    "depuis", "des", "dieu", "donc", "dont", "du", "elle", "elles", "en", "encore",
    "entre", "et", "eux", "fait", "fois", "font", "hors", "ici", "il", "ils",
    "je", "la", "le", "les", "leur", "leurs", "lui", "ma", "mais", "mal",
    "me", "même", "mes", "mien", "moins", "mon", "mot", "ni", "non", "nos",
    "notre", "nous", "on", "ou", "où", "par", "parce", "pas", "pendant",
    "peu", "plus", "pour", "pourquoi", "quand", "que", "quel", "quelle",
    "quelles", "quels", "qui", "sa", "sans", "se", "ses", "si", "soi", "son",
    "sont", "sous", "sur", "ta", "te", "tel", "telle", "tes", "toi", "ton",
    "toujours", "tout", "toute", "toutes", "très", "tu", "un", "une", "vos",
    "votre", "vous", "y", "salut", "oui", "merci", "vraiment", "bravo",
    "voilà", "voila", "ouf", "pardon", "allô", "allo", "adieu", "ouais",
    "bonsoir", "bien", "bon", "bonne", "coucou", "attention",
    "attends", "attendez", "vite", "viens", "venez", "regarde", "regardez",
    "écoute", "écoutez", "allez", "cours", "fuis", "pars", "reste", "reviens",
    "aide", "aidez",
    "chéri", "chérie", "cheri", "cherie", "chanson", "chapeau",
    "chimie", "chimère", "chinoise", "chienne", "pyjama", "tsunami",
    "banane", "marianne", "papaye",
    "enfin", "tiens", "hein", "docteur", "docteure", "monsieur", "madame",
    "mademoiselle", "mme", "mlle", "dr", "alors", "après", "apres",
    "ensuite", "maintenant", "pourtant", "cependant", "toutefois",
}

_ENGLISH_NAME_STOPWORDS = {
    "a", "about", "above", "after", "again", "against", "all", "am", "an", "and",
    "any", "are", "aren't", "as", "at", "be", "because", "been", "before", "being",
    "below", "between", "both", "but", "by", "can", "can't", "cannot", "could",
    "couldn't", "did", "didn't", "do", "does", "doesn't", "doing", "don't", "down",
    "during", "each", "few", "for", "from", "further", "had", "hadn't", "has",
    "hasn't", "have", "haven't", "having", "he", "he'd", "he'll", "he's", "her",
    "here", "here's", "hers", "herself", "him", "himself", "his", "how", "how's",
    "i", "i'd", "i'll", "i'm", "i've", "if", "in", "into", "is", "isn't", "it",
    "it's", "its", "itself", "let's", "me", "more", "most", "mustn't", "my",
    "myself", "no", "nor", "not", "of", "off", "on", "once", "only", "or",
    "other", "ought", "our", "ours", "ourselves", "out", "over", "own", "same",
    "shan't", "she", "she'd", "she'll", "she's", "should", "shouldn't", "so",
    "some", "such", "than", "that", "that's", "the", "their", "theirs", "them",
    "themselves", "then", "there", "there's", "these", "they", "they'd", "they'll",
    "they're", "they've", "this", "those", "through", "to", "too", "under", "until",
    "up", "very", "was", "wasn't", "we", "we'd", "we'll", "we're", "we've", "were",
    "weren't", "what", "what's", "when", "when's", "where", "where's", "which",
    "while", "who", "who's", "whom", "why", "why's", "with", "won't", "would",
    "wouldn't", "you", "you'd", "you'll", "you're", "you've", "your", "yours",
    "yourself", "yourselves", "yes", "yeah", "no", "oh", "ah", "hey", "well", "ok", "okay",
}

_CAPITALIZED_WORD = re.compile(r"(?<!\w)([A-ZÀ-ÖØ-Þ][a-zà-öø-ÿ]{1,})(?!\w)")
_UPPERCASE_WORD = re.compile(r"(?<!\w)([A-ZÀ-ÖØ-Þ]{2,})(?!\w)")
_ELLIPSIZED_NAME_PREFIX = re.compile(
    r"([A-ZÀ-ÖØ-Þ][a-zà-öø-ÿ]{1,})[ \t]*(?:\.{3,}|…)",
    re.UNICODE,
)
_WORD_TOKEN = re.compile(r"(?<!\w)[^\W\d_]+(?!\w)", re.UNICODE)
_FRENCH_CES_TOKEN = re.compile(r"(?<!\w)ces(?!\w)", re.IGNORECASE | re.UNICODE)
_QUOTE_PAIRS = {"\"": "\"", "«": "»", "“": "”", "‘": "’", "‹": "›", "「": "」", "『": "』"}
_QUOTE_CLOSERS = {closer: opener for opener, closer in _QUOTE_PAIRS.items()}
# Only used to recognize obvious demonstratives in entirely uppercase subtitle
# lines. Normal sentence case is handled directly by the spelling of ``ces``.
_ALL_CAPS_CES_NOUNS = frozenset({
    "acteurs", "avis", "choses", "données", "dossiers", "enfants", "femmes",
    "gens", "hommes", "livres", "maisons", "mots", "photos", "problèmes",
    "séries", "trucs",
})
_ALL_CAPS_CES_VERBS = frozenset({
    "arrive", "arrivent", "commentent", "étudient", "parle", "parlent",
    "présente", "présentent", "publie", "publient", "remplace", "remplacent",
    "représente", "représentent", "signifie",
})
_ROMAJI_VOWELS = "aeiou"
_ROMAJI_SYLLABLES = frozenset(
    set(_ROMAJI_VOWELS)
    | {consonant + vowel for consonant in "kstnhmyrwgzdbp" for vowel in _ROMAJI_VOWELS}
    | {
        "n", "shi", "sha", "shu", "she", "sho", "chi", "cha", "chu",
        "che", "cho", "tsu", "dzu", "ji", "ja", "ju", "je", "jo",
        "fu", "fa", "fi", "fe", "fo", "wa", "wi", "we", "wo",
    }
    | {
        onset + vowel
        for onset in ("ky", "gy", "sh", "ch", "ny", "hy", "my", "ry", "by", "py")
        for vowel in "auo"
    }
)
_ROMAJI_NAME_MARKERS = ("sh", "ts", "ky", "gy", "ny", "hy", "my", "ry", "by", "py")
# Name-only French-source identity is permitted only for individually reviewed
# unmarked tokens; phonetic resemblance alone is not enough against French.
# Akira is the observed repeated E14 name call; Kaori is the observed isolated
# E14 name call. Add tokens only after inspecting durable source/response
# evidence and checking French-word controls.
_REVIEWED_ROMAJI_NAME_ALLOWLIST = frozenset({
    "akira", "kaori", "sunako", "chizuru", "seishirou", "tatsumi", "natsuno",
    "tooru", "megumi", "toshio", "seishin", "masao", "ritsuko", "shiki",
    "yamairi", "senbu", "shakku", "kanemasa", "sotoba", "muroi", "ozaki",
    "kirishiki", "shimizu", "yasumori", "muto", "mutou", "tanaka", "kunihiro",
    "okura", "ookura", "itou", "ito", "sachiko", "kiyomi", "tarô", "taro",
    "atsushi", "yoshie", "kazuko", "kanami", "motoko", "tsurumi", "tamo",
    "yasuyo", "matsu", "atsuko", "mizobe", "takeshi", "oitarô", "oitaro",
    "satô", "sato", "shinmei", "tokujirô", "tokujiro", "maeda",
    "mikiyasu", "yano", "kitayama", "hasegawa", "hiromi", "hiroko", "motohashi",
    "goto", "gotou", "nao", "koide", "saito", "saitou",
    "ookawa", "okawa", "ebuchi", "tashiro",
})


@dataclass(frozen=True)
class _FrenchPtBrRisk:
    code: str
    source_pattern: re.Pattern[str]
    bad_output_pattern: re.Pattern[str]
    prompt_hint: str


def _risk(
    code: str,
    source: str,
    bad_output: str,
    prompt_hint: str,
) -> _FrenchPtBrRisk:
    return _FrenchPtBrRisk(
        code=code,
        source_pattern=re.compile(source, re.IGNORECASE | re.UNICODE | re.DOTALL),
        bad_output_pattern=re.compile(bad_output, re.IGNORECASE | re.UNICODE | re.DOTALL),
        prompt_hint=prompt_hint,
    )


# Narrow regression rules: the source phrase and its specific misleading
# rendering must both be present before a translation is rejected.
_FRENCH_PTBR_RISKS = (
    _risk(
        "FRENCH_CES_DEMONSTRATIVE_UNTRANSLATED",
        r"(?<!\w)ces\s+[\wÀ-ÿ]",
        r"(?<!\w)ces(?!\w)",
        "Traduza o demonstrativo francês ‘ces’ como ‘esses/essas’ ou ‘estes/estas’, "
        "conforme o contexto. Preserve nomes próprios e siglas em maiúsculas como ‘CES’; "
        "não mantenha ‘ces/Ces’ como palavra da tradução.",
    ),
    _risk(
        "FRENCH_INSISTEZ_ADDED_CONCESSION",
        r"^\s*(?:[-–—]\s*)?puisque\s+vous\s+insistez\s*[.!?…]*\s*$",
        r"^\s*como\s+você\s+insiste\s*(?:\.{2,}|…)\s+tudo\s+bem\b",
        "‘Puisque vous insistez’ significa ‘já que você insiste’/‘se insiste’; não acrescente "
        "uma segunda fala como ‘tudo bem’ se ela não está na fonte.",
    ),
    _risk(
        "FRENCH_OKIAGARI_MEANING_SHIFT",
        r"^\s*(?:[-–—]\s*)?parce\s+que\s+ce\s+sont\s+des?\s+okiagari\s*[.!?…]*\s*$",
        r"\b(?:ouça|escuta|me\s+escute)[\s\S]{0,120}\bse\s+n[aã]o\b[\s\S]{0,120}\bcaç",
        "A fonte afirma causalmente que eles são okiagari; traduza essa afirmação, sem "
        "substituí-la por uma ordem para ouvir ou por uma condição de caça.",
    ),
    _risk(
        "LATIN_VADE_RETRO_UNTRANSLATED",
        r"""^\s*(?:(?:"\s*vade\s+retro\s*[.!?…]*\s*")|(?:“\s*vade\s+retro\s*[.!?…]*\s*”)|(?:‘\s*vade\s+retro\s*[.!?…]*\s*’)|(?:'\s*vade\s+retro\s*[.!?…]*\s*')|(?:vade\s+retro\s*[.!?…]*))\s*$""",
        r"\bvade\s+retro\b",
        "‘Vade retro’ é uma fórmula latina de afastamento; nesta fala, traduza o sentido "
        "para pt-BR (‘Para trás!’/‘Afaste-se!’), em vez de manter a locução sem tradução.",
    ),
    _risk(
        "FRENCH_DORS_BIEN_LITERAL",
        r"\bdors\s+bien\b",
        r"\bdê\s+bom\s+sono\b",
        "‘Dors bien’ é uma despedida (‘durma bem’/‘descanse bem’), não ‘dê bom sono’.",
    ),
    _risk(
        "FRENCH_GARDE_LITERAL",
        r"\brester\s+de\s+garde\b",
        r"\bdevolver\s+a\s+guarda\b",
        "‘rester de garde’ significa ‘ficar de plantão’, não devolver uma guarda.",
    ),
    _risk(
        "FRENCH_HONTE_ROLE_REVERSAL",
        r"\bhonte\s+[àa]\s+toi\b",
        r"\bvocê\s+tem\s+vergonha\s+de\s+mim\b",
        "‘Honte à toi’ expressa reprovação (‘Que vergonha!’/‘Você deveria ter vergonha’); "
        "não inverta quem sente vergonha de quem.",
    ),
    _risk(
        "FRENCH_POWER_OUTAGE_FALSE_FRIEND",
        r"\bpanne\s+de\s+courant\b",
        r"\bblefe\s+de\s+energia\b",
        "‘panne de courant’ é falta/queda de energia, não blefe de energia.",
    ),
    _risk(
        "FRENCH_NYCTALOPE_FALSE_FRIEND",
        r"\bnyctalope(?:s)?\b",
        r"\bnotívago(?:s)?\b",
        "‘nyctalope’ descreve quem enxerga no escuro; não significa apenas alguém que fica acordado à noite.",
    ),
    _risk(
        "FRENCH_BELLE_MAMAN_KINSHIP",
        r"\bbelle[\s-]?maman\b",
        r"\bcunhad[oa]s?\b",
        "‘belle-maman’ refere-se à mãe/sogra conforme o contexto familiar, não à cunhada.",
    ),
    _risk(
        "FRENCH_SERIE_IDIOM_LITERAL",
        r"c['’]est\s+la\s+série\s+chez\s+eux",
        r"\bé\s+a\s+vez\s+deles\s+ter(?:em)?\s+problemas\b",
        "‘C’est la série chez eux’ indica que os problemas vêm acontecendo em sequência "
        "na família; não que chegou a vez deles.",
    ),
    _risk(
        "FRENCH_WIFE_DUTY_UNGRAMMATICAL",
        r"c['’]est\s+[àa]\s+sa\s+femme\s+de\s+s['’]occuper",
        r"\bé\s+com\s+(?:a\s+)?sua\s+mulher\s+de\s+(?:se\s+)?ocupar\b",
        "‘C’est à sa femme de s’occuper de ça’ significa que a esposa dele é quem deve cuidar disso.",
    ),
    _risk(
        "FRENCH_IMPERATIVE_PERSON_SHIFT",
        r"\bjauge\s+un\s+peu\s+l['’]humeur[\s\S]*\brepars\b",
        r"\bavalio\b[\s\S]*\bvou\s+embora\b",
        "Preserve o imperativo dirigido à outra pessoa; não o transforme em primeira pessoa.",
    ),
    _risk(
        "FRENCH_POMPES_FUNEBRES_UNTRANSLATED",
        r"\bpompes\s+fun[èe]bres\b",
        r"\bpompes\s+fun[èe]bres\b",
        "‘Pompes funèbres’ significa ‘funerária’ ou ‘serviços funerários’; traduza para o português do Brasil, não mantenha a expressão em francês.",
    ),
)

_PTBR_ARTICLE_GENDER = {
    "um": "m", "uns": "m", "o": "m", "os": "m", "do": "m", "dos": "m",
    "no": "m", "nos": "m", "num": "m", "nuns": "m", "ao": "m", "aos": "m",
    "uma": "f", "umas": "f", "a": "f", "as": "f", "da": "f", "das": "f",
    "na": "f", "nas": "f", "numa": "f", "numas": "f", "à": "f", "às": "f",
}
_PTBR_NOUN_GENDER = {
    "balde": "m", "baldes": "m",
    "parada": "f", "paradas": "f",
    "vítima": "f", "vítimas": "f",
}
_PTBR_ARTICLE_NOUN = re.compile(
    r"(?<!\w)(um|uma|uns|umas|o|a|os|as|do|da|dos|das|no|na|nos|nas|"
    r"num|numa|nuns|numas|ao|aos|à|às)\s+([^\W\d_]+)(?!\w)",
    re.IGNORECASE | re.UNICODE,
)

_FRENCH_PTBR_ADVISORY_HINTS = (
    (
        re.compile(r"\bfemme\s+volatile\b", re.IGNORECASE | re.UNICODE),
        "Se ‘femme volatile’ descreve personalidade, ‘mulher volúvel/inconstante’ "
        "tende a soar mais natural; use ‘volátil’ só se esse for o sentido contextual.",
    ),
    (
        re.compile(r"\bdis[-\s]moi\b", re.IGNORECASE | re.UNICODE),
        "Em pt-BR, ‘dis-moi’ normalmente fica mais natural como ‘me diga’, não ‘diz-me’.",
    ),
    (
        re.compile(r"\bje\s+me\s+souviens\b", re.IGNORECASE | re.UNICODE),
        "Em pt-BR coloquial, ‘je me souviens’ normalmente fica mais natural como ‘eu me lembro/me lembro’.",
    ),
    (
        re.compile(r"\bsœur\s+cadette\b|\bsoeur\s+cadette\b", re.IGNORECASE | re.UNICODE),
        "‘Sœur cadette’ significa ‘irmã mais nova’/‘irmã caçula’; evite o falso cognato ‘irmã cadete’.",
    ),
    (
        re.compile(r"\bma\s+clinique\b", re.IGNORECASE | re.UNICODE),
        "‘Clinique’ costuma ser ‘clínica/consultório’; escolha ‘hospital’ apenas se o contexto realmente indicar.",
    ),
    (
        re.compile(r"\bpompes\s+fun[èe]bres\b", re.IGNORECASE | re.UNICODE),
        "‘Pompes funèbres’ significa ‘funerária’ ou ‘serviços funerários’ (em maiúsculas: ‘FUNERÁRIA’); traduza para o português do Brasil, não deixe em francês.",
    ),
)


def _canonical_source_language(language: str | None) -> str:
    value = str(language or "").strip().casefold()
    if value in _FRENCH_ALIASES:
        return "french"
    if value in _ENGLISH_ALIASES:
        return "english"
    return value


def _is_pt_br(language: str | None) -> bool:
    value = str(language or "").strip().casefold()
    return value in _PT_BR_ALIASES or bool(re.search(r"\bpt[-_]br\b", value))


def _has_word(text: str, word: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(word)}(?!\w)", text, re.IGNORECASE | re.UNICODE) is not None


def _previous_word_before(text: str, position: int) -> str | None:
    matches = tuple(_WORD_TOKEN.finditer(text[:position]))
    return matches[-1].group(0).casefold() if matches else None


def _next_word_after(text: str, position: int) -> str | None:
    match = _WORD_TOKEN.search(text[position:])
    return match.group(0).casefold() if match else None


def _active_quote_context(text: str, position: int) -> tuple[str, ...]:
    """Return currently open typographic quote markers before a token."""
    stack: list[str] = []
    for char in text[:position]:
        if char in _QUOTE_PAIRS:
            if _QUOTE_PAIRS[char] == char and stack and stack[-1] == char:
                stack.pop()
            else:
                stack.append(char)
        elif char in _QUOTE_CLOSERS and stack and stack[-1] == _QUOTE_CLOSERS[char]:
            stack.pop()
    return tuple(stack)


def _source_ces_is_demonstrative(source: str, match: re.Match[str]) -> bool:
    """Recognize clear ``ces`` demonstratives without claiming POS certainty.

    In ordinary sentence case, lowercase ``ces`` and sentence-initial ``Ces``
    are demonstratives. Uppercase ``CES`` is treated as a siglum when the line
    contains normal mixed case. For lines styled entirely in capitals, only a
    small set of strong ``DE CES + noun`` / ``verb + CES + noun`` patterns is
    classified as demonstrative; ambiguous cases remain unclassified.
    """
    token = match.group(0)
    quote_context = _active_quote_context(source, match.start())
    if token == "ces":
        return True
    if token == "Ces":
        following = _WORD_TOKEN.search(source, match.end())
        title_case = following is not None and following.group(0).istitle()
        return not (quote_context and title_case)
    if token != "CES" or any(char.islower() for char in source if char.isalpha()):
        return False
    previous = _previous_word_before(source, match.start())
    following = _next_word_after(source, match.end())
    return (
        not quote_context
        and following in _ALL_CAPS_CES_NOUNS
        and (previous == "de" or previous in _ALL_CAPS_CES_VERBS)
    )


def _french_ces_output_has_unmatched_residue(source: str, output: str) -> bool:
    """Reject clear French demonstrative residue without guessing word sense.

    Lowercase ``ces`` in Portuguese output is always retained French. ``Ces``
    is allowed only when it is the same quoted title token in the source.
    Uppercase ``CES`` is allowed up to the number of source tokens confidently
    classified as acronyms. This intentionally does not claim to detect an
    acronym moved into a demonstrative's position when both occur in the same
    unit; that distinction needs broader semantic alignment and is ambiguous
    under free translation. The prompt hint still asks the model to translate
    demonstratives while preserving sigla.
    """
    source_tokens = tuple(_FRENCH_CES_TOKEN.finditer(source))
    source_acronym_count = sum(
        not _source_ces_is_demonstrative(source, match) for match in source_tokens
    )
    source_quoted_title_case_ces_count = sum(
        match.group(0) == "Ces"
        and not _source_ces_is_demonstrative(source, match)
        and bool(_active_quote_context(source, match.start()))
        for match in source_tokens
    )

    output_uppercase_count = 0
    output_title_case_count = 0
    for match in _FRENCH_CES_TOKEN.finditer(output):
        token = match.group(0)
        if token == "ces":
            return True
        if token == "Ces":
            output_title_case_count += 1
        if token == "CES":
            output_uppercase_count += 1
    return (
        output_title_case_count > source_quoted_title_case_ces_count
        or output_uppercase_count > source_acronym_count
    )


def is_likely_romanized_japanese_name_token(token: str) -> bool:
    """Conservatively recognize longer Hepburn-like names, not French call words."""
    original = str(token or "").strip()
    match = _CAPITALIZED_WORD.fullmatch(original) or (
        original.isupper() and _CAPITALIZED_WORD.fullmatch(original.title())
    )
    if not match or original.casefold() in _FRENCH_NAME_STOPWORDS:
        return False
    normalized = "".join(
        char for char in unicodedata.normalize("NFKD", original.casefold())
        if not unicodedata.combining(char)
    )
    if len(normalized) < 5:
        return False

    states: list[set[tuple[int, int]]] = [set() for _ in range(len(normalized) + 1)]
    states[0].add((0, 0))
    for offset, paths in enumerate(states[:-1]):
        for syllable_count, consonant_syllables in paths:
            for syllable in _ROMAJI_SYLLABLES:
                end = offset + len(syllable)
                if normalized.startswith(syllable, offset):
                    has_consonant_onset = syllable[0] not in _ROMAJI_VOWELS and syllable != "n"
                    states[end].add((
                        syllable_count + 1,
                        consonant_syllables + int(has_consonant_onset),
                    ))
    has_name_marker = any(marker in normalized for marker in _ROMAJI_NAME_MARKERS) or bool(
        re.search(r"[âîûêôāīūēō]", original.casefold())
    )
    return any(
        syllable_count >= 3
        and consonant_syllables >= 2
        and has_name_marker
        for syllable_count, consonant_syllables in states[-1]
    )


_ROMAJI_NORMALIZED_STOPWORDS = frozenset({
    "".join(c for c in unicodedata.normalize("NFKD", w.casefold()) if not unicodedata.combining(c))
    for w in set(_FRENCH_NAME_STOPWORDS) | {
        "au", "aussi", "autre", "autres", "aux", "avec", "avoir", "bien", "bon",
        "bonne", "car", "ce", "celle", "celui", "ces", "cet", "cette", "chez",
        "comment", "dans", "de", "des", "dont", "du", "elle", "elles", "en",
        "encore", "est", "et", "faire", "fait", "il", "ils", "je", "la", "le",
        "les", "leur", "mais", "me", "moi", "mon", "ne", "nous", "on", "ou",
        "où", "par", "pas", "pour", "que", "qui", "sa", "se", "ses", "son",
        "sur", "te", "toi", "ton", "tout", "tous", "tu", "un", "une", "va", "vous", "y",
    }
})


def is_clean_romaji_token(token: str) -> bool:
    """Conservatively check if a token is valid Hepburn romaji and not French vocabulary."""
    norm = "".join(
        c for c in unicodedata.normalize("NFKD", str(token or "").casefold())
        if not unicodedata.combining(c)
    )
    if not norm or len(norm) > 25 or norm in _ROMAJI_NORMALIZED_STOPWORDS:
        return False
    dp = [False] * (len(norm) + 1)
    dp[0] = True
    for i in range(len(norm)):
        if not dp[i]:
            continue
        for syl in _ROMAJI_SYLLABLES:
            if norm.startswith(syl, i):
                dp[i + len(syl)] = True
        if i + 1 < len(norm) and norm[i] in "bcdfghjklmpqrstvwxyz" and norm[i] == norm[i + 1]:
            dp[i + 1] = True
        if norm.startswith("tch", i):
            dp[i + 1] = True
    return dp[-1]


def is_reviewed_romanized_japanese_name_token(token: str) -> bool:
    """Check the closed allowlist used by the repeated-name identity gate."""
    original = str(token or "").strip()
    return (
        _CAPITALIZED_WORD.fullmatch(original) is not None
        and original.casefold() in _REVIEWED_ROMAJI_NAME_ALLOWLIST
    )


def is_french_name_stopword(token: str) -> bool:
    """Return whether a token is common French vocabulary, not name evidence."""
    return str(token or "").strip().casefold() in _FRENCH_NAME_STOPWORDS


def extract_repeated_names(source_texts: Iterable[str], source_language: str | None) -> tuple[str, ...]:
    """Find conservative, repeated title-cased source names (supporting French and English).

    A token must recur in the subtitle and have at least one mid-clause
    occurrence. This avoids treating ordinary sentence-initial words as names.
    """
    canonical = _canonical_source_language(source_language)
    if canonical == "french":
        stopwords = _FRENCH_NAME_STOPWORDS
    elif canonical == "english":
        stopwords = _ENGLISH_NAME_STOPWORDS
    else:
        return ()

    texts = tuple(str(text) for text in source_texts)
    occurrences: Counter[str] = Counter()
    mid_clause: Counter[str] = Counter()
    spellings: dict[str, str] = {}

    for raw_text in texts:
        for line in visible_text(str(raw_text)).splitlines():
            for match in _CAPITALIZED_WORD.finditer(line):
                token = match.group(1)
                key = token.casefold()
                if key in stopwords:
                    continue
                spellings.setdefault(key, token)
                occurrences[key] += 1
                prefix = line[:match.start()].rstrip()
                if prefix and prefix[-1].isalpha():
                    mid_clause[key] += 1

    # An all-caps ASS event may repeat a name already evidenced in normal
    # title case elsewhere (e.g. ``Shi Ki`` / ``SHI KI``). Count that as the
    # same lexical identity, but never discover a new name from capitals alone.
    for raw_text in texts:
        for line in visible_text(raw_text).splitlines():
            for match in _UPPERCASE_WORD.finditer(line):
                key = match.group(1).casefold()
                if key in spellings and key not in stopwords:
                    occurrences[key] += 1

    return tuple(
        spellings[key]
        for key in sorted(spellings)
        if occurrences[key] >= 2 and (mid_clause[key] >= 1 or (canonical == "french" and len(texts) < 20))
    )


def extract_repeated_french_names(source_texts: Iterable[str], source_language: str | None) -> tuple[str, ...]:
    """Find conservative, repeated title-cased French-source names (legacy helper)."""
    if _canonical_source_language(source_language) != "french":
        return ()
    return extract_repeated_names(source_texts, "french")


def names_in_source(text: str, protected_names: Iterable[str]) -> tuple[str, ...]:
    """Return protected names actually present in one source translation unit."""
    visible = visible_text(text)
    return tuple(name for name in protected_names if _has_word(visible, name))


def _ellipsized_name_fragment(text: str) -> str | None:
    """Return a non-stopword, capitalized fragment followed by real ellipsis."""
    visible = visible_text(str(text or "")).strip()
    match = _ELLIPSIZED_NAME_PREFIX.fullmatch(visible)
    if not match or is_french_name_stopword(match.group(1)):
        return None
    return match.group(1)


def progressive_protected_name_prefixes_in_sequence(
    source_texts: Iterable[str],
    source_index: int,
    protected_names: Iterable[str],
) -> tuple[str, ...]:
    """Authorize one fragment only inside a three-event progressive name reveal.

    All three adjacent source events must be ellipsized, each fragment must be
    strictly longer than and begin with the previous fragment, and the final
    fragment must exactly equal a protected name token. A unique prefix by
    itself is deliberately insufficient evidence for identity preservation.
    """
    texts = tuple(source_texts)
    if (
        len(texts) != 3
        or not isinstance(source_index, int)
        or isinstance(source_index, bool)
        or not 0 <= source_index < len(texts)
    ):
        return ()
    fragments = tuple(_ellipsized_name_fragment(text) for text in texts)
    if any(fragment is None for fragment in fragments):
        return ()

    normalized_fragments = tuple(str(fragment).casefold() for fragment in fragments)
    if any(
        len(normalized_fragments[index]) <= len(normalized_fragments[index - 1])
        or not normalized_fragments[index].startswith(normalized_fragments[index - 1])
        for index in (1, 2)
    ):
        return ()

    selected_fragment = normalized_fragments[source_index]
    final_fragment = normalized_fragments[-1].replace("’", "'")
    if len(selected_fragment) >= len(final_fragment):
        return ()

    matching_names: dict[str, str] = {}
    for name in protected_names:
        for token in re.findall(r"[\wÀ-ÿ]+(?:['’][\wÀ-ÿ]+)?", str(name), re.UNICODE):
            normalized = token.casefold().replace("’", "'")
            if normalized == final_fragment:
                matching_names.setdefault(normalized, token)
    return tuple(matching_names.values()) if len(matching_names) == 1 else ()


def source_name_spellings(source_text: str, protected_names: Iterable[str]) -> tuple[str, ...]:
    """Return literal source spellings of protected names, preserving case."""
    visible = visible_text(str(source_text or ""))
    spellings: list[str] = []
    for name in protected_names:
        token = str(name or "").strip()
        if not token:
            continue
        pattern = re.compile(rf"(?<!\w){re.escape(token)}(?!\w)", re.IGNORECASE | re.UNICODE)
        spellings.extend(match.group(0) for match in pattern.finditer(visible))
    return tuple(dict.fromkeys(spellings))


def french_ptbr_context_hints(
    source_texts: Iterable[str],
    source_language: str | None,
    target_language: str | None,
) -> tuple[str, ...]:
    """Return only lexical hints relevant to the current French-to-pt-BR batch."""
    if _canonical_source_language(source_language) != "french" or not _is_pt_br(target_language):
        return ()
    sources = [visible_text(str(text)) for text in source_texts]
    return tuple(
        risk.prompt_hint
        for risk in _FRENCH_PTBR_RISKS
        if any(
            (
                any(
                    _source_ces_is_demonstrative(source, match)
                    for match in _FRENCH_CES_TOKEN.finditer(source)
                )
                if risk.code == "FRENCH_CES_DEMONSTRATIVE_UNTRANSLATED"
                else risk.source_pattern.search(source)
            )
            for source in sources
        )
    ) + tuple(
        hint
        for pattern, hint in _FRENCH_PTBR_ADVISORY_HINTS
        if any(pattern.search(source) for source in sources)
    )


def translation_quality_flags(
    source: str,
    translated: str,
    *,
    source_language: str | None,
    target_language: str | None,
    protected_names: Iterable[str] = (),
) -> tuple[str, ...]:
    """Flag high-confidence known errors without rewriting model output."""
    source_visible = visible_text(source)
    output_visible = visible_text(translated)
    flags: list[str] = []

    flags.extend(prose_delimiter_flags(source, translated))

    if _is_pt_br(target_language):
        for article_match in _PTBR_ARTICLE_NOUN.finditer(output_visible):
            article = article_match.group(1).casefold()
            noun = article_match.group(2).casefold()
            expected_gender = _PTBR_NOUN_GENDER.get(noun)
            actual_gender = _PTBR_ARTICLE_GENDER.get(article)
            if expected_gender and actual_gender and expected_gender != actual_gender:
                flags.append(f"PTBR_GENDER_MISMATCH:{article}:{noun}")

    if _canonical_source_language(source_language) == "french" and _is_pt_br(target_language):
        for risk in _FRENCH_PTBR_RISKS:
            if not risk.source_pattern.search(source_visible):
                continue
            if risk.code == "FRENCH_CES_DEMONSTRATIVE_UNTRANSLATED":
                has_source_demonstrative = any(
                    _source_ces_is_demonstrative(source_visible, match)
                    for match in _FRENCH_CES_TOKEN.finditer(source_visible)
                )
                if (
                    has_source_demonstrative
                    and _french_ces_output_has_unmatched_residue(source_visible, output_visible)
                ):
                    flags.append(risk.code)
            elif risk.bad_output_pattern.search(output_visible):
                flags.append(risk.code)

    for name in protected_names:
        if _has_word(source_visible, name) and not _has_word(output_visible, name):
            flags.append(f"PROTECTED_NAME_NOT_PRESERVED:{name}")

    return tuple(dict.fromkeys(flags))


def is_portuguese_target_language(target_language: str | None) -> bool:
    normalized_target = str(target_language or "").strip().casefold().replace("_", "-")
    return normalized_target in {
        "pt-br",
        "português do brasil",
        "portugues do brasil",
        "português do brasil (pt-br)",
        "portugues do brasil (pt-br)",
    }


_is_portuguese_target_language = is_portuguese_target_language


def normalize_translated_punctuation_spacing(
    text: str,
    source_language: str | None,
    target_language: str | None,
) -> str:
    """Remove French pre-punctuation spaces from French-to-Portuguese output."""
    if (
        _canonical_source_language(source_language) != "french"
        or not _is_portuguese_target_language(target_language)
    ):
        return text
    # Deliberately exclude newlines: ASS event line breaks are structural data.
    return re.sub(r"[ \t\u00a0\u202f]+([?!;:])", r"\1", text)


_normalize_translated_punctuation_spacing = normalize_translated_punctuation_spacing


def is_french_name_call_exchange(
    source: str,
    translated: str,
    protected_names: Iterable[str],
    target_language: str,
) -> bool:
    """Allow exact pt-BR identity only for a dash-prefixed name exchange."""
    if not _is_portuguese_target_language(target_language):
        return False
    normalized_source = _normalize_translated_punctuation_spacing(
        visible_text(str(source or "")).replace("\r\n", "\n").replace("\r", "\n"),
        "francês", target_language,
    ).strip()
    normalized_translation = _normalize_translated_punctuation_spacing(
        visible_text(str(translated or "")).replace("\r\n", "\n").replace("\r", "\n"),
        "francês", target_language,
    ).strip()
    if not normalized_source or normalized_translation != normalized_source:
        return False

    protected_tokens = {
        token.casefold().replace("’", "'")
        for name in protected_names
        for token in re.findall(r"[^\W\d_]+", str(name), re.UNICODE)
    }
    if not protected_tokens:
        return False

    name_line = re.compile(
        r"^\s*[-–—]\s*([^\W\d_]+(?:['’\-][^\W\d_]+)*)\s*[.!?…]*\s*$",
        re.UNICODE,
    )
    tokens: list[str] = []
    for line in normalized_source.split("\n"):
        if not line.strip():
            continue
        match = name_line.fullmatch(line)
        if not match:
            return False
        token = match.group(1)
        normalized = token.casefold().replace("’", "'")
        if is_french_name_stopword(token):
            return False
        if normalized not in protected_tokens and not is_likely_romanized_japanese_name_token(token):
            return False
        tokens.append(normalized)
    return len(tokens) >= 2 and any(token in protected_tokens for token in tokens)


_is_french_name_call_exchange = is_french_name_call_exchange


def is_french_protected_name_only_identity(
    source: str,
    translated: str,
    protected_names: Iterable[str],
    target_language: str,
) -> bool:
    """Allow exact identity only for protected or strongly romanized names."""
    if not _is_portuguese_target_language(target_language):
        return False
    visible_source = visible_text(str(source or "")).replace("\r\n", "\n").replace("\r", "\n")
    visible_translation = visible_text(str(translated or "")).replace("\r\n", "\n").replace("\r", "\n")
    normalized_source = _normalize_translated_punctuation_spacing(
        visible_source, "francês", target_language,
    ).strip()
    normalized_translation = _normalize_translated_punctuation_spacing(
        visible_translation, "francês", target_language,
    ).strip()
    if not normalized_source or normalized_translation != normalized_source:
        return False

    protected_tokens = {
        token.casefold().replace("’", "'")
        for name in protected_names
        for token in re.findall(r"[\wÀ-ÿ]+(?:['’][\wÀ-ÿ]+)?", str(name), re.UNICODE)
    }
    visible_tokens = re.findall(
        r"[\wÀ-ÿ]+(?:['’][\wÀ-ÿ]+)?", normalized_source, flags=re.UNICODE,
    )
    normalized_tokens = {
        token.casefold().replace("’", "'") for token in visible_tokens
    }
    has_ellipsis = bool(re.search(r"\.{2,}|…", normalized_source))
    single_unanchored_name = (
        len(visible_tokens) == 1
        and not normalized_tokens.intersection(protected_tokens)
        and not is_french_name_stopword(visible_tokens[0])
        and (
            is_reviewed_romanized_japanese_name_token(visible_tokens[0])
            or is_likely_romanized_japanese_name_token(visible_tokens[0])
            or (not has_ellipsis and is_clean_romaji_token(visible_tokens[0]))
        )
    )
    all_clean_romaji = (
        1 <= len(visible_tokens) <= 4
        and not has_ellipsis
        and all(
            not is_french_name_stopword(token)
            and (
                is_clean_romaji_token(token)
                or token.casefold().replace("’", "'") in protected_tokens
            )
            for token in visible_tokens
        )
    )
    has_protected_name_anchor = bool(normalized_tokens.intersection(protected_tokens))
    return bool(visible_tokens) and (
        single_unanchored_name
        or all_clean_romaji
        or (
            has_protected_name_anchor
            and all(
                not is_french_name_stopword(token)
                and (
                    token.casefold().replace("’", "'") in protected_tokens
                    or is_likely_romanized_japanese_name_token(token)
                    or (not has_ellipsis and is_clean_romaji_token(token))
                )
                for token in visible_tokens
            )
        )
    )


_is_french_protected_name_only_identity = is_french_protected_name_only_identity


def is_french_protected_name_fragment_identity(
    source: str,
    translated: str,
    contextually_protected_names: Iterable[str],
    target_language: str,
) -> bool:
    """Allow exact identity only after the progressive-source gate approved it."""
    if not _is_portuguese_target_language(target_language):
        return False
    visible_source = visible_text(str(source or "")).replace("\r\n", "\n").replace("\r", "\n")
    visible_translation = visible_text(str(translated or "")).replace("\r\n", "\n").replace("\r", "\n")
    normalized_source = _normalize_translated_punctuation_spacing(
        visible_source, "francês", target_language,
    ).strip()
    normalized_translation = _normalize_translated_punctuation_spacing(
        visible_translation, "francês", target_language,
    ).strip()
    if not normalized_source or normalized_translation != normalized_source:
        return False

    match = re.fullmatch(
        r"([A-ZÀ-ÖØ-Þ][a-zà-öø-ÿ]{1,})[ \t]*(?:\.{3,}|…)",
        normalized_source,
        flags=re.UNICODE,
    )
    if not match:
        return False
    fragment = match.group(1)
    if is_french_name_stopword(fragment):
        return False

    normalized_fragment = fragment.casefold().replace("’", "'")
    protected_tokens = {
        token.casefold().replace("’", "'")
        for name in contextually_protected_names
        for token in re.findall(r"[\wÀ-ÿ]+(?:['’][\wÀ-ÿ]+)?", str(name), re.UNICODE)
    }
    matching_protected_tokens = {
        token for token in protected_tokens
        if len(token) > len(normalized_fragment) and token.startswith(normalized_fragment)
    }
    return len(matching_protected_tokens) == 1


_is_french_protected_name_fragment_identity = is_french_protected_name_fragment_identity


def is_french_interjection_title_name_call(
    source: str,
    translated: str,
    protected_names: Iterable[str],
    target_language: str,
) -> bool:
    """Allow vocative calls composed only of shared interjections, titles, and Japanese names.

    For example: 'Ah, Dr Ozaki !' -> 'Ah, Dr. Ozaki!' or 'Dr Ozaki !' -> 'Dr. Ozaki!'.
    Both Portuguese and French share 'Ah', 'Oh', and the abbreviation 'Dr' or 'Dr.'.
    No clause, sentence, or French-specific lexical material is exempted.
    """
    if not _is_portuguese_target_language(target_language):
        return False

    words = re.findall(r"[\wÀ-ÿ]+(?:['’][\wÀ-ÿ]+)?", source, flags=re.UNICODE)
    output_words = re.findall(r"[\wÀ-ÿ]+(?:['’][\wÀ-ÿ]+)?", translated, flags=re.UNICODE)
    normalized_source = [word.casefold().replace("’", "'") for word in words]
    normalized_output = [word.casefold().replace("’", "'") for word in output_words]
    if not normalized_source or normalized_source != normalized_output:
        return False
    if len(normalized_source) > 4:
        return False

    shared_interjections = frozenset({
        "ah", "oh", "eh", "ei", "hein", "ouais", "psiu", "shh", "chut",
    })
    shared_titles = frozenset({"dr", "dra", "prof", "m"})
    protected_tokens = {
        token.casefold().replace("’", "'")
        for name in protected_names
        for token in re.findall(r"[\wÀ-ÿ]+(?:['’][\wÀ-ÿ]+)?", str(name), re.UNICODE)
    }
    has_title = False
    has_name = False
    for token in normalized_source:
        if token in shared_titles:
            has_title = True
            continue
        if token in shared_interjections:
            continue
        if not is_french_name_stopword(token) and (
            token in protected_tokens
            or is_reviewed_romanized_japanese_name_token(token)
            or is_likely_romanized_japanese_name_token(token)
            or is_clean_romaji_token(token)
        ):
            has_name = True
            continue
        return False
    return has_title and has_name


_is_french_interjection_title_name_call = is_french_interjection_title_name_call


def is_french_repeated_name_call(
    source: str,
    translated: str,
    target_language: str,
) -> bool:
    """Allow exact identity only for a repeated, otherwise empty name call."""
    if not _is_portuguese_target_language(target_language):
        return False

    visible_source = visible_text(str(source or "")).replace("\r\n", "\n").replace("\r", "\n")
    visible_translation = visible_text(str(translated or "")).replace("\r\n", "\n").replace("\r", "\n")
    normalized_source = _normalize_translated_punctuation_spacing(
        visible_source, "francês", target_language,
    ).strip()
    normalized_translation = _normalize_translated_punctuation_spacing(
        visible_translation, "francês", target_language,
    ).strip()
    if not normalized_source or normalized_translation != normalized_source:
        return False

    name_line = re.compile(
        r"^\s*(?:[-–—][ \t]+)?([A-ZÀ-ÖØ-Þ][a-zà-öø-ÿ]{1,})"
        r"[ \t]*[.!?…]*[ \t]*$",
        re.UNICODE,
    )
    tokens: list[str] = []
    for line in normalized_source.split("\n"):
        match = name_line.fullmatch(line)
        if not match:
            return False
        tokens.append(match.group(1))

    if len(tokens) < 2 or len({token.casefold() for token in tokens}) != 1:
        return False
    return is_reviewed_romanized_japanese_name_token(tokens[0])


_is_french_repeated_name_call = is_french_repeated_name_call


def is_french_de_protected_name_phrase(
    source: str,
    translated: str,
    protected_names: Iterable[str],
    target_language: str,
) -> bool:
    """Allow only exact French/pt-BR identity for ``de`` plus one known name."""
    if not _is_portuguese_target_language(target_language):
        return False
    normalized_source = _normalize_translated_punctuation_spacing(
        source, "francês", target_language,
    ).strip()
    normalized_translation = str(translated or "").strip()
    if not normalized_source or normalized_translation != normalized_source:
        return False
    match = re.fullmatch(
        r"(?:[-–—][ \t\u00a0\u202f]*)?de(?:[ \t\u00a0\u202f]+|\r?\n)"
        r"([^\W\d_]+)[ \t\u00a0\u202f]*[.!?…]?",
        normalized_source,
        flags=re.IGNORECASE | re.UNICODE,
    )
    if not match:
        return False
    name = match.group(1)
    protected_tokens = {
        token.casefold().replace("’", "'")
        for protected_name in protected_names
        for token in re.findall(r"[^\W\d_]+", str(protected_name), re.UNICODE)
    }
    normalized_name = name.casefold().replace("’", "'")
    distinctive_romaji_marker = any(
        marker in normalized_name
        for marker in ("sh", "ts", "ky", "gy", "ny", "hy", "my", "ry", "by", "py")
    ) or re.search(r"[âîûêôāīūēō]", name, flags=re.IGNORECASE) is not None
    return (
        normalized_name in protected_tokens
        and distinctive_romaji_marker
        and is_likely_romanized_japanese_name_token(name)
    )


_is_french_de_protected_name_phrase = is_french_de_protected_name_phrase


def is_french_de_single_name_shape(source: str) -> bool:
    """Recognize copied ``de + one token`` forms, including ASS line breaks."""
    return re.fullmatch(
        r"[ \t\u00a0\u202f]*(?:[-–—][ \t\u00a0\u202f]*)?de"
        r"(?:[ \t\u00a0\u202f]+|\r?\n)[^\W\d_]+"
        r"[ \t\u00a0\u202f]*[:;]?[ \t\u00a0\u202f]*",
        str(source or ""),
        flags=re.IGNORECASE | re.UNICODE,
    ) is not None

_is_french_de_single_name_shape = is_french_de_single_name_shape
