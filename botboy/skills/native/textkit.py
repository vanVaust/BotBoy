"""Pure, dependency-free text analytics shared by the ``text`` and ``ws`` skills.

Algorithms are classic and deterministic so results are auditable:

* keywords: RAKE (Rose et al., 2010) — candidate phrases are split at stop
  words and punctuation, word score = degree / frequency, phrase score = sum;
* summaries: Luhn-style extractive scoring — sentences are ranked by the
  normalised frequency of their content words with a small lead bias, and the
  selected sentences are returned in their original order;
* readability: Flesch Reading Ease (English) or Amstad's adaptation (German),
  using a vowel-group syllable heuristic — an estimate, not a measurement.
"""
from __future__ import annotations

import difflib
import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Dict, List, Sequence, Tuple

STOPWORDS_EN = frozenset("""
a about above after again against all also am an and any are aren't as at be because been before being
below between both but by can can't cannot could couldn't did didn't do does doesn't doing don't down
during each either else ever every few for from further get gets got had hadn't has hasn't have haven't
having he he'd he'll he's her here here's hers herself him himself his how how's however i i'd i'll i'm
i've if in into is isn't it it's its itself just let's may me might more most much must mustn't my myself
need needs neither no nor not now of off often on once one only or other others otherwise ought our ours ourselves out
over own per quite rather same shall shan't she she'd she'll she's should shouldn't since so some such
than that that's the their theirs them themselves then there there's these they they'd they'll they're
they've this those though through thus to too toward under until up upon us use used using very via was
wasn't we we'd we'll we're we've were weren't what what's when when's where where's whether which while who
who's whom whose why why's will with within without won't would wouldn't yet you you'd you'll you're you've
your yours yourself yourselves
""".split())

STOPWORDS_DE = frozenset("""
aber alle allem allen aller alles als also am an ander andere anderem anderen anderer anderes anders auch
auf aus bei beim bin bis bist da dabei dadurch dafür dagegen daher damit dann darauf darum das dass dasselbe
dazu dein deine deinem deinen deiner dem demselben den denn denselben der derer derselbe derselben des
desselben dessen dich die dies diese dieselbe dieselben diesem diesen dieser dieses dir doch dort du durch
ein eine einem einen einer eines einig einige einigem einigen einiger einiges einmal er es etwas euch euer
eure eurem euren eurer für gegen gewesen hab habe haben hat hatte hatten hier hin hinter ich ihm ihn ihnen
ihr ihre ihrem ihren ihrer ihres im in indem ins ist jede jedem jeden jeder jedes jene jenem jenen jener
jenes jetzt kann kein keine keinem keinen keiner keines können könnte machen man manche manchem manchen
mancher manches mein meine meinem meinen meiner meines mich mir mit muss musste nach nicht nichts noch nun
nur ob oder ohne sehr sein seine seinem seinen seiner seines selbst sich sie sind so solche solchem solchen
solcher solches soll sollte sondern sonst über um und uns unser unsere unserem unseren unserer unter viel
vom von vor wann war waren warum was weg weil weiter welche welchem welchen welcher welches wenn wer werde
werden wie wieder will wir wird wirst wo wollen wollte während würde würden zu zum zur zwar zwischen
""".split())

STOPWORDS = STOPWORDS_EN | STOPWORDS_DE

_ABBREVIATIONS = frozenset({
    "e.g", "i.e", "etc", "vs", "mr", "mrs", "ms", "dr", "prof", "st", "jr", "sr", "fig", "no", "inc",
    "ltd", "co", "approx", "z.b", "bzw", "usw", "ca", "vgl", "nr", "s", "u.a", "d.h", "evtl", "ggf",
    "inkl", "bzgl", "max", "min", "str",
})
_WORD_RE = re.compile(r"[^\W\d_](?:[^\W_]|['’\-](?=[^\W\d_]))*", re.UNICODE)
_TOKEN_RE = re.compile(r"[^\W_]+(?:['’\-][^\W_]+)*|[.,;:!?()\[\]{}\"“”„«»…–—]", re.UNICODE)
_BOUNDARY_RE = re.compile(r"([.!?…]+)([\"'”»)\]]*)(\s+)")
_VOWEL_GROUP_RE = re.compile(r"[aeiouyäöüàâéèêëîïôûù]+", re.IGNORECASE)
_PHRASE_BREAKERS = frozenset(".,;:!?()[]{}\"“”„«»…–—")


