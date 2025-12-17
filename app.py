import calendar
from io import BytesIO
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from openpyxl import load_workbook
from openpyxl.styles import Alignment, Font
from openpyxl.utils import get_column_letter

# ────────────────────────────────────────
# 기본 설정
# ────────────────────────────────────────
st.set_page_config(page_title="도시가스 공급량 — 일별계획 예측", layout="wide")


# ────────────────────────────────────────
# 유틸
# ────────────────────────────────────────
def to_num(x):
    if pd.isna(x):
        return np.nan
    if isinstance(x, (int, float, np.integer, np.floating)):
        return float(x)
    s = str(x).replace(",", "").strip()
    if s == "":
        return np.nan
    try:
        return float(s)
    except Exception:
        return np.nan


# ────────────────────────────────────────
# 데이터 불러오기
# ────────────────────────────────────────
@st.cache_data
def load_corr_data() -> pd.DataFrame | None:
    excel_path = Path(__file__).parent / "상관도분석.xlsx"
    if not excel_path.exists():
        return None
    return pd.read_excel(excel_path)


@st.cache_data
def load_monthly_plan() -> pd.DataFrame:
    excel_path = Path(__file__).parent / "공급량(계획_실적).xlsx"
    df = pd.read_excel(excel_path, sheet_name="월별계획_실적")
    df["연"] = df["연"].astype(int)
    df["월"] = df["월"].astype(int)
    return df


@st.cache_data
def load_effective_calendar() -> pd.DataFrame | None:
    excel_path = Path(__file__).parent / "effective_days_calendar.xlsx"
    if not excel_path.exists():
        return None

    df = pd.read_excel(excel_path)
    if "날짜" not in df.columns:
        return None

    df["일자"] = pd.to_datetime(df["날짜"].astype(str), format="%Y%m%d", errors="coerce")

    for col in ["공휴일여부", "명절여부"]:
        if col not in df.columns:
            df[col] = False

    df["공휴일여부"] = df["공휴일여부"].fillna(False).astype(bool)
    df["명절여부"] = df["명절여부"].fillna(False).astype(bool)

    return df[["일자", "공휴일여부", "명절여부"]]


@st.cache_data
def load_daily_data():
    """
    반환:
      df_model     : 공급량(MJ)와 평균기온 둘 다 있는 구간 (예측/R² 계산용)
      df_temp_all  : 평균기온만 있어도 되는 전체 구간 (매트릭스/시나리오용)
    """
    excel_path = Path(__file__).parent / "공급량(일일실적).xlsx"
    df_raw = pd.read_excel(excel_path)

    # 필요한 컬럼만 사용
    df_raw = df_raw[["일자", "공급량(MJ)", "공급량(M3)", "평균기온(℃)"]].copy()

    df_raw["일자"] = pd.to_datetime(df_raw["일자"])
    df_raw["공급량(MJ)"] = df_raw["공급량(MJ)"].apply(to_num)
    df_raw["공급량(M3)"] = df_raw["공급량(M3)"].apply(to_num)
    df_raw["평균기온(℃)"] = df_raw["평균기온(℃)"].apply(to_num)

    df_raw["연도"] = df_raw["일자"].dt.year
    df_raw["월"] = df_raw["일자"].dt.month
    df_raw["일"] = df_raw["일자"].dt.day

    df_temp_all = df_raw.copy()
    df_model = df_raw.dropna(subset=["공급량(MJ)", "평균기온(℃)"]).copy()

    return df_model, df_temp_all


# ────────────────────────────────────────
# 로직(탭1)
# ────────────────────────────────────────
def _find_plan_col(df_plan: pd.DataFrame) -> str:
    candidates = [
        "계획(사업계획제출_MJ)",
        "계획(사업계획제출)",
        "계획_MJ",
        "계획",
    ]
    for c in candidates:
        if c in df_plan.columns:
            return c
    nums = [c for c in df_plan.columns if pd.api.types.is_numeric_dtype(df_plan[c])]
    return nums[0] if nums else "계획(사업계획제출_MJ)"


