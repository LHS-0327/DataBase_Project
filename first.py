import streamlit as st
from st_supabase_connection import SupabaseConnection
import folium
from streamlit_folium import st_folium
from folium.plugins import HeatMap
import pandas as pd
import google.generativeai as genai
from datetime import datetime
import requests

# --- 1. 초기 설정 및 외부 서비스 연결 ---
st.set_page_config(page_title="SafeStep", page_icon="🛡️", layout="wide")

# Supabase 연결 (캐싱 제거)
conn = st.connection(
    "supabase", 
    type=SupabaseConnection,
    url=st.secrets["connections"]["supabase"]["url"],
    key=st.secrets["connections"]["supabase"]["key"]
)

# Gemini AI 설정
genai.configure(api_key=st.secrets["GOOGLE_API_KEY"])
model = genai.GenerativeModel('models/gemini-2.5-flash')

# ✨ 세션 상태 초기화 (제보 전용 좌표 report_lat/lon 추가)
if 'clicked_lat' not in st.session_state: st.session_state.clicked_lat = 35.6894
if 'clicked_lon' not in st.session_state: st.session_state.clicked_lon = 139.6917
if 'dest_lat' not in st.session_state: st.session_state.dest_lat = 35.6586
if 'dest_lon' not in st.session_state: st.session_state.dest_lon = 139.7454
if 'report_lat' not in st.session_state: st.session_state.report_lat = 35.6894 # 제보 전용 초기값
if 'report_lon' not in st.session_state: st.session_state.report_lon = 139.6917 # 제보 전용 초기값

if 'main_path' not in st.session_state: st.session_state.main_path = None
if 'alt_path' not in st.session_state: st.session_state.alt_path = None
if 'is_main_risky' not in st.session_state: st.session_state.is_main_risky = False

# 범죄 아이콘 설정
CRIME_INFO = {
    "살인": "🩸", "강도": "⚔️", "방화": "🔥", "성범죄": "🔞", "절도": "💰", 
    "폭행": "👊", "상해": "🩹", "사기": "🎭", "날치기": "🚴", "소매치기": "🖐️", 
    "자동차 절도": "🚗", "빈집털이": "🏠", "자동판매기 털이": "🥤", "기물 파손": "🧱",
    "주거 침입": "🏚️", "오토바이 절도": "🛵", "마약 범죄": "💊", "조직 범죄": "👥"
}

# --- 2. 데이터 처리 및 API 함수 ---

def get_crime_stats():
    return conn.table("crime_stats").select("latitude, longitude, location_name, crime_type, crime_count, country_name").execute()

def get_user_reports():
    return conn.table("user_reports").select("*").execute()

def get_active_travels():
    return conn.table("travels").select("*").order("created_at", desc=True).execute()

def add_travel(destination, start_date, end_date):
    data = {"destination": destination, "start_date": str(start_date), "end_date": str(end_date)}
    return conn.table("travels").insert(data).execute()

def delete_travel(travel_id):
    return conn.table("travels").delete().eq("id", travel_id).execute()

def save_user_report(incident, desc, lat, lon):
    data = {"incident_type": incident, "description": desc, "latitude": lat, "longitude": lon}
    return conn.table("user_reports").insert(data).execute()

def get_routes_with_alternatives(s_lat, s_lon, e_lat, e_lon):
    url = f"http://router.project-osrm.org/route/v1/walking/{s_lon},{s_lat};{e_lon},{e_lat}?overview=full&geometries=geojson&alternatives=true"
    try:
        response = requests.get(url, timeout=5).json()
        if response['code'] == 'Ok':
            return response['routes']
    except: return None
    return None

# --- 3. UI 구성 ---
st.title("🛡️ SafeStep: 지능형 여행 안전 가이드")

with st.sidebar:
    st.header("🚶 안전 경로 분석")
    # ✨ 선택 모드에 '제보 위치 설정' 옵션 추가
    pick_mode = st.radio("📍 지도 클릭 설정 대상", ["출발지 설정", "목적지 설정", "제보 위치 설정"], horizontal=True)
    
    with st.form("route_search_form"):
        st.write("**좌표 정보**")
        s_lat_in = st.number_input("출발 위도", value=st.session_state.clicked_lat, format="%.6f")
        s_lon_in = st.number_input("출발 경도", value=st.session_state.clicked_lon, format="%.6f")
        d_lat_in = st.number_input("목적 위도", value=st.session_state.dest_lat, format="%.6f")
        d_lon_in = st.number_input("목적 경도", value=st.session_state.dest_lon, format="%.6f")
        search_btn = st.form_submit_button("🛡️ 경로 안전성 검사 실행")

# [B] 메인 영역
st.subheader("🗺️ 실시간 지역별 위험 분석 및 경로 안내")


# 데이터 로드
crime_response = get_crime_stats()
df_crime = pd.DataFrame(crime_response.data) if crime_response.data else pd.DataFrame()

