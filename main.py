# -*- coding: utf-8 -*-
import requests
import json
from bs4 import BeautifulSoup
import time
import os
import yfinance as yf
from datetime import datetime

# --- Notion API 설정 ---
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
    """실시간 USD/KRW 환율 조회 (실패 시 기본값 1350.0 반환)"""
    try:
        data = yf.Ticker("KRW=X")
        rate = data.history(period="1d")['Close'].iloc[-1]
        return float(rate)
    except Exception as e:
        print(f"  [환율 경고] 환율 정보를 가져오지 못했습니다. 기본값을 사용합니다: {e}")
        return 1350.0

def get_domestic_price(item_code: str) -> float:
    """네이버 증권 국내 주가 조회"""
    url = f"https://finance.naver.com/item/main.naver?code={item_code}"
    try:
        response = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=10)
        soup = BeautifulSoup(response.text, 'html.parser')
        price_element = soup.select_one("#chart_area > div.rate_info > div > p.no_today > em > span.blind")
        return float(price_element.text.replace(",", "")) if price_element else 0.0
    except Exception:
        return 0.0

def get_overseas_price(ticker: str) -> float:
    """yfinance 해외 주가 조회"""
    try:
        stock = yf.Ticker(ticker)
        price_info = stock.history(period="1d")
        return round(float(price_info['Close'].iloc[-1]), 2) if not price_info.empty else 0.0
    except Exception:
        return 0.0

def main():
    now = datetime.now()
    today_str = now.strftime('%Y-%m-%d')
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 자동화 프로세스 시작")
    
    if not NOTION_API_KEY or not DATABASE_ID:
        print("[오류] 환경 변수(Secrets) 설정이 누락되었습니다.")
        return

    # 1. 환율 정보 업데이트
    exchange_rate = get_usd_to_krw_rate()
    print(f"현재 환율: 1 USD = {exchange_rate:,.2f} KRW")

    # 2. 노션 데이터베이스 쿼리
    query_url = f"{NOTION_API_URL}/databases/{DATABASE_ID}/query"
    try:
        response = requests.post(query_url, headers=headers, timeout=15)
        response.raise_for_status()
        pages = response.json().get("results", [])
    except Exception as e:
        print(f"[API 오류] 노션 데이터베이스를 불러오지 못했습니다: {e}")
        return

    for page in pages:
        try:
            page_id = page["id"]
            props = page.get("properties", {})

            # --- 데이터 안전하게 읽기 (Missing Key 방지) ---
            def get_text(prop_name):
                p = props.get(prop_name, {}).get("rich_text", [])
                return p[0].get("plain_text", "").strip() if p else ""

            def get_title(prop_name):
                p = props.get(prop_name, {}).get("title", [])
                return p[0].get("plain_text", "Unknown").strip() if p else "Unknown"

            def get_select(prop_name):
                s = props.get(prop_name, {}).get("select")
                return s.get("name") if s else ""

            name = get_title("종목명")
            code = get_text("종목코드")
            category = get_select("분류") # '국내' 또는 '해외'
            
            if not code or not category:
                continue

            print(f"\n- 처리 중: {name} ({code})")

            # 수치 데이터 안전하게 읽기
            auto_buy_enabled = props.get("자동 매수", {}).get("checkbox", False)
            buy_freq = get_select("매수 주기") # '매일', '화요일' 등
            last_buy_date = props.get("최근 매수일", {}).get("date", {}).get("start") if props.get("최근 매수일", {}).get("date") else ""
            
            fixed_amount = props.get("정액 매수 금액", {}).get("number", 0) or 0
            fixed_qty = props.get("자동 매수 수량", {}).get("number", 0) or 0
            current_qty = props.get("수량", {}).get("number", 0) or 0

            # 3. 가격 조회
            price = get_domestic_price(code) if category == "국내" else get_overseas_price(code)
            
            if price <= 0:
                print(f"  [경고] {name}의 가격 정보를 가져올 수 없습니다. 업데이트를 건너뜁니다.")
                continue

            # 기본 업데이트 항목 (현재가, 환율)
            update_props = {
                "현재가": {"number": price},
                "환율": {"number": exchange_rate}
            }

            # 4. 자동 매수 판별 로직
            should_buy = False
            # 조건: 체크박스 ON + 오늘 아직 매수 안 함
            if auto_buy_enabled and last_buy_date != today_str:
                if buy_freq == "매일":
                    should_buy = True
                elif buy_freq == "화요일" and now.weekday() == 1: # 1: 화요일
                    should_buy = True

            # 5. 매수 수량 계산
            if should_buy:
                add_qty = 0.0
                # A. 정액 매수 (금액 / 현재가)
                if fixed_amount > 0:
                    cost_per_share = price * exchange_rate if category == "해외" else price
                    add_qty = fixed_amount / cost_per_share
                # B. 정량 매수 (지정된 수량만큼)
                elif fixed_qty > 0:
                    add_qty = fixed_qty
                
                if add_qty > 0:
                    new_total_qty = round(current_qty + add_qty, 4)
                    update_props["수량"] = {"number": new_total_qty}
                    update_props["최근 매수일"] = {"date": {"start": today_str}}
                    print(f"  -> [자동 매수 성공] {add_qty:.4f}주 추가 (합계: {new_total_qty})")

            # 6. 노션 전송
            update_url = f"{NOTION_API_URL}/pages/{page_id}"
            res = requests.patch(update_url, headers=headers, data=json.dumps({"properties": update_props}), timeout=10)
            if res.status_code == 200:
                print(f"  -> 정보 업데이트 완료")
            else:
                print(f"  -> [업데이트 실패] {res.text}")

        except Exception as e:
            print(f"  [오류] {name} 처리 중 예외 발생: {e}")
            continue # 다음 종목으로 넘어감

        time.sleep(0.5)

    print("\n모든 종목의 작업이 완료되었습니다.")

if __name__ == "__main__":
    main()