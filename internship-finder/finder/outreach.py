"""Draft the actual email.

Short, specific, one ask, easy to decline. A founder at a 20-person shop reads
their own inbox -- three sentences that show you looked at what they build beat
anything templated-sounding.
"""

from . import text

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


def mailto_link(contact, drafted):
    """A mailto: URL that opens the draft in the user's mail client."""
    from urllib.parse import quote

    return "mailto:%s?subject=%s&body=%s" % (
        contact.get("email") or "",
        quote(drafted["subject"]),
        quote(drafted["body"]),
    )
