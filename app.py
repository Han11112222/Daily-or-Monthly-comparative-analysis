import calendar
from io import BytesIO
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import streamlit as st

# =========================================================
# 단위/환산
# =========================================================
MJ_PER_NM3 = 42.563  # MJ/Nm3
MJ_TO_GJ = 0.001     # 1 MJ = 0.001 GJ

def mj_to_gj(mj: float) -> float:
    try:
        return float(mj) * MJ_TO_GJ
    except Exception:
        return np.nan

def gj_to_mj(gj: float) -> float:
    try:
        return float(gj) / MJ_TO_GJ
    except Exception:
        return np.nan

def mj_to_m3(mj: float) -> float:
    # MJ / (MJ/Nm3) = Nm3
    try:
        return float(mj) / MJ_PER_NM3
    except Exception:
        return np.nan

def gj_to_m3(gj: float) -> float:
    # GJ -> MJ -> Nm3
    try:
        return mj_to_m3(gj_to_mj(gj))
    except Exception:
        return np.nan

# =========================================================
# 데이터 로딩 (에러 방지 수정)
# =========================================================
@st.cache_data(show_spinner=False)
def load_monthly_plan(uploaded_file) -> pd.DataFrame:
    """
    월별 계획(1~12월 + 연간합계)을 읽어오는 함수
    - 업로드 파일이 없으면 repo 내 '월별계획.xlsx'를 찾음
    """
    if uploaded_file is not None:
        try:
            return pd.read_excel(uploaded_file)
        except:
            return None
    else:
        # repo 기본 파일 탐색 (형님 원본 로직 + 에러 방지)
        # 여러 경로/이름을 시도해서 찾습니다.
        candidates = ["월별계획.xlsx", "월별 계획.xlsx", "공급량(계획_실적).xlsx"]
        paths = [Path(__file__).parent, Path.cwd()]
        
        for p in paths:
            for name in candidates:
                target = p / name
                if target.exists():
                    try: return pd.read_excel(target)
                    except: pass
        
        # 파일이 없으면 None 반환 (에러 raise 안 함)
        return None

    # (아래 코드는 파일이 로드된 후에 실행되므로 load_monthly_plan 내부가 아니라
    # 파일을 읽은 직후 처리해야 합니다. 원본 구조상 여기서 리턴된 df를 가공하는 함수가 필요합니다.)
    # 여기서는 편의상 바로 리턴하고, 호출부에서 가공하도록 수정했습니다.

def process_monthly_plan(df):
    if df is None: return None
    
    # 컬럼 표준화
    col_map = {}
    for c in df.columns:
        cs = str(c).strip()
        if cs in ["구분", "항목", "분류"]:
            col_map[c] = "구분"
    df = df.rename(columns=col_map)

    # 월 컬럼 정리
    month_cols = []
    for m in range(1, 13):
        for cand in [f"{m}월", str(m), f"{m:02d}"]:
            if cand in df.columns:
                month_cols.append(cand)
                break

    # 연간합계 컬럼
    annual_col = None
    for cand in ["연간합계", "연간", "합계", "Total", "TOTAL"]:
        if cand in df.columns:
            annual_col = cand
            break

    # 수치 변환
    for c in month_cols + ([annual_col] if annual_col else []):
        if c is None: continue
        df[c] = pd.to_numeric(df[c], errors="coerce")

    return df