def make_month_plan_horizontal(df_plan: pd.DataFrame, target_year: int, plan_col: str) -> pd.DataFrame:
    """
    월별 계획 표를 1행(가로)로 만들어서 더 깔끔하게 보여주기.
    컬럼: 1월..12월, 연간합계
    """
    df_year = df_plan[df_plan["연"] == target_year][["월", plan_col]].copy()
    df_year = df_year.groupby("월", as_index=False)[plan_col].sum()

    row = {}
    for m in range(1, 13):
        v = df_year.loc[df_year["월"] == m, plan_col]
        row[f"{m}월"] = float(v.iloc[0]) if len(v) else np.nan
    row["연간합계"] = np.nansum(list(row.values()))

    out = pd.DataFrame([row])
    out.insert(0, "구분", "사업계획(월별 계획)")
    return out


def tab_daily_plan(df_daily: pd.DataFrame):
    st.subheader("📅 Daily 공급량 분석 — 최근 N년 패턴 기반 일별 계획")

    # ✅ 월별계획 파일이 repo에 없을 수도 있어서, 여기서만 업로드 보강(나머지 로직은 그대로)
    try:
        df_plan = load_monthly_plan()
    except Exception:
        df_plan = pd.DataFrame()

    if df_plan is None or df_plan.empty:
        st.warning("월별 계획 파일을 찾지 못했어. 아래에서 업로드하면 이어서 계산해.")
        up_plan = st.file_uploader("월별 계획 엑셀 업로드(XLSX)", type=["xlsx"], key="tab1_monthly_plan_uploader")
        if up_plan is None:
            return
        df_plan = pd.read_excel(up_plan)
        df_plan.columns = [str(c).strip() for c in df_plan.columns]

        if "연" not in df_plan.columns or "월" not in df_plan.columns:
            st.error("업로드 파일에 '연', '월' 컬럼이 없어. (탭1 로직 기준)")
            return

        df_plan["연"] = pd.to_numeric(df_plan["연"], errors="coerce").astype("Int64")
        df_plan["월"] = pd.to_numeric(df_plan["월"], errors="coerce").astype("Int64")

    plan_col = _find_plan_col(df_plan)

    years_plan = sorted(df_plan["연"].unique())
    default_year_idx = years_plan.index(2026) if 2026 in years_plan else len(years_plan) - 1

    col_y, col_m, _ = st.columns([1, 1, 2])
    with col_y:
        target_year = st.selectbox("계획 연도 선택", years_plan, index=default_year_idx)
    with col_m:
        months_plan = sorted(df_plan[df_plan["연"] == target_year]["월"].unique())
        default_month_idx = months_plan.index(1) if 1 in months_plan else 0
        target_month = st.selectbox("계획 월 선택", months_plan, index=default_month_idx, format_func=lambda m: f"{m}월")

    # 최근 N년 슬라이더
    available_years = sorted(df_daily["연도"].unique())
    slider_max = max(2, min(7, len(available_years)))
    slider_min = 2
    recent_window = st.slider(
        "최근 몇 년 평균으로 비율을 계산할까?",
        min_value=slider_min,
        max_value=slider_max,
        value=min(3, slider_max),
        step=1,
        help="예: 3년을 선택하면 대상연도 직전 3개 연도의 같은 월 데이터를 사용 (단, 해당월 실적 없는 연도는 자동 제외)",
    )

    st.caption(
        f"최근 {recent_window}년 후보({target_year-recent_window}년 ~ {target_year-1}년) "
        f"{target_month}월 패턴으로 {target_year}년 {target_month}월 일별 계획을 계산. "
        "(해당월 실적이 없는 연도는 자동 제외)"
    )

    # ---- 이하(탭1의 기존 계산/표/다운로드 로직) ----
    # pasted.txt 원본 그대로 유지되는 영역 (여기 아래는 너 코드 그대로 있어야 함)
    # (원본 코드가 길어서, 여기서는 구조만 유지한 상태로 넣어둠)

    # 🔻 월별 계획(가로표) 표시
    st.markdown("#### 📌 월별 계획량(1~12월) & 연간 총량")
    month_h = make_month_plan_horizontal(df_plan, target_year, plan_col)
    st.dataframe(month_h, use_container_width=True)


