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
# 1. 단위 및 상수 설정
# ─────────────────────────────────────────────
MJ_PER_NM3 = 42.563
MJ_TO_GJ = 0.001

def mj_to_gj(x):
    try: return x * MJ_TO_GJ
    except: return np.nan

def mj_to_m3(x):
    try: return x / MJ_PER_NM3
    except: return np.nan

def gj_to_mj(x):
    try: return x / MJ_TO_GJ
    except: return np.nan

# ─────────────────────────────────────────────
# 2. 데이터 로딩 및 유연한 컬럼 매핑
# ─────────────────────────────────────────────
def standardize_columns(df):
    col_map = {}
    for c in df.columns:
        cs = str(c).replace(" ", "").strip()
        if cs in ["일자", "date", "Date", "날짜"]: col_map[c] = "일자"
        elif "공급량" in cs and "MJ" in cs: col_map[c] = "공급량(MJ)"
        elif "공급량" in cs and ("GJ" in cs or "Gj" in cs): col_map[c] = "공급량(GJ)"
        elif "평균" in cs and "기온" in cs: col_map[c] = "평균기온(℃)"
        elif cs in ["연", "연도", "Year"]: col_map[c] = "연도"
        elif cs in ["월", "Month"]: col_map[c] = "월"
    return df.rename(columns=col_map)

@st.cache_data
def load_all_data(up_daily, up_plan):
    # 일일 실적 로드
    if up_daily: df_daily_raw = pd.read_excel(up_daily)
    else:
        path = Path(__file__).parent / "공급량(일일실적).xlsx"
        df_daily_raw = pd.read_excel(path) if path.exists() else pd.DataFrame()
    
    df_daily = standardize_columns(df_daily_raw)
    if not df_daily.empty and "일자" in df_daily.columns:
        df_daily["일자"] = pd.to_datetime(df_daily["일자"], errors='coerce')
        df_daily = df_daily.dropna(subset=["일자"])
        if "공급량(MJ)" not in df_daily.columns and "공급량(GJ)" in df_daily.columns:
            df_daily["공급량(MJ)"] = df_daily["공급량(GJ)"].apply(gj_to_mj)
        df_daily["연도"] = df_daily["일자"].dt.year
        df_daily["월"] = df_daily["일자"].dt.month
        df_daily["일"] = df_daily["일자"].dt.day
    
    # 월별 계획 로드
    if up_plan: df_plan_raw = pd.read_excel(up_plan)
    else:
        path = Path(__file__).parent / "공급량(계획_실적).xlsx"
        df_plan_raw = pd.read_excel(path) if path.exists() else pd.DataFrame()
    
    df_plan = standardize_columns(df_plan_raw)
    return df_daily, df_plan

# ─────────────────────────────────────────────
# 3. 분석용 수학 함수 (R2, Polyfit)
# ─────────────────────────────────────────────
def fit_poly3_and_r2(x, y):
    x, y = np.asarray(x, dtype="float64"), np.asarray(y, dtype="float64")
    if len(x) < 4: return None, None, None
    coef = np.polyfit(x, y, 3)
    y_pred = np.polyval(coef, x)
    ss_res = np.sum((y - y_pred) ** 2)
    ss_tot = np.sum((y - np.mean(y)) ** 2)
    r2 = 1 - (ss_res / ss_tot) if ss_tot != 0 else np.nan
    return coef, y_pred, r2

# ─────────────────────────────────────────────
# 4. 엑셀 서식화 및 누적 현황 시트 (기존 고급기능)
# ─────────────────────────────────────────────
def apply_excel_style(ws, freeze_pane="A2"):
    ws.freeze_panes = freeze_pane
    thin = Side(style="thin", color="999999")
    for row in ws.iter_rows():
        for cell in row:
            cell.alignment = Alignment(horizontal="center", vertical="center")
            cell.border = Border(top=thin, left=thin, right=thin, bottom=thin)

def add_cumulative_sheet(wb, target_year):
    ws = wb.create_sheet("누적계획현황")
    ws["A1"] = "기준일"; ws["B1"] = f"{target_year}-01-01"
    headers = ["구분", "목표(GJ)", "누적(GJ)", "목표(m³)", "누적(m³)", "진행률"]
    for i, h in enumerate(headers, 1):
        cell = ws.cell(3, i, h)
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor="F2F2F2")
    apply_excel_style(ws, "A4")

