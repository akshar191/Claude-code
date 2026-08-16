"""Command-line version, for when you want to pipe results somewhere.

    python cli.py --industry mechanical_engineering --location "Boston, MA" \
                  --max-employees 100 --companies 10 --csv out.csv
"""

from finder.dotenv import load as load_env

load_env()  # must run before finder.config reads the environment

import argparse  # noqa: E402
import csv  # noqa: E402
import sys  # noqa: E402

from finder import industries, pipeline, store  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--industry", default="mechanical_engineering",
                        choices=sorted(industries.INDUSTRIES))
    parser.add_argument("--location", default="", help='e.g. "Boston, MA"')
    parser.add_argument("--keyword", default="", help="extra search term")
    parser.add_argument("--min-employees", type=int, default=1)
    parser.add_argument("--max-employees", type=int, default=200)
    parser.add_argument("--min-seniority", type=int, default=3,
                        help="1=any employee ... 5=founder/CEO only")
    parser.add_argument("--radius-km", type=int, default=40,
                        help="how far from the city centre to search (default 40)")
    parser.add_argument("--companies", type=int, default=10)
    parser.add_argument("--per-company", type=int, default=3)
    parser.add_argument("--no-verify", action="store_true")
    parser.add_argument("--csv", help="also write results to this file")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    if not args.location and not args.keyword:
        parser.error("give me at least --location or --keyword")

    store.init_db()
    criteria = {
        "industry": args.industry,
        "location": args.location,
        "keyword": args.keyword,
        "min_employees": args.min_employees,
        "max_employees": args.max_employees,
        "min_seniority": args.min_seniority,
        "max_companies": args.companies,
        "contacts_per_company": args.per_company,
        "radius_m": args.radius_km * 1000,
        "verify_emails": not args.no_verify,
    }

    def progress(message):
        if not args.quiet:
            print("  " + message, file=sys.stderr)

    found, notes = pipeline.run(criteria, on_progress=progress)
    for note in notes:
        print("note: %s" % note, file=sys.stderr)

    rows = []
    for company in found:
        header = company["name"]
        if company.get("domain"):
            header += "  (%s)" % company["domain"]
        print("\n%s" % header)
        print("  %s" % (company.get("size_note") or ""))
        if not company.get("contacts"):
            print("  no senior contacts found")
        for contact in company.get("contacts") or []:
            print("  %-28s %-34s %-38s %s%%" % (
                contact["name"][:28],
                (contact.get("title") or "")[:34],
                contact.get("email") or "-",
                int((contact.get("email_confidence") or 0) * 100),
            ))
            rows.append({
                "company": company["name"],
                "domain": company.get("domain"),
                "name": contact["name"],
                "title": contact.get("title"),
                "email": contact.get("email"),
                "confidence": contact.get("email_confidence"),
                "status": contact.get("email_status"),
                "basis": contact.get("email_basis"),
                "linkedin": contact.get("linkedin_url"),
            })

    search_id = store.save_results(
        "%s in %s" % (industries.get(args.industry)["label"], args.location or "anywhere"),
        criteria, found, notes,
    )
    print("\nsaved as search #%d (%d companies)" % (search_id, len(found)), file=sys.stderr)

    if args.csv and rows:
        with open(args.csv, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
        print("wrote %s" % args.csv, file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