def words(text: str) -> List[str]:
    return _WORD_RE.findall(text)


def sentence_spans(text: str) -> List[Tuple[int, int]]:
    """Return (start, end) offsets of sentences in ``text``.

    Blank lines always end a sentence; terminal punctuation ends one unless it
    belongs to a common EN/DE abbreviation or an initial, or the next word is
    lower-case.
    """
    spans: List[Tuple[int, int]] = []
    for block in re.finditer(r"\S.*?(?=\n[ \t\r\f\v]*\n|\Z)", text, re.DOTALL):
        offset = block.start()
        body = block.group(0)
        start = 0
        for match in _BOUNDARY_RE.finditer(body):
            end = match.end(2)
            candidate = body[start:end].strip()
            last_word = candidate.rstrip(".!?…\"'”»)]").split()[-1].lower().rstrip(".") if candidate.split() else ""
            next_char = body[match.end():match.end() + 1]
            is_abbreviation = match.group(1) == "." and (
                last_word in _ABBREVIATIONS or (len(last_word) == 1 and last_word.isalpha())
            )
            if is_abbreviation or (next_char and next_char.islower()):
                continue
            if candidate:
                spans.append((offset + start, offset + end))
            start = match.end()
        tail = body[start:].rstrip()
        if tail.strip():
            spans.append((offset + start, offset + start + len(tail)))
    return spans


def split_sentences(text: str) -> List[str]:
    return [" ".join(text[start:end].split()) for start, end in sentence_spans(text)]


def detect_language(text: str) -> str:
    tokens = [token.lower() for token in words(text)]
    en = sum(1 for token in tokens if token in STOPWORDS_EN)
    de = sum(1 for token in tokens if token in STOPWORDS_DE)
    if en == de == 0:
        return "unknown"
    return "de" if de > en else "en"


def count_syllables(word: str) -> int:
    lowered = word.lower()
    groups = len(_VOWEL_GROUP_RE.findall(lowered))
    if lowered.endswith("e") and not lowered.endswith(("le", "ee")) and groups > 1:
        groups -= 1
    return max(1, groups)


@dataclass(frozen=True)
class TextStats:
    characters: int
    characters_no_spaces: int
    words: int
    unique_words: int
    sentences: int
    paragraphs: int
    avg_word_length: float
    avg_sentence_words: float
    lexical_diversity: float
    reading_time_minutes: float
    language: str
    readability: float
    readability_scale: str

    def to_dict(self) -> Dict[str, object]:
        return dict(self.__dict__)


def text_stats(text: str) -> TextStats:
    tokens = words(text)
    sentences = split_sentences(text) or ([text.strip()] if text.strip() else [])
    paragraphs = [block for block in re.split(r"\n\s*\n", text) if block.strip()]
    word_count = len(tokens)
    sentence_count = max(len(sentences), 1 if word_count else 0)
    unique = len({token.lower() for token in tokens})
    language = detect_language(text)
    readability = 0.0
    scale = "n/a"
    if word_count and sentence_count:
        asl = word_count / sentence_count
        asw = sum(count_syllables(token) for token in tokens) / word_count
        if language == "de":
            readability = 180 - asl - 58.5 * asw
            scale = "Amstad (DE, 0-100, higher is easier)"
        else:
            readability = 206.835 - 1.015 * asl - 84.6 * asw
            scale = "Flesch (EN, 0-100, higher is easier)"
    return TextStats(
        characters=len(text),
        characters_no_spaces=len(re.sub(r"\s", "", text)),
        words=word_count,
        unique_words=unique,
        sentences=sentence_count,
        paragraphs=len(paragraphs),
        avg_word_length=round(sum(len(token) for token in tokens) / word_count, 2) if word_count else 0.0,
        avg_sentence_words=round(word_count / sentence_count, 2) if sentence_count else 0.0,
        lexical_diversity=round(unique / word_count, 3) if word_count else 0.0,
        reading_time_minutes=round(word_count / 230, 2),
        language=language,
        readability=round(readability, 1),
        readability_scale=scale,
    )


@dataclass(frozen=True)
class Keyword:
    phrase: str
    score: float
    frequency: int


