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


@st.cache_data
def load_effective_calendar() -> pd.DataFrame | None:
    """
    effective_days_calendar.xlsx 읽어서
    - 날짜 → 일자(datetime)
    - 공휴일여부, 명절여부(bool) 만 사용
    """
    excel_path = Path(__file__).parent / "effective_days_calendar.xlsx"
    if not excel_path.exists():
        return None

    df = pd.read_excel(excel_path)

    if "날짜" not in df.columns:
        return None

    # 날짜를 datetime으로 변환
    df["일자"] = pd.to_datetime(df["날짜"].astype(str), format="%Y%m%d", errors="coerce")

    # 공휴일/명절 컬럼 없으면 False 로 채움
    for col in ["공휴일여부", "명절여부"]:
        if col not in df.columns:
            df[col] = False

    df["공휴일여부"] = df["공휴일여부"].fillna(False).astype(bool)
    df["명절여부"] = df["명절여부"].fillna(False).astype(bool)

    return df[["일자", "공휴일여부", "명절여부"]].copy()


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

    def _fmt_no_comma(x):
        if pd.isna(x):
            return ""
        try:
            return f"{int(x)}"
        except Exception:
            return str(x)

    for col in df.columns:
        # bool 컬럼 (예: 공휴일여부)
        if df[col].dtype == bool:
            df[col] = df[col].map(lambda x: "공휴일" if x else "")
            continue

        if col in percent_cols:
            df[col] = df[col].map(lambda x: f"{x:.4f}")
        elif col in temp_cols:
            df[col] = df[col].map(lambda x: f"{x:.2f}")
        elif pd.api.types.is_numeric_dtype(df[col]):
            # 연/연도/월/일은 콤마 없이
            if col in ["연", "연도", "월", "일"]:
                df[col] = df[col].map(_fmt_no_comma)
            else:
                df[col] = df[col].map(lambda x: f"{x:,.0f}")
    return df


def center_style(df: pd.DataFrame):
    """모든 표 숫자 및 헤더를 중앙 정렬 + 인덱스 숨김."""
    styler = (
        df.style
        .set_table_styles(
            [
                dict(selector="th", props=[("text-align", "center")]),
                dict(selector="td", props=[("text-align", "center")]),
            ]
        )
        .set_properties(**{"text-align": "center"})
    )
    try:
        styler = styler.hide(axis="index")
    except Exception:
        try:
            styler = styler.hide_index()
        except Exception:
            pass
    return styler


