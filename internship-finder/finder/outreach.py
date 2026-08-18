"""Draft the actual email.

Short, specific, one ask, easy to decline. A founder at a 20-person shop reads
their own inbox -- three sentences that show you looked at what they build beat
anything templated-sounding.
"""

import re

from . import config, text

STYLES = {
    "advice": "Ask for advice (highest reply rate)",
    "internship": "Ask directly about an internship",
    "short": "Very short note",
}


def _first_name(contact):
    first = contact.get("first_name")
    if not first:
        first, _ = text.split_name(contact.get("name") or "")
    return first or "there"


def _company(contact):
    return contact.get("company_name") or contact.get("company") or "your team"


def _hook(contact):
    """One line about what the company does, taken verbatim from their site.

    Quoted rather than stitched into a sentence: company blurbs are written in
    first person ("We design...") and splicing them into your prose produces
    garbage like "I saw that you we design...".
    """
    for field in ("company_description", "company_industry"):
        value = " ".join((contact.get(field) or "").split())
        if value:
            snippet = value.split(".")[0].strip(' "“”')
            if 10 <= len(snippet) <= 140:
                return snippet
    return None


def draft(contact, profile, style="advice"):
    """Return {subject, body} for one contact."""
    profile = profile or {}
    first = _first_name(contact)
    company = _company(contact)
    hook = _hook(contact)

    student = profile.get("name") or "[your name]"
    year = profile.get("year") or "student"
    school = profile.get("school") or "[your school]"
    major = profile.get("major") or "engineering"
    skills = profile.get("skills") or "[a project or skill worth naming]"
    portfolio = profile.get("portfolio_url")
    timeframe = profile.get("timeframe") or "this summer"
    city = profile.get("city")

    intro = "I'm %s, a %s at %s studying %s." % (student, year, school, major)
    context = "I came across %s while looking at %s companies%s." % (
        company, major.lower(), " around %s" % city if city else "",
    )
    if hook:
        context += ' Your site describes the work as "%s" — that is close to what I want to be doing.' % hook
    proof = "I've been working on %s." % skills

    if style == "internship":
        subject = "%s intern? — %s, %s" % (major.split()[0], student, school)
        ask = (
            "Are you taking on an intern %s? I'd be glad to send a resume, or to "
            "start with something small so you can see the work first. If the "
            "timing isn't right, no problem at all." % timeframe
        )
    elif style == "short":
        subject = "Quick question from a %s student" % school
        body_lines = [
            "Hi %s," % first,
            "",
            "%s %s" % (intro, context),
            "",
            "Would you be open to a 15-minute call about how you got into this work? "
            "Happy to work around your schedule.",
            "",
            "Thanks for reading,",
            student,
        ]
        return {"subject": subject, "body": "\n".join(body_lines)}
    else:
        subject = "15 minutes of advice? — %s student" % school
        ask = (
            "Would you be open to a 15-minute call in the next few weeks? I'd mostly "
            "want to hear how you'd spend a summer if you were starting out now. "
            "I'm not expecting you to have a role open — advice is genuinely the ask."
        )

    body_lines = [
        "Hi %s," % first,
        "",
        intro,
        "",
        context,
        "",
        proof,
    ]
    if portfolio:
        body_lines.append("You can see it here: %s" % portfolio)
    body_lines += ["", ask, "", "Thanks for your time,", student]
    if profile.get("phone"):
        body_lines.append(profile["phone"])

    return {"subject": subject, "body": "\n".join(body_lines)}


# Turning a sentence of marketing copy into something in your own voice.
_MAKE_VERBS = (r"build|builds|design|designs|develop|develops|make|makes|"
               r"manufacture|manufactures|engineer|engineers|produce|produces|"
               r"create|creates|deliver|delivers|provide|provides")

