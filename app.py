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
# Daily 공급량 분석용 함수
#   - Step1: 과거 N년 같은 월에서 그룹별 비율(weekday/weekend/festival) 계산
#   - Step2: 그룹별 내부 패턴(일자/요일/offset)으로 raw weight 생성
#   - Step3: 그룹별 비율로 스케일링 → 일별비율 합 = 1
# ─────────────────────────────────────────────
def make_daily_plan_table(
    df_daily: pd.DataFrame,
    df_plan: pd.DataFrame,
    target_year: int = 2026,
    target_month: int = 1,
    recent_window: int = 3,
):
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

    df_recent = df_recent.sort_values(["연도", "일자"]).copy()
    df_recent["weekday_idx"] = df_recent["일자"].dt.weekday  # 0=월, 6=일

    # ── 캘린더 정보를 머지해서 공휴일/명절 붙이기 ──
    if cal_df is not None:
        df_recent = df_recent.merge(
            cal_df,
            on="일자",
            how="left",
        )
        df_recent["공휴일여부"] = df_recent["공휴일여부"].fillna(False).astype(bool)
        df_recent["명절여부"] = df_recent["명절여부"].fillna(False).astype(bool)
    else:
        df_recent["공휴일여부"] = False
        df_recent["명절여부"] = False

    # ── 명절 블록(명절 당일 + 전후 하루) 마킹 ──
    def _mark_festival(group: pd.DataFrame) -> pd.DataFrame:
        is_center = group["명절여부"].fillna(False)
        prev_center = is_center.shift(1, fill_value=False)
        next_center = is_center.shift(-1, fill_value=False)

        festival_block = is_center | prev_center | next_center

        offset = np.full(len(group), np.nan)
        offset[is_center.to_numpy()] = 0          # 명절 당일
        offset[(~is_center & prev_center).to_numpy()] = 1   # 명절 다음날
        offset[(~is_center & next_center).to_numpy()] = -1  # 명절 전날

        group["festival_block"] = festival_block
        group["festival_offset"] = offset
        return group

    df_recent = df_recent.groupby("연도", group_keys=False).apply(_mark_festival)

    # 기본 주말(토·일 + 공휴일)
    df_recent["is_basic_weekend"] = (
        (df_recent["weekday_idx"] >= 5) | df_recent["공휴일여부"]
    )

    # 그룹 라벨: festival > weekend > weekday
    def _group_label(row):
        if row["festival_block"]:
            return "festival"
        if row["is_basic_weekend"]:
            return "weekend"
        return "weekday"

    df_recent["day_group"] = df_recent.apply(_group_label, axis=1)

    # 연도별 월 합계 및 ratio
    df_recent["month_total"] = (
        df_recent.groupby("연도")["공급량(MJ)"].transform("sum")
    )
    df_recent["ratio"] = df_recent["공급량(MJ)"] / df_recent["month_total"]

    # ── (A) 그룹별 전체 비율 share (step1) ──
    group_share = df_recent.groupby("day_group")["ratio"].sum().to_dict()
    share_festival = float(group_share.get("festival", 0.0))
    share_weekend = float(group_share.get("weekend", 0.0))
    share_weekday = float(group_share.get("weekday", 0.0))
    # (수치 오차 보정)
    total_share = share_festival + share_weekend + share_weekday
    if total_share > 0:
        share_festival /= total_share
        share_weekend /= total_share
        share_weekday = 1.0 - share_festival - share_weekend

    # ── (B) 그룹 내부 패턴 (step2) ──
    # 1) 평일 패턴: 같은 월 평일만 대상으로 일자별 평균, 요일별 평균
    mask_weekday = df_recent["day_group"] == "weekday"
    weekday_df_recent = df_recent[mask_weekday].copy()

    if not weekday_df_recent.empty:
        ratio_by_day_weekday = weekday_df_recent.groupby("일")["ratio"].mean()
        ratio_weekday_by_dow = weekday_df_recent.groupby("weekday_idx")["ratio"].mean()
    else:
        ratio_by_day_weekday = pd.Series(dtype=float)
        ratio_weekday_by_dow = pd.Series(dtype=float)

    ratio_by_day_weekday_dict = ratio_by_day_weekday.to_dict()
    ratio_weekday_by_dow_dict = ratio_weekday_by_dow.to_dict()

    # 2) 주말/공휴일 패턴 (명절 제외): (요일, nth_dow)
    mask_weekend = df_recent["day_group"] == "weekend"
    weekend_df_recent = df_recent[mask_weekend].copy()

    if not weekend_df_recent.empty:
        weekend_df_recent = weekend_df_recent.sort_values(["연도", "일"])
        weekend_df_recent["nth_dow"] = (
            weekend_df_recent.groupby(["연도", "weekday_idx"]).cumcount() + 1
        )

        ratio_weekend_group = weekend_df_recent.groupby(
            ["weekday_idx", "nth_dow"]
        )["ratio"].mean()
        ratio_weekend_by_dow = weekend_df_recent.groupby("weekday_idx")["ratio"].mean()
    else:
        ratio_weekend_group = pd.Series(dtype=float)
        ratio_weekend_by_dow = pd.Series(dtype=float)

    ratio_weekend_group_dict = ratio_weekend_group.to_dict()
    ratio_weekend_by_dow_dict = ratio_weekend_by_dow.to_dict()

    # 3) 명절 블록 패턴: offset(-1,0,+1)별 평균
    mask_festival = df_recent["day_group"] == "festival"
    festival_df_recent = df_recent[mask_festival].copy()

    if not festival_df_recent.empty:
        ratio_festival_by_offset = (
            festival_df_recent.groupby("festival_offset")["ratio"].mean().to_dict()
        )
        ratio_festival_global = float(festival_df_recent["ratio"].mean())
    else:
        ratio_festival_by_offset = {}
        ratio_festival_global = None

    # ─────────────────────────────────────────
    # 대상 연·월 날짜 생성
    # ─────────────────────────────────────────
    last_day = calendar.monthrange(target_year, target_month)[1]
    date_range = pd.date_range(
        f"{target_year}-{target_month:02d}-01", periods=last_day, freq="D"
    )

    df_target = pd.DataFrame({"일자": date_range})
    df_target["연"] = target_year
    df_target["월"] = target_month
    df_target["일"] = df_target["일자"].dt.day
    df_target["weekday_idx"] = df_target["일자"].dt.weekday

    # 캘린더 붙이기 (대상월)
    if cal_df is not None:
        df_target = df_target.merge(
            cal_df,
            on="일자",
            how="left",
        )
        df_target["공휴일여부"] = df_target["공휴일여부"].fillna(False).astype(bool)
        df_target["명절여부"] = df_target["명절여부"].fillna(False).astype(bool)
    else:
        df_target["공휴일여부"] = False
        df_target["명절여부"] = False

    # 명절 블록/offset 계산 (대상연도)
    df_target = df_target.sort_values("일자").copy()

    def _mark_festival_target(group: pd.DataFrame) -> pd.DataFrame:
        is_center = group["명절여부"].fillna(False)
        prev_center = is_center.shift(1, fill_value=False)
        next_center = is_center.shift(-1, fill_value=False)

        festival_block = is_center | prev_center | next_center

        offset = np.full(len(group), np.nan)
        offset[is_center.to_numpy()] = 0
        offset[(~is_center & prev_center).to_numpy()] = 1
        offset[(~is_center & next_center).to_numpy()] = -1

        group["festival_block"] = festival_block
        group["festival_offset"] = offset
        return group

    df_target = df_target.groupby("연", group_keys=False).apply(_mark_festival_target)

    df_target["is_basic_weekend"] = (
        (df_target["weekday_idx"] >= 5) | df_target["공휴일여부"]
    )

    def _group_label_target(row):
        if row["festival_block"]:
            return "festival"
        if row["is_basic_weekend"]:
            return "weekend"
        return "weekday"

    df_target["day_group"] = df_target.apply(_group_label_target, axis=1)

    weekday_names = ["월", "화", "수", "목", "금", "토", "일"]
    df_target["요일"] = df_target["weekday_idx"].map(lambda i: weekday_names[i])

    # 대상 월에서도 주말 nth_dow 계산 (weekend 그룹만)
    df_target = df_target.sort_values("일").copy()
    df_target["nth_dow"] = 0
    for dow in range(7):
        mask = (df_target["weekday_idx"] == dow) & (df_target["day_group"] == "weekend")
        df_target.loc[mask, "nth_dow"] = np.arange(mask.sum()) + 1

    # 평일/주말 표기 (명절은 주말 그룹과 같이 취급)
    def _label(row):
        return "주말" if row["day_group"] in ("weekend", "festival") else "평일"

    df_target["구분(평일/주말)"] = df_target.apply(_label, axis=1)

    # ── (C) 그룹별 raw weight 계산 ──
    df_target["raw_weekday"] = 0.0
    df_target["raw_weekend"] = 0.0
    df_target["raw_festival"] = 0.0

    # 평일 raw
    for idx, row in df_target[df_target["day_group"] == "weekday"].iterrows():
        day = row["일"]
        dow = row["weekday_idx"]
        val = ratio_by_day_weekday_dict.get(day, None)
        if val is None or pd.isna(val):
            val = ratio_weekday_by_dow_dict.get(dow, None)
        if val is None:
            val = 0.0
        df_target.at[idx, "raw_weekday"] = val

    # 주말 raw
    for idx, row in df_target[df_target["day_group"] == "weekend"].iterrows():
        dow = row["weekday_idx"]
        nth = row["nth_dow"]
        key = (dow, nth)
        val = ratio_weekend_group_dict.get(key, None)
        if val is None or pd.isna(val):
            val = ratio_weekend_by_dow_dict.get(dow, None)
        if val is None:
            val = 0.0
        df_target.at[idx, "raw_weekend"] = val

    # 명절 raw
    for idx, row in df_target[df_target["day_group"] == "festival"].iterrows():
        off = row["festival_offset"]
        if not np.isnan(off) and off in ratio_festival_by_offset:
            val = ratio_festival_by_offset[off]
        elif ratio_festival_global is not None:
            val = ratio_festival_global
        else:
            val = 0.0
        df_target.at[idx, "raw_festival"] = val

    # ── (D) 그룹별 비율 share 에 맞게 스케일링 (step3) ──
    df_target["scaled_weekday"] = 0.0
    df_target["scaled_weekend"] = 0.0
    df_target["scaled_festival"] = 0.0

    # festival
    fest_mask_t = df_target["day_group"] == "festival"
    fest_raw_sum = float(df_target.loc[fest_mask_t, "raw_festival"].sum())
    if fest_raw_sum > 0 and share_festival > 0:
        factor = share_festival / fest_raw_sum
        df_target.loc[fest_mask_t, "scaled_festival"] = (
            df_target.loc[fest_mask_t, "raw_festival"] * factor
        )

    # weekend
    wend_mask_t = df_target["day_group"] == "weekend"
    wend_raw_sum = float(df_target.loc[wend_mask_t, "raw_weekend"].sum())
    if wend_raw_sum > 0 and share_weekend > 0:
        factor = share_weekend / wend_raw_sum
        df_target.loc[wend_mask_t, "scaled_weekend"] = (
            df_target.loc[wend_mask_t, "raw_weekend"] * factor
        )

    # weekday
    wday_mask_t = df_target["day_group"] == "weekday"
    wday_raw_sum = float(df_target.loc[wday_mask_t, "raw_weekday"].sum())
    if wday_raw_sum > 0 and share_weekday > 0:
        factor = share_weekday / wday_raw_sum
        df_target.loc[wday_mask_t, "scaled_weekday"] = (
            df_target.loc[wday_mask_t, "raw_weekday"] * factor
        )

    # 최종 일별비율
    df_target["일별비율"] = (
        df_target["scaled_weekday"]
        + df_target["scaled_weekend"]
        + df_target["scaled_festival"]
    )
    total_ratio = float(df_target["일별비율"].sum())
    if total_ratio > 0:
        df_target["일별비율"] = df_target["일별비율"] / total_ratio
    else:
        df_target["일별비율"] = 1.0 / last_day

    # ── 최근 N년 기준 총·평균 공급량 ──
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
# (여기는 네가 준 기존 코드 그대로 사용)
# ─────────────────────────────────────────────
def tab_daily_monthly_compare(df: pd.DataFrame, df_temp_all: pd.DataFrame):
    # 👉 여기에는 네가 마지막에 준 tab_daily_monthly_compare 전체 코드를
    #    그대로 붙여서 사용하면 돼. 위쪽 Daily 로직과는 독립적이야.
    ...
    # (생략)


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
        # 탭1에서 보이는 상단 큰 제목
        st.title("도시가스 공급량 — 일별계획 예측")
        tab_daily_plan(df_daily=df)
    else:
        # 탭2에서 보이는 상단 큰 제목
        st.title("도시가스 공급량 — 일별 vs 월별 예측 검증")
        tab_daily_monthly_compare(df=df, df_temp_all=df_temp_all)


if __name__ == "__main__":
    main()
