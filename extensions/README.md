# AutoScribe Extensions

Authoritative local runtime for replaceable AutoScribe extensions.

- `engines/` contains runtime engine implementations.
- `scripts/` contains executable content transforms and utilities.
- `pandoc/` contains runtime defaults, filters, references, and templates.

The pipeline loads engines and scripts directly from this checkout. Local
operation does not require an upload or cache-materialisation step. Core
orchestration, dispatch, persistence, and UI logic do not belong here.