_REPHRASE_RULES = (
    # "We build robotic arms for research" -> "robotic arms for research"
    (re.compile(r"^(?:we|our team)\s+(?:are\s+|is\s+)?(?:%s)\s+(.+)$" % _MAKE_VERBS,
                re.IGNORECASE), ""),
    # "We have found that a holistic approach..." -> "a holistic approach..."
    (re.compile(r"^(?:we|our team)\s+\w+(?:\s+\w+){0,2}?\s+that\s+(.+)$",
                re.IGNORECASE), ""),
    # "Alfred is a collaborative robot arm that..." -> "a collaborative robot arm that..."
    (re.compile(r"^[A-Z][\w'-]*\s+(?:is|are)\s+((?:a|an|the)\s+.+)$"), ""),
    # "Our WAM arm delivers 7 degrees of freedom" -> "the WAM arm"
    (re.compile(r"^our\s+(.+)$", re.IGNORECASE), "the "),
)

# A finite verb turns the phrase back into a clause, and "your work on X
# delivers Y" is not a sentence. Cut before it.
_FINITE_VERB = re.compile(
    r"\s*(?:,\s*)?\b(?:produces|delivers|provides|enables|offers|allows|helps|"
    r"powers|supports|reduces|improves|lets|includes?|spans?|covers?|ranges?|"
    r"features?|serves?|is|are|was|were|has|have|can|will)\b.*$",
    re.IGNORECASE,
)


def _trim_to_noun_phrase(phrase):
    """Cut a phrase back to the noun part, if it runs on into a verb."""
    trimmed = _FINITE_VERB.sub("", phrase).strip(" ,;:-")
    # Only accept the trim if something substantial survives.
    if len(trimmed.split()) >= 2:
        return trimmed
    return phrase.strip(" ,;:-")


def reference_phrase(claim):
    """A site sentence -> a noun phrase that can follow "your work on ...".

    Returns (phrase, normalised). When normalised is False we could not get the
    sentence into a shape that reads naturally in someone's own voice, and the
    UI says so rather than quietly shipping an awkward line.

    Quoting marketing copy back at the person who wrote it is worse than saying
    nothing, so the email never contains quotation marks -- the raw claim and
    its source URL are shown beside the draft instead.
    """
    sentence = " ".join((claim or "").split()).rstrip(".")
    if not sentence:
        return "", False

    for pattern, prefix in _REPHRASE_RULES:
        match = pattern.match(sentence)
        if not match:
            continue

        phrase = _trim_to_noun_phrase(match.group(1).strip().rstrip("."))
        if not phrase:
            continue

        # Lowercase a leading determiner; leave product names capitalised.
        if phrase.split()[0].lower() in ("a", "an", "the", "our", "your"):
            phrase = phrase[0].lower() + phrase[1:]
        return (prefix + phrase).strip(), True

    # Could not get it into a natural shape. Hand it back lowercased and let the
    # UI flag it for rewriting rather than shipping an awkward line silently.
    return sentence[0].lower() + sentence[1:], False


class ResearchFailed(Exception):
    """Raised rather than writing an email with no company in it.

    A generic email dressed up as a personal one is worse than no email: the
    contact is spent either way, and the generic version guarantees no reply.

    Subclasses cover the other ways a draft can be not-worth-sending. They all
    inherit from this so callers keep one refusal path.
    """

    kind = "research"

    def __init__(self, reason, diagnostics=None):
        super().__init__(reason)
        self.reason = reason
        self.diagnostics = diagnostics or {}


class NameUnreliable(ResearchFailed):
    """The greeting would be wrong, so there is no draft worth editing."""

    kind = "name"


class DraftInvalid(ResearchFailed):
    """The assembled email is not grammatical, so it does not go out."""

    kind = "grammar"


# --------------------------------------------------------------------------
# Quality gates
# --------------------------------------------------------------------------

# A capability list is an enumeration of services, not a claim about a thing
# they build. "your work on the in-house capabilities include ..." is what
# happens when one is dropped into a sentence frame.
_ENUMERATION = re.compile(
    r"\b(include|includes|including|such as|ranging from|offerings?|capabilit(?:y|ies)|"
    r"services|expertise|specialt(?:y|ies)|disciplines)\b", re.IGNORECASE)

_TRAILING_JUNK = re.compile(
    r"\b(and|or|with|for|to|of|in|on|at|the|a|an|that|which|from|by)$", re.IGNORECASE)


