# 작성 후 검토와 재측정

`rebuild run`의 페이지 설계 이후 실제 본문을 작성하고 아래 순서로 확인합니다.

```text
Page design -> Editorial copy -> Offline review -> Human approval -> Publish -> Repeated observations
                                  |                                       |
                          Supplied profile comparison             Same measurement conditions
```

## 본문·프로필 검토

```sh
python tools/seo_geo.py rebuild review templates/rebuild/content-review.json --out out/content-review
```

입력 예제는 가상 데이터입니다. `pages[].body`에 완성된 텍스트 또는 Markdown을 넣고, `claims`에 주장과 출처 원문을 연결합니다. `source_text`는 직접 확인해 제공한 자료이며 프로그램이 URL에서 받아오지 않습니다. 숫자·후기를 지어내지 말고 누락된 자료는 검토 항목으로 남기세요.

자동 검토는 초안 표시, 제공된 주장·인용의 문자열 일치, 날짜 형식, 명시적 프로필 필드 차이를 확인합니다. 일치해도 사실 검증이나 의미적 정확성 통과를 뜻하지 않습니다. 질문에 제대로 답하는지, 출처가 신뢰할 만한지, 비교가 공정한지는 사람이 검토합니다. FAQ·통계·인용 개수를 강제하지 않습니다.

`identity`는 검증한 기준 정보, `profiles`는 직접 수집한 외부 소개 정보입니다. 누락은 미확인으로, 다른 값은 검토 대상으로 표시합니다. 동의어·번역 표현을 자동으로 오류 판정하지 않습니다. 보고서는 실행별 새 폴더의 JSON·Markdown·HTML로 저장되며 외부 프로필을 수정하거나 게시하지 않습니다.

## 반복 측정

```sh
python tools/seo_geo.py rebuild protocol templates/rebuild/brief.json --repeats 3 --out out/protocol
```

질문의 `type`을 `brand` 또는 `nonbrand`로 분류하세요. 브랜드 질문은 비브랜드 측정에서 제외하고, 미분류 질문은 먼저 검토합니다. 계획에는 아직 답변이 없으며 결과를 채우기 전에는 노출 결과로 사용할 수 없습니다. 기존 `measure`의 로그 수집·집계를 사용하고 실제 관측 조건을 함께 보관하세요. 프로토콜 자체는 측정 로그로 직접 import하지 않습니다.

발행 전 기준선과 발행 후 측정에서 질문·제품·모델·검색 모드·지역·언어·세션 조건을 맞추고 시간을 기록합니다. 조건이 바뀌면 별도 비교군으로 취급합니다. 브랜드 언급과 실제 자사 URL 인용은 분리하고, 차단·오류를 미노출로 기록하지 않습니다. 3회는 탐색용 표본이며 안정적인 노출 확률을 추정하기에 충분하다는 뜻이 아닙니다.

페이지 설계의 `plan.json`에도 `measurement_protocol`이 들어갑니다. 자동화는 검토 자료와 측정 계획 생성까지입니다. 사실·표현 승인, 게시, 로그인 제품의 실제 응답 수집은 별도 실행이 필요합니다. Aside는 해당 제품 로그인 세션이 필요한 경우에만 선택하며 이 명령들은 호출하지 않습니다.
