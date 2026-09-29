#!/usr/bin/env python3
"""Wrap the artifact page source into a standalone document for GitHub Pages.

The artifact host supplies a document skeleton; a self-hosted copy needs its
own. Generating rather than hand-editing keeps the two versions identical.

    python3 scripts/build-page.py SOURCE.html docs/index.html [SITE_PATH]

SITE_PATH is where the result will be served, used for the canonical and
og:url tags; it defaults to the site root. The title and description come out
of the source's own <title> and <meta name="description">, so a second page
describes itself rather than inheriting the front page's copy.
"""

import re
import sys
from pathlib import Path

SITE = "https://garmin.daash.run"

# Analytics belongs to the hosted page only. The Artifact copy runs under a
# CSP that blocks both the script host and PostHog's ingestion endpoint, so
# injecting it there would just log errors and send nothing.
#
# Configured to leave nothing on the visitor's machine: no cookies, no
# localStorage, no autocapture, no session recording, no person profiles. That
# keeps the page free of a consent banner, at the cost of unique-visitor counts
# being unreliable — each page load looks like a new anonymous visitor. Event
# counts are unaffected.
ANALYTICS = """
<script>
  !function(t,e){var o,n,p,r;e.__SV||(window.posthog=e,e._i=[],e.init=function(i,s,a){
  function g(t,e){var o=e.split(".");2==o.length&&(t=t[o[0]],e=o[1]);
  t[e]=function(){t.push([e].concat(Array.prototype.slice.call(arguments,0)))}}
  (p=t.createElement("script")).type="text/javascript",p.crossOrigin="anonymous",p.async=!0,
  p.src=s.api_host.replace(".i.posthog.com","-assets.i.posthog.com")+"/static/array.js",
  (r=t.getElementsByTagName("script")[0]).parentNode.insertBefore(p,r);
  var u=e;for(void 0!==a?u=e[a]=[]:a="posthog",u.people=u.people||[],
  u.toString=function(t){var e="posthog";return"posthog"!==a&&(e+="."+a),
  t||(e+=" (stub)"),e},u.people.toString=function(){return u.toString(1)+".people (stub)"},
  o="init capture register register_once unregister opt_out_capturing has_opted_out_capturing opt_in_capturing reset group".split(" "),
  n=0;n<o.length;n++)g(u,o[n]);e._i.push([i,s,a])},e.__SV=1)}(document,window.posthog||[]);

  posthog.init("phc_fjWuvRykdQ6CmNOATEnKvryGf5TVBT3z2ob7p8zJ787", {
    api_host: "https://eu.i.posthog.com",
    persistence: "memory",
    autocapture: false,
    disable_session_recording: true,
    capture_pageleave: false,
    person_profiles: "never"
  });

  document.addEventListener("DOMContentLoaded", function () {
    function track(name, props) {
      try { window.posthog && posthog.capture(name, props || {}); } catch (e) {}
    }

    // The funnel that matters: did they take the address away with them, and
    // by which route? copied_install_command now means they chose to self-host
    // rather than sign in here, which is worth knowing on its own.
    var COPY_EVENTS = {
      connector: "copied_connector_url",
      "connector-gpt": "copied_connector_url_chatgpt",
      install: "copied_install_command",
      coach: "copied_training_prompt"
    };
    document.querySelectorAll("button.copy").forEach(function (btn) {
      btn.addEventListener("click", function () {
        track(COPY_EVENTS[btn.dataset.copy] || "copied_other", {});
      });
    });

    // Which problems people actually hit.
    document.querySelectorAll("details").forEach(function (d) {
      d.addEventListener("toggle", function () {
        if (!d.open) return;
        var q = d.querySelector("summary");
        track("opened_troubleshooting", {question: q ? q.textContent.trim() : null});
      });
    });

    document.querySelectorAll("a[href^='http']").forEach(function (a) {
      a.addEventListener("click", function () {
        var dest = a.href.indexOf("github.com") > -1 ? "github" : "other";
        track("clicked_outbound", {destination: dest});
      });
    });

    // Did they read far enough to reach the setup steps?
    var steps = document.querySelector("ol.steps");
    if (steps && "IntersectionObserver" in window) {
      var seen = false;
      new IntersectionObserver(function (entries) {
        if (!seen && entries.some(function (x) { return x.isIntersecting; })) {
          seen = true;
          track("reached_setup_steps", {});
        }
      }, {threshold: 0.2}).observe(steps);
    }
  });
</script>
"""

