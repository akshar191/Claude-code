"""Canned results for the public /demo page.

Every company and person here is invented. The demo page is public, so putting
real contacts on it would mean publishing working email addresses for people who
never agreed to that -- the opposite of what a tool like this should do with the
data it collects. The shapes, confidence values and bases are copied from real
runs, so the confidence model is shown honestly even though the rows are not.
"""

NOTES = [
    "OpenStreetMap/Overpass unavailable (read timeout) -- Google Places carried the search.",
    "Skipped 3 result(s) with no website.",
    "1 company(ies) filtered out as too large.",
    "1 company(ies) skipped: not an employer (site reads as a directory).",
]

COMPANIES = [
    {
        "name": "Halbrook Motion Systems",
        "domain": "halbrookmotion.example",
        "website": "https://halbrookmotion.example",
        "address": "Waltham, MA",
        "email_pattern": "{first}.{last}",
        "sources": ["google_places"],
        "size_note": "23 employees",
        "contacts": [
            {
                "name": "Priya Raghavan",
                "title": "Co-Founder & CEO",
                "seniority": "Founder / CEO",
                "rank": 5,
                "email": "priya.raghavan@halbrookmotion.example",
                "email_confidence": 0.95,
                "email_status": "valid",
                "email_basis": "published on the company's own website",
                "source": "website",
            },
            {
                "name": "Daniel O'Brien",
                "title": "VP of Engineering",
                "seniority": "C-suite / VP / Partner",
                "rank": 4,
                "email": "daniel.obrien@halbrookmotion.example",
                "email_confidence": 0.75,
                "email_status": "unknown",
                "email_basis": "matches this company's known address format",
                "source": "website",
            },
        ],
    },
    {
        "name": "Cadence Robotics",
        "domain": "cadencerobotics.example",
        "website": "https://cadencerobotics.example",
        "address": "Somerville, MA",
        "sources": ["google_places", "hunter"],
        "size_note": "size not confirmed",
        "contacts": [
            {
                "name": "Marcus Webb",
                "title": "Founder",
                "seniority": "Founder / CEO",
                "rank": 5,
                "email": "mwebb@cadencerobotics.example",
                "email_confidence": 0.88,
                "email_status": "valid",
                "email_basis": "found by Hunter for this person",
                "source": "hunter",
            },
            {
                "name": "Sofia Marino",
                "title": "Director of Operations",
                "seniority": "Director",
                "rank": 3,
                "email": "smarino@cadencerobotics.example",
                "email_confidence": 0.71,
                "email_status": "unknown",
                "email_basis": "matches this company's known address format",
                "source": "website",
            },
        ],
    },
    {
        "name": "Fenway Instrument Works",
        "domain": "fenwayinstrument.example",
        "website": "https://fenwayinstrument.example",
        "address": "Newton, MA",
        "sources": ["google_places"],
        "size_note": "size not confirmed",
        "contacts": [
            {
                "name": "Hannah Okafor",
                "title": "Principal Engineer",
                "seniority": "Manager / Senior IC",
                "rank": 2,
                "email": "hannah.okafor@fenwayinstrument.example",
                "email_confidence": 0.34,
                "email_status": "unknown",
                "email_basis": "guessed — common format (34% of company domains)",
                "source": "website",
            },
        ],
    },
    {
        "name": "Trellis Design Studio",
        "domain": "trellisdesign.example",
        "website": "https://trellisdesign.example",
        "address": "Cambridge, MA",
        "sources": ["openstreetmap"],
        "size_note": "size not confirmed",
        "contacts": [
            {
                "name": "General inbox",
                "title": "Shared company address",
                "seniority": "Shared inbox",
                "rank": 0,
                "email": "hello@trellisdesign.example",
                "email_confidence": 0.90,
                "email_status": "published",
                "email_basis": "published on the company's own website",
                "source": "website",
            },
        ],
    },
]

# One canned draft per contact that has one, so the demo shows the whole flow --
# including the verification panel -- without calling anything.
DRAFTS = {
    "priya.raghavan@halbrookmotion.example": {
        "subject": "Summer 2027 intern? — Akshar Pathak, high school senior",
        "body": (
            "Hi Priya,\n\n"
            "I'm Akshar Pathak, a high school senior in Ashland, MA.\n\n"
            "What interests me about Halbrook Motion Systems is your work on "
            "direct-drive linear stages for semiconductor inspection. I'd like to "
            "understand how something like that actually gets built.\n\n"
            "I'm an intern at Silverside Detectors doing paid assembly work on "
            "lithium-6 neutron detectors. I'm also the founder of a mobile "
            "detailing business. Both mean careful hands-on work and being the one "
            "accountable when something is off.\n\n"
            "Would you be open to taking on an intern for summer 2027? Happy to "
            "send a resume. If it's not the right time, a pointer toward someone "
            "else worth talking to would be just as useful.\n\n"
            "Thanks for reading,\nAkshar Pathak"
        ),
        "source_claim": "We build direct-drive linear stages for semiconductor "
                        "inspection equipment.",
        "source_url": "https://halbrookmotion.example/products",
        "reference": "direct-drive linear stages for semiconductor inspection",
        "reference_normalised": True,
        "ask": "internship",
    },
    "smarino@cadencerobotics.example": {
        "subject": "Question about your work at Cadence Robotics",
        "body": (
            "Hi Sofia,\n\n"
            "I'm Akshar Pathak, a high school senior in Ashland, MA.\n\n"
            "What interests me about Cadence Robotics is your work on a mobile "
            "manipulator that picks parts from unstructured bins. I'd like to "
            "understand how something like that actually gets built.\n\n"
            "I'm an intern at Silverside Detectors doing paid assembly work on "
            "lithium-6 neutron detectors. I'm also the founder of a mobile "
            "detailing business. Both mean careful hands-on work and being the one "
            "accountable when something is off.\n\n"
            "I'm not asking you for a job — I know that's not your call. I'd just "
            "like to hear how you got into this kind of work, and what you'd learn "
            "first if you were starting now. Fifteen minutes whenever suits you, or "
            "a reply to this email is just as good.\n\n"
            "Thanks for reading,\nAkshar Pathak"
        ),
        "source_claim": "Our mobile manipulator picks parts from unstructured bins "
                        "on the factory floor.",
        "source_url": "https://cadencerobotics.example/technology",
        "reference": "a mobile manipulator that picks parts from unstructured bins",
        "reference_normalised": True,
        "ask": "about their work",
    },
}

# Shown when a demo contact has no canned draft, so the refusal path is visible too.
REFUSAL = {
    "error": "Could not find anything specific to say about this company.",
    "reason": ("the site returned almost no readable text -- it is probably "
               "rendered by JavaScript, which this crawler does not execute"),
    "kind": "research",
    "research_failed": True,
    "pages_read": ["https://fenwayinstrument.example/"],
}


def payload():
    """Everything the demo page needs, in the shape the UI already expects."""
    return {"companies": COMPANIES, "notes": NOTES, "drafts": DRAFTS,
            "refusal": REFUSAL}
