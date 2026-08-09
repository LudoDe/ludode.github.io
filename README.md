# ludode.github.io

Personal research site for Alessio Ludovico De Santis. It is deliberately a small static site: plain HTML and CSS, with JavaScript only where a tool needs it.

## Local preview

From the repository root:

```bash
python -m http.server 8000
```

Then open `http://localhost:8000`.

## Browser tools

Tools live in their own folders under `tools/`. The first one is the GBM orbit-window calculator:

```text
tools/gbm-orbit-windows/
├── index.html
├── calculator.js
├── app.js
└── calculator.test.mjs
```

Run its tests with:

```bash
node --test tools/gbm-orbit-windows/calculator.test.mjs
```

When adding another tool, give it a self-contained folder and add a card to `tools/index.html` and the software section on the home page. Keeping the calculation in a separate module makes it possible to test without a browser.

## Content sources

Biographical information comes from the current CV and verified public profiles. Publications are intentionally a selected list; ORCID and INSPIRE are linked for the full record.