def phrase_problem(phrase):
    """Why this phrase cannot follow "your work on", or None if it can."""
    if not phrase:
        return "empty"
    if "…" in phrase or "..." in phrase:
        return "was truncated mid-sentence"
    words = phrase.split()
    if len(words) < 2:
        return "too short to reference"
    if len(words) > 24:
        return "too long to read as a phrase"
    if phrase.count(",") >= 3:
        return "is a comma-separated list, not a claim"
    if _ENUMERATION.search(phrase):
        return "is a list of services rather than something they build"
    if _TRAILING_JUNK.search(words[-1].strip(".,;:")):
        return "ends mid-thought on %r" % words[-1]
    if _FINITE_VERB.match(" " + phrase):
        return "is a clause, not a noun phrase"
    return None


# Seams and breakages that must never reach a draft.
_ELLIPSIS = re.compile(r"…|\.\.\.")
_TEMPLATE_SEAM = re.compile(r"[{}]|%s|%\(|\[your |\[a project|\[insert", re.IGNORECASE)
_DOUBLED_WORD = re.compile(
    r"\b(on|in|at|of|for|with|to|about|the|a|an|and|is|are)\s+\1\b", re.IGNORECASE)
_DOUBLED_PREP = re.compile(
    r"\b(on|in|at|of|for|with|about)\s+(?:the|a|an)?\s*\b(on|in|at|of|for|with|about)\b",
    re.IGNORECASE)


def draft_problem(body):
    """Why this email body is not sendable, or None if it is.

    Runs over the finished text, after every fragment has been substituted --
    the point is to catch a broken seam no individual check saw.
    """
    if not body or not body.strip():
        return "empty body"
    if _ELLIPSIS.search(body):
        return "contains an ellipsis, so something was truncated"
    seam = _TEMPLATE_SEAM.search(body)
    if seam:
        return "contains an unfilled template seam (%r)" % seam.group(0)
    doubled = _DOUBLED_WORD.search(body) or _DOUBLED_PREP.search(body)
    if doubled:
        return "contains a doubled preposition (%r)" % doubled.group(0)

    # Every prose sentence should be a sentence: the greeting, sign-off and
    # signature lines are addressed separately.
    for paragraph in body.split("\n"):
        line = paragraph.strip()
        if not line or line.endswith(",") or len(line.split()) < 4:
            continue  # "Hi Dave,", "Thanks for reading,", a signature
        if not line.endswith((".", "?", "!")):
            return "a sentence does not end in punctuation: %r" % line[-60:]
        for sentence in re.split(r"(?<=[.?!])\s+", line):
            sentence = sentence.strip()
            if sentence and len(sentence.split()) < 3:
                return "a sentence is a fragment: %r" % sentence
    return None


# Rank 4 and up (founder, CEO, VP, partner) can say yes to an intern. Below
# that, asking for a job is asking the wrong person -- so ask about the work.
DIRECT_ASK_RANK = 4