# ─────────────────────────────────────────────
# Daily 공급량 분석용 함수  (★ 12/25 스무딩 포함 수정 버전)
# ─────────────────────────────────────────────
def make_daily_plan_table(
    df_daily: pd.DataFrame,
    df_plan: pd.DataFrame,
    target_year: int = 2026,
    target_month: int = 1,
    recent_window: int = 3,
) -> tuple[pd.DataFrame | None, pd.DataFrame | None, list[int]]:
    """
    최근 recent_window년 같은 월의 일별 공급 패턴으로
    target_year/target_month 일별 비율과 일별 계획 공급량을 계산.

    토·일 + 공휴일 + 명절(설날/추석 등)을 모두 '주말' 패턴으로 묶어서 사용.
    """
    cal_df = load_effective_calendar()

    # 사용 가능한 연도 범위
    all_years = sorted(df_daily["연도"].unique())
    start_year = target_year - recent_window
    recent_years = [y for y in range(start_year, target_year) if y in all_years]

    if len(recent_years) == 0:
        return None, None, []

    # 최근 N년 + 대상 월 데이터
    df_recent = df_daily[
        (df_daily["연도"].isin(recent_years)) & (df_daily["월"] == target_month)
    ].copy()
    if df_recent.empty:
        return None, None, recent_years

    df_recent = df_recent.sort_values(["연도", "일"]).copy()
    df_recent["weekday_idx"] = df_recent["일자"].dt.weekday  # 0=월, 6=일

    # 캘린더 정보 머지
    if cal_df is not None:
        df_recent = df_recent.merge(cal_df, on="일자", how="left")
        df_recent["공휴일여부"] = df_recent["공휴일여부"].fillna(False).astype(bool)
        df_recent["명절여부"] = df_recent["명절여부"].fillna(False).astype(bool)
    else:
        df_recent["공휴일여부"] = False
        df_recent["명절여부"] = False

    df_recent["is_holiday"] = df_recent["공휴일여부"] | df_recent["명절여부"]
    df_recent["is_weekend"] = (df_recent["weekday_idx"] >= 5) | df_recent["is_holiday"]

    # 연도별 월 합계 및 비율
    df_recent["month_total"] = df_recent.groupby("연도")["공급량(MJ)"].transform("sum")
    df_recent["ratio"] = df_recent["공급량(MJ)"] / df_recent["month_total"]

    # 같은 연도·요일 내 몇 번째 요일인지
    df_recent["nth_dow"] = (
        df_recent.sort_values(["연도", "일"])
        .groupby(["연도", "weekday_idx"])
        .cumcount()
        + 1
    )

    weekday_mask = ~df_recent["is_weekend"]
    weekend_mask = df_recent["is_weekend"]

    # 평일: 일자 기준 평균 / 요일 기준 백업
    ratio_by_day = (
        df_recent[weekday_mask].groupby("일")["ratio"].mean()
        if df_recent[weekday_mask].size > 0
        else pd.Series(dtype=float)
    )
    ratio_weekday_by_dow = (
        df_recent[weekday_mask].groupby("weekday_idx")["ratio"].mean()
        if df_recent[weekday_mask].size > 0
        else pd.Series(dtype=float)
    )

    # 주말/공휴일: (요일, nth_dow) 기준 평균 / 요일 기준 백업
    ratio_weekend_group = (
        df_recent[weekend_mask]
        .groupby(["weekday_idx", "nth_dow"])["ratio"]
        .mean()
        if df_recent[weekend_mask].size > 0
        else pd.Series(dtype=float)
    )
    ratio_weekend_by_dow = (
        df_recent[weekend_mask].groupby("weekday_idx")["ratio"].mean()
        if df_recent[weekend_mask].size > 0
        else pd.Series(dtype=float)
    )

    ratio_by_day_dict = ratio_by_day.to_dict()
    ratio_weekday_by_dow_dict = ratio_weekday_by_dow.to_dict()
    ratio_weekend_group_dict = ratio_weekend_group.to_dict()
    ratio_weekend_by_dow_dict = ratio_weekend_by_dow.to_dict()

    # 대상 연·월 날짜 생성
    last_day = calendar.monthrange(target_year, target_month)[1]
    date_range = pd.date_range(
        f"{target_year}-{target_month:02d}-01", periods=last_day, freq="D"
    )

    df_target = pd.DataFrame({"일자": date_range})
    df_target["연"] = target_year
    df_target["월"] = target_month
    df_target["일"] = df_target["일자"].dt.day
    df_target["weekday_idx"] = df_target["일자"].dt.weekday

    # 캘린더 머지 (대상월)
    if cal_df is not None:
        df_target = df_target.merge(cal_df, on="일자", how="left")
        df_target["공휴일여부"] = df_target["공휴일여부"].fillna(False).astype(bool)
        df_target["명절여부"] = df_target["명절여부"].fillna(False).astype(bool)
    else:
        df_target["공휴일여부"] = False
        df_target["명절여부"] = False

    df_target["is_holiday"] = df_target["공휴일여부"] | df_target["명절여부"]
    df_target["is_weekend"] = (df_target["weekday_idx"] >= 5) | df_target["is_holiday"]

    weekday_names = ["월", "화", "수", "목", "금", "토", "일"]
    df_target["요일"] = df_target["weekday_idx"].map(lambda i: weekday_names[i])

    # 대상월에서도 요일별 nth_dow 계산
    df_target["nth_dow"] = (
        df_target.sort_values("일")
        .groupby("weekday_idx")
        .cumcount()
        + 1
    )

    df_target["구분(평일/주말)"] = np.where(df_target["is_weekend"], "주말", "평일")

    # 비율 매핑 함수
    def _weekend_ratio(row):
        dow = row["weekday_idx"]
        nth = row["nth_dow"]
        key = (dow, nth)
        val = ratio_weekend_group_dict.get(key, None)
        if val is None or pd.isna(val):
            val = ratio_weekend_by_dow_dict.get(dow, None)
        return val

    def _weekday_ratio(row):
        day = row["일"]
        dow = row["weekday_idx"]
        val = ratio_by_day_dict.get(day, None)
        if val is None or pd.isna(val):
            val = ratio_weekday_by_dow_dict.get(dow, None)
        return val

    df_target["weekend_raw"] = 0.0
    df_target["weekday_raw"] = 0.0

    for idx, row in df_target.iterrows():
        if row["is_weekend"]:
            val = _weekend_ratio(row)
            df_target.at[idx, "weekend_raw"] = val if val is not None else np.nan
        else:
            val = _weekday_ratio(row)
            df_target.at[idx, "weekday_raw"] = val if val is not None else np.nan

    # NaN 처리
    if df_target["weekend_raw"].notna().any():
        mean_wend = df_target["weekend_raw"].dropna().mean()
        df_target["weekend_raw"] = df_target["weekend_raw"].fillna(mean_wend)
    else:
        df_target["weekend_raw"] = 0.0

    if df_target["weekday_raw"].notna().any():
        mean_wday = df_target["weekday_raw"].dropna().mean()
        df_target["weekday_raw"] = df_target["weekday_raw"].fillna(mean_wday)
    else:
        df_target["weekday_raw"] = 0.0

    weekend_raw_sum = df_target["weekend_raw"].sum()
    weekday_raw_sum = df_target["weekday_raw"].sum()

    # 전체 비율 합(주말+평일)이 0이면 균등 분배
    if weekend_raw_sum + weekday_raw_sum <= 0:
        df_target["일별비율"] = 1.0 / last_day
    else:
        total_raw = weekend_raw_sum + weekday_raw_sum
        scale_all = 1.0 / total_raw

        df_target["weekend_scaled"] = df_target["weekend_raw"] * scale_all
        weekend_total_share = df_target["weekend_scaled"].sum()

        # 남은 비율(평일 몫)
        rest_share = max(1.0 - weekend_total_share, 0.0)

        if weekday_raw_sum > 0 and rest_share > 0:
            weekday_norm = df_target["weekday_raw"] / weekday_raw_sum
            df_target["weekday_scaled"] = weekday_norm * rest_share
        else:
            df_target["weekday_scaled"] = rest_share / last_day

        df_target["일별비율"] = (
            df_target["weekend_scaled"] + df_target["weekday_scaled"]
        )

        total_ratio = df_target["일별비율"].sum()
        if total_ratio > 0:
            df_target["일별비율"] = df_target["일별비율"] / total_ratio
        else:
            df_target["일별비율"] = 1.0 / last_day

    # ── 12월 25일 비율 스무딩 ─────────────────────────────
    if target_month == 12:
        mask_25 = df_target["일"] == 25
        if mask_25.any():
            neighbor_mask = df_target["일"].between(23, 27) & (~mask_25)
            if neighbor_mask.any():
                neighbor_mean = df_target.loc[neighbor_mask, "일별비율"].mean()
                if not np.isnan(neighbor_mean):
                    min_factor = 0.6  # 필요하면 0.5~0.8 사이로 튜닝
                    min_ratio_25 = neighbor_mean * min_factor

                    current_25 = df_target.loc[mask_25, "일별비율"].iloc[0]
                    if current_25 < min_ratio_25:
                        df_target.loc[mask_25, "일별비율"] = min_ratio_25
                        # 다시 합이 1이 되도록 정규화
                        df_target["일별비율"] = (
                            df_target["일별비율"] / df_target["일별비율"].sum()
                        )

    # 최근 N년 기준 총·평균 공급량
    month_total_all = df_recent["공급량(MJ)"].sum()
    df_target["최근N년_총공급량(MJ)"] = df_target["일별비율"] * month_total_all
    df_target["최근N년_평균공급량(MJ)"] = (
        df_target["최근N년_총공급량(MJ)"] / len(recent_years)
    )

    # 대상 연도의 월 계획 총량
    row_plan = df_plan[
        (df_plan["연"] == target_year) & (df_plan["월"] == target_month)
    ]
    if row_plan.empty:
        plan_total = np.nan
    else:
        plan_total = float(row_plan["계획(사업계획제출_MJ)"].iloc[0])

    # 일별 예상 공급량 (계획 기준)
    df_target["예상공급량(MJ)"] = (df_target["일별비율"] * plan_total).round(0)

    # 정렬 및 컬럼 순서
    df_target = df_target.sort_values("일").reset_index(drop=True)
    df_result = df_target[
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
    ].copy()

    # 최근 N년 일별 실적 매트릭스 (Heatmap)
    df_mat = (
        df_recent.pivot_table(
            index="일", columns="연도", values="공급량(MJ)", aggfunc="sum"
        )
        .sort_index()
        .sort_index(axis=1)
    )

    return df_result, df_mat, recent_years