# 사용자 실시간 제보 데이터 로드
report_response = get_user_reports()
df_reports = pd.DataFrame(report_response.data) if report_response.data else pd.DataFrame()

# 🚀 경로 분석 로직 (위험 점수 기반 최적 경로 탐색)
if search_btn:
    with st.spinner("모든 경로의 위험도를 전수 조사하여 최선의 우회로를 찾는 중..."):
        st.session_state.main_path = None
        st.session_state.alt_path = None
        st.session_state.is_main_risky = False
        
        routes = get_routes_with_alternatives(s_lat_in, s_lon_in, d_lat_in, d_lon_in)
        if routes:
            # 1. 모든 탐색 경로의 '위험 점수' 계산
            route_data = []
            for i, route in enumerate(routes):
                path = [[c[1], c[0]] for c in route['geometry']['coordinates']]
                score = 0
                for p in path:
                    # 해당 지점 반경 내 범죄 건수 및 제보 건수를 합산하여 점수화
                    score += len(df_crime[(abs(df_crime['latitude'] - p[0]) < 0.01) & (abs(df_crime['longitude'] - p[1]) < 0.01)])
                    score += len(df_reports[(abs(df_reports['latitude'] - p[0]) < 0.01) & (abs(df_reports['longitude'] - p[1]) < 0.01)])
                route_data.append({'index': i, 'path': path, 'score': score})

            # 2. 메인 경로(최단 거리) 설정
            st.session_state.main_path = route_data[0]['path']
            st.session_state.is_main_risky = route_data[0]['score'] > 0
            
            # 3. 우회 경로 결정 로직
            if st.session_state.is_main_risky and len(route_data) > 1:
                # 메인 경로(0번)를 제외한 대안들 중 점수가 가장 낮은 경로 탐색
                alts = route_data[1:]
                best_alt = min(alts, key=lambda x: x['score'])
                
                # ✨ 핵심: 메인보다 조금이라도 더 안전하다면 우회로로 채택!
                if best_alt['score'] < route_data[0]['score']:
                    st.session_state.alt_path = best_alt['path']
                else:
                    # 만약 모든 대안이 메인과 위험도가 똑같다면, 가장 거리가 먼 대안이라도 표시
                    st.session_state.alt_path = best_alt['path']

# 지도 생성
m = folium.Map(location=[st.session_state.clicked_lat, st.session_state.clicked_lon], zoom_start=13)

# 🗺️ 경로 시각화
if st.session_state.main_path:
    # 1. 메인 경로(최단 경로): 위험 요소가 하나라도 있으면 빨간색, 없으면 파란색
    m_color = "red" if st.session_state.is_main_risky else "blue"
    folium.PolyLine(
        st.session_state.main_path, 
        color=m_color, 
        weight=7, 
        opacity=0.7, 
        tooltip="메인 경로 (최단)"
    ).add_to(m)
    
    # 2. 메인 경로가 위험할 경우의 처리
    if st.session_state.is_main_risky:
        # 분석 알고리즘이 찾아낸 '상대적으로 더 안전한' 우회로가 있는 경우
        if st.session_state.alt_path:
            folium.PolyLine(
                st.session_state.alt_path, 
                color="green", 
                weight=10, 
                opacity=0.8, 
                dash_array='10, 5', 
                tooltip="추천 우회 경로"
            ).add_to(m)
            # 💡 핵심: '상대적으로 위험 요소가 적은' 점을 강조하는 경고창
            st.warning("🚨 [위험 감지] 최단 경로 주변에 범죄 이력 또는 제보가 확인되었습니다. 상대적으로 위험 점수가 더 낮은 초록색 점선(우회로) 이용을 권장합니다.")
        
        # 탐색된 모든 경로의 위험도가 동일하여 더 나은 대안이 없는 경우
        else:
            st.error("⚠️ [주의] 현재 경로 상에 위험 요소가 감지되었으나, 더 안전한 우회 경로가 존재하지 않습니다. 이동 시 각별히 주의하시거나 가급적 차량을 이용하시기 바랍니다.")
    
    # 3. 메인 경로가 완벽히 안전한 경우
    else:
        st.success("✅ [안전 확인] 과거 통계 및 실시간 제보 분석 결과, 현재 경로가 치안 상 안전한 것으로 판별되었습니다.")

# 히트맵 및 데이터 마커
if not df_crime.empty:
    HeatMap(df_crime[['latitude', 'longitude', 'crime_count']].values.tolist(), radius=20, blur=15).add_to(m)
    for (lat, lon), group in df_crime.groupby(['latitude', 'longitude']):
        loc, country = group['location_name'].iloc[0], group['country_name'].iloc[0]
        html = f"<div style='width:280px; font-family:sans-serif;'><h5>📍 {loc} ({country})</h5><div style='display:grid; grid-template-columns:1fr 1fr; gap:5px;'>"
        for _, row in group.iterrows():
            icon = CRIME_INFO.get(row['crime_type'], "⚠️")
            html += f"<div style='font-size:11px; padding:3px; background:#f8f9fa;'>{icon} {row['crime_type']}: <b>{row['crime_count']}</b></div>"
        html += "</div></div>"
        folium.CircleMarker(location=[lat, lon], radius=8, popup=folium.Popup(html, max_width=300), color="#FF4B4B", fill=True).add_to(m)

