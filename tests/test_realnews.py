"""실제 뉴스 분류 테스트: 제목·요약의 단어로 요인과 방향을 찾고, 업종 영향을 게임 민감도표로 계산"""
import pytest

from server.services import realnews as RN


@pytest.mark.parametrize("title,expect", [
    ("한은, 기준금리 0.25%p 인하…연 2.25%", {"rate": -1}),
    ("기준금리 3회 연속 동결", {"rate": 0}),
    ("원·달러 환율 1,400원 돌파", {"fx": 1}),
    ("원화 강세에 환율 1,350원대로", {"fx": -1}),          # 원화가 강해지면 환율은 내려감
    ("달러 강세 지속", {"fx": 1}),
    ("국제유가 급등…WTI 배럴당 90달러", {"oil": 1}),
    ("치솟는 유가에 항공사 비상", {"oil": 1}),             # 방향 단어가 요인 단어 앞에 있을 때
    ("9월 수출 12% 증가, 반도체 호조", {"econ": 1}),
    ("소비심리 위축에 경기 둔화 우려", {"econ": -1}),
    ("경기도, 청년 일자리 사업 확대", {}),                  # 지명 '경기도'는 경기가 아님
    ("국가대표 축구 경기 결과", {}),                        # 방향 단어가 없으면 분류하지 않음
    # 실제 뉴스에서 틀렸던 제목들 (2026-10-04)
    ("연준 10월 금리 인상 기대 후퇴…달러-원 1345~1370원 전망", {"rate": -1}),   # '기대 후퇴'는 뜻을 뒤집음
    ("美 금리인상 ‘주춤’ 전망에도…“韓 주담대는 계속 오를 것”", {"rate": -1}),
    ("“원·달러 환율 다시 오르나” 1400원 대 넘어설 전망", {"fx": 1}),
    ("달러 하락에 베팅한 곱버스 ETF, 3분기 수익률 상위권", {"fx": -1}),
    ("대출↑환율↓…4대 금융, 3분기 최대 실적 눈앞", {"fx": -1}),
    ("고금리·고유가에도 나스닥100 굳건한 이유", {}),           # 고금리·고유가는 변화가 아니라 수준
    ("비트코인(BTC), 골든크로스에 ETF 폭발...10만 달러 돌파 기대 ↑", {}),   # 금액 단위 '달러'
    ("원화 강세에 수출주 실적 비상?", {"fx": -1}),            # 수출주 = 수출 기업 주식
    ("명절 지나자 다시 한숨…소상공인·中企 경기전망 나란히 ‘뚝’", {"econ": -1}),
    ("경기지역 9월 소비자심리지수 106.3", {}),                # 지명 '경기지역'
    ("‘왕과 전 챔피언의 대결’ 굽네 ROAD FC 079, 메인부터 1부까지 15경기 확대", {}),
])
def test_classify_title(title, expect):
    assert RN.classify(title) == expect


def test_sector_impacts_follow_game_sensitivity():
    imp = RN.sector_impacts({"fx": 1})                      # 환율 상승 = 원화 약세
    assert imp["자동차"] > 0 and imp["반도체"] > 0 and imp["항공"] < 0 and imp["식품"] < 0
    assert "은행" not in imp                                 # 영향이 작은 업종은 뺌
    assert RN.sector_impacts({"rate": 0}) == {}             # 동결은 업종 차이 없음


def test_parse_items_cleans_and_drops_unclassified():
    payload = {"items": [
        {"title": "&quot;<b>국제유가</b>&quot; 급락", "description": "산유국 <b>증산</b> 합의에 환율도 올랐다",
         "originallink": "https://www.yna.co.kr/view/1", "link": "https://n.news.naver.com/1",
         "pubDate": "Sat, 04 Oct 2026 14:30:00 +0900"},
        {"title": "연예 소식", "description": "", "originallink": "https://ex.com/2", "link": "x",
         "pubDate": "Sat, 04 Oct 2026 14:31:00 +0900"},
        {"title": "기준금리 인상", "description": "", "originallink": "", "link": "https://n.news.naver.com/3",
         "pubDate": "엉뚱한 날짜"},
    ]}
    rows = RN.parse_items(payload, "국제유가")
    assert [r["title"] for r in rows] == ['"국제유가" 급락', "기준금리 인상"]
    assert rows[0]["source"] == "www.yna.co.kr" and rows[0]["factors"] == {"oil": -1}   # 요약의 '환율'은 안 봄
    assert rows[0]["summary"] == "산유국 증산 합의에 환율도 올랐다"
    assert rows[0]["published_at"].utcoffset().total_seconds() == 9 * 3600
    assert rows[1]["link"] == "https://n.news.naver.com/3"         # 언론사 링크가 없으면 네이버 링크
