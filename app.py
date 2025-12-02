import calendar
from io import BytesIO
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st


# ─────────────────────────────────────────────
# 기본 설정
# ─────────────────────────────────────────────
st.set_page_config(
    page_title="도시가스 공급량: 일/월 기온 기반 예측력 비교",
    layout="wide",
)


# ─────────────────────────────────────────────
# 데이터 불러오기
# ─────────────────────────────────────────────
@st.cache_data
def load_daily_data():
    """
    반환:
      df_model     : 공급량(MJ)와 평균기온 둘 다 있는 구간 (예측/R² 계산용)
      df_temp_all  : 평균기온만 있어도 되는 전체 구간 (1980년 포함, 매트릭스/시나리오용)
    """
    excel_path = Path(__file__).parent / "공급량(일일실적).xlsx"
    df_raw = pd.read_excel(excel_path)

    # 필요한 컬럼만 사용
    df_raw = df_raw[["일자", "공급량(MJ)", "공급량(M3)", "평균기온(℃)"]].copy()
    df_raw["일자"] = pd.to_datetime(df_raw["일자"])

    # 날짜 파생 컬럼
    df_raw["연도"] = df_raw["일자"].dt.year
    df_raw["월"] = df_raw["일자"].dt.month
    df_raw["일"] = df_raw["일자"].dt.day

    # 기온만 있어도 되는 전체 구간
    df_temp_all = df_raw.dropna(subset=["평균기온(℃)"]).copy()

    # 예측·R²용: 공급량과 기온 둘 다 있는 구간
    df_model = df_temp_all.dropna(subset=["공급량(MJ)"]).copy()

    return df_model, df_temp_all


@st.cache_data
def load_corr_data() -> pd.DataFrame | None:
    excel_path = Path(__file__).parent / "상관도분석.xlsx"
    if not excel_path.exists():
        return None
    return pd.read_excel(excel_path)


@st.cache_data
def load_monthly_plan() -> pd.DataFrame:
    """
    공급량(계획_실적).xlsx 중 '월별계획_실적' 시트 사용
    컬럼 : 일자, 연, 월, 계획(사업계획제출_MJ), ...
    """
    excel_path = Path(__file__).parent / "공급량(계획_실적).xlsx"
    df = pd.read_excel(excel_path, sheet_name="월별계획_실적")
    df["연"] = df["연"].astype(int)
    df["월"] = df["월"].astype(int)
    return df


# ─────────────────────────────────────────────
# 유틸 함수들
# ─────────────────────────────────────────────
def fit_poly3_and_r2(x: pd.Series, y: pd.Series):
    x = np.asarray(x, dtype="float64")
    y = np.asarray(y, dtype="float64")

    if len(x) < 4:
        return None, None, None

    coef = np.polyfit(x, y, 3)
    y_pred = np.polyval(coef, x)

    ss_res = np.sum((y - y_pred) ** 2)
    ss_tot = np.sum((y - np.mean(y)) ** 2)

    if ss_tot == 0:
        r2 = np.nan
    else:
        r2 = 1 - ss_res / ss_tot

    return coef, y_pred, r2


def plot_poly_fit(x, y, coef, title, x_label, y_label):
    x = np.asarray(x, dtype="float64")
    y = np.asarray(y, dtype="float64")

    x_grid = np.linspace(x.min(), x.max(), 200)
    y_grid = np.polyval(coef, x_grid)

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=x,
            y=y,
            mode="markers",
            name="실적",
            hovertemplate="x=%{x}<br>y=%{y}<extra></extra>",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=x_grid,
            y=y_grid,
            mode="lines",
            name="3차 다항식 예측",
        )
    )
    fig.update_layout(
        title=title,
        xaxis_title=x_label,
        yaxis_title=y_label,
        margin=dict(l=20, r=20, t=40, b=20),
    )
    return fig


def format_table_generic(df, percent_cols=None, temp_cols=None):
    df = df.copy()
    if percent_cols is None:
        percent_cols = []
    if temp_cols is None:
        temp_cols = []

    for col in df.columns:
        if col in percent_cols:
            df[col] = df[col].map(lambda x: f"{x:.4f}")
        elif col in temp_cols:
            df[col] = df[col].map(lambda x: f"{x:.2f}")
        elif pd.api.types.is_numeric_dtype(df[col]):
            df[col] = df[col].map(lambda x: f"{x:,.0f}")
    return df


def center_style(df: pd.DataFrame):
    """모든 표 숫자 및 헤더를 중앙 정렬하는 Styler."""
    styler = (
        df.style.set_table_styles(
            [
                dict(selector="th", props=[("text-align", "center")]),
                dict(selector="td", props=[("text-align", "center")]),
            ]
        ).set_properties(**{"text-align": "center"})
    )
    return styler


