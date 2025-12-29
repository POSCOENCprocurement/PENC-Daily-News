import os
import smtplib
import feedparser
import time
import urllib.parse
import re
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import google.generativeai as genai

# --- 환경 변수 설정 (GitHub Secrets) ---
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY")
EMAIL_SENDER = os.environ.get("EMAIL_SENDER")
EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD")
EMAIL_RECEIVERS = os.environ.get("EMAIL_RECEIVERS")

# --- 설정: 키워드 및 필터 ---
KEYWORDS = [
    "포스코이앤씨", 
    "건설 원자재 가격", 
    "공정위 하도급 건설", 
    "건설 중대재해처벌법",
    "건설사 협력사 ESG",
    "주요 건설사 구매 동향",
    "건설 자재 환율 유가",
    "해상 운임 SCFI 건설",
    "스마트 건설 모듈러 OSC",
    "건설 현장 인력난 외국인",
    "건설 노조 파업 노란봉투법",
    "납품대금 연동제 건설",
    "건설산업기본법 개정",
    "화물연대 레미콘 운송 파업"
]

# 주식/투자 관련 노이즈 제거를 위한 금지어 목록
EXCLUDE_KEYWORDS = [
    "특징주", "테마주", "관련주", "주가", "급등", "급락", "상한가", "하한가",
    "거래량", "매수", "매도", "목표가", "체결", "증시", "종목", "투자자",
    "지수", "코스피", "코스닥", "마감"
]

def get_korea_time():
    """서버 시간(UTC)을 한국 시간(KST)으로 변환"""
    utc_now = datetime.now(timezone.utc)
    kst_now = utc_now + timedelta(hours=9)
    return kst_now

def is_stock_noise(title):
    """제목에 주식 관련 금지어가 있는지 검사"""
    for bad_word in EXCLUDE_KEYWORDS:
        if bad_word in title:
            return True
    return False

def is_recent(published_str):
    """뉴스 날짜가 24시간 이내인지 확인"""
    if not published_str: return False
    try:
        pub_date = parsedate_to_datetime(published_str)
        if pub_date.tzinfo:
            pub_date = pub_date.astimezone(timezone.utc)
        else:
            pub_date = pub_date.replace(tzinfo=timezone.utc)
        
        now_utc = datetime.now(timezone.utc)
        one_day_ago = now_utc - timedelta(hours=24)
        return pub_date > one_day_ago
    except:
        return True

def fetch_news():
    """RSS 뉴스 수집 (스크랩 제거로 속도 향상)"""
    news_items = []
    print("🔍 뉴스 수집 시작...")
    
    for keyword in KEYWORDS:
        # 검색어 뒤에 '-주식 -종목' 등을 붙여서 구글 검색 단계에서도 1차 필터링
        negative_query = " -주식 -종목 -테마 -특징주"
        encoded_query = urllib.parse.quote(f"{keyword}{negative_query} when:1d")
        url = f"https://news.google.com/rss/search?q={encoded_query}&hl=ko&gl=KR&ceid=KR:ko"
        
        try:
            feed = feedparser.parse(url)
            
            if not feed.entries and hasattr(feed, 'bozo_exception'): pass

            for entry in feed.entries[:3]:
                if is_recent(entry.published):
                    # 2차 필터링: 제목에 금지어 포함 여부 확인
                    if is_stock_noise(entry.title):
                        continue

                    if not any(item['link'] == entry.link for item in news_items):
                        news_items.append({
                            "title": entry.title,
                            "link": entry.link,
                            "keyword": keyword,
                            "date": entry.published
                        })
        except Exception as e:
            print(f"⚠️ '{keyword}' 오류: {e}")
            continue
            
    print(f"✅ 총 {len(news_items)}개의 최신 뉴스 수집 완료.")
    return news_items