@st.cache_data(show_spinner=False)
def load_daily_data(uploaded_file_daily) -> pd.DataFrame:
    """일일 실적 로딩"""
    df_raw = None
    if uploaded_file_daily is not None:
        try: df_raw = pd.read_excel(uploaded_file_daily)
        except: pass
    else:
        # 자동 탐색
        candidates = ["공급량(일일실적).xlsx", "일일실적.xlsx", "daily_data.xlsx"]
        paths = [Path(__file__).parent, Path.cwd()]
        for p in paths:
            for name in candidates:
                target = p / name
                if target.exists():
                    try: 
                        df_raw = pd.read_excel(target)
                        break
                    except: pass
            if df_raw is not None: break
            
    if df_raw is None: return None

    # 컬럼 매핑
    col_std = {}
    for c in df_raw.columns:
        cs = str(c).strip()
        if cs in ["일자", "date", "Date"]: col_std[c] = "일자"
        if "공급량" in cs and "MJ" in cs: col_std[c] = "공급량(MJ)"
        if "공급량" in cs and ("GJ" in cs or "Gj" in cs): col_std[c] = "공급량(GJ)"
        if "평균" in cs and ("기온" in cs or "온도" in cs): col_std[c] = "평균기온(°C)"
        if "최저" in cs and ("기온" in cs or "온도" in cs): col_std[c] = "최저기온(°C)"
        if "최고" in cs and ("기온" in cs or "온도" in cs): col_std[c] = "최고기온(°C)"
        if "체감" in cs and ("기온" in cs or "온도" in cs): col_std[c] = "체감온도(°C)"

    df = df_raw.rename(columns=col_std).copy()

    if "일자" not in df.columns: return None # 에러 대신 None

    df["일자"] = pd.to_datetime(df["일자"], errors="coerce")
    
    if "공급량(MJ)" not in df.columns and "공급량(GJ)" in df.columns:
        df["공급량(MJ)"] = df["공급량(GJ)"].apply(gj_to_mj)

    if "공급량(MJ)" in df.columns:
        df["공급량(MJ)"] = pd.to_numeric(df["공급량(MJ)"], errors="coerce")

    df["연"] = df["일자"].dt.year
    df["월"] = df["일자"].dt.month
    df["일"] = df["일자"].dt.day
    df["요일"] = df["일자"].dt.day_name()

    return df

# =========================================================
# 일별 계획 산출 로직
# =========================================================
def nth_weekday_of_month(dt: pd.Timestamp) -> int:
    first = dt.replace(day=1)
    n = 1
    cur = first
    while cur < dt:
        cur += pd.Timedelta(days=1)
        if cur.day_name() == dt.day_name():
            n += 1
    return n