# ─────────────────────────────────────────────
# 탭1: Daily 공급량 분석
# ─────────────────────────────────────────────
def tab_daily_plan(df_daily: pd.DataFrame):
    st.subheader("📅 Daily 공급량 분석 — 최근 N년 패턴 기반 일별 계획")

    df_plan = load_monthly_plan()

    # 기본값: 2026년 1월
    years_plan = sorted(df_plan["연"].unique())
    default_year_idx = years_plan.index(2026) if 2026 in years_plan else len(years_plan) - 1

    col_y, col_m, _ = st.columns([1, 1, 2])
    with col_y:
        target_year = st.selectbox("계획 연도 선택", years_plan, index=default_year_idx)
    with col_m:
        months_plan = sorted(df_plan[df_plan["연"] == target_year]["월"].unique())
        default_month_idx = months_plan.index(1) if 1 in months_plan else 0
        target_month = st.selectbox(
            "계획 월 선택", months_plan, index=default_month_idx, format_func=lambda m: f"{m}월"
        )

    # 사용할 수 있는 과거 연도 수에 따라 슬라이더 범위 설정
    all_years = sorted(df_daily["연도"].unique())
    hist_years = [y for y in all_years if y < target_year]
    if len(hist_years) < 1:
        st.warning("해당 연도는 직전 연도가 없어 최근 N년 분석을 할 수 없어.")
        return

    slider_min = 1    # 1년~10년
    slider_max = min(10, len(hist_years))

    col_slider, _ = st.columns([2, 3])
    with col_slider:
        recent_window = st.slider(
            "최근 몇 년 평균으로 비율을 계산할까?",
            min_value=slider_min,
            max_value=slider_max,
            value=min(3, slider_max),
            step=1,
            help="예: 3년을 선택하면 대상연도 직전 3개 연도(예: 2023~2025년)의 같은 월 데이터를 사용",
        )

    st.caption(
        f"최근 {recent_window}년 ({target_year-recent_window}년 ~ {target_year-1}년) "
        f"{target_month}월 일별 공급 패턴으로 {target_year}년 {target_month}월 일별 계획을 계산."
    )

    df_result, df_mat, recent_years = make_daily_plan_table(
        df_daily=df_daily,
        df_plan=df_plan,
        target_year=target_year,
        target_month=target_month,
        recent_window=recent_window,
    )

    if df_result is None or len(recent_years) == 0:
        st.warning("해당 연도/월에 대해 선택한 최근 N년 기준으로 계산할 수 있는 데이터가 없어.")
        return

    # 실제로 사용된 연도 범위 안내
    st.markdown(
        f"- 실제 사용된 과거 연도: {min(recent_years)}년 ~ {max(recent_years)}년 "
        f"(총 {len(recent_years)}개 연도)"
    )

    plan_total = df_result["예상공급량(MJ)"].sum()
    st.markdown(
        f"**{target_year}년 {target_month}월 사업계획 제출 공급량 합계:** "
        f"`{plan_total:,.0f} MJ`"
    )

    # 1. 일별 테이블 (합계 행 추가)
    st.markdown("#### 1. 일별 비율·예상 공급량 테이블")

    view = df_result.copy()

    total_row = {
        "연": "",
        "월": "",
        "일": "",
        "일자": "",
        "요일": "합계",
        "구분(평일/주말)": "",
        "공휴일여부": False,
        "최근N년_평균공급량(MJ)": view["최근N년_평균공급량(MJ)"].sum(),
        "최근N년_총공급량(MJ)": view["최근N년_총공급량(MJ)"].sum(),
        "일별비율": view["일별비율"].sum(),
        "예상공급량(MJ)": view["예상공급량(MJ)"].sum(),
    }
    view_with_total = pd.concat(
        [view, pd.DataFrame([total_row])], ignore_index=True
    )

    view_for_format = view_with_total[
        [
            "연",
            "월",
            "일",
            "요일",
            "구분(평일/주말)",
            "공휴일여부",
            "최근N년_평균공급량(MJ)",
            "최근N년_총공급량(MJ)",
            "일별비율",
            "예상공급량(MJ)",
        ]
    ]
    view_for_format = format_table_generic(
        view_for_format,
        percent_cols=["일별비율"],
    )
    st.table(center_style(view_for_format))

    # 2. 그래프 (Bar: 예상공급량, Line: 일별비율)
    st.markdown("#### 2. 일별 예상 공급량 & 비율 그래프")

    weekday_df = view[view["구분(평일/주말)"] == "평일"]
    weekend_df = view[view["구분(평일/주말)"] == "주말"]

    fig = go.Figure()
    fig.add_bar(
        x=weekday_df["일"],
        y=weekday_df["예상공급량(MJ)"],
        name="평일 예상공급량(MJ)",
    )
    fig.add_bar(
        x=weekend_df["일"],
        y=weekend_df["예상공급량(MJ)"],
        name="주말/공휴일 예상공급량(MJ)",
    )
    fig.add_trace(
        go.Scatter(
            x=view["일"],
            y=view["일별비율"],
            mode="lines+markers",
            name=f"일별비율 (최근{recent_window}년)",
            yaxis="y2",
        )
    )

    fig.update_layout(
        title=(
            f"{target_year}년 {target_month}월 일별 공급량 계획 "
            f"(최근{recent_window}년 {target_month}월 비율 기반)"
        ),
        xaxis_title="일",
        yaxis=dict(title="예상 공급량 (MJ)"),
        yaxis2=dict(
            title="일별비율",
            overlaying="y",
            side="right",
        ),
        barmode="group",
        margin=dict(l=20, r=20, t=60, b=40),
    )
    st.plotly_chart(fig, use_container_width=True)

    # 3. 매트릭스(Heatmap) — 최근 N년 일별 실적
    st.markdown("#### 3. 최근 N년 일별 실적 매트릭스")

    if df_mat is not None:
        fig_hm = go.Figure(
            data=go.Heatmap(
                z=df_mat.values,
                x=[str(c) for c in df_mat.columns],
                y=df_mat.index,
                colorbar_title="공급량(MJ)",
                colorscale="RdBu_r",
            )
        )
        fig_hm.update_layout(
            title=f"최근 {len(recent_years)}년 {target_month}월 일별 실적 공급량(MJ) 매트릭스",
            xaxis=dict(title="연도", type="category"),
            yaxis=dict(title="일", autorange="reversed"),
            margin=dict(l=40, r=40, t=60, b=40),
        )
        st.plotly_chart(fig_hm, use_container_width=False)

    # 4. 평일·주말 비중 요약
    st.markdown("#### 4. 평일·주말 비중 요약")

    summary = (
        view.groupby("구분(평일/주말)", as_index=False)[["일별비율", "예상공급량(MJ)"]]
        .sum()
        .rename(columns={"일별비율": "일별비율합계"})
    )

    total_row_sum = {
        "구분(평일/주말)": "합계",
        "일별비율합계": summary["일별비율합계"].sum(),
        "예상공급량(MJ)": summary["예상공급량(MJ)"].sum(),
    }
    summary = pd.concat([summary, pd.DataFrame([total_row_sum])], ignore_index=True)
    summary = summary.rename(columns={"구분(평일/주말)": "구분"})
    summary = format_table_generic(summary, percent_cols=["일별비율합계"])
    st.table(center_style(summary))

    # 5. 엑셀 다운로드
    st.markdown("#### 5. 일별 계획 엑셀 다운로드")

    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        view_with_total.to_excel(
            writer,
            index=False,
            sheet_name=f"{target_year}_{target_month:02d}_일별계획",
        )

    st.download_button(
        label=f"📥 {target_year}년 {target_month}월 일별공급계획 다운로드 (Excel)",
        data=buffer.getvalue(),
        file_name=f"{target_year}_{target_month:02d}_일별공급계획.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


# ─────────────────────────────────────────────
# 탭2: Daily·Monthly 공급량 비교
# ─────────────────────────────────────────────
def tab_daily_monthly_compare(df: pd.DataFrame, df_temp_all: pd.DataFrame):
    # (여기는 너가 준 코드 그대로 – 수정 안 함)

    # 공급량이 있는 구간(예측/R²용) 연도 범위
    min_year_model = int(df["연도"].min())
    max_year_model = int(df["연도"].max())

    # 기온 전체 구간 연도 범위
    min_year_temp = int(df_temp_all["연도"].min())
    max_year_temp = int(df_temp_all["연도"].max())

    # 0. 상관도 분석
    st.subheader("📊 0. 상관도 분석 (공급량 vs 주요 변수)")

    df_corr_raw = load_corr_data()
    if df_corr_raw is None:
        st.caption("상관도분석.xlsx 파일이 없어서 상관도 매트릭스를 표시하지 못했어.")
    else:
        num_df = df_corr_raw.select_dtypes(include=["number"]).copy()
        num_cols = list(num_df.columns)

        if len(num_cols) >= 2:
            corr = num_df.corr()

            z = corr.values
            z_display = np.clip(z, -0.7, 0.7)
            text = corr.round(2).astype(str).values   # 매트릭스 안 숫자도 2자리

            side = 600  # 정사각형 크기

            nice_colorscale = [
                [0.0, "#313695"],
                [0.2, "#4575b4"],
                [0.4, "#abd9e9"],
                [0.5, "#ffffbf"],
                [0.6, "#fdae61"],
                [0.8, "#d73027"],
                [1.0, "#a50026"],
            ]

            fig_corr = go.Figure(
                data=go.Heatmap(
                    z=z_display,
                    x=corr.columns,
                    y=corr.index,
                    colorscale=nice_colorscale,
                    zmin=-0.7,
                    zmax=0.7,
                    zmid=0,
                    colorbar_title="상관계수",
                    text=text,
                    texttemplate="%{text}",
                    textfont=dict(size=10, color="black"),
                )
            )
            fig_corr.update_layout(
                xaxis_title="변수",
                yaxis_title="변수",
                xaxis=dict(
                    side="top",
                    tickangle=45,
                ),
                yaxis=dict(autorange="reversed"),
                width=side,
                height=side,
                margin=dict(l=80, r=20, t=80, b=80),
            )

            # 기준 변수(공급량)와의 상관계수 표
            target_col = None
            for c in num_cols:
                if "공급량" in str(c):
                    target_col = c
                    break
            if target_col is None:
                target_col = num_cols[0]

            if target_col in corr.columns:
                target_series = corr[target_col].drop(target_col)
                target_series = target_series.reindex(
                    target_series.abs().sort_values(ascending=False).index
                )

                tbl_df = target_series.to_frame(name="상관계수")
                tbl_df_disp = tbl_df.copy()
                tbl_df_disp["상관계수"] = tbl_df_disp["상관계수"].map(
                    lambda x: f"{x:.2f}"
                )

                col_hm, col_tbl = st.columns([3, 2])
                with col_hm:
                    st.plotly_chart(fig_corr, use_container_width=True)
                with col_tbl:
                    st.markdown(
                        f"**기준 변수: `{target_col}` 과(와) 다른 변수들의 상관계수**"
                    )
                    st.table(center_style(tbl_df_disp))
        else:
            st.caption("숫자 컬럼이 2개 미만이라 상관도 분석을 할 수 없어.")

    # 이하 tab_daily_monthly_compare 나머지 부분은
    # 너가 준 코드 그대로 두면 돼 (생략 없이 이미 가지고 있으니까 그대로 유지)


# ─────────────────────────────────────────────
# 메인
# ─────────────────────────────────────────────
def main():
    df, df_temp_all = load_daily_data()

    mode = st.sidebar.radio(
        "좌측 탭 선택",
        ("📅 Daily 공급량 분석", "📊 Daily·Monthly 공급량 비교"),
        index=0,
    )

    if mode == "📅 Daily 공급량 분석":
        st.title("도시가스 공급량 — 일별계획 예측")
        tab_daily_plan(df_daily=df)
    else:
        st.title("도시가스 공급량 — 일별 vs 월별 예측 검증")
        tab_daily_monthly_compare(df=df, df_temp_all=df_temp_all)


if __name__ == "__main__":
    main()
