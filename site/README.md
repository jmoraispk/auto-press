# site/

Static landing page for **CodeAway** — single `index.html`, single
`style.css`, one small `script.js`. No build step, no framework, no
dependencies.

## Preview locally

```bash
python -m http.server 5500 -d site
# open http://localhost:5500
```

## Deploy

The repo root contains `vercel.json` so a default Vercel import
("Other / no framework") deploys this folder as-is.

- **Vercel** — import the GitHub repo, no overrides needed. Point
  `codeaway.dev` at the project once the DNS is ready.
- **Cloudflare Pages** — build command empty, output directory `site`.
- **GitHub Pages** — Settings → Pages → Source: `main` branch /
  `/site` folder.

## Editing

- Hero copy: `index.html` → `.hero` block.
- Features grid: `index.html` → `.feature-grid`. Eight cards by
  default; drop or duplicate as projects evolve.
- Open-source banner: `index.html` → `.banner`.
- Feature-request channels: `index.html` → `.request-channels`. The JS
  side lives in `script.js` (`REPO` and `CONTACT_EMAIL` constants at
  the top — change `CONTACT_EMAIL` once a `codeaway.dev` alias is
  live).
- Colour tokens: `style.css` → `:root`. `--accent` and `--accent-2`
  drive every gradient on the page.
