"""The Coach's reply guard (native app build prompt, sections 1, 5.10 and 12).

The Coach states observations, never causes, and gives no medical advice. This
checks a reply's words for the four ways a reply breaks that:

- cause: a body signal or a day said to have caused something ("because your HRV
  dropped, you are...", "in line with your short sleep", "that's why...");
- medical: treatment or medication advice ("ice it", "take ibuprofen", "run
  through the pain");
- diagnosis: naming a condition ("sounds like shin splints", "you're overtrained");
- certainty: prescriptive certainty ("you must", "definitely", "will be fine").

And the numbers rule: every number in a reply must be one the model was given
(`unmatched_numbers`), or the reply is regenerated.

Pure and deterministic: no model, no network. The app has a twin
(mobile/src/domain/replyGuard.ts) that runs on received replies as a second
line; both are held to the same corpus (fixtures/coach_replies.json, written by
scripts/domain_fixtures.py), and the twin reads its rules from that file's
`rules`, so the two can't drift. TODO(server): chat_pilot.py (on main) runs
`check` on each finished reply and `unmatched_numbers` against the tool results
the model read, and regenerates once on a finding.
"""

from __future__ import annotations

import json
import re
from typing import Any

KINDS = ("cause", "medical", "diagnosis", "certainty")

# Words for body signals and how a day felt, the things a reply must not make a cause of.
_BODY = (r"(?:sleep|slept|hrv|heart rate|hr|resting|stress|load|fatigue|tired|tiredness|recovery|readiness|legs|body"
         r"|illness|cold|travel|travelling|alcohol|caffeine|nutrition|hydration|heat|night)")
_CONDITIONS = (r"(?:a stress fracture|stress fracture|tendinitis|tendonitis|shin splints|plantar fasciitis|an infection|infection"
               r"|a virus|virus|a cold|the flu|flu|covid|anaemia|anemia|iron deficiency|overtraining syndrome|overtraining"
               r"|overreaching|an injury|injury|a strain|strain|a sprain|sprain|illness|burnout)")
# What a reply must not say a number came "from": "your HRV dropped from the alcohol".
_CAUSES = (r"(?:alcohol|caffeine|drinks?|drinking|the heat|heat|late night|short night|poor sleep|short sleep|bad sleep"
           r"|lack of sleep|illness|stress|travel|travelling|jet ?lag)")
# "Treat" is medical only next to a body part or an injury ("treat the achilles"), not "treat the first 2 km as a warm-up".
_INJURY = (r"(?:knee|knees|calf|calves|achilles|shin|shins|ankle|ankles|foot|feet|hamstring|hamstrings|hip|hips|back|quad"
           r"|quads|glute|glutes|injury|niggle|pain|soreness|strain|sprain|blister|area)")
# What may come right before "you must / you need to": after a condition word ("tell me if you need to move it")
# it's an offer, not an order. Without lookbehind, the match takes the word (or punctuation) before "you" with it.
_NOT_AFTER = r"(?:^|[^\w\s']\s*|\b(?!(?:if|when|unless|whether|once|until|in case)\b)\w+\s+)"