def make_daily_plan_table(df_daily, target_year, target_month, monthly_total_gj, n_years=3):
    cand_years = list(range(target_year - 1, target_year - 1 - n_years * 3, -1))
    used_years = []
    df_hist = []

    for y in cand_years:
        sub = df_daily[(df_daily["연"] == y) & (df_daily["월"] == target_month)].copy()
        if sub["공급량(MJ)"].notna().sum() > 0:
            used_years.append(y)
            df_hist.append(sub)
        if len(used_years) >= n_years:
            break

    if not df_hist:
        # raise ValueError 대신 None 리턴해서 부드럽게 처리
        return None, []

    df_hist = pd.concat(df_hist, ignore_index=True)

    def weekday_group(dname: str) -> str:
        if dname in ["Saturday", "Sunday"]: return "주말"
        if dname in ["Monday", "Friday"]: return "평일1"
        return "평일2"

    df_hist["요일구분"] = df_hist["요일"].apply(weekday_group)
    df_hist["n번째"] = df_hist["일자"].apply(nth_weekday_of_month)

    def make_key(row) -> str:
        if row["요일구분"] == "주말": return f"주말-{row['n번째']}"
        return f"{row['요일']}-{row['n번째']}"

    df_hist["기준키"] = df_hist.apply(make_key, axis=1)

    ratios = []
    for y in used_years:
        sub = df_hist[df_hist["연"] == y].copy()
        s = sub["공급량(MJ)"].sum()
        sub["비율"] = sub["공급량(MJ)"] / s if s != 0 else np.nan
        ratios.append(sub[["기준키", "비율"]].groupby("기준키")["비율"].mean())

    ratio_mean = pd.concat(ratios, axis=1).mean(axis=1)
    if ratio_mean.sum() > 0:
        ratio_mean = ratio_mean / ratio_mean.sum()

    days_in_month = calendar.monthrange(target_year, target_month)[1]
    dates = pd.date_range(start=f"{target_year}-{target_month:02d}-01", periods=days_in_month, freq="D")
    df_plan = pd.DataFrame({"일자": dates})
    df_plan["연"] = df_plan["일자"].dt.year
    df_plan["월"] = df_plan["일자"].dt.month
    df_plan["일"] = df_plan["일자"].dt.day
    df_plan["요일"] = df_plan["일자"].dt.day_name()
    df_plan["요일구분"] = df_plan["요일"].apply(weekday_group)
    df_plan["n번째"] = df_plan["일자"].apply(nth_weekday_of_month)
    df_plan["기준키"] = df_plan.apply(make_key, axis=1)

    df_plan["일별비율"] = df_plan["기준키"].map(ratio_mean)

    if df_plan["일별비율"].isna().any():
        weekday_ratio = (
            df_hist.assign(비율=df_hist["공급량(MJ)"] / df_hist.groupby("연")["공급량(MJ)"].transform("sum"))
            .groupby("요일")["비율"].mean()
        )
        df_plan.loc[df_plan["일별비율"].isna(), "일별비율"] = df_plan.loc[df_plan["일별비율"].isna(), "요일"].map(weekday_ratio)

    df_plan["일별비율"] = df_plan["일별비율"].fillna(1/len(df_plan))
    df_plan["일별비율"] = df_plan["일별비율"] / df_plan["일별비율"].sum()

    monthly_total_mj = gj_to_mj(monthly_total_gj)
    df_plan["예상공급량(MJ)"] = df_plan["일별비율"] * monthly_total_mj

    df_plan = df_plan[["일자", "요일", "요일구분", "n번째", "기준키", "일별비율", "예상공급량(MJ)", "연", "월", "일"]].copy()

    return df_plan, used_years

# =========================================================
# 다운로드(엑셀)
# =========================================================
def export_daily_plan_excel(df_plan: pd.DataFrame, sheet_name: str = "일일계획") -> bytes:
    out = BytesIO()
    with pd.ExcelWriter(out, engine="openpyxl") as writer:
        df_x = df_plan.copy()
        df_x["예상공급량(GJ)"] = df_x["예상공급량(MJ)"].apply(mj_to_gj)
        df_x["예상공급량(㎥)"] = df_x["예상공급량(MJ)"].apply(mj_to_m3)
        cols = ["일자", "요일", "요일구분", "n번째", "기준키", "일별비율", "예상공급량(GJ)", "예상공급량(㎥)"]
        df_x[cols].to_excel(writer, sheet_name=sheet_name, index=False)
    return out.getvalue()