# The inline SVG is the same mark as the site's accent: a route climbing over
# three waypoints. Inline so there is no second request and nothing to 404.
FAVICON = """<link rel="icon" href="data:image/svg+xml,\
%3Csvg xmlns=&#39;http://www.w3.org/2000/svg&#39; viewBox=&#39;0 0 32 32&#39;%3E\
%3Crect width=&#39;32&#39; height=&#39;32&#39; rx=&#39;7&#39; fill=&#39;%232F6B4F&#39;/%3E\
%3Cpath d=&#39;M6 22 L12.5 14 L18 18.5 L26 8.5&#39; fill=&#39;none&#39; stroke=&#39;%23F4F6F3&#39; \
stroke-width=&#39;3.4&#39; stroke-linecap=&#39;round&#39; stroke-linejoin=&#39;round&#39;/%3E%3C/svg%3E">"""

BASE_STYLE = """<style>
  html { color-scheme: light; }
  body { margin: 0; }
  img { max-width: 100%; }
  [hidden] { display: none !important; }
</style>
"""


def head_for(title: str, description: str, path: str) -> str:
    """The document head for one page, built from that page's own metadata."""
    url = SITE + path
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="description" content="{description}">
<link rel="canonical" href="{url}">
<meta property="og:title" content="{title}">
<meta property="og:description" content="{description}">
<meta property="og:type" content="website">
<meta property="og:url" content="{url}">
<meta property="og:site_name" content="Garmin for Claude">
<!-- Absolute URL: Slack, iMessage and the rest will not resolve a relative one.
     Cache-busted, because every platform caches previews aggressively. -->
<meta property="og:image" content="{SITE}/og.png?v=1">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta property="og:image:alt" content="Talk to your training — a rising elevation profile with three waypoints">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:image" content="{SITE}/og.png?v=1">
<meta name="theme-color" media="(prefers-color-scheme: light)" content="#F4F6F3">
<meta name="theme-color" media="(prefers-color-scheme: dark)" content="#0E1512">
{FAVICON}
{BASE_STYLE}"""


def meta_from(head: str) -> tuple[str, str]:
    """Pull the page's own title and description out of the source head."""
    title = re.search(r"<title>(.*?)</title>", head, re.S)
    description = re.search(
        r'<meta\s+name="description"\s+content="(.*?)"', head, re.S
    )
    if not title or not description:
        raise SystemExit(
            f"{'title' if not title else 'meta description'} missing from the "
            "source head — social previews and search results both need it."
        )
    return title.group(1).strip(), description.group(1).strip()


def main() -> int:
    source, dest = Path(sys.argv[1]), Path(sys.argv[2])
    path = sys.argv[3] if len(sys.argv) > 3 else "/"
    body = source.read_text()

    marker = "</style>\n"
    if marker not in body:
        print("No </style> found; is this the artifact source?", file=sys.stderr)
        return 1
    split = body.index(marker) + len(marker)
    head, page = body[:split], body[split:]

    # The description lives in the source so each page carries its own, but it
    # is re-emitted by head_for; leaving the original would duplicate the tag.
    head = re.sub(r'<meta\s+name="description"[^>]*>\n?', "", head, count=1)

    title, description = meta_from(body[:split])
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(
        head_for(title, description, path)
        + head
        + ANALYTICS
        + "</head>\n<body>\n"
        + page
        + "\n</body>\n</html>\n"
    )
    print(f"wrote {dest} ({dest.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
