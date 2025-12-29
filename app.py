import calendar
from io import BytesIO
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import streamlit as st
from openpyxl.styles import Alignment, Font, Border, Side, PatternFill

# ─────────────────────────────────────────────
# 1. 기본 설정 및 상수
# ─────────────────────────────────────────────
st.set_page_config(
    page_title="도시가스 공급량 예측 시스템",
    layout="wide",
)

MJ_PER_NM3 = 42.563
MJ_TO_GJ = 0.001

def mj_to_gj(x):
    try: return float(x) * MJ_TO_GJ
    except: return np.nan

def mj_to_m3(x):
    try: return float(x) / MJ_PER_NM3
    except: return np.nan

def gj_to_mj(x):
    try: return float(x) / MJ_TO_GJ
    except: return np.nan

# ─────────────────────────────────────────────
# 2. 데이터 로딩 (에러 방지 및 유연성 강화)
# ─────────────────────────────────────────────
def standardize_columns(df):
    """컬럼명 표준화: 띄어쓰기나 유사 단어 자동 매핑"""
    col_map = {}
    for c in df.columns:
        cs = str(c).replace(" ", "").strip()
        if cs in ["일자", "date", "Date", "날짜"]: col_map[c] = "일자"
        elif "공급량" in cs and "MJ" in cs: col_map[c] = "공급량(MJ)"
        elif "공급량" in cs and ("GJ" in cs or "Gj" in cs): col_map[c] = "공급량(GJ)"
        elif "평균" in cs and ("기온" in cs or "온도" in cs): col_map[c] = "평균기온(℃)"
        elif cs in ["연", "연도", "Year"]: col_map[c] = "연도"
        elif cs in ["월", "Month"]: col_map[c] = "월"
        elif cs in ["일", "Day"]: col_map[c] = "일"
    return df.rename(columns=col_map)

@st.cache_data(show_spinner=False)
def load_daily_data(uploaded_file):
    """일일 실적 로딩 (파일 없으면 None 반환하여 에러 방지)"""
    if uploaded_file is not None:
        try:
            df_raw = pd.read_excel(uploaded_file)
        except Exception:
            return None, None
    else:
        # 로컬 파일 탐색 (없으면 그냥 무시)
        excel_path = Path(__file__).parent / "공급량(일일실적).xlsx"
        if excel_path.exists():
            df_raw = pd.read_excel(excel_path)
        else:
            return None, None # 에러 내지 않고 빈 값 반환

    df_raw = standardize_columns(df_raw)
    
    if "일자" not in df_raw.columns:
        return None, None

    # 공급량 단위 환산
    if "공급량(MJ)" not in df_raw.columns and "공급량(GJ)" in df_raw.columns:
        df_raw["공급량(MJ)"] = df_raw["공급량(GJ)"].apply(gj_to_mj)

    # 날짜 처리
    df_raw["일자"] = pd.to_datetime(df_raw["일자"], errors='coerce')
    df_raw = df_raw.dropna(subset=["일자"])
    df_raw["연도"] = df_raw["일자"].dt.year
    df_raw["월"] = df_raw["일자"].dt.month
    df_raw["일"] = df_raw["일자"].dt.day

    df_temp_all = df_raw.dropna(subset=["평균기온(℃)"]).copy() if "평균기온(℃)" in df_raw.columns else pd.DataFrame()
    df_model = df_raw.dropna(subset=["공급량(MJ)"]).copy() if "공급량(MJ)" in df_raw.columns else pd.DataFrame()
    
    return df_model, df_temp_all

