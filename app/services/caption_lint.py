from __future__ import annotations

import re
from dataclasses import dataclass

from app.models import TrainingDataset


EYE_DESCRIPTORS = frozenset(
    {
        "amber eyes",
        "amber-eyed",
        "blue eyes",
        "blue-eyed",
        "brown eyes",
        "brown-eyed",
        "gray eyes",
        "gray-eyed",
        "green eyes",
        "green-eyed",
        "grey eyes",
        "grey-eyed",
        "hazel eyes",
        "hazel-eyed",
    }
)
HAIR_DESCRIPTORS = frozenset(
    {
        "auburn hair",
        "black hair",
        "blond hair",
        "blonde hair",
        "brown hair",
        "curly hair",
        "dark hair",
        "gray hair",
        "grey hair",
        "light hair",
        "long hair",
        "red hair",
        "short hair",
        "straight hair",
        "wavy hair",
        "white hair",
    }
)
FACIAL_DESCRIPTORS = frozenset(
    {
        "cheek",
        "cheekbone",
        "cheekbones",
        "cheeks",
        "freckle",
        "freckles",
        "jaw",
        "jawline",
        "lip",
        "lips",
        "nose",
        "skin",
    }
)
ETHNICITY_DESCRIPTORS = frozenset(
    {
        "african person",
        "asian",
        "black person",
        "caucasian",
        "chinese",
        "east asian",
        "filipino",
        "hispanic",
        "indian",
        "indigenous",
        "japanese",
        "korean",
        "latina",
        "latino",
        "middle eastern",
        "native american",
        "pacific islander",
        "south asian",
        "southeast asian",
        "white person",
    }
)
IDENTITY_LEAK_TERMS = frozenset(
    EYE_DESCRIPTORS
    | HAIR_DESCRIPTORS
    | FACIAL_DESCRIPTORS
    | ETHNICITY_DESCRIPTORS
)
OTHER_PEOPLE_TERMS = frozenset(
    {
        "another man",
        "another woman",
        "couple",
        "group",
        "two people",
    }
)
TEXT_ARTIFACT_TERMS = frozenset(
    {
        "caption",
        "logo",
        "subtitle",
        "text",
        "watermark",
    }
)

_WORD_RE = re.compile(r"\b[\w'-]+\b", re.UNICODE)


@dataclass(frozen=True)
class CaptionLintFinding:
    code: str
    severity: str
    message: str
    start: int
    end: int


def _term_matches(text: str, terms: frozenset[str]):
    for term in sorted(terms, key=len, reverse=True):
        pattern = r"(?<!\w)" + re.escape(term).replace(r"\ ", r"\s+") + r"(?!\w)"
        yield from re.finditer(pattern, text, flags=re.IGNORECASE)


def _tokens(text: str) -> set[str]:
    return {match.group(0).casefold() for match in _WORD_RE.finditer(text)}


def lint_caption(
    text: str,
    dataset: TrainingDataset,
    other_captions: list[str],
) -> list[CaptionLintFinding]:
    """Return deterministic, non-persisted caption review findings."""
    findings: list[CaptionLintFinding] = []

    for match in _term_matches(text, IDENTITY_LEAK_TERMS):
        findings.append(
            CaptionLintFinding(
                code="identity-leak",
                severity="warn",
                message=f"Identity descriptor '{match.group(0)}' should not be in a varying caption.",
                start=match.start(),
                end=match.end(),
            )
        )

    if dataset.person_id is not None:
        for match in _term_matches(text, OTHER_PEOPLE_TERMS):
            findings.append(
                CaptionLintFinding(
                    code="other-people",
                    severity="warn",
                    message=f"'{match.group(0)}' suggests more than one subject.",
                    start=match.start(),
                    end=match.end(),
                )
            )

    for match in _term_matches(text, TEXT_ARTIFACT_TERMS):
        findings.append(
            CaptionLintFinding(
                code="text-artifacts",
                severity="error",
                message=f"The caption mentions a visible {match.group(0).lower()} artifact.",
                start=match.start(),
                end=match.end(),
            )
        )

    words = list(_WORD_RE.finditer(text))
    if len(words) < 4:
        findings.append(
            CaptionLintFinding(
                code="too-short",
                severity="warn",
                message="Caption has fewer than 4 words.",
                start=0,
                end=len(text),
            )
        )
    elif len(words) > 75:
        findings.append(
            CaptionLintFinding(
                code="too-long",
                severity="warn",
                message="Caption has more than 75 words.",
                start=0,
                end=len(text),
            )
        )

    tokens = _tokens(text)
    if tokens:
        for other in other_captions:
            other_tokens = _tokens(other)
            union = tokens | other_tokens
            if union and len(tokens & other_tokens) / len(union) >= 0.9:
                findings.append(
                    CaptionLintFinding(
                        code="near-duplicate",
                        severity="info",
                        message="Caption is nearly identical to another dataset item.",
                        start=0,
                        end=len(text),
                    )
                )
                break

    trigger = dataset.trigger_word.strip()
    if trigger:
        matches = list(_term_matches(text, frozenset({trigger.casefold()})))
        for match in matches:
            findings.append(
                CaptionLintFinding(
                    code="trigger-in-caption",
                    severity="warn",
                    message="The trigger word belongs in the template, not the caption body.",
                    start=match.start(),
                    end=match.end(),
                )
            )

    return sorted(findings, key=lambda finding: (finding.start, finding.end, finding.code))
