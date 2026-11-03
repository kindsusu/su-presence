# 측정·재구축 연결 사이클 사용법

`cycle` 설정을 추가하면 측정 작업 큐, 실제 인용 출처 조사, 페이지 설계와 완성 원고 검토가 같은 실행 폴더에 연결됩니다. 사이트 수정·발행·배포는 수행하지 않습니다. 고객별 입력과 결과는 Git에서 제외된 `out/` 아래에 보관하세요.

## 실행

브리프 질문에 `type: nonbrand` 또는 `type: brand`를 명시합니다. 유형이 없는 질문은 확인 대기로 남고, 측정이 끝난 것으로 표시되지 않습니다. 기존 설정에 아래 항목을 추가합니다.

```json
{
  "brief_file": "brief.json",
  "cycle": {"repeats": 3}
}
```

위 JSON은 기존 설정에 추가할 항목 예시입니다. 검색·분석 제공자 설정은 기존 실행 설정을 유지합니다. `cycle`이 없는 기존 실행은 이전 흐름을 유지합니다.

```powershell
python tools/seo_geo.py rebuild run https://example.com --topic "서비스 비교" --config out/example.com/config.json --out out/example.com/rebuild
python tools/seo_geo.py rebuild status out/example.com/rebuild/RUN_ID
python tools/seo_geo.py rebuild resume out/example.com/rebuild/RUN_ID --config out/example.com/config.json
```

`cycle.observations_file`, `cycle.log_file`, `cycle.queue_file`, `cycle.review_file`로 기존 입력을 연결할 수 있습니다. 경로는 설정 파일 기준입니다. 실제 측정 출처는 조사 대상에 추가되며, 검색 후보와 실제 인용 근거는 구분됩니다. 완성 원고는 현재 설계의 페이지 ID를 사용해야 합니다. 입력이 바뀌면 해당 입력에 의존하는 결과를 다시 계산합니다.

## 브라우저 실행 경계

CLI는 브라우저나 로그인 세션을 내장하지 않습니다. 브라우저 제어가 가능한 호스트가 `BrowserBridge.execute`와 `reconcile`을 구현하거나 CLI 작업을 받아 처리해야 실제 AI 화면을 조작할 수 있습니다. 코드에 포함된 제품별 안내는 화면 선택자의 실서비스 검증을 대신하지 않습니다. 현재 테스트는 가상 브리지로 작업 배정·결과 검증·재개 계약을 검증합니다.

- `browser next QUEUE`는 질문을 전송하지 않고 대기 작업 하나를 점유합니다.
- 호스트는 정확한 질문을 새 세션에 한 번 전송한 뒤 `browser submit`으로 세션 근거를 기록합니다.
- 실제 응답 완료와 출처 확인 범위를 담은 JSON을 `browser record`로 기록합니다.
- 전송 후 중단된 작업은 기존 세션을 다시 확인하며 자동 재전송하지 않습니다.
- 로그인·할당량·UI 변경은 차단 사유로 남깁니다. 미전송이 확인된 작업만 근거를 남겨 `requeue`할 수 있습니다.
- 기본 브라우저 호스트를 우선하며 Aside는 명시적으로 선택되고 사용 가능한 로그인 호스트일 때 연결할 수 있습니다. 토큰을 추출하거나 결제를 진행하지 않습니다.

```powershell
python tools/seo_geo.py rebuild browser status out/example.com/rebuild/RUN_ID/browser-queue.json
python tools/seo_geo.py rebuild browser next out/example.com/rebuild/RUN_ID/browser-queue.json
python tools/seo_geo.py rebuild browser --help
```

## 결과 판단

자연검색 노출, AI 본문 브랜드 언급, 인용 확인 여부를 별도로 봅니다. 응답이 끝나지 않았거나 출처를 일부만 확인한 음성 결과는 ‘인용 없음’의 분모에 넣지 않습니다. 일부 출처에서 자사 인용을 실제로 찾았다면 그 양성 근거는 보존합니다. 기존 데이터에 확인 범위가 없으면 미상으로 유지합니다.

보고서의 회차 수는 매체·질문별 실제 기록입니다. 반복 횟수만으로 대표성이나 개선 효과가 보장되지는 않습니다. 제안된 다음 측정일도 자동 예약을 뜻하지 않습니다.

원고 검토는 제공된 문장·근거의 문자 단위 검사입니다. 자사 약관·금액·승인 조건의 진실성 및 발행 승인은 사람이 확인해야 합니다. 측정·조사·검토가 남은 실행을 전체 완료로 표시하지 않습니다.

[영문 실행 흐름과 아키텍처](measurement-cycle.md)
