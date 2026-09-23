#!/usr/bin/env python3
"""Wrap the artifact page source into a standalone document for GitHub Pages.

The artifact host supplies a document skeleton; a self-hosted copy needs its
own. Generating rather than hand-editing keeps the two versions identical.

    python3 scripts/build-page.py SOURCE.html docs/index.html
"""

import sys
from pathlib import Path

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

    // The funnel that matters: did they take the install command away with them?
    var COPY_EVENTS = {
      install: "copied_install_command",
      register: "copied_register_command",
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

HEAD = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="description" content="Connect Claude to your Garmin Connect account: read your runs, splits and heart rate, and write structured workouts onto your watch.">
<meta property="og:title" content="Garmin for Claude">
<meta property="og:description" content="Let Claude read your running data and build workouts onto your watch.">
<meta property="og:type" content="website">
<style>
  html { color-scheme: light; }
  body { margin: 0; }
  img { max-width: 100%; }
  [hidden] { display: none !important; }
</style>
"""


def main() -> int:
    source, dest = Path(sys.argv[1]), Path(sys.argv[2])
    body = source.read_text()

    marker = "</style>\n"
    if marker not in body:
        print("No </style> found; is this the artifact source?", file=sys.stderr)
        return 1
    split = body.index(marker) + len(marker)
    head, page = body[:split], body[split:]

    dest.write_text(
        HEAD + head + ANALYTICS + "</head>\n<body>\n" + page + "\n</body>\n</html>\n"
    )
    print(f"wrote {dest} ({dest.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
