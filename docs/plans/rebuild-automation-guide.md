# Rebuild automation · 재구축 자동 실행

사이트와 주제를 입력해 질문 초안 → 자사 진단 → 검색 출처 발견 → 본문 수집 → 비교 → 페이지 설계를 연결한다. `run`을 호출할 때만 실행되며 상주 서비스나 예약 작업은 아니다. 기존 `init/research/plan`도 그대로 사용할 수 있다.

## 시작 / Start

Python 3.10+에서 저장소 루트 기준으로 실행한다. 설정 예제를 복사해 필요한 한도를 조정하고, 검색을 사용할 경우 `BRAVE_SEARCH_API_KEY`를 환경 변수로 설정한다. 비밀값을 JSON이나 Git에 넣지 않는다.

```bash
python tools/seo_geo.py rebuild run https://example.com --topic "핵심 서비스" --locale ko-KR --config templates/rebuild/run-config.json --out out/example.com/rebuild
python tools/seo_geo.py rebuild status out/example.com/rebuild/<run-id>
python tools/seo_geo.py rebuild resume out/example.com/rebuild/<run-id>
```

실제 실행 폴더는 첫 명령이 출력한다. `--out`은 실행 폴더들의 상위 경로다. 기본 주제 질문은 같은 의도로 묶은 초안이다. 여러 의도·페이지를 정교하게 설계하려면 `brief_file`에 기존 [브리프](../../templates/rebuild/brief.json) 형식으로 질문·의도·기존 URL·확인된 사실을 작성한다. 자동 생성 질문은 사업 내용을 확정하지 않는다.

Run from the repository root with Python 3.10+. Set `BRAVE_SEARCH_API_KEY` in the environment to use Brave search. The command prints the run folder. Default topic questions share one conservative intent; supply `brief_file` for reviewed intent groups, page mappings and facts. No browser, publishing or deployment is triggered.

## 설정 / Configuration

```json
{
  "max_queries": 4,
  "max_audit_pages": 20,
  "max_research_pages": 30,
  "crawl_delay": 0.3,
  "channels": ["google", "bing", "naver", "daum", "chatgpt"],
  "discovery": {"provider": "brave", "max_requests": 4, "max_results": 5, "max_age_hours": 24},
  "analysis": {"provider": "none"}
}
```

- `brief_file`, `audit_file`: 기존 입력을 재사용할 때 지정한다. 경로는 설정 파일 위치를 기준으로 해석한다.
- `discovery.provider`: `brave` 또는 `import`. 가져오기는 `discovery.file`에 아래 형식의 파일을 지정한다.
- `analysis.provider`: `none`은 기존 구조·어휘 비교와 명시적인 한계를 제공한다. `openai`는 `model`을 명시하고 `OPENAI_API_KEY` 환경 변수를 설정해야 한다. `api_key_env`로 환경 변수 이름을 바꿀 수 있다. `analysis.analysis_file`로 외부 분석을 가져오는 방식도 지원한다.
- AI 분석 한도: `max_pages`(기본 12), `max_chars_per_page`(3000), `max_output_tokens`(3000), `timeout`(30초). 한도를 넘어 제외한 자료는 부분 분석으로 표시한다. 모델 사용 요금은 별도이며 금액 예측 대신 호출·입출력 한도를 적용한다.
- AI 분석을 선택하면 질문과 수집한 본문 일부를 해당 API에 전송한다. 로그인 세션이나 ChatGPT 인증 토큰을 사용하지 않는다.

Relative input paths resolve against the configuration file. `analysis.provider: openai` requires an explicit `model` and an environment API key; it sends bounded question/source excerpts to the Responses API. `none` is a limited structural fallback. API usage may incur provider charges; request and output limits are enforced instead of estimated currency budgets.

## 외부 검색 가져오기 / Host search import

```json
{
  "schema": "su-presence/rebuild-discovery/1",
  "candidates": [{
    "query_id": "q1",
    "query_text": "핵심 서비스",
    "url": "https://example.org/service/",
    "title": "Example service",
    "provider": "host-search",
    "locale": "ko-KR",
    "observed_at": "2026-10-13T10:00:00+09:00",
    "position": 1
  }]
}
```

위 데이터는 형식 설명용 가상 예시다. 실제 조사한 결과로 대체한다. 질문 문구·언어가 현재 실행과 일치해야 한다. 외부 검색을 맡은 에이전트는 실행 폴더의 `brief.json`을 읽고 결과를 이 형식으로 저장한 뒤 `resume --config <설정>`을 실행한다. CLI가 Codex의 내장 검색 도구를 직접 호출하지 않는다.

