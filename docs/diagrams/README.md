# Runtime architecture — 3.0.0

- [GitHub preview](../../assets/runtime-architecture.svg)
- [Interactive HTML](runtime-architecture.html): download and open locally; GitHub shows source.
- [Editable architecture specification](runtime-architecture.json)

The diagram follows `tools/rebuild_run.py` stage order and distinguishes automatic execution, saved local state, human publication, and separately invoked measurement. The technical `generate` path remains separate.

The interactive viewer was generated with Archify 2.17. Its MIT notice is in [ARCHIFY-LICENSE.txt](ARCHIFY-LICENSE.txt); this does not change the repository's license.

Validation: showcase 9/9, zero composition errors or warnings. Browser containment passed at 1440×900, 1600×1000, 1920×1080 and 2048×1320. Light/dark screenshots were visually reviewed. Generated browser evidence stays outside the repository under `out/`.