@st.cache_data(show_spinner=False)
def load_monthly_plan(uploaded_file):
    """월별 계획 로딩 (파일 없으면 None 반환)"""
    if uploaded_file is not None:
        try:
            df = pd.read_excel(uploaded_file)
        except Exception:
            return None
    else:
        excel_path = Path(__file__).parent / "공급량(계획_실적).xlsx" # 혹은 '월별계획.xlsx'
        # 파일명을 유연하게 찾기 위해 리스트 확인
        if not excel_path.exists():
            excel_path = Path(__file__).parent / "월별계획.xlsx"
            
        if excel_path.exists():
            df = pd.read_excel(excel_path)
        else:
            return None # ⚠️ 여기서 에러(raise)를 내지 않고 None을 줍니다.

    df = standardize_columns(df)
    # 숫자형 변환
    for col in ["연도", "월"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')
    
    return df

# ─────────────────────────────────────────────
# 3. 계산 및 시각화 로직
# ─────────────────────────────────────────────
def make_daily_plan_table(df_daily, df_plan, target_year, target_month, recent_window=3):
    # 데이터 유효성 검사
    if df_daily is None or df_daily.empty: return None, [], None
    
    # 1. 과거 데이터 추출
    past_data = df_daily[(df_daily["연도"] < target_year) & (df_daily["월"] == target_month)].copy()
    if past_data.empty: return None, [], None
    
    used_years = sorted(past_data["연도"].unique())[-recent_window:]
    df_recent = past_data[past_data["연도"].isin(used_years)].copy()
    
    if df_recent.empty: return None, [], None

    # 2. 요일별 패턴 분석
    df_recent["weekday"] = df_recent["일자"].dt.weekday
    df_recent["nth"] = df_recent.groupby(["연도", "weekday"]).cumcount() + 1
    
    # 연도별 총량으로 나누어 비율 계산
    df_recent["yearly_sum"] = df_recent.groupby("연도")["공급량(MJ)"].transform("sum")
    df_recent["ratio"] = df_recent["공급량(MJ)"] / df_recent["yearly_sum"]
    
    # (요일, n번째) 키로 평균 비율 산출
    ratio_map = df_recent.groupby(["weekday", "nth"])["ratio"].mean().to_dict()
    dow_map = df_recent.groupby("weekday")["ratio"].mean().to_dict() # n번째가 없을 경우 대비
    
    # 3. 타겟 월 생성
    last_day = calendar.monthrange(target_year, target_month)[1]
    dr = pd.date_range(f"{target_year}-{target_month:02d}-01", periods=last_day)
    df_target = pd.DataFrame({"일자": dr, "일": dr.day, "weekday": dr.weekday})
    df_target["nth"] = df_target.groupby("weekday").cumcount() + 1
    
    # 4. 비율 적용
    def get_ratio(row):
        val = ratio_map.get((row["weekday"], row["nth"]))
        if pd.isna(val): val = dow_map.get(row["weekday"])
        return val

    df_target["일별비율"] = df_target.apply(get_ratio, axis=1)
    df_target["일별비율"] = df_target["일별비율"].fillna(1/last_day) # 안전장치
    df_target["일별비율"] /= df_target["일별비율"].sum() # 합계 1 맞춤
    
    # 5. 계획량 적용
    plan_val_mj = 0
    if df_plan is not None and not df_plan.empty:
        # 계획 컬럼 찾기
        plan_cols = [c for c in df_plan.columns if "계획" in str(c) or pd.api.types.is_numeric_dtype(df_plan[c])]
        plan_col = plan_cols[0] if plan_cols else None
        
        if plan_col:
            row = df_plan[(df_plan["연도"] == target_year) & (df_plan["월"] == target_month)]
            if not row.empty:
                val = row[plan_col].iloc[0]
                plan_val_mj = gj_to_mj(val) if val < 1000000 else val # 100만 이하면 GJ로 간주

    df_target["예상공급량(MJ)"] = df_target["일별비율"] * plan_val_mj
    df_target["예상공급량(GJ)"] = df_target["예상공급량(MJ)"].apply(mj_to_gj)
    
    weekdays = ["월", "화", "수", "목", "금", "토", "일"]
    df_target["요일"] = df_target["weekday"].map(lambda x: weekdays[x])
    
    return df_target, used_years, df_recent

# ─────────────────────────────────────────────
# 4. 엑셀 다운로드 (서식 포함)
# ─────────────────────────────────────────────
def to_excel_download(df):
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Sheet1')
        ws = writer.book['Sheet1']
        
        # 간단한 서식
        thin = Side(border_style="thin", color="000000")
        for row in ws.iter_rows(min_row=1, max_row=ws.max_row, min_col=1, max_col=ws.max_column):
            for cell in row:
                cell.alignment = Alignment(horizontal='center', vertical='center')
                cell.border = Border(top=thin, left=thin, right=thin, bottom=thin)
                if row[0].row == 1: # 헤더
                    cell.font = Font(bold=True)
                    cell.fill = PatternFill(start_color="D3D3D3", end_color="D3D3D3", fill_type="solid")
    return output.getvalue()

# ─────────────────────────────────────────────
# 5. 메인 앱 실행
# ─────────────────────────────────────────────
def main():
    st.title("🏙️ 도시가스 공급량 - 일별계획 예측")
    
    # 사이드바: 데이터 업로드
    st.sidebar.header("📁 데이터 업로드")
    up_daily = st.sidebar.file_uploader("일일 실적(공급량) 업로드", type=["xlsx"], key="daily")
    
    # 탭 구분
    tab1, tab2 = st.tabs(["📅 Daily 공급량 분석", "📊 Daily·Monthly 비교"])
    
    # 데이터 로드 (실패 시 None 반환)
    df_daily, df_temp = load_daily_data(up_daily)

    # --- 탭 1: 분석 ---
    with tab1:
        st.subheader("🗓️ 월별계획 파일 업로드")
        st.info("💡 분석을 위해 '월별계획.xlsx' 파일을 아래에 업로드해주세요. (파일이 없으면 분석이 진행되지 않습니다)")
        
        up_plan = st.file_uploader("월별 계획 엑셀 파일", type=["xlsx"], key="plan")
        df_plan = load_monthly_plan(up_plan)
        
        st.markdown("---")
        
        # ⚠️ 여기가 핵심 수정: 파일이 없으면 에러 대신 안내 메시지를 띄우고 중단
        if df_daily is None or df_daily.empty:
            st.warning("👈 먼저 왼쪽 사이드바에서 '일일 실적' 파일을 업로드해주세요.")
            st.stop()
            
        if df_plan is None or df_plan.empty:
            st.warning("👆 위에서 '월별 계획' 파일을 업로드해주세요.")
            st.stop() # 에러 없이 여기서 멈춤

        # --- 파일이 다 있을 때만 아래 실행 ---
        c1, c2, c3 = st.columns(3)
        with c1: 
            plan_years = sorted(df_plan["연도"].dropna().unique().astype(int))
            ty = st.selectbox("계획 연도", plan_years if plan_years else [2025, 2026], index=0)
        with c2: 
            tm = st.selectbox("계획 월", range(1, 13))
        with c3: 
            win = st.slider("과거 패턴 학습 기간(년)", 1, 5, 3)

        df_res, used_yrs, _ = make_daily_plan_table(df_daily, df_plan, ty, tm, win)
        
        if df_res is not None:
            st.success(f"✅ {used_yrs}년 데이터를 기반으로 {ty}년 {tm}월 일별 계획 생성 완료")
            
            # 그래프
            fig = go.Figure()
            fig.add_trace(go.Bar(x=df_res["일"], y=df_res["예상공급량(GJ)"], name="예상(GJ)", marker_color='rgb(55, 83, 109)'))
            fig.add_trace(go.Scatter(x=df_res["일"], y=df_res["일별비율"], name="비율", yaxis="y2", line=dict(color='rgb(219, 64, 82)', width=3)))
            fig.update_layout(
                title=f"{ty}년 {tm}월 일별 공급 계획",
                yaxis=dict(title="공급량(GJ)"),
                yaxis2=dict(title="비율", overlaying="y", side="right"),
                legend=dict(x=0, y=1.1, orientation="h")
            )
            st.plotly_chart(fig, use_container_width=True)
            
            # 데이터프레임
            st.dataframe(df_res[["일자", "요일", "일별비율", "예상공급량(GJ)"]].style.format({
                "일별비율": "{:.4%}", "예상공급량(GJ)": "{:,.0f}"
            }), use_container_width=True)
            
            # 다운로드
            excel_data = to_excel_download(df_res)
            st.download_button(f"📥 {ty}년 {tm}월 계획 다운로드", excel_data, f"Plan_{ty}_{tm}.xlsx")
        else:
            st.error("선택한 연도/월을 예측하기 위한 과거 데이터가 부족합니다.")

    # --- 탭 2: 비교 ---
    with tab2:
        st.subheader("📊 기온 및 상관도 분석")
        if df_daily is not None and not df_daily.empty and "평균기온(℃)" in df_daily.columns:
            # 상관도
            corr = df_daily[["공급량(MJ)", "평균기온(℃)", "연도", "월"]].corr()
            fig_corr = px.imshow(corr, text_auto=".2f", color_continuous_scale="RdBu_r", title="변수간 상관계수")
            st.plotly_chart(fig_corr)
            
            # 기온 히트맵
            st.subheader("🌡️ 일별 평균기온 히트맵")
            sel_m = st.selectbox("월 선택 (히트맵)", range(1, 13))
            df_hm = df_daily[df_daily["월"] == sel_m]
            if not df_hm.empty:
                piv = df_hm.pivot_table(index="일", columns="연도", values="평균기온(℃)")
                fig_hm = px.imshow(piv, labels=dict(color="기온(℃)"), color_continuous_scale="RdBu_r", title=f"{sel_m}월 연도별 기온 패턴")
                fig_hm.update_layout(height=600)
                st.plotly_chart(fig_hm)
        else:
            st.info("데이터가 없거나 기온 정보가 포함되지 않았습니다.")

if __name__ == "__main__":
    main()