Brave 결과 위치는 Brave에서의 위치다. Google·Naver 순위나 AI 답변의 실제 인용을 뜻하지 않는다. 매체 분류는 호스트 기반 힌트이며 경쟁 사업자 판정은 검토가 필요하다. 검색 후보와 실제 노출 관측을 분리하며 실제 AI 관측은 기존 `collect/measure` 절차를 사용한다.

This is a synthetic format example, not an observation. Import exact current query text, locale, provider, timestamp and result position. Host agents perform their own search and hand off the file. Search candidates never become AI citations or another engine's ranking automatically.

## 분석 가져오기 / Analysis import

`analysis.analysis_file`은 `su-presence/rebuild-analysis-import/1` 객체다. `intents`의 각 항목에는 `intent_id`, `findings`, `section_suggestions`, 선택적인 `decision`이 들어간다. `findings`는 `kind`(`answer_scope`, `condition`, `evidence`, `freshness`, `conversion`), `summary`, `source_id`, `quote`를 포함한다. 섹션 제안은 `heading`, `purpose`, `source_id`, `quote`를 포함한다. 페이지 결정은 `action`(`keep`, `improve`, `consolidate`, `new`, `review`), `reason`, `source_id`, `quote`를 포함한다.

`source_id`는 현재 `research.json`의 0부터 시작하는 페이지 인덱스 `research-page-0` 형식이다. 출처는 해당 의도의 질문 ID(`research.pages[].query_ids`) 또는 자사 페이지 연결과도 일치해야 한다. 성공적으로 읽은 자료에 실제로 존재하는 인용만 허용한다. 인용이 존재한다는 검사는 해석의 정확성·사실성 검증과 다르다. 외부 자료는 검증된 자사 사실로 자동 편입하지 않는다. 자료가 변경되면 현재 research를 기준으로 분석을 다시 작성해야 한다.

Imports use `su-presence/rebuild-analysis-import/1`. Findings, section suggestions and optional decisions must reference observed research pages by zero-based `research-page-N` IDs and literal quotes. Exact quotation validation does not establish truth or semantic correctness; editorial review remains required.

## 재개와 결과 / Resume and outputs

`run.json`이 단계 상태·시각·시도 횟수·입력 fingerprint·산출물 해시를 저장한다. `resume`은 유효한 완료 결과를 재사용하고 변경·손상된 결과와 영향을 받는 후속 단계를 다시 계산한다. 재계산 전 결과는 `history/`에 보존한다. `resume --refresh`는 검색 이후 단계를 다시 실행한다. Brave 검색의 기본 재사용 기간은 24시간이다.

`status`로 단계별 상태와 산출물 유효성을 확인한다. 부분 완료 단계의 결과도 보존·재사용하므로 접근 실패 등을 재시도하려면 `resume --refresh`를 사용한다. 설정이 없으면 `needs_input`, 일부 수집·분석이 불완전하면 `partial`이다. 미측정 채널은 인용 없음으로 해석하지 않는다. 종료 코드 0은 설정한 범위의 실행 완료, 2는 입력 대기·부분 완료·실패다. 규칙 분석만 완료된 경우에도 의미 분석은 제한적이라는 경고가 남는다.

동시 실행은 잠금으로 차단한다. 강제 종료 후 잠금이 남았으면 소유 프로세스 종료를 확인하고 `resume --recover-lock`을 사용한다. 안전하게 소유자 종료를 확인할 수 없는 잠금은 자동 해제하지 않는다. 외부 요청 후 저장 전에 종료되면 요청이 중복될 가능성이 있다.

산출물은 `brief.json`, `audit.json`, `discovery.json`, `research.json`, `analysis.json`, `plan.json`, `guide.md`, `report.html`, `pages/*.json|md|html`이다. 페이지 파일은 검토용 설계이며 완성된 발행 콘텐츠가 아니다. 사업 조건·가격·인증·실적 확인, 최종 본문, URL 통합·삭제 결정, 코드 구현·배포는 별도 작업이다.

State and artifact hashes support resumption; previous outputs are archived before recomputation. `--refresh` reruns discovery onward. `status` reports artifact validity. Exit 0 means completion within the configured scope, not successful exposure. Exit 2 means partial, needs-input or failed. Review facts, copy, URL changes and publication separately. Generated files belong under ignored `out/` and must not be committed.

Aside는 로그인한 AI 웹 화면의 실제 관측이 필요하고 연결을 검증한 경우에만 외부 조사자가 선택한다. 이 실행기는 Aside를 자동 호출하지 않는다. [브라우저 연결 지침](../../ops/aside.md).

구현 계약 참고: [Brave 공식 API](https://api-dashboard.search.brave.com/app/documentation/web-search/get-started), [OpenAI 구조화 출력](https://developers.openai.com/api/docs/guides/structured-outputs).
