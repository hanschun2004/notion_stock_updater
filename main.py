# -*- coding: utf-8 -*-
import requests
import json
from bs4 import BeautifulSoup
import time
import os
import yfinance as yf # yfinance 라이브러리 추가

# --- 설정 영역 ---
# 이 값들은 GitHub Secrets에서 안전하게 관리됩니다.
NOTION_API_KEY = os.environ.get('NOTION_API_KEY')
DATABASE_ID = os.environ.get('DATABASE_ID')
# --------------------

# Notion API 기본 설정
NOTION_API_URL = "https://api.notion.com/v1"
headers = {
    "Authorization": f"Bearer {NOTION_API_KEY}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28",
}

def get_usd_to_krw_rate() -> float:
    """
    yfinance를 사용하여 실시간 USD/KRW 환율을 가져옵니다.
    """
    try:
        # KRW=X는 USD 대비 KRW 환율 Ticker입니다.
        data = yf.Ticker("KRW=X")
        rate = data.history(period="1d")['Close'].iloc[0]
        print(f"✅ 실시간 환율 정보 조회 성공: 1 USD = {rate:.2f} KRW")
        return rate
    except Exception as e:
        print(f"  [오류] 환율 정보를 가져오는 데 실패했습니다: {e}")
        # 실패 시 기본값 또는 이전 값 사용 등의 fallback 로직을 추가할 수 있습니다.
        return 1300.0 # 예시 기본값

def get_domestic_price(item_code: str) -> int:
    """
    네이버 증권에서 주어진 종목 코드의 현재가를 크롤링합니다.
    """
    url = f"https://finance.naver.com/item/main.naver?code={item_code}"
    crawler_headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3'
    }
    try:
        response = requests.get(url, headers=crawler_headers)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        price_element = soup.select_one("#chart_area > div.rate_info > div > p.no_today > em > span.blind")
        if price_element:
            price_str = price_element.text.replace(",", "")
            return int(price_str)
        else:
            print(f"  [오류] {item_code}: 가격 정보를 찾을 수 없습니다.")
            return 0
    except Exception as e:
        print(f"  [오류] {item_code} 처리 중 오류 발생: {e}")
        return 0

def get_overseas_price(ticker: str) -> float:
    """
    yfinance를 사용하여 주어진 티커의 현재가를 가져옵니다.
    """
    try:
        data = yf.Ticker(ticker)
        # 가장 최신 가격 정보를 가져옵니다.
        price = data.history(period="1d")['Close'].iloc[0]
        return price
    except Exception as e:
        print(f"  [오류] {ticker}: yfinance로 가격 정보를 가져오는 데 실패했습니다: {e}")
        return 0.0

def get_pages_from_db(database_id: str) -> list:
    """
    Notion 데이터베이스에서 모든 페이지(항목)를 가져옵니다.
    """
    query_url = f"{NOTION_API_URL}/databases/{database_id}/query"
    try:
        response = requests.post(query_url, headers=headers)
        response.raise_for_status()
        return response.json().get("results", [])
    except Exception as e:
        print(f"[API 오류] Notion 데이터베이스 조회 실패: {e}")
        return []

def update_notion_page(page_id: str, price: float, exchange_rate: float):
    """
    특정 Notion 페이지의 '현재가'와 '환율' 속성을 업데이트합니다.
    """
    update_url = f"{NOTION_API_URL}/pages/{page_id}"
    payload = {
        "properties": {
            "현재가": {"number": price},
            "환율": {"number": exchange_rate}
        }
    }
    try:
        response = requests.patch(update_url, headers=headers, data=json.dumps(payload))
        response.raise_for_status()
    except Exception as e:
        print(f"  [API 오류] Notion 페이지 업데이트 실패 (Page ID: {page_id}): {e}")

