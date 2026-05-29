"""Edge Validation — 실거래 전환 전 "이 전략이 진짜 돈을 버는가" 검증.

이미 쌓인 페이퍼 트레이딩 데이터(orders, persona_decisions, risk_reviews)와 매 거래일
잔고 스냅샷(daily_equity_snapshot)으로 실제 성적을 정직하게 측정한다.

모듈
----
- snapshot   : 일별 자산 스냅샷 기록 (Phase 0)
- roundtrips : orders FIFO 원가 매칭 → 라운드트립 (Phase 1)
- analytics  : 실현 순손익·승률·손익비·기대값·자산곡선 (Phase 1/2)
- benchmark  : KOSPI 매수후보유 대비 알파 (Phase 1)
- confidence : LLM 확신도 구간별 성적 + 위험게이트 override 분석 (Phase 2)
- scorecard  : 표본 등급 + go/no-go 판정 + 한계 푸터 + 실거래 준비 게이트 (Phase 1/3)
- report     : 위 모듈 조립 → CLI 출력 / 텔레그램
"""
