import os
import smtplib
import feedparser
import time
import urllib.parse
import urllib.request # 폰트 다운로드를 위해 추가
import re # HTML 태그 제거용
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication # 파일 첨부용
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import google.generativeai as genai

# --- 환경 변수 설정 (GitHub Secrets) ---
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY")
EMAIL_SENDER = os.environ.get("EMAIL_SENDER")
EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD")
EMAIL_RECEIVERS = os.environ.get("EMAIL_RECEIVERS")

# --- 설정: 키워드 ---
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
    "건설 노조 동향"
]

def get_korea_time():
    """서버 시간(UTC)을 한국 시간(KST)으로 변환"""
    utc_now = datetime.now(timezone.utc)
    kst_now = utc_now + timedelta(hours=9)
    return kst_now

def is_recent(published_str):
    """뉴스 날짜가 24시간 이내인지 확인"""
    if not published_str: return False
    try:
        pub_date = parsedate_to_datetime(published_str)
        if pub_date.tzinfo:
            pub_date = pub_date.replace(tzinfo=None)
        
        # UTC 기준 24시간 이내 비교
        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
        one_day_ago = now_utc - timedelta(hours=24)
        return pub_date > one_day_ago
    except:
        return True

def fetch_news():
    """RSS를 통해 뉴스 수집"""
    news_items = []
    print("🔍 뉴스 수집 시작...")
    
    for keyword in KEYWORDS:
        encoded_query = urllib.parse.quote(f"{keyword} when:1d")
        url = f"https://news.google.com/rss/search?q={encoded_query}&hl=ko&gl=KR&ceid=KR:ko"
        
        try:
            feed = feedparser.parse(url)
            if hasattr(feed, 'bozo_exception') and feed.bozo_exception: continue

            for entry in feed.entries[:3]:
                if is_recent(entry.published):
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
    """Gemini AI로 리포트 생성"""
    if not news_items: return None
    
    print("🧠 AI 분석 시작...")
    try:
        genai.configure(api_key=GOOGLE_API_KEY)
        model = genai.GenerativeModel('gemini-2.5-flash-preview-09-2025')

        news_text = ""
        for idx, item in enumerate(news_items):
            news_text += f"[{idx+1}] {item['title']}\n"

        prompt = f"""
        당신은 포스코이앤씨 구매실의 노련한 전문가입니다. 
        아래 뉴스 목록을 보고, 'Daily Market & Risk Briefing' 이메일 본문을 HTML로 작성해 주세요.

        [뉴스 목록]
        {news_text}

        [작성 지침]
        1. 주식/투자 내용 제외. 구매/자재/법규 실무 관점 유지.
        2. 상단에 [오늘의 시장 날씨] 요약(1줄) 포함.
        3. 각 기사는 '핵심'과 '💡시사점'으로 정리.
        4. HTML 형식으로 작성 (제목 제외, 본문만).
        """
        
        response = model.generate_content(prompt)
        return response.text.replace("```html", "").replace("```", "")
    except Exception as e:
        print(f"❌ AI 분석 중 오류: {e}")
        return None