def generate_report(news_items):
    """Gemini AI 리포트 (와이드 레이아웃 및 디자인 최적화)"""
    if not news_items: return None
    
    kst_now = get_korea_time()
    today_formatted = kst_now.strftime("%Y년 %m월 %d일") 
    
    print("🧠 AI 분석 시작...")
    try:
        genai.configure(api_key=GOOGLE_API_KEY)
        model = genai.GenerativeModel('gemini-2.5-flash-preview-09-2025')

        news_text = ""
        for idx, item in enumerate(news_items):
            # 링크를 포함하여 AI에게 전달
            news_text += f"[{idx+1}] {item['title']} (키워드: {item['keyword']}) | Link: {item['link']}\n"

        # 프롬프트 수정: 와이드 레이아웃에 맞춘 큼직한 디자인 요청
        prompt = f"""
        오늘은 {today_formatted}입니다.
        당신은 **포스코이앤씨 구매계약실**의 수석 애널리스트입니다.
        아래 뉴스들을 바탕으로 경영진 및 실무자가 PC에서 보기 편한 'Daily Market & Risk Briefing' 이메일을 작성하세요.

        [뉴스 목록]
        {news_text}

        [작성 원칙]
        1. **날짜 준수**: 반드시 오늘 날짜({today_formatted})를 기준으로 작성하세요.
        2. **주식/투자 배제**: 건설 테마주, 주가 등락 내용은 절대 포함하지 마세요.
        3. **구매계약실 관점**: 계약, 납기, 단가, 법적 리스크 위주로 분석하세요.

        [보고서 형식 (HTML Style - Wide Layout)]
        - **절대** `<html>`, `<head>`, `<body>` 태그를 쓰지 마세요. `<div>`로 시작하는 본문 내용만 작성하세요.
        - **디자인 컨셉**: 시원시원한 여백, 큰 폰트, 명확한 구분선.
        - **링크**: 제목에 링크를 걸지 말고, 우측 하단이나 별도 라인에 '🔗 원문 보기' 버튼을 배치하세요.
        
        [HTML 구조 가이드]
        1. **시장 날씨 요약 (Executive Summary)**: 
           `<div style="background-color: #f1f8ff; padding: 25px; border-radius: 4px; margin-bottom: 40px; border: 1px solid #cce5ff;">`
           - 제목: `<h3>` 태그로 "Today's Market Weather" 작성.
           - 내용: ☀️/☁️/☔ 아이콘과 함께 시장 요약 1~2문장을 16px 크기로 작성.
        
        2. **카테고리 섹션**: 
           `[규제/리스크]`, `[자재/시황]`, `[글로벌/물류]` 등 섹션 제목을 `<h2>` 태그로 작성.
           - 스타일: `color: #0054a6; border-bottom: 2px solid #0054a6; padding-bottom: 10px; margin-top: 40px; margin-bottom: 20px; font-size: 22px;`
        
        3. **기사 카드 (Wide Card)**:
           각 기사는 아래 스타일을 적용하세요:
           `<div style="background-color: #ffffff; border-bottom: 1px solid #eeeeee; padding: 25px 0; margin-bottom: 0;">`
           
           - **제목**: `<div style="font-size: 20px; font-weight: bold; color: #222; margin-bottom: 12px; line-height: 1.4;">제목</div>`
           - **내용**: `<div style="font-size: 16px; color: #555; line-height: 1.7; margin-bottom: 15px;">기사 핵심 요약 내용...</div>`
           - **인사이트 박스**: `<div style="background-color: #f8f9fa; padding: 15px; border-left: 4px solid #0054a6; font-size: 15px; color: #333; margin-bottom: 15px;"><strong>💡 Insight:</strong> 구매계약실 대응 방안...</div>`
           - **버튼**: `<div style="text-align: right;"><a href="..." style="display: inline-block; background-color: #f1f3f5; color: #495057; padding: 8px 16px; text-decoration: none; border-radius: 4px; font-size: 14px; font-weight: 600; border: 1px solid #dee2e6;">🔗 기사 원문 보기</a></div>`
        """
        
        response = model.generate_content(prompt)
        return response.text.replace("```html", "").replace("```", "")
    except Exception as e:
        print(f"❌ AI 분석 중 오류: {e}")
        return None

