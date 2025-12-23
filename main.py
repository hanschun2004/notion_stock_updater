# -*- coding: utf-8 -*-
import requests
import json
from bs4 import BeautifulSoup
import time
import os
import yfinance as yf
from datetime import datetime

# --- 설정 영역 ---
NOTION_API_KEY = os.environ.get('NOTION_API_KEY')
DATABASE_ID = os.environ.get('DATABASE_ID')
# --------------------

NOTION_API_URL = "https://api.notion.com/v1"
headers = {
    "Authorization": f"Bearer {NOTION_API_KEY}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28",
}

def get_usd_to_krw_rate() -> float:
    """실시간 USD/KRW 환율을 가져옵니다."""
    try:
        data = yf.Ticker("KRW=X")
        rate = data.history(period="1d")['Close'].iloc[-1]
        return float(rate)
    except Exception as e:
        print(f"  [오류] 환율 정보 조회 실패: {e}")
        return 1350.0 # 실패 시 예비값

def get_domestic_price(item_code: str) -> float:
    """네이버 증권에서 국내 가격 조회"""
    url = f"https://finance.naver.com/item/main.naver?code={item_code}"
    try:
        response = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'})
        soup = BeautifulSoup(response.text, 'html.parser')
        price_element = soup.select_one("#chart_area > div.rate_info > div > p.no_today > em > span.blind")
        if price_element:
            return float(price_element.text.replace(",", ""))
        return 0.0
    except:
        return 0.0

def get_overseas_price(ticker: str) -> float:
    """yfinance에서 해외 가격 조회"""
    try:
        stock = yf.Ticker(ticker)
        price_info = stock.history(period="1d")
        if not price_info.empty:
            return round(float(price_info['Close'].iloc[-1]), 2)
        return 0.0
    except:
        return 0.0

def main():
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 자동화 스크립트 실행")
    
    # 오늘이 화요일인지 확인 (0:월, 1:화, ...)
    is_tuesday = datetime.today().weekday() == 1
    
    if not NOTION_API_KEY or not DATABASE_ID:
        print("[오류] API 키 또는 DATABASE_ID가 설정되지 않았습니다.")
        return

    # 환율 정보 미리 가져오기
    exchange_rate = get_usd_to_krw_rate()
    print(f"현재 적용 환율: 1 USD = {exchange_rate:,.2f} KRW")

    # 데이터베이스 조회
    query_url = f"{NOTION_API_URL}/databases/{DATABASE_ID}/query"
    response = requests.post(query_url, headers=headers)
    pages = response.json().get("results", [])

    print(f"총 {len(pages)}개의 종목을 처리합니다.")

    for page in pages:
        page_id = page["id"]
        props = page.get("properties", {})

        # 정보 읽기
        name = props.get("종목명", {}).get("title", [{}])[0].get("plain_text", "Unknown")
        code = props.get("종목코드", {}).get("rich_text", [{}])[0].get("plain_text", None)
        category = props.get("분류", {}).get("select", {}).get("name", None)
        
        # 자동 매수 관련
        auto_buy_enabled = props.get("자동 매수", {}).get("checkbox", False)
        auto_buy_amount = props.get("자동 매수 수량", {}).get("number", 0)
        current_qty = props.get("수량", {}).get("number", 0)

        if not code or not category:
            continue

        print(f"처리 중: {name} ({code})")

        # 1. 가격 조회
        price = get_domestic_price(code) if category == "국내" else get_overseas_price(code)
        
        # 2. 업데이트할 속성 꾸리기
        update_props = {
            "현재가": {"number": price},
            "환율": {"number": exchange_rate}
        }

        # 3. 화요일 + 자동매수 체크 시 수량 추가
        if is_tuesday and auto_buy_enabled and auto_buy_amount > 0:
            new_qty = current_qty + auto_buy_amount
            update_props["수량"] = {"number": new_qty}
            print(f"  -> [화요일 자동매수] 수량 변경: {current_qty} -> {new_qty}")

        # 4. 노션 업데이트 전송
        if price > 0:
            update_url = f"{NOTION_API_URL}/pages/{page_id}"
            requests.patch(update_url, headers=headers, data=json.dumps({"properties": update_props}))
            print(f"  -> 업데이트 완료 (현재가: {price:,.2f})")
        
        time.sleep(0.5)

    print("모든 작업이 완료되었습니다.")

if __name__ == "__main__":
    main()