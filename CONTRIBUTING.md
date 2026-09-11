# Contributing · 기여 안내

한국어와 영어 이슈·PR을 받습니다. 버그는 재현 가능한 입력과 기대 결과를, 기능 제안은
해결하려는 실제 사용 사례를 먼저 설명해 주세요.

Issues and pull requests in Korean or English are welcome. Start with a reproducible problem
or a concrete use case. See [the documentation index](docs/README.md) for design and tool references.

## Local setup

Python 3.10+ is required. The Python tools use the standard library; no `pip install` is needed.
The optional shell inspector and its regression checks also require Bash and curl.

```bash
git clone https://github.com/kindsusu/su-presence.git
cd su-presence
python tools/seo_geo.py doctor
python -m unittest discover tests -q
bash tools/test_audit.sh
git diff --check
```

Tests use synthetic fixtures and local HTTP servers. They do not need paid API keys or external
site access. On Windows use Git Bash for the shell command; Python commands work in PowerShell.
CI runs the suite on Ubuntu, Windows and macOS with Python 3.10, 3.12 and 3.13.

## Changes and reviews

- Keep changes focused. Describe the observed behavior, the correction and the checks run.
- Add a regression case for a behavior bug. Documentation-only changes need link, example and diagram checks.
- Keep Korean and English instructions consistent. Use current official sources for engine-policy claims.
- Preserve existing data. Document schema changes and compatibility in [CHANGELOG.md](CHANGELOG.md).
- Do not turn an incomplete observation into a successful audit or a citation improvement.
- Keep credentials, customer reports and generated output out of commits. Use sanitized fixtures for reproductions.

## License

The project uses [PolyForm Noncommercial 1.0.0](LICENSE). Review the existing license before use or
contribution; this guide does not change its terms. Commercial licensing inquiries: scitusu@gmail.com.
