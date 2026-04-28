# Stock Research Bot (KR/US)

아래 기능을 갖춘 종목 리서치/공부 봇입니다.

## 기능

1. **리서치 자료 다운로드 및 읽기**
   - URL(PDF/HTML/TXT/JSON)을 다운로드하고 텍스트 프리뷰를 추출합니다.
2. **네이버 증권 차트 API 연동**
   - `api.finance.naver.com/siseJson.naver`에서 OHLCV 데이터를 받아 분석할 수 있습니다.
3. **DART Open API 공시/사업보고서 연동**
   - 회사명으로 `corp_code` 조회
   - 공시 목록 조회
   - 접수번호(`rcept_no`) 기준 원문 XML 다운로드
4. **3종 기관투자자 스타일 노트 생성**
   - Goldman 스타일 기본적 분석
   - Morgan Stanley 스타일 기술적 분석
   - JPMorgan 스타일 실적 분석

## 설치

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

DART 사용 시:

```bash
cp .env.example .env
# .env에 DART_API_KEY 입력
export DART_API_KEY=YOUR_DART_API_KEY
```

## 실행 예시

```bash
python stock_research_bot.py \
  --company "삼성전자" \
  --ticker "005930.KS" \
  --naver-symbol "005930"
```

DART까지 함께 실행:

```bash
python stock_research_bot.py \
  --company "삼성전자" \
  --ticker "005930.KS" \
  --naver-symbol "005930" \
  --dart-api-key "$DART_API_KEY"
```

## 참고

- 본 스크립트는 API 제공 데이터 품질/가용성에 영향을 받습니다.
- 투자 판단의 최종 책임은 사용자에게 있습니다.
