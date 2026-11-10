# Contributing

Thank you for helping. Corrections to the mapping are the most valuable contributions: a
section that lost words, a wrong heading, a citation that does not resolve.

## Set up

Python 3.12 or later, standard library only. `pdftotext` (poppler) is needed only to re-map
the Constitution PDFs, and `zstd` only to compress release assets.

```sh
git clone https://github.com/bajalabs/us-federal-law.git
cd us-federal-law
python3 -m unittest              # or: uv run python -m unittest
```

## Build

```sh
python3 -m usfl fetch all        # about 1 GB into ./source, verified against manifests/
python3 -m usfl build            # dist/us-federal-law.sqlite, about 3 minutes
python3 -m usfl verify           # non-zero exit on any problem
python3 -m usfl export           # dist/release/: compressed assets and SHA256SUMS
```

A single title is enough to work on a mapper:

```sh
python3 -m usfl fetch usc --only 9
python3 -m usfl build --corpus usc --only 9 --db dist/usc-9.sqlite
python3 -m usfl verify --db dist/usc-9.sqlite --allow-partial
```

## Propose a change

1. Open an issue first for anything larger than a fix (the data error template asks for the
   law, the article key, the release and the official source's URL).
2. A mapper change comes with a small fixture in `tests/fixtures/` (trimmed from the real
   source; the texts are public domain) and a test that fails without the change.
3. Run `python3 -m unittest`. If the change affects the data, also run a build and
   `python3 -m usfl verify` on at least the titles it touches, and say what moved.
4. Keep the schema the same as the sister repository `leyes-federales-mx`. A schema change is
   proposed in an issue and lands in both.
5. Never commit `source/`, `dist/`, databases or archives. Only manifests, code, schema,
   fixtures, samples and the Constitution's text are tracked.

## Refresh to a new edition

```sh
python3 -m usfl fetch all --refresh   # re-pins manifests/*.json to the current editions
python3 -m usfl build && python3 -m usfl verify && python3 -m usfl export --samples samples
```

Commit the changed manifests and samples with a `CHANGELOG.md` entry that names the new
editions (USC release point, eCFR date, last Public Law), then attach `dist/release/*` to a
GitHub Release.