def send_email(html_body):
    """이메일 발송 (PC 최적화 와이드 레이아웃 적용)"""
    if not html_body: return

    kst_now = get_korea_time()
    today_str = kst_now.strftime("%Y년 %m월 %d일")
    subject = f"[Daily] {today_str} 구매계약실 시장 동향 보고"
    
    # 이메일 클라이언트를 위한 인라인 스타일이 적용된 HTML 템플릿 (Width 800px)
    full_html = f"""
    <!DOCTYPE html>
    <html>
    <head>
    <meta charset="utf-8">
    <style>
        body {{ font-family: 'Malgun Gothic', 'Apple SD Gothic Neo', sans-serif; line-height: 1.6; color: #333; background-color: #f4f4f4; margin: 0; padding: 0; }}
        .email-wrapper {{ width: 100%; background-color: #f4f4f4; padding: 20px 0; }}
        .email-container {{ max-width: 800px; margin: 0 auto; background-color: #ffffff; border-radius: 8px; overflow: hidden; box-shadow: 0 4px 6px rgba(0,0,0,0.05); }}
        .header {{ background-color: #0054a6; color: #ffffff; padding: 30px 40px; }}
        .header h1 {{ margin: 0; font-size: 28px; font-weight: 800; letter-spacing: -0.5px; }}
        .header-sub {{ font-size: 16px; margin-top: 10px; opacity: 0.9; font-weight: 500; }}
        .content {{ padding: 40px; }}
        .intro-text {{ margin-bottom: 40px; font-size: 18px; color: #444; border-bottom: 1px solid #eee; padding-bottom: 20px; }}
        .footer {{ background-color: #333333; padding: 30px; text-align: center; font-size: 14px; color: #bbbbbb; }}
        .footer p {{ margin: 5px 0; }}
    </style>
    </head>
    <body>
        <div class="email-wrapper">
            <div class="email-container">
                <!-- 헤더 -->
                <div class="header">
                    <h1>Daily Market & Risk Briefing</h1>
                    <div class="header-sub">
                        POSCO E&C 구매계약실 | {today_str}
                    </div>
                </div>
                
                <!-- 본문 -->
                <div class="content">
                    <div class="intro-text">
                        안녕하십니까, 구매계약실 여러분.<br>
                        <strong>{today_str}</strong> 주요 시장 이슈와 리스크 요인을 정리해 드립니다.
                    </div>
                    
                    {html_body}
                </div>
                
                <!-- 푸터 -->
                <div class="footer">
                    <p>본 리포트는 AI Agent 시스템에 의해 실시간으로 생성되었습니다.</p>
                    <p>문의: 구매기획 그룹 | © POSCO E&C</p>
                </div>
            </div>
        </div>
    </body>
    </html>
    """

    msg = MIMEMultipart()
    msg['From'] = EMAIL_SENDER
    msg['To'] = EMAIL_RECEIVERS
    msg['Subject'] = subject
    msg.attach(MIMEText(full_html, 'html'))

    try:
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(EMAIL_SENDER, EMAIL_PASSWORD)
        receivers = [r.strip() for r in EMAIL_RECEIVERS.split(',')]
        server.sendmail(EMAIL_SENDER, receivers, msg.as_string())
        server.quit()
        print(f"📧 발송 성공: {len(receivers)}명에게 전송 완료.")
    except Exception as e:
        print(f"❌ 발송 실패: {e}")

if __name__ == "__main__":
    if not GOOGLE_API_KEY:
        print("❌ API Key가 설정되지 않았습니다.")
    else:
        items = fetch_news()
        if items:
            report_html = generate_report(items)
            
            if report_html:
                send_email(report_html)
            else:
                print("❌ 리포트 생성 실패")
        else:
            print("수집된 뉴스가 없습니다.")