# --- PDF 생성 관련 기능 ---
def create_pdf(news_items, ai_summary_html):
    """뉴스 목록과 AI 요약을 PDF로 생성"""
    print("📄 PDF 생성 시작...")
    try:
        from fpdf import FPDF
    except ImportError:
        print("❌ fpdf2 라이브러리가 없습니다. requirements.txt를 확인하세요.")
        return None

    # 1. 한글 폰트 다운로드 (나눔고딕)
    font_path = 'NanumGothic.ttf'
    if not os.path.exists(font_path):
        url = "https://raw.githubusercontent.com/google/fonts/main/ofl/nanumgothic/NanumGothic-Regular.ttf"
        urllib.request.urlretrieve(url, font_path)

    # 2. PDF 설정
    pdf = FPDF()
    pdf.add_page()
    pdf.add_font('Nanum', '', font_path)
    pdf.set_font('Nanum', size=10)

    # 3. 타이틀 및 날짜
    kst_now = get_korea_time()
    date_str = kst_now.strftime("%Y년 %m월 %d일 (%a)")
    
    pdf.set_font('Nanum', size=16)
    pdf.cell(0, 10, 'POSCO E&C 구매실 Daily Briefing', ln=True, align='C')
    pdf.set_font('Nanum', size=10)
    pdf.cell(0, 10, f'발행일: {date_str} | Generated by AI Agent', ln=True, align='R')
    pdf.ln(5)

    # 4. AI 요약 (HTML 태그 제거 후 텍스트만 넣기)
    pdf.set_font('Nanum', size=12)
    pdf.cell(0, 10, '[Part 1. AI Insight Summary]', ln=True)
    pdf.set_font('Nanum', size=10)
    
    # 간단한 태그 제거 (정규식)
    clean_summary = re.sub('<[^<]+?>', '', ai_summary_html).strip()
    # 텍스트가 너무 길면 잘릴 수 있으므로 multi_cell 사용
    pdf.multi_cell(0, 6, clean_summary)
    pdf.ln(10)

    # 5. 뉴스 스크랩 (링크 포함)
    pdf.set_font('Nanum', size=12)
    pdf.cell(0, 10, '[Part 2. News Scrap]', ln=True)
    
    for item in news_items:
        pdf.set_font('Nanum', size=10)
        # 키워드
        pdf.set_text_color(100, 100, 100) # 회색
        pdf.cell(0, 6, f"[{item['keyword']}]", ln=True)
        
        # 제목 (링크 연결)
        pdf.set_text_color(0, 0, 255) # 파란색
        pdf.set_font('Nanum', size=11, style='U') # 밑줄 효과 흉내(폰트 지원시) 또는 그냥 파란색
        # FPDF link 기능 사용
        pdf.cell(0, 6, item['title'], ln=True, link=item['link'])
        
        pdf.ln(2)
    
    filename = f"Purchase_Briefing_{kst_now.strftime('%Y%m%d')}.pdf"
    pdf.output(filename)
    print(f"✅ PDF 생성 완료: {filename}")
    return filename

def send_email(html_body, pdf_file=None):
    """이메일 발송 (PDF 첨부 기능 추가)"""
    if not html_body: return

    kst_now = get_korea_time()
    today_str = kst_now.strftime("%Y년 %m월 %d일 (%a)")
    subject = f"[구매실 Daily] {today_str} Market & Risk Briefing"
    
    full_html = f"""
    <html>
    <body style="font-family: 'Malgun Gothic', sans-serif; color: #333; line-height: 1.6;">
        <div style="background-color: #0054a6; color: white; padding: 15px; text-align: center;">
            <h2 style="margin:0;">POSCO E&C 구매실 News Agent</h2>
        </div>
        <div style="padding: 20px; border: 1px solid #ddd;">
            <p>안녕하십니까, 구매실 여러분.<br>
            AI Agent가 선별한 {today_str} 주요 리스크 및 시황 정보입니다.<br>
            <strong>상세 내용은 첨부된 PDF 파일을 참고해 주세요.</strong></p>
            <hr style="border:0; border-top:1px solid #eee; margin: 20px 0;">
            {html_body}
        </div>
    </body>
    </html>
    """

    msg = MIMEMultipart()
    msg['From'] = EMAIL_SENDER
    msg['To'] = EMAIL_RECEIVERS
    msg['Subject'] = subject
    
    # 본문 추가
    msg.attach(MIMEText(full_html, 'html'))

    # PDF 첨부
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
            # PDF 생성
            pdf_filename = create_pdf(items, report_html)
            
            if report_html:
                send_email(report_html, pdf_filename)
            else:
                print("❌ 리포트 생성 실패")
        else:
            print("수집된 뉴스가 없습니다.")
```

### 🚨 중요: requirements.txt 파일 수정

PDF 기능을 쓰려면 **`requirements.txt`** 파일도 꼭 수정해야 합니다. 깃허브에서 `requirements.txt` 파일을 열고 내용을 아래와 같이 바꿔주세요. (`fpdf2`가 추가되었습니다.)

```text
feedparser
google-generativeai
fpdf2
