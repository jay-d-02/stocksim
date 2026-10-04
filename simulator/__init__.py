"""Event-driven Stock Market Simulator — 도메인 계층

웹·DB와 무관한 순수 파이썬 모듈 모음.
- catalog  : 종목·업종 민감도·사건 목록 (게임 규칙의 숫자)
- engine   : 시장 진행 (장 시작·5분 틱·마감, 사건 발생, 가격 결정)
- ledger   : 매매 계산 (수수료·거래세·평균 매수가·실현손익)
- analysis : 거래 기록으로 투자 성향 분석
"""
