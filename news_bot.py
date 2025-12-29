import os
import smtplib
import feedparser
import time
import urllib.parse
import urllib.request
import re
import requests # 웹페이지 접속용
from bs4 import BeautifulSoup # HTML 본문 추출용
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
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

def fetch_article_content(url):
    """링크를 타고 들어가서 기사 본문 텍스트를 긁어옴 (스크랩용)"""
    try:
        # 구글 뉴스 리다이렉트 등을 통과하기 위한 헤더 보강
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7'
        }
        
        # Session 사용으로 리다이렉트 추적 능력 향상
        session = requests.Session()
        # 타임아웃 10초로 넉넉하게 설정
        response = session.get(url, headers=headers, timeout=10, allow_redirects=True)
        response.encoding = response.apparent_encoding # 한글 깨짐 방지
        
        # 구글 뉴스 기본 페이지가 긁혔는지 확인 (실패로 간주)
        if "Comprehensive up-to-date news coverage" in response.text:
            return None

        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # 불필요한 요소 제거 (광고, 메뉴, 스크립트 등)
            for element in soup(["script", "style", "header", "footer", "nav", "aside", "iframe", "form"]):
                element.decompose()

            content = ""
            
            # 1. <article> 태그 우선 검색
            article = soup.find('article')
            if article:
                content = article.get_text(strip=True, separator='\n')
            else:
                # 2. 본문으로 추정되는 <div>나 <p> 태그 수집
                # 주요 언론사별 본문 클래스명 패턴 시도
                target_divs = soup.find_all('div', class_=re.compile(r'(article|content|body|detail)', re.I))
                if target_divs:
                    # 가장 텍스트가 긴 div 선택
                    best_div = max(target_divs, key=lambda x: len(x.get_text()))
                    content = best_div.get_text(strip=True, separator='\n')
                else:
                    # 최후의 수단: 모든 <p> 태그 수집
                    paragraphs = soup.find_all('p')
                    # 너무 짧은 문장 제외하고 합치기
                    content = "\n".join([p.get_text(strip=True) for p in paragraphs if len(p.get_text(strip=True)) > 20])
            
            # 텍스트 정제 (연속된 공백/줄바꿈 제거)
            content = re.sub(r'\n\s*\n', '\n\n', content)
            
            # 내용이 너무 짧거나 구글 안내 문구면 실패 처리
            if len(content) < 50 or "Comprehensive up-to-date" in content:
                return None

            return content[:1500] + "..." if len(content) > 1500 else content # 최대 1500자
    except Exception as e:
        print(f"    - Scraping Error: {e}")
        return None
    
    return None

def fetch_news():
    """RSS 뉴스 수집 + 본문 스크랩"""
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
                        # RSS에 기본적으로 포함된 요약문(summary) 가져오기 (HTML 태그 제거)
                        rss_summary = ""
                        if hasattr(entry, 'summary'):
                             rss_summary = BeautifulSoup(entry.summary, "html.parser").get_text(strip=True)
                        elif hasattr(entry, 'description'):
                             rss_summary = BeautifulSoup(entry.description, "html.parser").get_text(strip=True)

                        print(f"  Scraping: {entry.title[:10]}...")
                        # 본문 스크랩 시도
                        scraped_text = fetch_article_content(entry.link)
                        
                        # 스크랩 성공하면 본문 사용, 실패하면 RSS 요약문 사용
                        final_text = scraped_text if scraped_text else f"(본문 수집 불가 - 요약본 제공)\n\n{rss_summary}"
                        
                        if not final_text.strip():
                            final_text = "(본문 내용을 불러올 수 없습니다. 링크를 확인해주세요.)"

                        news_items.append({
                            "title": entry.title,
                            "link": entry.link,
                            "keyword": keyword,
                            "date": entry.published,
                            "full_text": final_text # 본문 저장
                        })
        except Exception as e:
            print(f"⚠️ '{keyword}' 오류: {e}")
            continue
            
    print(f"✅ 총 {len(news_items)}개의 최신 뉴스 수집 완료.")
    return news_items

def generate_report(news_items):
    """Gemini AI 리포트 (날짜 강제 주입)"""
    if not news_items: return None
    
    kst_now = get_korea_time()
    today_formatted = kst_now.strftime("%Y년 %m월 %d일") # 예: 2025년 05월 20일
    
    print("🧠 AI 분석 시작...")
    try:
        genai.configure(api_key=GOOGLE_API_KEY)
        model = genai.GenerativeModel('gemini-2.5-flash-preview-09-2025')

        news_text = ""
        for idx, item in enumerate(news_items):
            news_text += f"[{idx+1}] {item['title']} ({item['keyword']})\n"

        # 프롬프트에 날짜와 부서명을 명확히 박아넣음
        prompt = f"""
        오늘은 {today_formatted}입니다.
        당신은 **포스코이앤씨 구매계약실**의 수석 애널리스트입니다.
        아래 뉴스들을 바탕으로 'Daily Market & Risk Briefing' 이메일을 작성하세요.

        [뉴스 목록]
        {news_text}

        [작성 원칙]
        1. **날짜 준수**: 반드시 오늘 날짜({today_formatted})를 기준으로 작성하세요. 2024년 등 과거 연도 표기 금지.
        2. **주식/투자 배제**: 건설 테마주, 주가 등락 내용은 절대 포함하지 마세요.
        3. **구매계약실 관점**: 계약, 납기, 단가, 법적 리스크 위주로 분석하세요.

        [보고서 형식 (HTML)]
        - `<div>` 태그로 감싸서 작성.
        - **[오늘의 시장 날씨]**: ☀️/☁️/☔ 아이콘 사용하여 1줄 요약.
        - **분야별 뉴스**: 
          - [규제/리스크], [자재/시황], [글로벌/물류] 등으로 분류.
          - 각 기사 하단에 `💡Insight: (내용)` 한 줄 추가.
        """
        
        response = model.generate_content(prompt)
        return response.text.replace("```html", "").replace("```", "")
    except Exception as e:
        print(f"❌ AI 분석 중 오류: {e}")
        return None