def main():
    """
    메인 실행 함수입니다.
    """
    print("GitHub Actions 실행 시작: 노션 데이터베이스 업데이트를 시작합니다...")
    
    if not NOTION_API_KEY or not DATABASE_ID:
        print("[오류] GitHub Secrets에 NOTION_API_KEY 또는 DATABASE_ID가 설정되지 않았습니다.")
        return

    # 1. 실시간 환율 정보 가져오기 (스크립트 실행 시 1회만)
    exchange_rate = get_usd_to_krw_rate()
    if exchange_rate is None:
        print("[오류] 환율 정보를 가져올 수 없어 스크립트를 중단합니다.")
        return

    # 2. Notion 데이터베이스에서 모든 종목 가져오기
    pages = get_pages_from_db(DATABASE_ID)
    if not pages:
        print("데이터베이스에 항목이 없거나 조회에 실패했습니다. 함수를 종료합니다.")
        return
        
    print(f"\n총 {len(pages)}개의 항목에 대한 가격 업데이트를 시작합니다.\n")
    
    for page in pages:
        page_id = page["id"]
        properties = page.get("properties", {})
        
        item_name_prop = properties.get("종목명", {}).get("title", [])
        item_name = item_name_prop[0].get("text", {}).get("content", "이름 없음") if item_name_prop else "이름 없음"

        item_code_prop = properties.get("종목코드", {}).get("rich_text", [])
        item_code = item_code_prop[0].get("text", {}).get("content", None) if item_code_prop else None
        
        category_prop = properties.get("분류", {}).get("select", {})
        category = category_prop.get("name", None) if category_prop else None

        current_price = 0
        
        if not item_code or not category:
            print(f"'{item_name}' 항목에 종목코드 또는 분류가 없습니다. 건너뜁니다.")
            continue
            
        print(f"'{item_name}' ({item_code}) 가격 조회 중...")

        if category == "국내":
            current_price = get_domestic_price(item_code)
        elif category == "해외":
            current_price = get_overseas_price(item_code)
        
        if current_price > 0:
            print(f"  -> 현재가: {current_price:,.2f}. Notion 페이지를 업데이트합니다.")
            update_notion_page(page_id, current_price, exchange_rate)
        else:
            print(f"  -> 가격을 가져오지 못해 업데이트를 건너뜁니다.")
            
        time.sleep(0.5) # API 요청 간에 약간의 딜레이
        
    print("\n모든 항목 업데이트가 완료되었습니다.")

if __name__ == "__main__":
    main()



# # -*- coding: utf-8 -*-
# import requests
# import json
# from bs4 import BeautifulSoup
# import time
# import os
# import yfinance as yf # 해외 주식용 라이브러리 추가

# # --- 설정 영역 ---
# # GitHub Secrets의 값을 안전하게 불러옵니다.
# NOTION_API_KEY = os.environ.get('NOTION_API_KEY')
# DATABASE_ID = os.environ.get('DATABASE_ID')
# # --------------------

# # Notion API 기본 설정
# NOTION_API_URL = "https://api.notion.com/v1"
# headers = {
#     "Authorization": f"Bearer {NOTION_API_KEY}",
#     "Content-Type": "application/json",
#     "Notion-Version": "2022-06-28",
# }

# def get_domestic_price(item_code: str) -> float:
#     """
#     네이버 증권에서 국내 종목의 현재가를 크롤링합니다.
#     """
#     url = f"https://finance.naver.com/item/main.naver?code={item_code}"
#     crawler_headers = {
#         'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3'
#     }
#     try:
#         response = requests.get(url, headers=crawler_headers)
#         response.raise_for_status()
#         soup = BeautifulSoup(response.text, 'html.parser')
#         price_element = soup.select_one("#chart_area > div.rate_info > div > p.no_today > em > span.blind")
#         if price_element:
#             price_str = price_element.text.replace(",", "")
#             return float(price_str)
#         return 0.0
#     except Exception as e:
#         print(f"  [오류] 국내 종목({item_code}) 처리 중 오류: {e}")
#         return 0.0


# def get_overseas_price(ticker: str) -> float:
#     """
#     Yahoo Finance에서 해외 종목의 현재가를 가져옵니다.
#     """
#     try:
#         stock = yf.Ticker(ticker)
#         # 가장 최근의 가격 정보를 가져옵니다. (장중)
#         price_info = stock.history(period="1d", interval="1m")
#         if not price_info.empty:
#             return round(price_info['Close'].iloc[-1], 2)
#         else: # 장 마감 후에는 당일 종가 정보를 가져옵니다.
#             price_info = stock.history(period="2d")
#             if not price_info.empty:
#                 return round(price_info['Close'].iloc[-1], 2)
#         print(f"  [정보] 해외 종목({ticker}): 가격 정보를 찾을 수 없습니다.")
#         return 0.0
#     except Exception as e:
#         print(f"  [오류] 해외 종목({ticker}) 처리 중 오류: {e}")
#         return 0.0


