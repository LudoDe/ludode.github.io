# ludode.github.io

My personal research website and browser tools.

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

Each tool gets its own folder under `tools/`, plus a card on `tools/index.html` and in the software section of the home page. Keep calculations in a separate module so they can be tested with Node.

## Content sources

Biographical information comes from my current CV and public profiles. The website only shows a selection of publications; the full list is in the CV.

- `AlessioLudovicoDeSantisCV.pdf` is the academic CV shown in the page preview.
- `AlessioLudovicoDeSantisCV-Industry.pdf` is the shorter industry CV linked underneath it.