# --- PDF 스크랩북 생성 (본문 포함) ---
def create_scrap_pdf(news_items):
    print("📄 스크랩 PDF 생성 시작...")
    try:
        from fpdf import FPDF
    except ImportError:
        print("❌ fpdf2 라이브러리가 없습니다.")
        return None

    font_path = 'NanumGothic.ttf'
    if not os.path.exists(font_path):
        urllib.request.urlretrieve("https://raw.githubusercontent.com/google/fonts/main/ofl/nanumgothic/NanumGothic-Regular.ttf", font_path)

    pdf = FPDF()
    pdf.add_font('Nanum', '', font_path) # 폰트 등록 먼저!
    pdf.add_page()

    kst_now = get_korea_time()
    date_str = kst_now.strftime("%Y년 %m월 %d일")

    # 타이틀
    pdf.set_font('Nanum', size=20)
    pdf.cell(0, 15, f'구매계약실 일일 뉴스 스크랩 ({date_str})', ln=True, align='C')
    pdf.ln(10)

    # 뉴스 루프
    for idx, item in enumerate(news_items):
        # 기사 제목
        pdf.set_font('Nanum', size=14)
        pdf.set_text_color(0, 84, 166) # 포스코 블루
        pdf.multi_cell(0, 8, f"{idx+1}. {item['title']}")
        
        # 메타 정보
        pdf.set_font('Nanum', size=9)
        pdf.set_text_color(100, 100, 100)
        pdf.cell(0, 6, f"키워드: {item['keyword']} | 링크: {item['link'][:50]}...", ln=True, link=item['link'])
        pdf.ln(2)

        # 기사 본문 (스크랩 내용)
        pdf.set_font('Nanum', size=10)
        pdf.set_text_color(30, 30, 30)
        # 본문 텍스트 정리 (줄바꿈 등)
        body_text = item.get('full_text', '내용 없음').replace('\t', '  ')
        # 한글 폰트에서 유니코드 문자가 깨질 수 있으므로 기본 정제
        body_text = body_text.encode('latin-1', 'replace').decode('latin-1') # fpdf 인코딩 에러 방지용 (심플 처리)
        # 실제로는 fpdf2가 유니코드를 꽤 잘 처리하지만, 안전장치로 특수문자 일부 제외
        
        pdf.multi_cell(0, 5, body_text)
        
        # 기사 간 구분선
        pdf.ln(5)
        pdf.set_draw_color(200, 200, 200)
        pdf.line(10, pdf.get_y(), 200, pdf.get_y())
        pdf.ln(10)

    filename = f"News_Scrap_{kst_now.strftime('%Y%m%d')}.pdf"
    pdf.output(filename)
    return filename

def send_email(html_body, pdf_file=None):
    if not html_body: return

    kst_now = get_korea_time()
    today_str = kst_now.strftime("%Y년 %m월 %d일")
    subject = f"[Daily] {today_str} 구매계약실 시장 동향 보고"
    
    # 깔끔한 리스트 형태의 HTML (Original Style)
    full_html = f"""
    <html>
    <body style="font-family: 'Malgun Gothic', sans-serif; color: #333; line-height: 1.6;">
        <div style="padding: 20px; border: 1px solid #ddd;">
            <h2 style="color: #0054a6; margin-bottom: 20px;">POSCO E&C 구매계약실 Daily Briefing</h2>
            <p>안녕하십니까, 구매계약실 여러분.<br>
            {today_str} 주요 시장 동향입니다.</p>
            
            <div style="background-color: #f9f9f9; padding: 15px; border-left: 5px solid #0054a6; margin: 20px 0;">
                <strong>📂 유첨:</strong> 금일 주요 기사 전문 스크랩 (PDF)
            </div>

            <hr style="border:0; border-top:1px solid #eee; margin: 20px 0;">
            
            {html_body}
            
            <hr style="border:0; border-top:1px solid #eee; margin: 20px 0;">
            <p style="font-size: 12px; color: #888;">* 본 메일은 AI Agent가 자동 발송했습니다.</p>
        </div>
    </body>
    </html>
    """

    msg = MIMEMultipart()
    msg['From'] = EMAIL_SENDER
    msg['To'] = EMAIL_RECEIVERS
    msg['Subject'] = subject
    msg.attach(MIMEText(full_html, 'html'))

    if pdf_file and os.path.exists(pdf_file):
        with open(pdf_file, "rb") as f:
            attach = MIMEApplication(f.read(), _subtype="pdf")
            attach.add_header('Content-Disposition', 'attachment', filename=pdf_file)
            msg.attach(attach)

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
            pdf_filename = create_scrap_pdf(items) # 스크랩 전용 PDF 생성
            
            if report_html:
                send_email(report_html, pdf_filename)
            else:
                print("❌ 리포트 생성 실패")
        else:
            print("수집된 뉴스가 없습니다.")