def extract_keywords(text: str, top: int = 10, *, max_phrase_words: int = 4) -> List[Keyword]:
    """RAKE keyword extraction with EN+DE stop words."""
    phrases: List[List[str]] = []
    current: List[str] = []
    for token in _TOKEN_RE.findall(text):
        lowered = token.lower()
        if token in _PHRASE_BREAKERS or lowered in STOPWORDS or not _WORD_RE.fullmatch(token) or len(token) < 2:
            if current:
                phrases.append(current)
            current = []
            continue
        current.append(lowered)
        if len(current) >= max_phrase_words:
            phrases.append(current)
            current = []
    if current:
        phrases.append(current)
    if not phrases:
        return []
    frequency: Counter = Counter()
    degree: Counter = Counter()
    for phrase in phrases:
        for word in phrase:
            frequency[word] += 1
            degree[word] += len(phrase)
    word_score = {word: degree[word] / frequency[word] for word in frequency}
    phrase_counts: Counter = Counter(" ".join(phrase) for phrase in phrases)
    scored = {
        phrase: sum(word_score[word] for word in phrase.split())
        for phrase in phrase_counts
    }
    # Repeated phrases matter more than a single long run of rare words.
    ranked = sorted(
        scored.items(),
        key=lambda item: (item[1] * (1 + math.log(phrase_counts[item[0]])), phrase_counts[item[0]], item[0]),
        reverse=True,
    )
    return [
        Keyword(phrase=phrase, score=round(score * (1 + math.log(phrase_counts[phrase])), 3), frequency=phrase_counts[phrase])
        for phrase, score in ranked[:top]
    ]


def term_frequencies(text: str, top: int = 10) -> List[Tuple[str, int]]:
    counts = Counter(
        token.lower() for token in words(text) if token.lower() not in STOPWORDS and len(token) > 2
    )
    return counts.most_common(top)


@dataclass(frozen=True)
class Summary:
    sentences: List[str]
    indices: List[int]
    total_sentences: int


def summarize(text: str, max_sentences: int = 3) -> Summary:
    sentences = split_sentences(text)
    if len(sentences) <= max_sentences:
        return Summary(sentences=sentences, indices=list(range(len(sentences))), total_sentences=len(sentences))
    content_counts: Counter = Counter(
        token.lower() for token in words(text) if token.lower() not in STOPWORDS and len(token) > 2
    )
    if not content_counts:
        chosen = list(range(max_sentences))
        return Summary(sentences=[sentences[i] for i in chosen], indices=chosen, total_sentences=len(sentences))
    peak = max(content_counts.values())
    weights = {word: count / peak for word, count in content_counts.items()}
    scores: List[Tuple[float, int]] = []
    for index, sentence in enumerate(sentences):
        content = [token.lower() for token in words(sentence) if token.lower() in weights]
        if len(words(sentence)) < 4 or not content:
            scores.append((0.0, index))
            continue
        score = sum(weights[token] for token in content) / math.sqrt(len(content))
        if index == 0:
            score *= 1.15
        scores.append((score, index))
    chosen = sorted(index for _, index in sorted(scores, key=lambda item: (-item[0], item[1]))[:max_sentences])
    return Summary(sentences=[sentences[i] for i in chosen], indices=chosen, total_sentences=len(sentences))


@dataclass(frozen=True)
class Chunk:
    index: int
    start: int
    end: int
    text: str


def chunk_text(text: str, max_chars: int = 1200, overlap_chars: int = 150) -> List[Chunk]:
    """Greedy sentence-aware chunking with sentence-level overlap.

    Chunks never cut a sentence unless the sentence alone exceeds
    ``max_chars``; then it is split at word boundaries. Consecutive chunks
    share the trailing sentences of the previous chunk that fit into
    ``overlap_chars``, which keeps context for retrieval without duplication.
    """
    if max_chars < 50:
        raise ValueError("max_chars must be >= 50")
    if not 0 <= overlap_chars < max_chars:
        raise ValueError("overlap_chars must be >= 0 and smaller than max_chars")
    units: List[Tuple[int, int]] = []
    for start, end in sentence_spans(text):
        while end - start > max_chars:
            cut = text.rfind(" ", start + 1, start + max_chars)
            cut = cut if cut > start else start + max_chars
            units.append((start, cut))
            start = cut
            while start < end and text[start].isspace():
                start += 1
        if end > start:
            units.append((start, end))
    chunks: List[Chunk] = []
    current: List[Tuple[int, int]] = []
    for unit in units:
        if current and unit[1] - current[0][0] > max_chars:
            chunks.append(Chunk(len(chunks), current[0][0], current[-1][1], text[current[0][0]:current[-1][1]]))
            carried = [span for span in current if current[-1][1] - span[0] <= overlap_chars]
            current = carried if carried and unit[1] - carried[0][0] <= max_chars else []
        current.append(unit)
    if current:
        chunks.append(Chunk(len(chunks), current[0][0], current[-1][1], text[current[0][0]:current[-1][1]]))
    return chunks


