import calendar
import os
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
# 2. 스마트 데이터 로딩 (핵심 수정 부분)
# ─────────────────────────────────────────────
def find_repo_file(filename_candidates):
    """
    여러 경로와 파일명 후보를 검색하여 존재하는 파일 경로를 반환
    """
    # 검색할 경로: 현재 스크립트 위치, 현재 작업 디렉토리
    search_dirs = [Path(__file__).parent, Path.cwd()]
    
    for folder in search_dirs:
        for name in filename_candidates:
            target = folder / name
            if target.exists():
                return target
    return None

def standardize_columns(df):
    """컬럼명 표준화"""
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
    """일일 실적 로딩 (업로드 없으면 repo 파일 자동 탐색)"""
    df_raw = None
    
    # 1. 업로드 파일 확인
    if uploaded_file is not None:
        try:
            df_raw = pd.read_excel(uploaded_file)
        except: pass
    
    # 2. 없으면 로컬 파일 자동 탐색
    if df_raw is None:
        candidates = ["공급량(일일실적).xlsx", "일일실적.xlsx", "daily_data.xlsx", "공급량.xlsx"]
        file_path = find_repo_file(candidates)
        if file_path:
            try:
                df_raw = pd.read_excel(file_path)
            except: pass
            
    # 데이터가 여전히 없으면 None 반환
    if df_raw is None:
        return None, None

    # 데이터 전처리
    df_raw = standardize_columns(df_raw)
    
    if "일자" not in df_raw.columns:
        return None, None

    if "공급량(MJ)" not in df_raw.columns and "공급량(GJ)" in df_raw.columns:
        df_raw["공급량(MJ)"] = df_raw["공급량(GJ)"].apply(gj_to_mj)

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
    """월별 계획 로딩 (업로드 없으면 repo 파일 자동 탐색)"""
    df = None
    
    # 1. 업로드 파일 확인
    if uploaded_file is not None:
        try:
            df = pd.read_excel(uploaded_file)
        except: pass
        
    # 2. 없으면 로컬 파일 자동 탐색
    if df is None:
        candidates = ["공급량(계획_실적).xlsx", "월별계획.xlsx", "monthly_plan.xlsx", "계획.xlsx"]
        file_path = find_repo_file(candidates)
        if file_path:
            try:
                df = pd.read_excel(file_path)
            except: pass
            
    if df is None:
        return None

    df = standardize_columns(df)
    for col in ["연도", "월"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')
    
    return df

@st.cache_data(show_spinner=False)
def load_effective_calendar() -> pd.DataFrame | None:
    file_path = find_repo_file(["effective_days_calendar.xlsx", "calendar.xlsx"])
    if not file_path:
        return None

    df = pd.read_excel(file_path)
    if "날짜" in df.columns:
        df["일자"] = pd.to_datetime(df["날짜"].astype(str), format="%Y%m%d", errors="coerce")
    elif "일자" in df.columns:
        df["일자"] = pd.to_datetime(df["일자"], errors="coerce")
    else:
        return None

    for col in ["공휴일여부", "명절여부"]:
        if col not in df.columns: df[col] = False

    df["공휴일여부"] = df["공휴일여부"].fillna(False).astype(bool)
    df["명절여부"] = df["명절여부"].fillna(False).astype(bool)
    return df[["일자", "공휴일여부", "명절여부"]].copy()

# ─────────────────────────────────────────────
# 3. 유틸 함수 (수학 및 엑셀 포맷)
# ─────────────────────────────────────────────
def fit_poly3_and_r2(x: pd.Series, y: pd.Series):
    if len(x) < 4: return None, None, None
    try:
        coef = np.polyfit(x, y, 3)
        y_pred = np.polyval(coef, x)
        ss_res = np.sum((y - y_pred) ** 2)
        ss_tot = np.sum((y - np.mean(y)) ** 2)
        r2 = np.nan if ss_tot == 0 else 1 - ss_res / ss_tot
        return coef, y_pred, r2
    except: return None, None, None

def _add_cumulative_status_sheet(wb, annual_year: int):
    """엑셀 마지막 시트에 누적계획현황 추가 (수식 포함)"""
    if "누적계획현황" in wb.sheetnames: return
    ws = wb.create_sheet("누적계획현황")
    thin = Side(style="thin", color="999999")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    header_fill = PatternFill("solid", fgColor="F2F2F2")

    ws["A1"] = "기준일"; ws["B1"] = f"{annual_year}-01-01"
    ws["A1"].font = Font(bold=True); ws["B1"].font = Font(bold=True)
    
    headers = ["구분", "목표(GJ)", "누적(GJ)", "목표(m³)", "누적(m³)", "진행률(GJ)"]
    for j, h in enumerate(headers, 1):
        c = ws.cell(3, j, h)
        c.fill = header_fill; c.border = border; c.alignment = Alignment(horizontal="center")
    
    # 엑셀 수식 삽입
    d = "$B$1"
    ws["B4"] = f'=IFERROR(XLOOKUP({d},연간!$D:$D,연간!$O:$O),"")'
    ws["C4"] = "=B4"
    ws["F4"] = '=IFERROR(IF(B4=0,"",C4/B4),"")'
    ws["B5"] = f'=SUMIFS(연간!$O:$O,연간!$A:$A,YEAR({d}),연간!$B:$B,MONTH({d}))'
    ws["C5"] = f'=SUMIFS(연간!$O:$O,연간!$D:$D,">="&EOMONTH({d},-1)+1,연간!$D:$D,"<="&{d})'
    ws["B6"] = f'=SUMIFS(연간!$O:$O,연간!$A:$A,YEAR({d}))'
    ws["C6"] = f'=SUMIFS(연간!$O:$O,연간!$D:$D,">="&DATE(YEAR({d}),1,1),연간!$D:$D,"<="&{d})'
    
    # 테두리 적용
    for r in range(4, 7):
        for c in range(1, 7):
            ws.cell(r, c).border = border

def to_excel_download(df_res, sheet_name="DailyPlan"):
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df_res.to_excel(writer, index=False, sheet_name=sheet_name)
        ws = writer.book[sheet_name]
        thin = Side(style="thin", color="000000")
        for row in ws.iter_rows():
            for cell in row:
                cell.border = Border(top=thin, left=thin, right=thin, bottom=thin)
                cell.alignment = Alignment(horizontal='center', vertical='center')
    return output.getvalue()

# ─────────────────────────────────────────────
# 4. 핵심 분석 로직
# ─────────────────────────────────────────────
def make_daily_plan_table(df_daily, df_plan, target_year, target_month, recent_window=3):
    cal_df = load_effective_calendar()
    
    if df_daily is None or df_daily.empty: return None, [], None, None
    
    past_data = df_daily[(df_daily["연도"] < target_year) & (df_daily["월"] == target_month)].copy()
    if past_data.empty: return None, [], None, None
    
    used_years = sorted(past_data["연도"].unique())[-recent_window:]
    df_recent = past_data[past_data["연도"].isin(used_years)].copy()
    
    if df_recent.empty: return None, [], None, None

    # 요일/공휴일 패턴 분석
    if cal_df is not None:
        df_recent = df_recent.merge(cal_df, on="일자", how="left").fillna({"공휴일여부": False, "명절여부": False})
    else:
        df_recent["공휴일여부"] = False; df_recent["명절여부"] = False

    df_recent["is_weekend"] = (df_recent["일자"].dt.weekday >= 5) | df_recent["공휴일여부"] | df_recent["명절여부"]
    df_recent["weekday"] = df_recent["일자"].dt.weekday
    df_recent["nth"] = df_recent.groupby(["연도", "weekday"]).cumcount() + 1
    
    df_recent["yearly_sum"] = df_recent.groupby("연도")["공급량(MJ)"].transform("sum")
    df_recent["ratio"] = df_recent["공급량(MJ)"] / df_recent["yearly_sum"]
    
    # 패턴 맵 생성
    ratio_map = df_recent.groupby(["is_weekend", "weekday", "nth"])["ratio"].mean().to_dict()
    dow_map = df_recent.groupby(["is_weekend", "weekday"])["ratio"].mean().to_dict()
    
    # 타겟 생성
    last_day = calendar.monthrange(target_year, target_month)[1]
    dr = pd.date_range(f"{target_year}-{target_month:02d}-01", periods=last_day)
    df_target = pd.DataFrame({"일자": dr, "일": dr.day, "weekday": dr.weekday})
    
    if cal_df is not None:
        df_target = df_target.merge(cal_df, on="일자", how="left").fillna({"공휴일여부": False, "명절여부": False})
    else:
        df_target["공휴일여부"] = False; df_target["명절여부"] = False
        
    df_target["is_weekend"] = (df_target["weekday"] >= 5) | df_target["공휴일여부"] | df_target["명절여부"]
    df_target["nth"] = df_target.groupby("weekday").cumcount() + 1
    
    # 비율 적용
    df_target["일별비율"] = df_target.apply(lambda r: ratio_map.get((r["is_weekend"], r["weekday"], r["nth"]), 
                                            dow_map.get((r["is_weekend"], r["weekday"]), np.nan)), axis=1)
    df_target["일별비율"] = df_target["일별비율"].fillna(1/last_day)
    df_target["일별비율"] /= df_target["일별비율"].sum()
    
    # 계획량 적용
    plan_val_mj = 0
    if df_plan is not None and not df_plan.empty:
        plan_cols = [c for c in df_plan.columns if "계획" in str(c) or pd.api.types.is_numeric_dtype(df_plan[c])]
        plan_col = next((c for c in plan_cols if c not in ["연도", "월", "일"]), None)
        if plan_col:
            row = df_plan[(df_plan["연도"] == target_year) & (df_plan["월"] == target_month)]
            if not row.empty:
                val = row[plan_col].iloc[0]
                plan_val_mj = gj_to_mj(val) if val < 1000000 else val

    df_target["예상공급량(MJ)"] = df_target["일별비율"] * plan_val_mj
    df_target["예상공급량(GJ)"] = df_target["예상공급량(MJ)"].apply(mj_to_gj)
    weekdays = ["월", "화", "수", "목", "금", "토", "일"]
    df_target["요일"] = df_target["weekday"].map(lambda x: weekdays[x])
    
    df_mat = df_recent.pivot_table(index="일", columns="연도", values="공급량(MJ)", aggfunc="sum")
    return df_target, used_years, df_recent, df_mat

# ─────────────────────────────────────────────
# 5. 메인 앱 실행
# ─────────────────────────────────────────────
def main():
    st.sidebar.title("데이터 로드 설정")
    # 업로더는 유지하되, 선택사항임을 명시
    up_daily = st.sidebar.file_uploader("일일 실적(선택사항)", type=["xlsx"], key="daily")
    
    # 1. 데이터 로드 (파일 없으면 자동 탐색)
    df_daily, df_temp_all = load_daily_data(up_daily)
    
    # 2. 로드 실패 시 디버깅 정보 제공 (에러 대신 안내)
    if df_daily is None:
        st.error("⚠️ '공급량(일일실적).xlsx' 파일을 찾을 수 없습니다.")
        st.write("📂 **현재 시스템이 인식하는 파일 목록:**")
        try:
            st.code(os.listdir(Path(__file__).parent)) # 현재 폴더 파일 목록 표시
        except:
            st.code(os.listdir('.'))
        st.warning("위 목록에 엑셀 파일이 없다면, 깃허브 레포지토리에 파일이 제대로 올라갔는지 확인해주세요.")
        return

    # 탭 구성
    tab1, tab2 = st.tabs(["📅 Daily 공급량 분석", "📊 Daily·Monthly 비교"])
    
    # --- 탭 1 ---
    with tab1:
        st.title("🏙️ 도시가스 공급량 - 일별계획 예측")
        up_plan = st.file_uploader("월별 계획(선택사항)", type=["xlsx"], key="plan")
        df_plan = load_monthly_plan(up_plan)
        
        if df_plan is None:
            st.error("⚠️ '월별계획.xlsx' 파일을 찾을 수 없습니다.")
            st.info("파일명을 확인하거나 파일을 업로드해주세요.")
            st.stop()
            
        # 설정 UI
        c1, c2, c3 = st.columns(3)
        with c1: 
            p_years = sorted(df_plan["연도"].dropna().unique().astype(int))
            ty = st.selectbox("계획 연도", p_years if p_years else [2025, 2026])
        with c2: tm = st.selectbox("계획 월", range(1, 13))
        with c3: win = st.slider("학습 기간(년)", 1, 5, 3)
        
        # 분석 실행
        df_res, used_yrs, _, _ = make_daily_plan_table(df_daily, df_plan, ty, tm, win)
        
        if df_res is not None:
            st.success(f"✅ {used_yrs}년 데이터를 기반으로 분석 완료")
            
            # 그래프
            fig = go.Figure()
            fig.add_trace(go.Bar(x=df_res["일"], y=df_res["예상공급량(GJ)"], name="예상(GJ)", marker_color='#1f77b4'))
            fig.add_trace(go.Scatter(x=df_res["일"], y=df_res["일별비율"], name="비율", yaxis="y2", line=dict(color='#d62728', width=2)))
            fig.update_layout(yaxis2=dict(overlaying="y", side="right"), title=f"{ty}년 {tm}월 예측", legend=dict(orientation="h", y=1.1))
            st.plotly_chart(fig, use_container_width=True)
            
            # 결과 표
            st.dataframe(df_res[["일자", "요일", "일별비율", "예상공급량(GJ)"]].style.format({"일별비율": "{:.2%}", "예상공급량(GJ)": "{:,.0f}"}))
            
            # 다운로드
            st.download_button(f"📥 결과 엑셀 다운로드", to_excel_download(df_res), f"Plan_{ty}_{tm}.xlsx")
        else:
            st.warning("분석할 과거 데이터가 부족합니다.")

    # --- 탭 2 ---
    with tab2:
        st.title("📊 기온 상관도 분석")
        if df_daily is not None and "평균기온(℃)" in df_daily.columns:
            corr = df_daily[["공급량(MJ)", "평균기온(℃)"]].corr()
            c1, c2 = st.columns([1, 2])
            with c1: st.write("#### 상관계수", corr)
            with c2:
                fig = px.scatter(df_daily, x="평균기온(℃)", y="공급량(MJ)", trendline="lowess", title="기온 vs 공급량")
                st.plotly_chart(fig, use_container_width=True)
            
            # 히트맵
            st.subheader("🌡️ 월별 기온 히트맵")
            sel_m = st.selectbox("월 선택", range(1, 13), key="hm_m")
            df_hm = df_daily[df_daily["월"] == sel_m]
            if not df_hm.empty:
                piv = df_hm.pivot_table(index="일", columns="연도", values="평균기온(℃)")
                fig_hm = px.imshow(piv, color_continuous_scale="RdBu_r", title=f"{sel_m}월 기온 패턴")
                st.plotly_chart(fig_hm)
        else:
            st.info("기온 데이터가 없습니다.")

if __name__ == "__main__":
    main()
