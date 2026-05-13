"""Reusable rule definitions for deterministic QA extraction."""

from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata


@dataclass(frozen=True, slots=True)
class CueRule:
    """One lexical rule used by the QA heuristics."""

    pattern: re.Pattern[str]
    reason_code: str
    label: str
    weight: float


QUESTION_CUE_RULES: tuple[CueRule, ...] = (
    CueRule(
        re.compile(r"\bwhat happens if\b"),
        "cue_en_what_happens_if",
        "conditional",
        0.28,
    ),
    CueRule(
        re.compile(r"\bwhat does it mean\b"),
        "cue_en_what_does_it_mean",
        "meaning",
        0.26,
    ),
    CueRule(
        re.compile(r"\bwhat is the difference\b"),
        "cue_en_what_is_the_difference",
        "difference",
        0.26,
    ),
    CueRule(
        re.compile(r"\bcosa succede se\b"),
        "cue_it_cosa_succede_se",
        "conditional",
        0.28,
    ),
    CueRule(
        re.compile(r"\bin che senso\b"),
        "cue_it_in_che_senso",
        "meaning",
        0.24,
    ),
    CueRule(
        re.compile(r"\bche differenza c[' ]?e\b"),
        "cue_it_che_differenza_ce",
        "difference",
        0.26,
    ),
    CueRule(
        re.compile(r"\bche cos[' ]?e\b"),
        "cue_it_che_cose",
        "what",
        0.24,
    ),
    CueRule(
        re.compile(r"\bcos[' ]?e\b"),
        "cue_it_cose",
        "what",
        0.22,
    ),
    CueRule(re.compile(r"\bwhat\b"), "cue_en_what", "what", 0.18),
    CueRule(re.compile(r"\bwhy\b"), "cue_en_why", "why", 0.18),
    CueRule(re.compile(r"\bhow\b"), "cue_en_how", "how", 0.18),
    CueRule(re.compile(r"\bwhen\b"), "cue_en_when", "when", 0.18),
    CueRule(re.compile(r"\bwhere\b"), "cue_en_where", "where", 0.18),
    CueRule(re.compile(r"\bwhich\b"), "cue_en_which", "which", 0.18),
    CueRule(re.compile(r"\bwho\b"), "cue_en_who", "who", 0.18),
    CueRule(re.compile(r"\bperche\b"), "cue_it_perche", "why", 0.18),
    CueRule(re.compile(r"\bcome\b"), "cue_it_come", "how", 0.18),
    CueRule(re.compile(r"\bquando\b"), "cue_it_quando", "when", 0.18),
    CueRule(re.compile(r"\bdove\b"), "cue_it_dove", "where", 0.18),
    CueRule(re.compile(r"\bquale\b"), "cue_it_quale", "which", 0.18),
    CueRule(re.compile(r"\bquali\b"), "cue_it_quali", "which", 0.18),
    CueRule(re.compile(r"\bchi\b"), "cue_it_chi", "who", 0.18),
    CueRule(re.compile(r"\bquanto\b"), "cue_it_quanto", "quantity", 0.16),
)


DIDACTIC_QUESTION_RULES: tuple[CueRule, ...] = (
    CueRule(
        re.compile(r"\bla domanda e\b"),
        "didactic_it_la_domanda_e",
        "didactic_prompt",
        0.30,
    ),
    CueRule(
        re.compile(r"\bci chiediamo\b"),
        "didactic_it_ci_chiediamo",
        "didactic_prompt",
        0.28,
    ),
    CueRule(
        re.compile(r"\bci possiamo chiedere\b"),
        "didactic_it_ci_possiamo_chiedere",
        "didactic_prompt",
        0.28,
    ),
    CueRule(
        re.compile(r"\bthe question is\b"),
        "didactic_en_the_question_is",
        "didactic_prompt",
        0.30,
    ),
    CueRule(
        re.compile(r"\bwe may ask\b"),
        "didactic_en_we_may_ask",
        "didactic_prompt",
        0.28,
    ),
)


