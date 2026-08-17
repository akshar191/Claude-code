"""Draft the actual email.

Short, specific, one ask, easy to decline. A founder at a 20-person shop reads
their own inbox -- three sentences that show you looked at what they build beat
anything templated-sounding.
"""

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


class ResearchFailed(Exception):
    """Raised rather than writing an email with no company in it.

    A generic email dressed up as a personal one is worse than no email: the
    contact is spent either way, and the generic version guarantees no reply.
    """

    def __init__(self, reason, diagnostics=None):
        super().__init__(reason)
        self.reason = reason
        self.diagnostics = diagnostics or {}


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
    first = _first_name(contact)
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

    # The verifiable hook, quoted rather than paraphrased.
    quoted = details[0]["text"].rstrip(".")
    if len(quoted) > 180:
        quoted = quoted[:177].rsplit(" ", 1)[0] + "…"
    why = 'Your site says: "%s"' % quoted

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

    # The quoted claim, attributed rather than paraphrased.
    lines += [
        "%s — that's the part I keep coming back to, and it's why I'm writing to "
        "you specifically rather than sending this everywhere." % why,
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

    return {
        "subject": subject,
        "body": "\n".join(lines),
        "why": why,
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