# Each rule is a regular expression in the subset Python and JavaScript read the same way
# (no lookbehind, no named groups), matched case-insensitively on the reply with curly
# apostrophes made straight.
RULES: dict[str, list[str]] = {
    "cause": [
        r"\b(?:because|due to|caused by|as a result of|thanks to|owing to|driven by)\b(?:\W+\w+){0,5}?\W+" + _BODY + r"\b",
        r"\bin line with (?:your|the|this week's|last night's)\b",
        r"\b(?:explains|explained|that's why|that is why|which is why|this is why|caused|causes|causing|to blame)\b",
        # A body signal or a day as the subject of a causal verb: "your short sleep made the reps harder".
        r"\b" + _BODY + r"\b(?:\W+\w+){0,3}?\W+(?:made|makes|making|affected|affects|affecting|led to|leads to|leading to"
        r"|resulted in|results in|is why|was why|are why|were why|is the reason|was the reason)\b",
        r"\bfrom (?:the |your |all the |too much |that |this |last night's )?" + _CAUSES + r"\b",
        r"\bso (?:you|your (?:legs|body|heart|hrv))\b(?:\W+\w+){0,3}?\W+(?:will|are|were|feel|felt|should|need)\b",
        r"\bmeans? (?:you|your body) (?:are|were|is|need|needs|should)\b",
    ],
    "medical": [
        r"\b(?:ibuprofen|paracetamol|acetaminophen|naproxen|aspirin|painkillers?|anti-?inflammator(?:y|ies)|antibiotics?"
        r"|supplements?|iron tablets|cortisone)\b",
        r"\b(?:ice|heat|massage|foam roll|stretch|strap|tape) (?:it|the (?:area|knee|calf|achilles|shin|ankle|foot|hamstring))\b",
        r"\brun(?:ning)? through (?:it|the pain|pain)\b",
        r"\b(?:will|should) heal\b",
        r"\b(?:safe|fine|ok|okay) to run (?:on|with) (?:it|an? (?:injury|niggle|cold|fever))\b",
        r"\btreat(?:ment|ing|s)? (?:for )?(?:the |your |that |an? )?(?:\w+ )?" + _INJURY + r"\b",
    ],
    "diagnosis": [
        r"\byou(?:'ve| have| may have| might have| probably have| likely have| could have) (?:got )?" + _CONDITIONS + r"\b",
        r"\b(?:sounds|looks|seems) like (?:a |an )?" + _CONDITIONS + r"\b",
        r"\byou(?:'re| are)(?: (?:probably|likely|maybe|possibly|definitely))? (?:overtrained|overtraining|overreaching|ill|sick"
        r"|injured|getting ill|getting sick|coming down with)\b",
        r"\b(?:probably|likely|might be|may be|could be) (?:getting|coming down with) (?:ill|sick|a cold|the flu|flu|something)\b",
        r"\bsigns? of (?:" + _CONDITIONS + r"|fatigue)\b",
        r"\b(?:it's|that's|its|thats) (?:(?:just|only|probably|likely) ){0,2}(?:a |an )?(?:strain|sprain|tendinitis|tendonitis"
        r"|shin splints|cold|virus|infection)\b",
    ],
    "certainty": [
        _NOT_AFTER + r"you (?:must|have to|need to|should definitely)\b",
        r"\b(?:definitely|certainly|guaranteed?|without (?:a )?doubt|no doubt|for sure)\b",
        r"\b100 ?%",
        r"\bwill (?:be fine|be ok|be okay|make you)\b",
        # An order ("Never skip the long run"), not the runner's own words ("I can never do intervals on Monday").
        r"(?:^|[.!?;:]\s+|\byou should )(?:always|never) (?:run|skip|do|train)\b",
    ],
}

_COMPILED = {kind: [re.compile(p, re.IGNORECASE | re.ASCII) for p in pats] for kind, pats in RULES.items()}


def _plain(text: str) -> str:
    return (text or "").replace("’", "'").replace("‘", "'")


def check(text: str) -> list[dict[str, Any]]:
    """Every finding in a reply, in the order they appear: {kind, match, at}."""
    plain = _plain(text)
    found = []
    for kind in KINDS:
        for rx in _COMPILED[kind]:
            for m in rx.finditer(plain):
                found.append({"kind": kind, "match": m.group(0), "at": m.start()})
    return sorted(found, key=lambda f: (f["at"], KINDS.index(f["kind"])))


def kinds(text: str) -> list[str]:
    """The kinds of finding in a reply, each once, in KINDS order. Empty: the reply passes."""
    seen = {f["kind"] for f in check(text)}
    return [k for k in KINDS if k in seen]


# -- numbers ---------------------------------------------------------------------------

_NUMBER = re.compile(r"\d+(?:[.:]\d+)*", re.ASCII)


def _norm(token: str) -> str:
    """One way to write a number: no leading zero on the first part, no trailing .0."""
    token = re.sub(r"^0+(?=\d)", "", token)
    return re.sub(r"\.0+$", "", token)


def numbers(text: str) -> list[str]:
    """The numbers written in a text, as written (normalised), in order."""
    return [_norm(m.group(0)) for m in _NUMBER.finditer(_plain(text))]


def _payload_numbers(value: Any, out: set[str]) -> None:
    if isinstance(value, bool) or value is None:
        return
    if isinstance(value, (int, float)):
        out.add(_norm(str(int(value)) if float(value).is_integer() else repr(float(value))))
    elif isinstance(value, str):
        out.update(numbers(value))
    elif isinstance(value, dict):
        for v in value.values():
            _payload_numbers(v, out)
    elif isinstance(value, (list, tuple)):
        for v in value:
            _payload_numbers(v, out)


def unmatched_numbers(text: str, payload: Any) -> list[str]:
    """Numbers in the reply that appear nowhere in what the model was given, each once."""
    known: set[str] = set()
    _payload_numbers(json.loads(json.dumps(payload, default=str)), known)
    out: list[str] = []
    for n in numbers(text):
        if n not in known and n not in out:
            out.append(n)
    return out