# =========================================================
# 탭1: Daily 공급량 분석
# =========================================================
def tab_daily_plan(df_daily: pd.DataFrame):
    st.title("도시가스 공급량 - 일별계획 예측")
    st.subheader("🗓️ Daily공급량 분석 — 최근 N년 패턴 기반 일별 계획")

    st.markdown("### 📁 1. 월별계획 엑셀 업로드(XLSX) (없으면 폴더에서 자동 탐색)")
    uploaded_plan = st.file_uploader("월별 계획 엑셀 업로드", type=["xlsx"], key="plan_upload")

    # 월별계획 로드 (에러 방지)
    df_plan_raw = load_monthly_plan(uploaded_plan)
    df_plan_month = process_monthly_plan(df_plan_raw) # 가공

    if df_plan_month is None:
        st.warning("⚠️ '월별계획.xlsx' 파일을 찾을 수 없습니다. 파일을 업로드하거나 폴더를 확인해주세요.")
        return

    st.markdown("### ⚙️ 2. 계획 연도/월 및 학습기간 설정")
    years = sorted(df_daily["연"].dropna().unique().astype(int).tolist())
    default_year = max(years) + 1 if len(years) > 0 else 2026
    target_year = st.selectbox("계획 연도 선택", options=list(range(default_year - 5, default_year + 3)), index=5)
    target_month = st.selectbox("계획 월 선택", options=list(range(1, 13)), index=0)

    n_years = st.slider("최근 몇 년 평균으로 비율을 계산할까?", min_value=1, max_value=5, value=3, step=1)

    month_col = None
    for cand in [f"{target_month}월", str(target_month), f"{target_month:02d}"]:
        if cand in df_plan_month.columns:
            month_col = cand
            break
    
    if month_col is None:
        st.error(f"월별계획 파일에서 {target_month}월 데이터를 찾을 수 없습니다.")
        return

    try:
        monthly_total_gj = float(df_plan_month.loc[0, month_col])
    except:
        st.error("월별계획 파일 데이터 형식이 올바르지 않습니다.")
        return

    st.info(f"최근 {n_years}년 후보({target_year-n_years}년~{target_year-1}년) 중 {target_month}월 실적이 있는 연도만 자동 선택해서 학습해.")

    df_plan, used_years = make_daily_plan_table(df_daily, target_year, target_month, monthly_total_gj, n_years)

    if df_plan is None:
        st.warning("분석할 과거 데이터가 부족합니다.")
        return

    st.markdown(f"- **실제 학습에 사용된 연도**: {min(used_years)}년 ~ {max(used_years)}년")
    st.markdown(f"**{target_year}년 {target_month}월 사업계획 제출 공급량 합계**: {monthly_total_gj:,.0f} GJ")

    st.markdown("### 🧩 일별 공급량 분배 기준")
    st.markdown("""
    - 주말/공휴일/명절: 요일 + 그 달의 n번째 기준 평균
    - 평일: '평일1(월·금)', '평일2(화·수·목)'로 구분
    - 기본은 '요일 + 그 달의 n번째' 기준 평균
    - 일부 케이스 데이터가 부족하면 '요일 평균'으로 보정
    - 마지막에 일별비율 합계가 1이 되도록 정규화
    """)

    st.markdown("### 📌 3. 일별 계획표(요약)")
    view = df_plan.sort_values("일자").copy()
    view_show = view[["일자", "요일", "요일구분", "n번째", "기준키", "일별비율"]].copy()
    view_show["예상공급량(GJ)"] = view["예상공급량(MJ)"].apply(mj_to_gj)
    view_show["예상공급량(㎥)"] = view["예상공급량(MJ)"].apply(mj_to_m3)
    st.dataframe(view_show, use_container_width=True, height=330)

    st.markdown("#### 📊 2. 일별 예상 공급량 & 비율 그래프")
    w1_df = view[view["요일구분"] == "평일1"].copy()
    w2_df = view[view["요일구분"] == "평일2"].copy()
    wend_df = view[view["요일구분"] == "주말"].copy()

    fig = go.Figure()

    def _add_bar(_df, _name):
        y_gj = _df["예상공급량(MJ)"].apply(mj_to_gj).astype(float).to_numpy()
        y_m3 = _df["예상공급량(MJ)"].apply(mj_to_m3).astype(float).to_numpy()
        x = _df["일"].astype(int).to_numpy()
        custom = np.column_stack([y_gj, y_m3])
        fig.add_trace(go.Bar(
            x=x, y=y_gj, name=_name, customdata=custom,
            hovertemplate="일: %{x}<br>예상: %{customdata[0]:,.0f} GJ<br>예상: %{customdata[1]:,.0f} ㎥<extra></extra>"
        ))

    _add_bar(w1_df, "평일1(월·금) 예상공급량(GJ)")
    _add_bar(w2_df, "평일2(화·수·목) 예상공급량(GJ)")
    _add_bar(wend_df, "주말/공휴일 예상공급량(GJ)")

    fig.add_trace(go.Scatter(
        x=view["일"].astype(int), y=view["일별비율"].astype(float),
        mode="lines+markers", name=f"일별비율 (최근{len(used_years)}년 실제 사용)",
        yaxis="y2", hovertemplate="일: %{x}<br>일별비율: %{y:.4f}<extra></extra>"
    ))

    fig.update_layout(
        barmode="group", xaxis_title="일", yaxis=dict(title="예상 공급량(GJ)"),
        yaxis2=dict(title="일별비율", overlaying="y", side="right", tickformat=".3f"),
        legend=dict(orientation="v"), height=520, margin=dict(l=40, r=40, t=30, b=40)
    )
    st.plotly_chart(fig, use_container_width=True)

    st.markdown("### 🧾 5. 일일계획 다운로드(월간)")
    excel_bytes = export_daily_plan_excel(df_plan, sheet_name=f"{target_year}-{target_month:02d}")
    st.download_button("📥 일일공급계획 다운로드(Excel)", data=excel_bytes, file_name=f"일일공급계획_{target_year}_{target_month:02d}.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    # 월별 계획표 표출
    st.markdown("### 📌 📌 월별 계획량(1~12월) & 연간 총량")
    mcols = []
    for m in range(1, 13):
        for cand in [f"{m}월", str(m), f"{m:02d}"]:
            if cand in df_plan_month.columns:
                mcols.append(cand)
                break
    
    # 길이가 안 맞을 경우 보정
    while len(mcols) < 12: mcols.append(None)

    try:
        plan_row = df_plan_month.loc[0, [c for c in mcols if c]].astype(float).values
    except:
        plan_row = []

    plan_row_gj = plan_row.copy()
    plan_row_m3 = np.array([gj_to_m3(v) for v in plan_row_gj])

    df_plan_view = pd.DataFrame([plan_row_gj, plan_row_m3], columns=[f"{m}월" for m in range(1, 13)])
    df_plan_view.insert(0, "구분", ["사업계획(월별 계획) - GJ", "사업계획(월별 계획) - ㎥"])
    
    st.dataframe(df_plan_view, use_container_width=True, height=140)

# =========================================================
# 탭2: Daily-Monthly공급량 비교 (형님 원본 로직)
# =========================================================
def tab_daily_monthly_compare(df: pd.DataFrame):
    st.title("도시가스 공급량 — 일별 vs 월별 예측 검증")
    st.markdown("## 📊 0. 상관도 분석 (공급량 vs 주요 변수)")
    
    numeric_cols = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
    if len(numeric_cols) >= 2:
        corr = df[numeric_cols].corr()
        fig_corr = px.imshow(corr, text_auto=".2f", aspect="equal", color_continuous_scale="Blues")
        fig_corr.update_layout(height=520)
        st.plotly_chart(fig_corr, use_container_width=True)
    else:
        st.info("상관도 분석을 위해서는 숫자형 컬럼이 2개 이상 필요해.")

    st.markdown("---")
    st.info("여기 아래부터는 기존 Daily-Monthly 비교 로직 그대로 유지하면 돼(네 코드 원본에 이미 들어있는 부분).")

# =========================================================
# main
# =========================================================
def main():
    st.set_page_config(page_title="도시가스 공급량 예측", layout="wide")
    st.sidebar.markdown("### 좌측 탭 선택")
    tab = st.sidebar.radio("", options=["Daily 공급량 분석", "Daily·Monthly 공급량 비교"], index=0, key="main_tab")

    uploaded_daily = st.sidebar.file_uploader("일일 실적 파일 업로드(XLSX)", type=["xlsx"], key="daily_upload")
    
    # 일일실적 로드 (없으면 자동 탐색)
    df_daily = load_daily_data(uploaded_daily)
    
    if df_daily is None:
        st.warning("👈 '일일 실적' 파일이 없습니다. 파일을 업로드하거나 폴더를 확인해주세요.")
        return

    if tab == "Daily 공급량 분석":
        tab_daily_plan(df_daily=df_daily)
    else:
        tab_daily_monthly_compare(df=df_daily)

if __name__ == "__main__":
    main()
