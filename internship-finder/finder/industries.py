"""Industry taxonomy: how each field maps onto each discovery source.

`places` strings become Google Places text queries. `osm` strings are raw
Overpass tag filters. `keywords` are matched against a company's own website
copy to confirm it really is in that field.
"""

INDUSTRIES = {
    "mechanical_engineering": {
        "label": "Mechanical Engineering",
        "places": [
            "mechanical engineering firm",
            "product design and engineering company",
            "machine design consultancy",
        ],
        "osm": ['["office"="engineering"]', '["craft"="engineer"]', '["man_made"="works"]'],
        "apollo": ["mechanical engineering", "industrial design", "machinery"],
        "keywords": [
            "mechanical", "cad", "solidworks", "thermal", "fea", "tolerance",
            "machining", "prototype", "hvac", "mechatronics", "dfm", "cnc",
        ],
    },
    "software": {
        "label": "Software / Tech",
        "places": ["software company", "software development agency", "SaaS company"],
        "osm": ['["office"="it"]', '["office"="company"]'],
        "apollo": ["computer software", "information technology"],
        "keywords": [
            "software", "platform", "api", "saas", "engineering team", "developers",
            "cloud", "open source", "backend", "frontend",
        ],
    },
    "biotech": {
        "label": "Biotech / Life Sciences",
        "places": ["biotechnology company", "life sciences company", "pharmaceutical research company"],
        "osm": ['["office"="research"]', '["office"="biotech"]'],
        "apollo": ["biotechnology", "pharmaceuticals", "medical device"],
        "keywords": [
            "biotech", "therapeutics", "clinical", "assay", "laboratory", "genomics",
            "preclinical", "fda", "molecule", "bioprocess",
        ],
    },
    "aerospace": {
        "label": "Aerospace / Defense",
        "places": ["aerospace company", "aerospace engineering firm", "defense contractor"],
        "osm": ['["office"="engineering"]', '["man_made"="works"]'],
        "apollo": ["aviation & aerospace", "defense & space"],
        "keywords": [
            "aerospace", "avionics", "propulsion", "satellite", "uav", "flight",
            "spacecraft", "as9100", "itar", "aerodynamics",
        ],
    },
    "robotics": {
        "label": "Robotics / Automation",
        "places": ["robotics company", "industrial automation company"],
        "osm": ['["office"="engineering"]', '["office"="company"]'],
        "apollo": ["robotics", "industrial automation"],
        "keywords": [
            "robotics", "automation", "actuator", "ros", "motion control", "gripper",
            "autonomous", "plc", "kinematics", "end effector",
        ],
    },
    "civil_engineering": {
        "label": "Civil / Structural Engineering",
        "places": ["civil engineering firm", "structural engineering firm", "geotechnical engineering company"],
        "osm": ['["office"="engineering"]', '["office"="surveyor"]'],
        "apollo": ["civil engineering", "construction"],
        "keywords": [
            "civil", "structural", "geotechnical", "site plan", "permitting",
            "bridge", "stormwater", "surveying", "land development", "revit",
        ],
    },
    "electrical_engineering": {
        "label": "Electrical / Hardware",
        "places": ["electrical engineering firm", "electronics design company", "PCB design company"],
        "osm": ['["office"="engineering"]', '["craft"="electronics_repair"]'],
        "apollo": ["electrical & electronic manufacturing", "semiconductors"],
        "keywords": [
            "electrical", "pcb", "firmware", "embedded", "analog", "rf", "fpga",
            "power electronics", "altium", "schematic",
        ],
    },
    "manufacturing": {
        "label": "Manufacturing / Hardware Production",
        "places": ["manufacturing company", "contract manufacturer", "fabrication shop"],
        "osm": ['["man_made"="works"]', '["industrial"="factory"]', '["craft"="metal_construction"]'],
        "apollo": ["mechanical or industrial engineering", "machinery"],
        "keywords": [
            "manufacturing", "fabrication", "assembly", "iso 9001", "tooling",
            "injection molding", "welding", "production line", "quality control",
        ],
    },
    "medical_devices": {
        "label": "Medical Devices",
        "places": ["medical device company", "medical device design firm"],
        "osm": ['["office"="research"]', '["office"="company"]'],
        "apollo": ["medical device", "hospital & health care"],
        "keywords": [
            "medical device", "iso 13485", "510(k)", "catheter", "implant",
            "clinical", "fda", "biocompatibility", "surgical",
        ],
    },
    "energy": {
        "label": "Energy / Cleantech",
        "places": ["renewable energy company", "cleantech company", "energy engineering firm"],
        "osm": ['["office"="energy_supplier"]', '["office"="engineering"]'],
        "apollo": ["renewables & environment", "oil & energy"],
        "keywords": [
            "solar", "battery", "renewable", "grid", "decarbon", "energy storage",
            "wind", "hydrogen", "electrification", "kilowatt",
        ],
    },
    "architecture": {
        "label": "Architecture / Design",
        "places": ["architecture firm", "architectural design studio"],
        "osm": ['["office"="architect"]'],
        "apollo": ["architecture & planning", "design"],
        "keywords": [
            "architecture", "architectural", "design studio", "rendering", "aia",
            "schematic design", "adaptive reuse", "interiors",
        ],
    },
    "finance": {
        "label": "Finance / Investment",
        "places": ["investment firm", "financial advisory firm", "venture capital firm"],
        "osm": ['["office"="financial"]', '["office"="insurance"]'],
        "apollo": ["financial services", "investment management"],
        "keywords": [
            "portfolio", "investment", "capital", "advisory", "assets under management",
            "fund", "wealth", "equity", "underwriting",
        ],
    },
    "consulting": {
        "label": "Consulting",
        "places": ["consulting firm", "management consulting company"],
        "osm": ['["office"="consulting"]', '["office"="company"]'],
        "apollo": ["management consulting"],
        "keywords": ["consulting", "advisory", "engagement", "client", "strategy"],
    },
    "marketing": {
        "label": "Marketing / Creative",
        "places": ["marketing agency", "branding agency", "creative studio"],
        "osm": ['["office"="advertising_agency"]', '["office"="graphic_design"]'],
        "apollo": ["marketing & advertising", "design"],
        "keywords": ["branding", "campaign", "creative", "marketing", "content", "seo"],
    },
    "other": {
        "label": "Anything (broad search)",
        "places": ["company", "small business office"],
        "osm": ['["office"="company"]', '["office"="engineering"]', '["office"="research"]'],
        "apollo": [],
        "keywords": [],
    },
}


