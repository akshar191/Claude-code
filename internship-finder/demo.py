"""Offline demo: run the whole pipeline against three fake company sites.

No internet, no API keys, no quota burned. Useful for seeing the output format
and for checking the crawler still works after you change the parsers.

    python demo.py
"""

import http.server
import socketserver
import threading

from finder import config

config.CRAWL_DELAY = 0.0  # no need to be polite to ourselves

from finder import pipeline  # noqa: E402

# Three companies, chosen to exercise the three confidence tiers.
SITES = {
    # Publishes one real address -> the house format gets learned and reused.
    "acmerobotics.test": {
        "/robots.txt": "User-agent: *\nAllow: /\n",
        "/": """<html><body><h1>Acme Robotics</h1>
              <p>We design surgical robotics for small hospitals.</p>
              <nav><a href="/leadership">Leadership</a></nav></body></html>""",
        "/leadership": """<html><body>
              <div><h3>Priya Raghavan</h3><p>Co-Founder &amp; CEO</p>
                   <a href="https://www.linkedin.com/in/priya-raghavan-8a41">in</a></div>
              <div><h3>Daniel O'Brien</h3><p>VP of Engineering</p></div>
              <div><h3>Sofia Marino</h3><p>Director of Operations</p></div>
              <div><h3>Marcus Webb</h3><p>Junior Technician</p></div>
              <p>Reach Priya at
                 <a href="mailto:priya.raghavan@acmerobotics.test">this address</a>.</p>
              </body></html>""",
    },
    # Names people but publishes nothing -> guesses from base rates only.
    "brightfieldeng.test": {
        "/robots.txt": "User-agent: *\nAllow: /\n",
        "/": """<html><body><h1>Brightfield Engineering</h1>
              <p>Thermal systems consulting for industrial clients.</p>
              <nav><a href="/about-us">About Us</a></nav></body></html>""",
        "/about-us": """<html><body>
              <li>Hannah Okafor — Founder &amp; Principal Engineer</li>
              <li>Tom Bergstrom — Director of Engineering</li>
              </body></html>""",
    },
    # Nobody named at all -> falls back to the published shared inbox.
    "northpointdesign.test": {
        "/robots.txt": "User-agent: *\nAllow: /\n",
        "/": """<html><body><h1>Northpoint Design</h1>
              <p>A product design studio.</p>
              <nav><a href="/contact">Contact</a></nav></body></html>""",
        "/contact": """<html><body><p>Say hello:
              <a href="mailto:hello@northpointdesign.test">hello@northpointdesign.test</a>
              </p></body></html>""",
    },
}


def serve(pages):
    """Start a throwaway server for one fixture site, return its port."""

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = pages.get(self.path)
            if body is None:
                self.send_error(404)
                return
            encoded = body.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, *args):
            pass

    server = socketserver.TCPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, server.server_address[1]


def between(html, start, end):
    return html.split(start)[1].split(end)[0].strip()


def main():
    servers = []
    criteria = pipeline.normalize(
        {
            "industry": "mechanical_engineering",
            "min_seniority": 3,
            "max_employees": 200,
            # The .test domains have no MX records, so there is nothing to
            # verify against. Real runs leave this on.
            "verify_emails": False,
        }
    )

    print("\nDirector and up, companies under 200 people.")
    print("Verification off: .test domains have no MX records.\n")

    try:
        for domain, pages in SITES.items():
            server, port = serve(pages)
            servers.append(server)

            company = pipeline.process_company(
                {
                    "name": between(pages["/"], "<h1>", "</h1>"),
                    "domain": domain,
                    "website": "http://127.0.0.1:%d/" % port,
                    "description": between(pages["/"], "<p>", "</p>"),
                    "source": "openstreetmap",
                },
                criteria,
            )

            print("=" * 78)
            print("%s   [email pattern: %s]" % (
                company["name"], company.get("email_pattern") or "not established"
            ))
            print("crawled %d page(s)" % len(company["pages_crawled"]))
            for contact in company["contacts"]:
                print("\n  %s — %s" % (contact["name"], contact.get("title") or ""))
                print("    %-44s %3d%%" % (
                    contact.get("email") or "no address found",
                    int((contact.get("email_confidence") or 0) * 100),
                ))
                print("    %s" % (contact.get("email_basis") or ""))
                if contact.get("alternates"):
                    print("    if it bounces, try: %s"
                          % ", ".join(contact["alternates"][:3]))
            print()
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    main()