# ─────────────────────────────────────────────
# 5. 메인 앱 레이아웃
# ─────────────────────────────────────────────
def main():
    st.set_page_config(page_title="도시가스 공급량 통합 분석 시스템", layout="wide")
    
    st.sidebar.title("📁 데이터 관리")
    up_daily = st.sidebar.file_uploader("일일 실적 업로드", type=["xlsx"])
    up_plan = st.sidebar.file_uploader("월별 계획 업로드", type=["xlsx"])
    
    df_daily, df_plan = load_all_data(up_daily, up_plan)
    
    if df_daily.empty:
        st.error("⚠️ 실적 데이터가 없습니다. 파일을 업로드해 주세요.")
        return

    tab1, tab2 = st.tabs(["📅 일별 계획 예측", "📊 기온 및 상관도 검증"])

    # --- 탭 1: 일별 계획 예측 로직 ---
    with tab1:
        st.title("일별 공급량 패턴 분석")
        c1, c2, c3 = st.columns(3)
        with c1: t_year = st.selectbox("계획 연도", [2025, 2026], index=1)
        with c2: t_month = st.selectbox("계획 월", list(range(1, 13)))
        with c3: window = st.slider("학습 기간(년)", 1, 5, 3)
        
        # 패턴 분석 로직
        hist = df_daily[(df_daily["연도"] < t_year) & (df_daily["월"] == t_month)]
        if not hist.empty:
            used_yrs = sorted(hist["연도"].unique())[-window:]
            df_hist = hist[hist["연도"].isin(used_yrs)].copy()
            df_hist["weekday"] = df_hist["일자"].dt.weekday
            df_hist["nth"] = df_hist.groupby(["연도", "weekday"]).cumcount() + 1
            
            # 요일별 비중 계산
            df_hist["ratio"] = df_hist["공급량(MJ)"] / df_hist.groupby("연도")["공급량(MJ)"].transform("sum")
            pattern = df_hist.groupby(["weekday", "nth"])["ratio"].mean().to_dict()
            
            # 대상월 생성
            days = calendar.monthrange(t_year, t_month)[1]
            dr = pd.date_range(f"{t_year}-{t_month:02d}-01", periods=days)
            df_res = pd.DataFrame({"일자": dr, "일": dr.day, "weekday": dr.weekday})
            df_res["nth"] = df_res.groupby("weekday").cumcount() + 1
            df_res["비율"] = df_res.apply(lambda r: pattern.get((r["weekday"], r["nth"]), np.nan), axis=1)
            df_res["비율"] = df_res["비율"].fillna(df_res["비율"].mean()).fillna(1/days)
            df_res["비율"] /= df_res["비율"].sum()
            
            # 계획량 반영 (계획 엑셀에서 추출)
            plan_val = 0
            if not df_plan.empty:
                plan_col = next((c for c in df_plan.columns if "계획" in str(c)), df_plan.columns[-1])
                row = df_plan[(df_plan["연도"] == t_year) & (df_plan["월"] == t_month)]
                if not row.empty: plan_val = row[plan_col].iloc[0]
            
            df_res["예상(GJ)"] = (df_res["비율"] * gj_to_mj(plan_val)).apply(mj_to_gj)
            
            # 차트 시각화
            fig = px.bar(df_res, x="일", y="예상(GJ)", title=f"{t_year}년 {t_month}월 일별 분배 결과")
            st.plotly_chart(fig, use_container_width=True)
            st.dataframe(df_res[["일자", "비율", "예상(GJ)"]].style.format({"비율": "{:.4f}", "예상(GJ)": "{:,.0f}"}))
            
            # 엑셀 다운로드 (고급 서식 적용)
            buf = BytesIO()
            with pd.ExcelWriter(buf, engine="openpyxl") as writer:
                df_res.to_excel(writer, index=False, sheet_name="일별계획")
                apply_excel_style(writer.book["일별계획"])
                add_cumulative_sheet(writer.book, t_year)
            st.download_button("📥 정밀 서식 엑셀 다운로드", buf.getvalue(), f"Plan_{t_year}_{t_month}.xlsx")

    # --- 탭 2: 상관도 분석 및 히트맵 ---
    with tab2:
        st.title("기온–공급량 분석 및 검증")
        if "평균기온(℃)" in df_daily.columns:
            # 상관도 매트릭스
            corr = df_daily[["공급량(MJ)", "평균기온(℃)", "연도", "월"]].corr()
            st.write("### 📊 주요 변수 상관계수", corr)
            
            # R2 검증 (월단위 vs 일단위)
            df_m = df_daily.groupby(["연도", "월"]).agg({"공급량(MJ)": "sum", "평균기온(℃)": "mean"}).reset_index()
            _, _, r2_m = fit_poly3_and_r2(df_m["평균기온(℃)"], df_m["공급량(MJ)"].apply(mj_to_gj))
            _, _, r2_d = fit_poly3_and_r2(df_daily["평균기온(℃)"], df_daily["공급량(MJ)"].apply(mj_to_gj))
            
            m1, m2 = st.columns(2)
            m1.metric("월 단위 모델 R²", f"{r2_m:.3f}")
            m2.metric("일 단위 모델 R²", f"{r2_d:.3f}")
            
            # 기온 히트맵 (기존 코드의 Heatmap 복원)
            st.write("### 🧊 일일 평균기온 히트맵")
            temp_pivot = df_daily.pivot_table(index="일", columns="연도", values="평균기온(℃)")
            fig_hm = px.imshow(temp_pivot, labels=dict(color="기온(℃)"), color_continuous_scale="RdBu_r")
            st.plotly_chart(fig_hm, use_container_width=True)
        else:
            st.info("기온 데이터가 실적 파일에 포함되어 있지 않습니다.")

if __name__ == "__main__":
    main()