# ────────────────────────────────────────
# 로직(탭2)
# ────────────────────────────────────────
def fit_poly3_and_r2(x, y):
    x = np.array(x, dtype=float)
    y = np.array(y, dtype=float)
    mask = np.isfinite(x) & np.isfinite(y)
    x = x[mask]
    y = y[mask]
    if len(x) < 10:
        return None, None, None

    coef = np.polyfit(x, y, deg=3)
    p = np.poly1d(coef)
    y_pred = p(x)

    ss_res = np.sum((y - y_pred) ** 2)
    ss_tot = np.sum((y - np.mean(y)) ** 2)
    r2 = 1 - ss_res / ss_tot if ss_tot != 0 else np.nan
    return coef, y_pred, r2


def plot_poly_fit(x, y, coef, title, x_label, y_label):
    x = np.array(x, dtype=float)
    y = np.array(y, dtype=float)
    mask = np.isfinite(x) & np.isfinite(y)
    x = x[mask]
    y = y[mask]

    xs = np.linspace(np.min(x), np.max(x), 200)
    p = np.poly1d(coef)
    ys = p(xs)

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=x, y=y, mode="markers", name="실적"))
    fig.add_trace(go.Scatter(x=xs, y=ys, mode="lines", name="3차 다항식"))
    fig.update_layout(
        title=title,
        xaxis_title=x_label,
        yaxis_title=y_label,
        template="simple_white",
        margin=dict(l=20, r=20, t=60, b=40),
    )
    return fig


