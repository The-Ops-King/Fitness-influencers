# Post Builder

A single-page carousel post builder for Instagram/LinkedIn-style 1080 × 1350 slides.
Everything runs in the browser on a `<canvas>` — no build step, no backend, no data
leaves the page.

## What it does

- Write slide copy in the left rail; tap any word to accent it in green.
- Add or remove slides, and optionally end the carousel with a CTA slide
  (a blank line in the CTA text splits the ask from its supporting line).
- Set the handle, name, CTA destination line, and the CTA arrow direction.
- Toggle the "swipe" cue on every slide but the last.
- Export the current slide, or all slides, as PNGs at full 1080 × 1350.

Arrow keys move between slides when focus isn't in a text field.

## Running locally

It's a static file — serve the directory and open it:

```
python3 -m http.server 8000
# then open http://localhost:8000
```

Opening `index.html` straight off the filesystem also works, but serving it over
HTTP is the closer match to production.

## Deploying to Vercel

The repo is a zero-config static deploy: `index.html` at the root, no framework,
no build command.

**From the dashboard** — import the GitHub repo, set the production branch to
this branch, and leave Framework Preset as "Other". No build or output settings
needed.

**From the CLI:**

```
npx vercel        # preview deploy
npx vercel --prod # production deploy
```

`vercel.json` only sets response headers: `X-Robots-Tag: noindex, nofollow` so
the tool stays out of search results (matching the `robots` meta tag in the
page), plus `Referrer-Policy` and `nosniff`, and a no-cache rule on the HTML so
edits show up immediately after a redeploy.

Note that the deployment is public to anyone with the URL. If you want it
private, use Vercel's Deployment Protection (Settings → Deployment Protection)
on the project.

## Layout

```
index.html    the whole app — markup, styles, canvas renderer, and the
              base64 figure drawn on the CTA slide
vercel.json   response headers for the static deploy
```