# def get_pages_from_db(database_id: str) -> list:
#     """
#     Notion 데이터베이스에서 모든 페이지(항목)를 가져옵니다.
#     """
#     query_url = f"{NOTION_API_URL}/databases/{database_id}/query"
#     try:
#         response = requests.post(query_url, headers=headers)
#         response.raise_for_status()
#         return response.json().get("results", [])
#     except Exception as e:
#         print(f"[API 오류] Notion 데이터베이스 조회 실패: {e}")
#         return []

# def update_notion_page_price(page_id: str, price: float):
#     """
#     특정 Notion 페이지의 '현재가' 속성을 업데이트합니다.
#     """
#     update_url = f"{NOTION_API_URL}/pages/{page_id}"
#     payload = {"properties": {"현재가": {"number": price}}}
#     try:
#         response = requests.patch(update_url, headers=headers, data=json.dumps(payload))
#         response.raise_for_status()
#     except Exception as e:
#         print(f"  [API 오류] Notion 페이지 업데이트 실패 (Page ID: {page_id}): {e}")


# # --- 여기가 스크립트의 실제 시작점입니다 ---
# def main():
#     print("GitHub Actions 실행 시작: 노션 데이터베이스에서 주식 목록을 가져옵니다...")
    
#     if not NOTION_API_KEY or not DATABASE_ID:
#         print("[오류] GitHub Secrets에 NOTION_API_KEY 또는 DATABASE_ID가 설정되지 않았습니다.")
#         # 실패를 알리기 위해 exit(1) 사용
#         exit(1)

#     pages = get_pages_from_db(DATABASE_ID)
    
#     if not pages:
#         print("데이터베이스에 항목이 없거나 조회에 실패했습니다. 작업을 종료합니다.")
#         return
        
#     print(f"총 {len(pages)}개의 항목에 대한 가격 업데이트를 시작합니다.\n")
    
#     for page in pages:
#         properties = page.get("properties", {})
        
#         # --- 각 속성 값 읽어오기 ---
#         item_name_prop = properties.get("종목명", {}).get("title", [])
#         item_name = item_name_prop[0].get("text", {}).get("content", "이름 없음") if item_name_prop else "이름 없음"

#         item_code_prop = properties.get("종목코드", {}).get("rich_text", [])
#         item_code = item_code_prop[0].get("text", {}).get("content", None) if item_code_prop else None

#         asset_type_prop = properties.get("분류", {}).get("select", {})
#         asset_type = asset_type_prop.get("name") if asset_type_prop else None

#         current_price = 0.0
        
#         # --- 분류에 따라 다른 함수 호출 ---
#         if item_code and asset_type:
#             print(f"'{item_name}' ({item_code}) / 분류: {asset_type} / 가격 조회 중...")
#             if asset_type == '국내':
#                 current_price = get_domestic_price(item_code)
#             elif asset_type == '해외':
#                 current_price = get_overseas_price(item_code)
#             else:
#                 print(f"  -> 알 수 없는 분류({asset_type})입니다. 건너뜁니다.")
#                 continue

#             if current_price > 0:
#                 print(f"  -> 현재가: {current_price:,.2f}. Notion 페이지를 업데이트합니다.")
#                 update_notion_page_price(page["id"], current_price)
#             else:
#                 print(f"  -> 가격을 가져오지 못해 업데이트를 건너뜁니다.")

#         else:
#             print(f"'{item_name}' 항목에 '종목코드' 또는 '분류'가 없습니다. 건너뜁니다.")
        
#         # API 요청 사이에 지연 시간을 두어 서버 부하를 줄입니다.
#         time.sleep(1) 
        
#     print("\n모든 항목의 가격 업데이트가 완료되었습니다.")

# if __name__ == "__main__":
#     main()




# # -*- coding: utf-8 -*-
# import requests
# import json
# from bs4 import BeautifulSoup
# import time
# import os

# # --- 설정 영역 ---
# # README.md에서 설정한 GitHub Secrets의 값을 안전하게 불러옵니다.
# # 이름이 정확히 일치해야 합니다.
# NOTION_API_KEY = os.environ.get('NOTION_API_KEY')
# DATABASE_ID = os.environ.get('DATABASE_ID')
# # --------------------

# # Notion API 기본 설정
# NOTION_API_URL = "https://api.notion.com/v1"
# headers = {
#     "Authorization": f"Bearer {NOTION_API_KEY}",
#     "Content-Type": "application/json",
#     "Notion-Version": "2022-06-28",
# }

