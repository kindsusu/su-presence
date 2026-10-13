# 재구축 실행 안내

한 번에 연결 실행하려면 [자동 실행 안내](rebuild-automation-guide.md)의 `rebuild run`을 사용한다. 아래는 기존 개별 명령 사용법이다.

기존 진단과 명시적으로 수집한 검색·AI 출처를 비교해 질문 의도별 페이지 설계와 작업표를 만드는 절차다. CLI는 호출 시에만 실행된다. 이 계획은 기술 수정 초안인 `generate`와 별개이며 자동 배포·발행으로 이어지지 않는다. [실행 아키텍처](../architecture.ko.md) · [CLI 상세](../../tools/README.md)

저장소 루트에서 다음 순서로 실행한다. `example.com`은 대상 공개 호스트로, 경로는 실제 산출물로 바꾼다.

```bash
python tools/seo_geo.py audit https://example.com --out out --max-pages 300
python tools/measure.py init out/example.com/audit.json
python tools/seo_geo.py rebuild init out/example.com/audit.json
# out/example.com/rebuild/brief.json 및 observations.json을 검토·작성
python tools/seo_geo.py rebuild research out/example.com/rebuild/brief.json --observations out/example.com/rebuild/observations.json --out out/example.com/rebuild
# 위 명령이 출력한 research.json 경로를 다음 명령에 넣는다
python tools/seo_geo.py rebuild plan out/example.com/audit.json --brief out/example.com/rebuild/brief.json --observations out/example.com/rebuild/observations.json --research out/example.com/rebuild/run-YYYYMMDDTHHMMSS-xxxx/research.json --out out/example.com/rebuild
```

`init`은 audit에서 사이트 URL을, 기본 `measure/queries.json`에서 질문의 ID·문구를 복사한다. 질의 파일이 없으면 빈 질문 목록을 만든다. `--queries FILE`로 다른 파일을 지정할 수 있다. 새 `brief.json`은 `su-presence/rebuild-brief/1`이고, 기존 파일이 있으면 덮지 않고 오류를 반환한다. `observations.json`은 `su-presence/rebuild-observations/1` 빈 배열로 시작하며 기존 내용은 보존한다.

사람 또는 외부 조사자가 brief에 `queries`별 `intent_id`, `terms`, `required_sections`, `page_type`, `priority`, 검증된 `facts`와 `source_url`, 사이트 내 기존 `pages`, 관심 `channels`, 선언된 `competitors` URL을 채운다. 동일 의도로 묶을 질문은 같은 `intent_id`를 사용한다. `competitors`는 비교 대상으로 선언한 사이트일 뿐 검색 노출의 증거가 아니다. 관측 파일의 각 행에는 고유 `id`, 현재 질문과 일치하는 `query_id`·`query_text`, brief에 있는 `channel`, 시간대가 있는 `observed_at`, `outcome`을 넣는다. `observed`에는 실제 `url`과 근거 `evidence_url`인 공개 HTTP(S) URL이 필요하다. `unmeasured`·`error`는 인용 부재로 해석하지 않는다.

`research`는 brief·관측에 **이미 적힌 URL**과 자사 사이트 URL만 조사한다. 검색어로 새 결과를 찾지 않는다. 기본 최대 20페이지이며 `--max-pages`는 1~100이다. 공개 주소·robots·DNS·리다이렉트·크기·시간 제한을 적용한다. 결과는 새 `run-*` 폴더의 `research.json` (`su-presence/rebuild-research/1`)이다. 접근 실패나 페이지 제한으로 범위가 불완전하면 파일을 남기고 exit 2를 반환한다. 그 결과는 삭제하지 말고 미확인 근거로 취급한다.

`plan`은 파일만 읽는 오프라인 단계다. `--research`는 선택 사항이다. 관측 파일이나 측정 로그도 `--observations FILE`, `--log FILE`로 선택적으로 제공할 수 있다. 측정 로그에서 현재 질문 fingerprint가 맞는 웹 표면 관측 URL만 가져오며 API 및 검색 꺼짐 행은 제외한다. 로그의 경쟁 도메인 문자열만으로는 개별 페이지 관측을 만들지 않는다. 비교 대상 URL을 더 조사해야 하면 별도 관측에 정확한 URL과 시점을 기록한다.

`plan`은 새 `run-*` 폴더에 `plan.json` (`su-presence/rebuild-plan/1`), `guide.md`, 이스케이프된 `report.html`, 의도별 `pages/*.json`, `*.md`, `*.html`을 쓴다. `plan.json`은 증거 ID와 입력 파일 SHA-256을 기록하고, 기존 페이지·관측 출처·선언 경쟁 사이트를 구분한다. 채널별 미관측은 `unmeasured`이며, 불완전 crawl이나 차단된 조사 페이지는 없는 페이지의 증거가 아니다. 제목/H1 단어 일치는 의도 적합성 검토 단서일 뿐이다. 생성한 페이지 문구·스키마 제안은 사실 확인과 편집을 거쳐야 한다. `plan`은 `generate` 또는 배포 명령을 실행하지 않는다.

기술 초안이 필요하면 별도로 `python tools/seo_geo.py generate all out/example.com/audit.json --site <검증한-사실.json>`을 실행한다. 배포는 사람이 결정하고, 배포 후 `verify`, 재진단·재측정, `drift`로 확인한다. `drift`의 `next_due`는 14일 재측정일 계산값이며 예약 실행이 아니다. `seo_geo.py status`는 rebuild 실행 기록을 열거하지 않으므로 해당 `run-*` 폴더에서 결과를 확인한다.