ANSWER_CUE_RULES: tuple[CueRule, ...] = (
    CueRule(
        re.compile(r"\bthe answer is\b"),
        "answer_en_the_answer_is",
        "direct_answer",
        0.24,
    ),
    CueRule(
        re.compile(r"\bthe difference is\b"),
        "answer_en_the_difference_is",
        "difference",
        0.22,
    ),
    CueRule(
        re.compile(r"\bthe reason is\b"),
        "answer_en_the_reason_is",
        "explanation",
        0.24,
    ),
    CueRule(
        re.compile(r"\bin other words\b"),
        "answer_en_in_other_words",
        "rephrase",
        0.20,
    ),
    CueRule(
        re.compile(r"\bthat'?s how many\b"),
        "answer_en_thats_how_many",
        "direct_answer",
        0.24,
    ),
    CueRule(
        re.compile(r"\bit happens at\b"),
        "answer_en_it_happens_at",
        "direct_answer",
        0.22,
    ),
    CueRule(
        re.compile(r"\bbecause\b"),
        "answer_en_because",
        "explanation",
        0.18,
    ),
    CueRule(
        re.compile(r"\btherefore\b"),
        "answer_en_therefore",
        "conclusion",
        0.18,
    ),
    CueRule(re.compile(r"\bso\b"), "answer_en_so", "conclusion", 0.08),
    CueRule(
        re.compile(r"\bla ragione e\b"),
        "answer_it_la_ragione_e",
        "explanation",
        0.24,
    ),
    CueRule(
        re.compile(r"\bla risposta e\b"),
        "answer_it_la_risposta_e",
        "direct_answer",
        0.24,
    ),
    CueRule(
        re.compile(r"\bla differenza e\b"),
        "answer_it_la_differenza_e",
        "difference",
        0.22,
    ),
    CueRule(
        re.compile(r"\bil punto e\b"),
        "answer_it_il_punto_e",
        "explanation",
        0.18,
    ),
    CueRule(re.compile(r"\bperche\b"), "answer_it_perche", "explanation", 0.18),
    CueRule(re.compile(r"\bquindi\b"), "answer_it_quindi", "conclusion", 0.18),
    CueRule(re.compile(r"\ballora\b"), "answer_it_allora", "transition", 0.10),
    CueRule(re.compile(r"\binfatti\b"), "answer_it_infatti", "support", 0.18),
    CueRule(re.compile(r"\bcioe\b"), "answer_it_cioe", "rephrase", 0.16),
)


INTERROGATIVE_START_WORDS = {
    "what",
    "why",
    "how",
    "when",
    "where",
    "which",
    "who",
    "perche",
    "come",
    "quando",
    "dove",
    "quale",
    "quali",
    "chi",
    "quanto",
    "cosa",
    "cos'e",
    "che",
}


DECLARATIVE_WHAT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^\s*that'?s what\b"),
    re.compile(r"^\s*what it means\b"),
    re.compile(r"\bwhat we call\b"),
    re.compile(r"^\s*what happens is\b"),
)


TOKEN_RE = re.compile(r"\b[\w']+\b", flags=re.UNICODE)


def normalize_rule_text(text: str) -> str:
    """Return a normalized representation used by lexical rules."""

    normalized = text.replace("’", "'").replace("`", "'").lower()
    normalized = unicodedata.normalize("NFKD", normalized)
    normalized = normalized.encode("ascii", "ignore").decode("ascii")
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip()


def count_tokens(text: str) -> int:
    """Return a lightweight token count for rule heuristics."""

    return len(TOKEN_RE.findall(text))


def collect_rule_matches(text: str, rules: tuple[CueRule, ...]) -> list[CueRule]:
    """Return all rules whose pattern matches the normalized text."""

    return [rule for rule in rules if rule.pattern.search(text)]