# def get_etf_price(item_code: str) -> int:
#     """
#     네이버 증권에서 주어진 종목 코드의 현재가를 크롤링합니다.
#     """
#     url = f"https://finance.naver.com/item/main.naver?code={item_code}"
#     crawler_headers = {
#         'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3'
#     }
#     try:
#         response = requests.get(url, headers=crawler_headers)
#         response.raise_for_status()
#         soup = BeautifulSoup(response.text, 'html.parser')
#         price_element = soup.select_one("#chart_area > div.rate_info > div > p.no_today > em > span.blind")
#         if price_element:
#             price_str = price_element.text.replace(",", "")
#             return int(price_str)
#         else:
#             print(f"  [오류] {item_code}: 가격 정보를 찾을 수 없습니다.")
#             return 0
#     except Exception as e:
#         print(f"  [오류] {item_code} 처리 중 오류 발생: {e}")
#         return 0

# def get_pages_from_db(database_id: str) -> list:
#     """
#     Notion 데이터베이스에서 모든 페이지(항목)를 가져옵니다.
#     """
#     query_url = f"{NOTION_API_URL}/databases/{database_id}/query"
#     try:
#         response = requests.post(query_url, headers=headers)
#         response.raise_for_status()
#         return response.json().get("results", [])
#     except Exception as e:
#         print(f"[API 오류] Notion 데이터베이스 조회 실패: {e}")
#         return []

# def update_notion_page_price(page_id: str, price: int):
#     """
#     특정 Notion 페이지의 '현재가' 속성을 업데이트합니다.
#     """
#     update_url = f"{NOTION_API_URL}/pages/{page_id}"
#     payload = {"properties": {"현재가": {"number": price}}}
#     try:
#         response = requests.patch(update_url, headers=headers, data=json.dumps(payload))
#         response.raise_for_status()
#     except Exception as e:
#         print(f"  [API 오류] Notion 페이지 업데이트 실패 (Page ID: {page_id}): {e}")

# # --- 여기가 스크립트의 실제 시작점입니다 ---
# def main():
#     """
#     스크립트의 메인 로직을 실행합니다.
#     """
#     print("GitHub Actions 실행 시작: 노션 데이터베이스에서 ETF 목록을 가져옵니다...")
    
#     if not NOTION_API_KEY or not DATABASE_ID:
#         print("[오류] GitHub Secrets에 NOTION_API_KEY 또는 DATABASE_ID가 설정되지 않았습니다.")
#         # 실패를 의미하는 상태 코드로 종료하여 GitHub Actions에 알립니다.
#         exit(1)

#     pages = get_pages_from_db(DATABASE_ID)
    
#     if not pages:
#         print("데이터베이스에 항목이 없거나 조회에 실패했습니다. 작업을 종료합니다.")
#         return # 성공적으로 종료
        
#     print(f"총 {len(pages)}개의 ETF 항목에 대한 가격 업데이트를 시작합니다.\n")
    
#     for page in pages:
#         page_id = page["id"]
#         properties = page.get("properties", {})
        
#         item_name_prop = properties.get("종목명", {}).get("title", [])
#         item_name = item_name_prop[0].get("text", {}).get("content", "이름 없음") if item_name_prop else "이름 없음"

#         item_code_prop = properties.get("종목코드", {}).get("rich_text", [])
#         item_code = item_code_prop[0].get("text", {}).get("content", None) if item_code_prop else None

#         if item_code:
#             print(f"'{item_name}' ({item_code}) 가격 조회 중...")
#             current_price = get_etf_price(item_code)
#             if current_price > 0:
#                 print(f"  -> 현재가: {current_price:,}원. Notion 페이지를 업데이트합니다.")
#                 update_notion_page_price(page_id, current_price)
#             else:
#                 print(f"  -> 가격을 가져오지 못해 업데이트를 건너뜁니다.")
#         else:
#             print(f"'{item_name}' 항목에 종목코드가 없습니다. 건너뜁니다.")
            
#         # API 요청 사이에 약간의 지연을 줍니다.
#         time.sleep(0.5)
        
#     print("\n모든 ETF 가격 업데이트가 완료되었습니다.")

# # 이 스크립트 파일이 직접 실행될 때만 main() 함수를 호출합니다.
# if __name__ == "__main__":
#     main()