def internship_draft(contact, company, research, profile=None):
    """A cold email for a specific internship, built on what the site actually says.

    Returns {subject, body, why, details, unverified}. `why` is a separate line
    the sender can check against the cited source and delete if it does not hold
    up -- it is the one claim in the email that came from a machine reading a
    web page, and a wrong one is worse than no line at all.
    """
    profile = dict(config.APPLICANT, **{
        k: v for k, v in (profile or {}).items() if v not in (None, "")
    })
    # Strict: the generic "there" fallback that _first_name uses elsewhere is
    # itself a tell in a cold email addressed to a named person.
    first = (contact.get("first_name") or "").strip()
    if not first:
        first = (text.split_name(contact.get("name") or "")[0] or "").strip()
    company_name = company.get("name") or "your team"
    details = (research or {}).get("details") or []

    # No specific detail means no personalised email. Refuse loudly.
    if not details:
        raise ResearchFailed(
            (research or {}).get("error")
            or "found nothing specific to say about this company",
            (research or {}).get("diagnostics"),
        )

    student = profile.get("name") or "[your name]"
    year = profile.get("year") or "high school junior"
    town = profile.get("town") or ""
    work = profile.get("work") or ""
    venture = profile.get("venture") or ""
    target = profile.get("target") or "summer 2027"

    # The greeting has to be right or there is no point drafting: "Hi M Dinne,"
    # announces the email as automated before anything else is read.
    problem = text.first_name_problem(first)
    if problem:
        if not first:
            problem = "could not be determined from %r" % (contact.get("name") or "")
        raise NameUnreliable(
            "The first name for this contact (%r) %s. Check the real name on "
            "their site or LinkedIn, correct it, and draft again -- a wrong "
            "greeting is the most obvious tell there is." % (first, problem)
        )

    # Take the first researched detail that survives the phrase checks; a
    # capability list or a truncated fragment is not worth an email.
    source_claim = source_url = phrase = None
    normalised = False
    rejected = []
    for detail in details:
        claim = " ".join((detail.get("text") or "").split())
        candidate, candidate_ok = reference_phrase(claim)
        why_not = phrase_problem(candidate)
        if why_not:
            rejected.append({"claim": claim[:120], "why": why_not})
            continue
        source_claim, source_url = claim, detail.get("url")
        phrase, normalised = candidate, candidate_ok
        break

    if not phrase:
        raise ResearchFailed(
            "Nothing found on their site works as a specific reference: %s"
            % "; ".join("%s (%s)" % (r["why"], r["claim"][:60]) for r in rejected),
            {"rejected_phrases": rejected},
        )

    rank = contact.get("rank") or 0
    direct_ask = rank >= DIRECT_ASK_RANK

    if direct_ask:
        subject = "%s intern? — %s, %s" % (
            target.capitalize() if target else "Summer", student, year,
        )
    else:
        subject = "Question about your work at %s" % company_name

    lines = ["Hi %s," % first, ""]
    lines += ["I'm %s, a %s%s." % (student, year, " in %s" % town if town else ""), ""]

    # The researched fact, in the sender's voice. Nothing here comments on the
    # outreach itself -- an email that insists it is not a mass email reads as
    # exactly the thing it denies being.
    lines += [
        "What interests me about %s is your work on %s. I'd like to understand "
        "how something like that actually gets built." % (company_name, phrase),
        "",
    ]

    # These come from config and are inserted exactly as written: no rewriting,
    # no softening, no re-conjugating. Each already reads as "I'm <phrase>".
    background = []
    if work:
        background.append("I'm %s." % work)
    if venture:
        background.append("I'm also %s." % venture)
    if background:
        background.append(
            "Both mean careful hands-on work and being the one accountable when "
            "something is off."
        )
        lines += [" ".join(background), ""]

    if direct_ask:
        lines += [
            "Would you be open to taking on an intern for %s? Happy to send a "
            "resume. If it's not the right time, a pointer toward someone else "
            "worth talking to would be just as useful." % target,
        ]
    else:
        # Not their call to make, so don't put them on the spot -- ask about the
        # work itself, which is the thing they can actually answer.
        lines += [
            "I'm not asking you for a job — I know that's not your call. I'd just "
            "like to hear how you got into this kind of work, and what you'd "
            "learn first if you were starting now. Fifteen minutes whenever suits "
            "you, or a reply to this email is just as good.",
        ]

    lines += ["", "Thanks for reading,", student]
    if profile.get("phone"):
        lines.append(profile["phone"])

    body = "\n".join(lines)
    broken = draft_problem(body)
    if broken:
        raise DraftInvalid(
            "The generated email is not sendable: %s. Nothing was drafted." % broken,
            {"body": body},
        )

    return {
        "subject": subject,
        "body": body,
        # For the UI's verification panel -- deliberately NOT in the email body.
        "source_claim": source_claim,
        "source_url": source_url,
        "reference": phrase,
        "reference_normalised": normalised,
        "details": details,
        "sources": (research or {}).get("pages") or [],
        "unverified": True,
        "ask": "internship" if direct_ask else "about their work",
        "seniority_rank": rank,
    }


def mailto_link(contact, drafted):
    """A mailto: URL that opens the draft in the user's mail client."""
    from urllib.parse import quote

    return "mailto:%s?subject=%s&body=%s" % (
        contact.get("email") or "",
        quote(drafted["subject"]),
        quote(drafted["body"]),
    )