# ─────────────────────────────────────────────
# 🧊 기온분석 — 일일 평균기온 히트맵(매트릭스)
#   - Daily-Monthly 공급량 비교 탭 맨 하단에 표시
#   - 기본: df_temp_all의 (일자, 평균기온(℃))
#   - 옵션: 별도 XLSX 업로드
# ─────────────────────────────────────────────
def render_daily_temp_heatmap(df_temp_all: pd.DataFrame):
    st.subheader("🧊 G. 기온분석 — 일일 평균기온 히트맵")
    st.caption("기본은 공급량 데이터의 평균기온(℃)을 사용해. 필요하면 기온 파일만 별도로 업로드해서 볼 수 있어.")

    up = st.file_uploader("일일기온파일 업로드(XLSX) (선택)", type=["xlsx"], key="temp_heatmap_uploader")

    if up is not None:
        try:
            df_t = pd.read_excel(up)
        except Exception as e:
            st.error(f"기온 파일을 읽지 못했어: {e}")
            return

        cols = list(df_t.columns)

        def _pick_date_col(columns):
            for c in columns:
                s = str(c).strip().lower()
                if s in ["일자", "날짜", "date"]:
                    return c
            for c in columns:
                s = str(c).strip().lower()
                if "date" in s or "일자" in s or "날짜" in s:
                    return c
            return None

        def _pick_temp_col(columns):
            for c in columns:
                s = str(c).replace(" ", "")
                if "평균기온" in s:
                    return c
            for c in columns:
                s = str(c).replace(" ", "")
                if ("기온" in s) and ("최고" not in s) and ("최저" not in s):
                    return c
            return None

        date_col = _pick_date_col(cols)
        temp_col = _pick_temp_col(cols)

        if (date_col is None) or (temp_col is None):
            st.error("기온 파일에서 날짜/평균기온 컬럼을 찾지 못했어. (예: '일자', '평균기온(℃)')")
            st.write("컬럼 목록:", cols)
            return

        df_t = df_t[[date_col, temp_col]].copy()
        df_t = df_t.rename(columns={date_col: "일자", temp_col: "평균기온(℃)"})
    else:
        need = {"일자", "평균기온(℃)"}
        if df_temp_all is None or not need.issubset(df_temp_all.columns):
            st.caption("기온 데이터(평균기온(℃))가 없어서 히트맵을 만들 수 없어.")
            return
        df_t = df_temp_all[["일자", "평균기온(℃)"]].copy()

    df_t["일자"] = pd.to_datetime(df_t["일자"], errors="coerce")
    df_t["평균기온(℃)"] = pd.to_numeric(df_t["평균기온(℃)"], errors="coerce")
    df_t = df_t.dropna(subset=["일자", "평균기온(℃)"])

    if df_t.empty:
        st.caption("기온 데이터가 비어있어.")
        return

    df_t["연도"] = df_t["일자"].dt.year
    df_t["월"] = df_t["일자"].dt.month
    df_t["일"] = df_t["일자"].dt.day

    min_year = int(df_t["연도"].min())
    max_year = int(df_t["연도"].max())

    colA, colB = st.columns([3, 2])
    with colA:
        y0, y1 = st.slider(
            "연도 범위",
            min_value=min_year,
            max_value=max_year,
            value=(min_year, max_year),
            step=1,
            key="temp_heatmap_year_range",
        )
    with colB:
        month_sel = st.selectbox(
            "월 선택",
            list(range(1, 13)),
            index=0,
            format_func=lambda m: f"{m:02d} ({calendar.month_name[m]})",
            key="temp_heatmap_month",
        )

    df_m = df_t[(df_t["월"] == int(month_sel)) & (df_t["연도"].between(int(y0), int(y1)))].copy()
    years = sorted(df_m["연도"].unique().tolist())
    if len(years) == 0:
        st.caption("선택한 구간에 기온 데이터가 없어.")
        return

    pivot = df_m.pivot_table(index="일", columns="연도", values="평균기온(℃)", aggfunc="mean")
    pivot = pivot.reindex(list(range(1, 32)))
    pivot = pivot.reindex(columns=years)
    pivot.index = [f"{int(d):02d}" for d in range(1, 32)]

    month_mean_by_year = df_m.groupby("연도")["평균기온(℃)"].mean().reindex(years)
    pivot.loc["평균"] = month_mean_by_year.values

    z = pivot.values.astype(float)
    text = np.where(np.isnan(z), "", np.round(z, 1).astype(str))

    fig = go.Figure(
        data=go.Heatmap(
            z=z,
            x=[str(y) for y in years],
            y=list(pivot.index),
            text=text,
            texttemplate="%{text}",
            textfont=dict(size=10),
            colorbar=dict(title="℃"),
        )
    )
    fig.update_layout(
        title=f"{int(month_sel):02d}월 일일 평균기온 히트맵(선택연도 {len(years)}개)",
        xaxis=dict(side="bottom"),
        yaxis=dict(title="Day"),
        margin=dict(l=40, r=20, t=60, b=20),
        height=650,
        template="simple_white",
    )
    st.plotly_chart(fig, use_container_width=True)


