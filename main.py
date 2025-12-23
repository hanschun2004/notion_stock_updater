# -*- coding: utf-8 -*-
import requests
import json
from bs4 import BeautifulSoup
import time
import os
import yfinance as yf
from datetime import datetime

# --- Notion API 설정 ---
# GitHub Secrets에서 값을 가져옵니다.
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
    """실시간 USD/KRW 환율 정보를 가져옵니다."""
    try:
        data = yf.Ticker("KRW=X")
        rate = data.history(period="1d")['Close'].iloc[-1]
        return float(rate)
    except Exception as e:
        print(f"  [오류] 환율 조회 실패: {e}")
        return 1350.0 # 실패 시 기본값

def get_domestic_price(item_code: str) -> float:
    """네이버 증권에서 국내 종목의 현재가를 가져옵니다."""
    url = f"https://finance.naver.com/item/main.naver?code={item_code}"
    try:
        response = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'})
        soup = BeautifulSoup(response.text, 'html.parser')
        price_element = soup.select_one("#chart_area > div.rate_info > div > p.no_today > em > span.blind")
        if price_element:
            return float(price_element.text.replace(",", ""))
        return 0.0
    except Exception:
        return 0.0

def get_overseas_price(ticker: str) -> float:
    """yfinance를 사용하여 해외 종목의 현재가를 가져옵니다."""
    try:
        stock = yf.Ticker(ticker)
        price_info = stock.history(period="1d")
        if not price_info.empty:
            return round(float(price_info['Close'].iloc[-1]), 2)
        return 0.0
    except Exception:
        return 0.0

def main():
    now = datetime.now()
    today_str = now.strftime('%Y-%m-%d')
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 자동화 스크립트 실행")
    
    if not NOTION_API_KEY or not DATABASE_ID:
        print("[오류] API 키 또는 데이터베이스 ID 설정이 필요합니다.")
        return

    # 1. 환율 정보 가져오기
    exchange_rate = get_usd_to_krw_rate()
    print(f"현재 환율: 1 USD = {exchange_rate:,.2f} KRW")

    # 2. Notion 데이터베이스 조회
    query_url = f"{NOTION_API_URL}/databases/{DATABASE_ID}/query"
    response = requests.post(query_url, headers=headers)
    pages = response.json().get("results", [])

    print(f"총 {len(pages)}개의 종목을 처리합니다.")

    for page in pages:
        page_id = page["id"]
        props = page.get("properties", {})

        # 기본 정보 추출
        name_prop = props.get("종목명", {}).get("title", [])
        name = name_prop[0].get("plain_text", "Unknown") if name_prop else "Unknown"
        code_prop = props.get("종목코드", {}).get("rich_text", [])
        code = code_prop[0].get("plain_text", None) if code_prop else None
        category = props.get("분류", {}).get("select", {}).get("name", None) # '국내' 또는 '해외'
        
        if not code or not category:
            continue

        # 자동 매수 설정 추출
        auto_buy_enabled = props.get("자동 매수", {}).get("checkbox", False)
        buy_freq = props.get("매수 주기", {}).get("select", {}).get("name", "") # '매일' 또는 '화요일'
        last_buy_date = props.get("최근 매수일", {}).get("date", {}).get("start") if props.get("최근 매수일", {}).get("date") else ""
        
        fixed_amount = props.get("정액 매수 금액", {}).get("number", 0) # 매일 1만원 등
        fixed_qty = props.get("자동 매수 수량", {}).get("number", 0)   # 화요일 2개 등
        current_qty = props.get("수량", {}).get("number", 0)

        print(f"\n조회: {name} ({code})")

        # 가격 조회
        price = get_domestic_price(code) if category == "국내" else get_overseas_price(code)
        update_props = {
            "현재가": {"number": price},
            "환율": {"number": exchange_rate}
        }

        # 자동 매수 실행 여부 판단
        should_buy = False
        if auto_buy_enabled and last_buy_date != today_str:
            if buy_freq == "매일":
                should_buy = True
            elif buy_freq == "화요일" and now.weekday() == 1: # 1은 화요일
                should_buy = True

        # 매수 계산 및 수량 업데이트
        if should_buy and price > 0:
            add_qty = 0.0
            # A. 정액 매수 (금액 기준)
            if fixed_amount and fixed_amount > 0:
                cost_per_share = price * exchange_rate if category == "해외" else price
                add_qty = fixed_amount / cost_per_share
            # B. 정량 매수 (수량 기준)
            elif fixed_qty and fixed_qty > 0:
                add_qty = fixed_qty
            
            if add_qty > 0:
                new_total_qty = round(current_qty + add_qty, 4)
                update_props["수량"] = {"number": new_total_qty}
                update_props["최근 매수일"] = {"date": {"start": today_str}}
                print(f"  -> [매수 진행] 추가 수량: {add_qty:.4f} / 총 수량: {new_total_qty}")

        # Notion 페이지 업데이트
        if price > 0:
            update_url = f"{NOTION_API_URL}/pages/{page_id}"
            requests.patch(update_url, headers=headers, data=json.dumps({"properties": update_props}))
            print(f"  -> 업데이트 완료 (현재가: {price:,.2f})")
        
        time.sleep(0.5)

    print("\n모든 작업이 완료되었습니다.")

if __name__ == "__main__":
    main()