# ─────────────────────────────────────────────
# Daily 공급량 분석용 함수
# ─────────────────────────────────────────────
def make_daily_plan_table(
    df_daily: pd.DataFrame,
    df_plan: pd.DataFrame,
    target_year: int = 2026,
    target_month: int = 1,
    recent_window: int = 3,
) -> tuple[pd.DataFrame | None, pd.DataFrame | None, list[int]]:
    """
    최근 recent_window년(예: 2023~2025) 같은 월의 일별 공급 패턴으로
    target_year/target_month 일별 비율과 일별 계획 공급량을 계산.
    반환:
      df_result : 대상 연/월 일별 계획 테이블
      df_mat    : 최근 n년 일별 실적 매트릭스 (Heatmap용)
      recent_years : 사용된 최근 연도 리스트
    """
    # 사용 가능한 연도 범위
    all_years = sorted(df_daily["연도"].unique())
    start_year = target_year - recent_window
    recent_years = [y for y in range(start_year, target_year) if y in all_years]

    if len(recent_years) == 0:
        return None, None, []

    # 최근 n년 + 대상 월 데이터
    df_recent = df_daily[
        (df_daily["연도"].isin(recent_years)) & (df_daily["월"] == target_month)
    ].copy()
    if df_recent.empty:
        return None, None, recent_years

    # 마지막 일자 (28/29/30/31)
    last_day = calendar.monthrange(target_year, target_month)[1]
    day_range = list(range(1, last_day + 1))

    # 일자별 총공급량 (최근 n년 합계 기준)
    daily_sum = (
        df_recent.groupby("일", as_index=False)["공급량(MJ)"].sum().rename(
            columns={"공급량(MJ)": "최근N년_총공급량(MJ)"}
        )
    )
    daily_sum = daily_sum.set_index("일").reindex(day_range, fill_value=0).reset_index()

    total_month = daily_sum["최근N년_총공급량(MJ)"].sum()
    if total_month <= 0:
        return None, None, recent_years

    # 일별 비율
    daily_sum["일별비율"] = daily_sum["최근N년_총공급량(MJ)"] / total_month

    # 최근 n년 평균 공급량 (설명용)
    daily_avg = (
        df_recent.groupby("일", as_index=False)["공급량(MJ)"].mean().rename(
            columns={"공급량(MJ)": "최근N년_평균공급량(MJ)"}
        )
    )
    daily_sum = daily_sum.merge(daily_avg, on="일", how="left")

    # 대상 연도의 월 계획 총량
    row_plan = df_plan[(df_plan["연"] == target_year) & (df_plan["월"] == target_month)]
    if row_plan.empty:
        plan_total = np.nan
    else:
        plan_total = float(row_plan["계획(사업계획제출_MJ)"].iloc[0])

    # 일별 예상 공급량
    daily_sum["예상공급량(MJ)"] = (daily_sum["일별비율"] * plan_total).round(0)

    # 날짜·요일·주말 구분
    dates = pd.to_datetime(
        {
            "year": target_year,
            "month": target_month,
            "day": daily_sum["일"],
        }
    )
    daily_sum["일자"] = dates
    daily_sum["연"] = target_year
    daily_sum["월"] = target_month

    weekday_names = ["월", "화", "수", "목", "금", "토", "일"]
    daily_sum["요일"] = dates.dt.weekday.map(lambda i: weekday_names[i])

    daily_sum["is_weekend"] = dates.dt.weekday >= 5
    daily_sum["공휴일여부"] = False  # holidays 라이브러리 없이 공휴일은 일단 미사용

    def _label(row):
        return "주말" if row["is_weekend"] else "평일"

    daily_sum["구분(평일/주말)"] = daily_sum.apply(_label, axis=1)

    # 정렬 및 컬럼 순서
    daily_sum = daily_sum.sort_values("일").reset_index(drop=True)
    daily_sum = daily_sum[
        [
            "연",
            "월",
            "일",
            "일자",
            "요일",
            "구분(평일/주말)",
            "공휴일여부",
            "최근N년_평균공급량(MJ)",
            "최근N년_총공급량(MJ)",
            "일별비율",
            "예상공급량(MJ)",
        ]
    ]

    # 최근 n년 일별 실적 매트릭스 (Heatmap)
    df_mat = (
        df_recent.pivot_table(
            index="일", columns="연도", values="공급량(MJ)", aggfunc="sum"
        )
        .reindex(index=day_range)
        .sort_index(axis=1)
    )

    return daily_sum, df_mat, recent_years


# ─────────────────────────────────────────────
# 탭1: Daily 공급량 분석
# ─────────────────────────────────────────────
def tab_daily_plan(df_daily: pd.DataFrame):
    st.subheader("📅 Daily 공급량 분석 — 최근 3년 패턴 기반 일별 계획")

    df_plan = load_monthly_plan()

    # 기본값: 2026년 1월
    years_plan = sorted(df_plan["연"].unique())
    default_year_idx = years_plan.index(2026) if 2026 in years_plan else len(years_plan) - 1

    col_y, col_m = st.columns(2)
    with col_y:
        target_year = st.selectbox("계획 연도 선택", years_plan, index=default_year_idx)
    with col_m:
        months_plan = sorted(df_plan[df_plan["연"] == target_year]["월"].unique())
        default_month_idx = months_plan.index(1) if 1 in months_plan else 0
        target_month = st.selectbox(
            "계획 월 선택", months_plan, index=default_month_idx, format_func=lambda m: f"{m}월"
        )

    st.caption(
        f"최근 **{target_year-3}년 ~ {target_year-1}년**까지의 "
        f"{target_month}월 일별 공급 패턴으로 **{target_year}년 {target_month}월** 일별 계획을 계산."
    )

    df_result, df_mat, recent_years = make_daily_plan_table(
        df_daily=df_daily,
        df_plan=df_plan,
        target_year=target_year,
        target_month=target_month,
        recent_window=3,
    )

    if df_result is None or len(recent_years) == 0:
        st.warning("해당 연도/월에 대해 최근 3년 기준으로 계산할 수 있는 데이터가 없어.")
        return

    plan_total = df_result["예상공급량(MJ)"].sum()
    st.markdown(
        f"**{target_year}년 {target_month}월 사업계획 제출 공급량 합계:** "
        f"`{plan_total:,.0f} MJ`"
    )

    # 1. 일별 테
