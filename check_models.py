import streamlit as st
from st_supabase_connection import SupabaseConnection
import folium
from streamlit_folium import st_folium
from folium.plugins import HeatMap
import pandas as pd
import google.generativeai as genai

# --- 1. 초기 설정 및 연결 ---
st.set_page_config(page_title="SafeStep", layout="wide")

# Supabase 연결 설정 [cite: 64, 80]
conn = st.connection(
    "supabase", 
    type=SupabaseConnection,
    url=st.secrets["connections"]["supabase"]["url"],
    key=st.secrets["connections"]["supabase"]["key"]
)

# Gemini API 설정 [cite: 38, 41]
genai.configure(api_key=st.secrets["GOOGLE_API_KEY"])

# --- 1. 초기 설정 및 연결 부분 수정 ---

# 안전한 모델 로딩을 위한 리스트 (목록에서 확인된 명칭들)
AVAILABLE_MODELS = [
    'models/gemini-2.5-flash',
    'models/gemini-2.0-flash',
    'models/gemini-1.5-flash'
]

model = None
for model_name in AVAILABLE_MODELS:
    try:
        temp_model = genai.GenerativeModel(model_name)
        # 실제로 작동하는지 가벼운 테스트
        temp_model.generate_content("ping")
        model = temp_model
        print(f"✅ {model_name} 모델로 연결되었습니다.")
        break
    except Exception:
        continue

if model is None:
    st.error("사용 가능한 Gemini 모델을 찾을 수 없습니다. API 키와 네트워크를 확인하세요.")
    st.stop()
    
# --- 2. 데이터 처리 함수들 ---
def get_active_travels():
    """현재 여행 일정 가져오기""" 
    return conn.table("travels").select("*").execute()

def get_crime_stats():
    """범죄 통계 위경도 데이터 로드""" 
    return conn.table("crime_stats").select("latitude, longitude, crime_count").execute()

def get_user_reports():
    """실시간 사용자 제보 데이터 로드"""
    return conn.table("user_reports").select("latitude, longitude, incident_type, description").execute()

def get_country_info(country_name):
    """국가별 경찰 및 구급 번호 조회""" 
    country = country_name.split(",")[-1].strip()
    return conn.table("countries_info").select("*").ilike("country_name", f"%{country}%").execute()

def save_user_report(incident, desc, lat, lon):
    """제보 내용을 DB에 즉시 반영""" 
    data = {"incident_type": incident, "description": desc, "latitude": lat, "longitude": lon}
    conn.table("user_reports").insert(data).execute()

# --- 3. UI 구성 (User Service Flow 반영) [cite: 4] ---
st.title("🛡️ SafeStep: 실시간 안전 가이드")

# [A] 실시간 안전 지도 섹션 [cite: 4, 46]
st.subheader("🗺️ 실시간 안전 지도")
m = folium.Map(location=[37.5665, 126.9780], zoom_start=3)

# 1. 범죄 통계 히트맵 추가 [cite: 8, 46]
crime_data = get_crime_stats()
if crime_data.data:
    df_crime = pd.DataFrame(crime_data.data)
    heat_data = df_crime[['latitude', 'longitude', 'crime_count']].values.tolist()
    HeatMap(heat_data, radius=15, blur=10, gradient={0.4: 'blue', 0.65: 'lime', 1: 'red'}).add_to(m)

# 2. 사용자 제보 경고 마커 추가 [cite: 9, 58, 126]
report_data = get_user_reports()
if report_data.data:
    for rep in report_data.data:
        popup = folium.Popup(f"<b>{rep['incident_type']}</b><br>{rep['description']}", max_width=300)
        folium.Marker(
            location=[rep['latitude'], rep['longitude']],
            popup=popup,
            icon=folium.Icon(color='red', icon='exclamation-triangle', prefix='fa')
        ).add_to(m)

st_folium(m, width=1200, height=500)

st.divider()

col_left, col_right = st.columns([1, 1])

with col_left:
    # [B] 나의 여행 일정 섹션 [cite: 4, 68]
    st.subheader("🗓️ 나의 여행 일정")
    travels = get_active_travels()
    if travels.data:
        for t in travels.data:
            st.info(f"📍 **{t['destination']}** ({t['start_date']} ~ {t['end_date']})")
    
    # [C] 실시간 제보 입력 섹션 [cite: 4, 22, 166]
    st.subheader("🚨 실시간 상황 제보")
    with st.form("report_form", clear_on_submit=True):
        incident = st.selectbox("사고 유형", ["교통사고", "자연재해", "치안불안", "기타"])
        desc = st.text_area("상세 내용")
        c1, c2 = st.columns(2)
        lat = c1.number_input("위도(Lat)", value=37.5665, format="%.6f")
        lon = c2.number_input("경도(Lon)", value=126.9780, format="%.6f")
        if st.form_submit_button("제보하기"):
            if desc:
                save_user_report(incident, desc, lat, lon)
                st.success("제보 완료! 지도를 확인하세요.")
                st.rerun()

with col_right:
    # [D] Gemini AI 긴급 대응 가이드 [cite: 4, 11, 54-57]
    st.subheader("🤖 AI 긴급 대응 비서")
    with st.container(border=True):
        user_input = st.text_input("현재 상황 입력", placeholder="예: 주변에 수상한 사람이 따라와요")
        if st.button("AI 가이드 요청"):
            if user_input:
                with st.spinner("전문가 가이드를 생성 중입니다..."):
                    dest = travels.data[0]['destination'] if travels.data else "Unknown"
                    c_info = get_country_info(dest)
                    # DB에서 실제 경찰 번호 추출 [cite: 78, 134]
                    p_num = c_info.data[0]['police_number'] if c_info.data else "현지 경찰"
                    
                    # 계획서 기반 전문가 페르소나 및 3단계 대응 전략 프롬프트 
                    prompt = f"""너는 전 세계 치안 정보를 숙지한 안전 전문가야.
                    위치: {dest}, 해당 국가 경찰번호: {p_num}.
                    사용자 상황: {user_input}.
                    
                    다음 지침에 따라 3단계 이내의 즉각적인 행동 요령과 긴급 전화번호를 알려줘:
                    1. 상황별 즉각적인 행동 요령
                    2. 맞춤형 긴급 번호 제공 (경찰번호 {p_num} 포함)
                    3. 안전 대피 경로 또는 인근 도움 시설 안내
                    """
                    response = model.generate_content(prompt)
                    st.markdown(response.text)