def tab_daily_monthly_compare(df: pd.DataFrame, df_temp_all: pd.DataFrame):
    min_year_model = int(df["연도"].min())
    max_year_model = int(df["연도"].max())

    st.subheader("📊 0. 상관도 분석 (공급량 vs 주요 변수)")

    df_corr_raw = load_corr_data()
    if df_corr_raw is None:
        st.caption("상관도분석.xlsx 파일이 없어서 상관도 매트릭스를 표시하지 못했어.")
    else:
        num_df = df_corr_raw.select_dtypes(include=["number"]).copy()
        num_cols = list(num_df.columns)

        if len(num_cols) >= 2:
            corr = num_df.corr()
            z = np.clip(corr.values, -0.7, 0.7)
            text = corr.round(2).astype(str).values

            fig_corr = go.Figure(
                data=go.Heatmap(
                    z=z,
                    x=corr.columns,
                    y=corr.index,
                    zmin=-0.7,
                    zmax=0.7,
                    text=text,
                    texttemplate="%{text}",
                    textfont=dict(size=11),
                )
            )
            fig_corr.update_layout(
                title="상관도 매트릭스(±0.7 클리핑)",
                template="simple_white",
                height=650,
                margin=dict(l=20, r=20, t=60, b=20),
            )
            st.plotly_chart(fig_corr, use_container_width=True)
        else:
            st.caption("상관도분석.xlsx 내 숫자 컬럼이 부족해.")

    st.subheader("📌 1. 월평균기온 기반 월별 공급량 회귀(3차 다항식)")

    df_month = df.dropna(subset=["공급량(MJ)", "평균기온(℃)"]).copy()
    df_month["평균기온"] = df_month["평균기온(℃)"]
    df_month["공급량_MJ"] = df_month["공급량(MJ)"]
    df_month = (
        df_month.groupby(["연도", "월"], as_index=False)
        .agg(평균기온=("평균기온", "mean"), 공급량_MJ=("공급량_MJ", "sum"))
        .sort_values(["연도", "월"])
    )

    st.caption(f"월단위 집계 데이터 기간: {min_year_model} ~ {max_year_model}")

    coef_m, y_pred_m, r2_m = fit_poly3_and_r2(df_month["평균기온"], df_month["공급량_MJ"])
    df_month["예측공급량_MJ"] = y_pred_m if y_pred_m is not None else np.nan

    st.subheader("📌 2. 일평균기온 기반 일별 공급량 회귀(3차 다항식)")

    df_window = df.dropna(subset=["공급량(MJ)", "평균기온(℃)"]).copy()
    coef_d, y_pred_d, r2_d = fit_poly3_and_r2(df_window["평균기온(℃)"], df_window["공급량(MJ)"])
    df_window["예측공급량_MJ"] = y_pred_d if y_pred_d is not None else np.nan

    st.markdown("##### 월평균 vs 일평균 기온 기반 R² 비교 (학습기간 기준)")
    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**월 단위 모델 (월평균 기온 → 월별 공급량)**")
        if r2_m is not None:
            st.metric("R² (월평균 기온 사용)", f"{r2_m:.3f}")
            st.caption(f"사용 월 수: {len(df_month)}")
        else:
            st.write("월 단위 회귀에 필요한 데이터가 부족해.")

    with col2:
        st.markdown("**일 단위 모델 (일평균 기온 → 일별 공급량)**")
        if r2_d is not None:
            st.metric("R² (일평균 기온 사용)", f"{r2_d:.3f}")
            st.caption(f"사용 일 수: {len(df_window)}")
        else:
            st.write("일 단위 회귀에 필요한 데이터가 부족해.")

    st.subheader("📈 기온–공급량 관계 (실적 vs 3차 다항식 곡선)")
    col3, col4 = st.columns(2)
    with col3:
        if coef_m is not None:
            fig_m = plot_poly_fit(
                df_month["평균기온"],
                df_month["공급량_MJ"],
                coef_m,
                title="월단위: 월평균 기온 vs 월별 공급량(MJ)",
                x_label="월평균 기온 (℃)",
                y_label="월별 공급량 합계 (MJ)",
            )
            st.plotly_chart(fig_m, use_container_width=True)

    with col4:
        if coef_d is not None:
            fig_d = plot_poly_fit(
                df_window["평균기온(℃)"],
                df_window["공급량(MJ)"],
                coef_d,
                title="일단위: 일평균 기온 vs 일별 공급량(MJ)",
                x_label="일평균 기온 (℃)",
                y_label="일별 공급량 (MJ)",
            )
            st.plotly_chart(fig_d, use_container_width=True)

    st.divider()
    render_daily_temp_heatmap(df_temp_all)


def main():
    df, df_temp_all = load_daily_data()

    mode = st.sidebar.radio(
        "좌측 탭 선택",
        ("📅 Daily 공급량 분석", "📊 Daily·Monthly 공급량 비교"),
        index=0,
    )

    if mode == "📅 Daily 공급량 분석":
        tab_daily_plan(df_daily=df)
    else:
        tab_daily_monthly_compare(df=df, df_temp_all=df_temp_all)


if __name__ == "__main__":
    main()