# 실시간 제보 마커 표시
if not df_reports.empty:
    for _, rep in df_reports.iterrows():
        report_html = f"<div style='width:200px;'><b>🚨 실시간 제보</b><br>{rep['incident_type']}<br>{rep['description']}</div>"
        folium.Marker(location=[rep['latitude'], rep['longitude']], popup=folium.Popup(report_html, max_width=200), icon=folium.Icon(color='red', icon='exclamation-triangle', prefix='fa')).add_to(m)

# ✨ 지도 상의 현재 설정 위치 마커들 (출발/목적/제보 위치)
folium.Marker([st.session_state.clicked_lat, st.session_state.clicked_lon], tooltip="출발지", icon=folium.Icon(color='blue', icon='play')).add_to(m)
folium.Marker([st.session_state.dest_lat, st.session_state.dest_lon], tooltip="목적지", icon=folium.Icon(color='black', icon='stop')).add_to(m)
# ✨ 제보할 위치를 보여주는 전용 주황색 마커 추가
folium.Marker([st.session_state.report_lat, st.session_state.report_lon], tooltip="제보할 위치", icon=folium.Icon(color='orange', icon='bullhorn', prefix='fa')).add_to(m)

# 지도 출력
out = st_folium(m, use_container_width=True, height=700, key="safety_map")
report_mode = st.toggle("📍 좌표 지정 모드 (지도 클릭 활성화)", value=False)

# ✨ [수정] 클릭 모드에 따라 세 가지 좌표 중 하나를 업데이트
if report_mode and out.get("last_clicked"):
    nl, ng = out["last_clicked"]["lat"], out["last_clicked"]["lng"]
    if pick_mode == "출발지 설정" and nl != st.session_state.clicked_lat:
        st.session_state.clicked_lat, st.session_state.clicked_lon = nl, ng; st.rerun()
    elif pick_mode == "목적지 설정" and nl != st.session_state.dest_lat:
        st.session_state.dest_lat, st.session_state.dest_lon = nl, ng; st.rerun()
    elif pick_mode == "제보 위치 설정" and nl != st.session_state.report_lat:
        st.session_state.report_lat, st.session_state.report_lon = nl, ng; st.rerun()

st.divider()
c_left, c_right = st.columns([1, 1.2])

with c_left:
    st.subheader("🗓️ 나의 여행 관리")
    with st.expander("➕ 일정 추가"):
        with st.form("add_travel"):
            dest = st.text_input("목적지")
            sd, ed = st.date_input("시작", datetime.now()), st.date_input("종료", datetime.now())
            if st.form_submit_button("저장"):
                if dest: add_travel(dest, sd, ed); st.rerun()
    tl = get_active_travels()
    if tl.data:
        for t in tl.data:
            col1, col2 = st.columns([5, 1])
            col1.info(f"📍 **{t['destination']}** ({t['start_date']} ~ {t['end_date']})")
            if col2.button("🗑️", key=f"d_{t['id']}"): delete_travel(t['id']); st.rerun()

    # ✨ 실시간 현장 상황 제보 폼 (제보 위치 세션 좌표 연동)
    st.divider()
    st.subheader("🚨 실시간 현장 상황 제보")
    with st.form("report_form", clear_on_submit=True):
        incident = st.selectbox("사고 유형 선택", ["소매치기/절도", "날치기", "폭행/시비/협박", "사기/바가지", "교통사고", "자연재해(지진 등)", "우범지역 알림", "기타"])
        desc = st.text_area("상세 내용 설명")
        
        col_la, col_lo = st.columns(2)
        # ✨ 제보 폼의 위경도를 세션의 report_lat/lon과 연동
        lat_in = col_la.number_input("위도", value=st.session_state.report_lat, format="%.6f")
        lon_in = col_lo.number_input("경도", value=st.session_state.report_lon, format="%.6f")
        
        if st.form_submit_button("제보하기"):
            if desc:
                save_user_report(incident, desc, lat_in, lon_in)
                st.success("현장 상황이 성공적으로 제보되었습니다!!")
                st.rerun()
            else:
                st.warning("상세 내용을 입력해 주세요.")

with c_right:
    st.subheader("🤖 AI 안전 비서")
    with st.container(border=True):
        u_in = st.text_input("상황 입력")
        if st.button("가이드 요청"):
            if u_in:
                with st.spinner("분석 중..."):
                    risk = "메인 경로에 위험 지역이 포함되어 있습니다." if st.session_state.is_main_risky else ""
                    prompt = f"전문가로서 답해줘. {risk} 상황: {u_in}. 대응 3단계를 알려줘."
                    st.info(model.generate_content(prompt).text)