@dataclass(frozen=True)
class DiffResult:
    unified: str
    added_lines: int
    removed_lines: int
    similarity: float
    inline: str


def diff_texts(a: str, b: str, *, a_label: str = "a", b_label: str = "b", context: int = 3) -> DiffResult:
    a_lines = a.splitlines()
    b_lines = b.splitlines()
    unified_lines = list(difflib.unified_diff(a_lines, b_lines, fromfile=a_label, tofile=b_label, n=context, lineterm=""))
    added = sum(1 for line in unified_lines if line.startswith("+") and not line.startswith("+++"))
    removed = sum(1 for line in unified_lines if line.startswith("-") and not line.startswith("---"))
    matcher = difflib.SequenceMatcher(None, a_lines, b_lines, autojunk=len(a_lines) > 200)
    similarity = matcher.ratio() if (a_lines or b_lines) else 1.0
    inline = ""
    if len(a_lines) <= 1 and len(b_lines) <= 1:
        a_words, b_words = a.split(), b.split()
        word_matcher = difflib.SequenceMatcher(None, a_words, b_words)
        pieces: List[str] = []
        for tag, i1, i2, j1, j2 in word_matcher.get_opcodes():
            if tag == "equal":
                pieces.extend(a_words[i1:i2])
                continue
            if tag in ("replace", "delete"):
                pieces.append("[-" + " ".join(a_words[i1:i2]) + "-]")
            if tag in ("replace", "insert"):
                pieces.append("{+" + " ".join(b_words[j1:j2]) + "+}")
        inline = " ".join(pieces)
        similarity = word_matcher.ratio() if (a_words or b_words) else 1.0
    return DiffResult(
        unified="\n".join(unified_lines),
        added_lines=added,
        removed_lines=removed,
        similarity=round(similarity, 4),
        inline=inline,
    )


_MD_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
_MD_LINK_RE = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_MD_PREFIX_RE = re.compile(r"^\s{0,3}(?:#{1,6}\s+|>\s?|[-*+]\s+|\d+[.)]\s+)", re.MULTILINE)


def strip_markup(text: str, suffix: str = "") -> str:
    """Remove Markdown syntax that would otherwise pollute sentence scoring."""
    if suffix.lower() not in {".md", ".markdown", ".rst", ".txt", ""}:
        return text
    text = _MD_FENCE_RE.sub("\n", text)
    text = _MD_LINK_RE.sub(r"\1", text)
    text = _MD_PREFIX_RE.sub("", text)
    return re.sub(r"[*_`]{1,3}", "", text)


def render_keywords(keywords: Sequence[Keyword]) -> str:
    if not keywords:
        return "No keywords found."
    width = max(len(keyword.phrase) for keyword in keywords)
    return "\n".join(
        f"{idx + 1:>2}. {keyword.phrase.ljust(width)}  score={keyword.score:<7} freq={keyword.frequency}"
        for idx, keyword in enumerate(keywords)
    )


def render_stats(stats: TextStats) -> str:
    return "\n".join([
        f"Words: {stats.words} (unique {stats.unique_words}, diversity {stats.lexical_diversity})",
        f"Sentences: {stats.sentences}  Paragraphs: {stats.paragraphs}",
        f"Characters: {stats.characters} ({stats.characters_no_spaces} without whitespace)",
        f"Avg word length: {stats.avg_word_length}  Avg sentence: {stats.avg_sentence_words} words",
        f"Language guess: {stats.language}  Reading time: {stats.reading_time_minutes} min",
        f"Readability: {stats.readability} [{stats.readability_scale}]",
    ])