def get(key):
    return INDUSTRIES.get(key or "", INDUSTRIES["other"])


def choices():
    return [{"value": key, "label": value["label"]} for key, value in INDUSTRIES.items()]


# Seniority ladder. Higher rank == more likely to personally answer an
# internship email and be able to say yes to it.
SENIORITY_TIERS = [
    (5, ["chief executive officer", "ceo", "co-founder", "cofounder", "founder",
         "president", "owner", "proprietor", "managing director", "managing partner",
         "chairman", "chairwoman", "chairperson"]),
    (4, ["chief technology officer", "cto", "chief operating officer", "coo",
         "chief financial officer", "cfo", "chief scientific officer", "cso",
         "chief product officer", "cpo", "chief of staff", "chief engineer",
         "partner", "principal", "vice president", "vp", "svp", "evp", "head of"]),
    (3, ["director", "general manager", "practice lead", "department head",
         "engineering manager", "studio director"]),
    (2, ["manager", "team lead", "tech lead", "supervisor", "principal engineer",
         "staff engineer", "senior engineer", "senior scientist", "hiring manager"]),
    (1, ["senior", "lead", "recruiter", "talent", "engineer", "scientist",
         "designer", "analyst", "associate"]),
]

SENIORITY_LABELS = {
    5: "Founder / CEO",
    4: "C-suite / VP / Partner",
    3: "Director",
    2: "Manager / Senior IC",
    1: "Individual contributor",
    0: "Unknown",
}


def rank_title(title):
    """Return (rank, matched_phrase) for a job title string."""
    if not title:
        return 0, None
    lowered = " " + " ".join(title.lower().replace("&", " and ").split()) + " "
    for rank, phrases in SENIORITY_TIERS:
        for phrase in phrases:
            if len(phrase) <= 4:  # acronyms need word boundaries to avoid false hits
                if (" %s " % phrase) in lowered or ("%s," % phrase) in lowered:
                    return rank, phrase
            elif phrase in lowered:
                return rank, phrase
    return 0, None
