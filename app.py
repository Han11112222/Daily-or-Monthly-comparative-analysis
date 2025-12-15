# app.py
import calendar
from io import BytesIO
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from openpyxl.styles import Alignment, Font


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
      df_temp_all  : 평균기온만 있어도 되는 전체 구간
    """
    excel_path = Path(__file__).parent / "공급량(일일실적).xlsx"
    df_raw = pd.read_excel(excel_path)

    df_raw = df_raw[["일자", "공급량(MJ)", "공급량(M3)", "평균기온(℃)"]].copy()
    df_raw["일자"] = pd.to_datetime(df_raw["일자"])

    df_raw["연도"] = df_raw["일자"].dt.year
    df_raw["월"] = df_raw["일자"].dt.month
    df_raw["일"] = df_raw["일자"].dt.day

    df_temp_all = df_raw.dropna(subset=["평균기온(℃)"]).copy()
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
    컬럼 : 연, 월, 계획(사업계획제출_MJ), ...
    """
    excel_path = Path(__file__).parent / "공급량(계획_실적).xlsx"
    df = pd.read_excel(excel_path, sheet_name="월별계획_실적")
    df["연"] = df["연"].astype(int)
    df["월"] = df["월"].astype(int)
    return df


@st.cache_data
def load_effective_calendar() -> pd.DataFrame | None:
    """
    effective_days_calendar.xlsx:
      - 날짜(YYYYMMDD) 필수
      - 공휴일여부(bool) / 명절여부(bool) 기본
      - (옵션) 설날여부, 추석여부, 명절구분, 대체공휴일여부 등 있으면 더 정확히 분류
    """
    excel_path = Path(__file__).parent / "effective_days_calendar.xlsx"
    if not excel_path.exists():
        return None

    df = pd.read_excel(excel_path)

    if "날짜" not in df.columns:
        return None

    df["일자"] = pd.to_datetime(df["날짜"].astype(str), format="%Y%m%d", errors="coerce")

    # 기본 컬럼 안전 생성
    for col in ["공휴일여부", "명절여부"]:
        if col not in df.columns:
            df[col] = False
        df[col] = df[col].fillna(False).astype(bool)

    # 옵션 컬럼들(있으면 사용)
    opt_cols = []
    for c in ["설날여부", "추석여부", "대체공휴일여부", "명절구분", "공휴일구분"]:
        if c in df.columns:
            opt_cols.append(c)

    keep_cols = ["일자", "공휴일여부", "명절여부"] + opt_cols
    return df[keep_cols].copy()


@st.cache_data
def load_effective_days_matrix() -> pd.DataFrame | None:
    """
    effective_days_matrix.xlsx (네가 만든 유효일수 매트릭스)
    기대 컬럼 예:
      연, 월, 월일수,
      일수_평일_1, 일수_평일_2, 일수_토요일, 일수_일요일, 일수_공휴일_대체, 일수_명절_설날, 일수_명절_추석,
      유효일수합, 적용_비율(유효/월일수)
    """
    excel_path = Path(__file__).parent / "effective_days_matrix.xlsx"
    if not excel_path.exists():
        return None
    df = pd.read_excel(excel_path)
    if "연" not in df.columns or "월" not in df.columns:
        return None
    df["연"] = df["연"].astype(int)
    df["월"] = df["월"].astype(int)
    return df.copy()


# ─────────────────────────────────────────────
# 유틸
# ─────────────────────────────────────────────
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
        if df[col].dtype == bool:
            df[col] = df[col].map(lambda x: "Y" if x else "")
            continue

        if col in percent_cols:
            df[col] = df[col].map(lambda x: f"{x:.4f}" if pd.notna(x) else "")
        elif col in temp_cols:
            df[col] = df[col].map(lambda x: f"{x:.2f}" if pd.notna(x) else "")
        elif pd.api.types.is_numeric_dtype(df[col]):
            if col in ["연", "연도", "월", "일", "월일수"]:
                df[col] = df[col].map(_fmt_no_comma)
            else:
                df[col] = df[col].map(lambda x: f"{x:,.0f}" if pd.notna(x) else "")
    return df


def center_style(df: pd.DataFrame):
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


def _format_excel_sheet(ws, freeze="A2", center=True, width_map=None):
    if freeze:
        ws.freeze_panes = freeze

    if center:
        for row in ws.iter_rows(min_row=1, max_row=ws.max_row, min_col=1, max_col=ws.max_column):
            for c in row:
                c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    if width_map:
        for col_letter, w in width_map.items():
            ws.column_dimensions[col_letter].width = w


def _week_of_month(dt_series: pd.Series) -> pd.Series:
    """
    week_of_month = 1..6 (월요일 시작 기준)
    """
    first_day = dt_series.dt.to_period("M").dt.start_time
    first_w = first_day.dt.weekday  # 0=월
    return ((dt_series.dt.day + first_w - 1) // 7) + 1


def _korean_dow_name(weekday_idx: int) -> str:
    names = ["월", "화", "수", "목", "금", "토", "일"]
    return names[int(weekday_idx)]


def _classify_weekday_group(weekday_idx: int) -> str:
    # 평일만 들어온다는 가정(0~4)
    if weekday_idx in (0, 4):
        return "평일_1(월/금)"
    return "평일_2(화/수/목)"


def _classify_effective_category(row: pd.Series) -> str:
    """
    유효일수 탭에서 사용할 상세 카테고리:
      - 평일_1(월/금), 평일_2(화/수/목), 토요일, 일요일, 공휴일_대체, 명절_설날, 명절_추석
    우선순위: 명절(설/추석) > 공휴일 > 요일(토/일) > 평일그룹
    """
    widx = int(row["weekday_idx"])
    is_hol = bool(row.get("공휴일여부", False))
    is_m = bool(row.get("명절여부", False))

    # 명절 상세(가능하면 파일 컬럼 활용)
    seollal = bool(row.get("설날여부", False))
    chuseok = bool(row.get("추석여부", False))
    if "명절구분" in row.index and pd.notna(row["명절구분"]):
        s = str(row["명절구분"])
        if "설" in s:
            seollal = True
        if "추석" in s:
            chuseok = True

    if is_m or seollal or chuseok:
        if chuseok:
            return "명절_추석"
        return "명절_설날"

    if is_hol:
        return "공휴일_대체"

    if widx == 5:
        return "토요일"
    if widx == 6:
        return "일요일"

    return _classify_weekday_group(widx)


# ─────────────────────────────────────────────
# (A) 패턴 기반 Daily 계획: 평일 2그룹 + 주말(nth_dow)
# ─────────────────────────────────────────────
def make_daily_plan_table_pattern(
    df_daily: pd.DataFrame,
    df_plan: pd.DataFrame,
    target_year: int = 2026,
    target_month: int = 1,
    recent_window: int = 3,
) -> tuple[pd.DataFrame | None, pd.DataFrame | None, list[int]]:
    cal_df = load_effective_calendar()

    all_years = sorted(df_daily["연도"].unique())
    start_year = target_year - recent_window
    recent_years = [y for y in range(start_year, target_year) if y in all_years]
    if len(recent_years) == 0:
        return None, None, []

    df_recent = df_daily[(df_daily["연도"].isin(recent_years)) & (df_daily["월"] == target_month)].copy()
    if df_recent.empty:
        return None, None, recent_years

    df_recent = df_recent.sort_values(["연도", "일"]).copy()
    df_recent["weekday_idx"] = df_recent["일자"].dt.weekday  # 0=월

    # 캘린더 merge
    if cal_df is not None:
        df_recent = df_recent.merge(cal_df, on="일자", how="left")
        for col in ["공휴일여부", "명절여부"]:
            if col not in df_recent.columns:
                df_recent[col] = False
            df_recent[col] = df_recent[col].fillna(False).astype(bool)
    else:
        df_recent["공휴일여부"] = False
        df_recent["명절여부"] = False

    df_recent["is_holiday"] = df_recent["공휴일여부"] | df_recent["명절여부"]
    df_recent["is_weekend"] = (df_recent["weekday_idx"] >= 5) | df_recent["is_holiday"]

    # 평일 2그룹
    df_recent["weekday_group"] = np.where(
        df_recent["is_weekend"],
        "주말/공휴일",
        df_recent["weekday_idx"].map(lambda x: _classify_weekday_group(int(x))),
    )

    # week_of_month (평일 학습용)
    df_recent["week_of_month"] = _week_of_month(df_recent["일자"])

    # nth_dow (주말 학습용: 토/일 중심)
    df_recent["nth_dow"] = (
        df_recent.sort_values(["연도", "일"])
        .groupby(["연도", "weekday_idx"])
        .cumcount()
        + 1
    )

    # 월합계 & ratio
    df_recent["month_total"] = df_recent.groupby("연도")["공급량(MJ)"].transform("sum")
    df_recent["ratio"] = df_recent["공급량(MJ)"] / df_recent["month_total"]

    # ── 학습 비율 사전 생성 ──────────────────────
    # 1) 평일: (weekday_group, week_of_month)
    wmask = ~df_recent["is_weekend"]
    ratio_wk_group_week = (
        df_recent[wmask].groupby(["weekday_group", "week_of_month"])["ratio"].mean()
        if df_recent[wmask].size > 0 else pd.Series(dtype=float)
    )
    ratio_wk_group_overall = (
        df_recent[wmask].groupby(["weekday_group"])["ratio"].mean()
        if df_recent[wmask].size > 0 else pd.Series(dtype=float)
    )

    # 2) 주말/공휴일: (weekday_idx, nth_dow)
    emask = df_recent["is_weekend"]
    ratio_wend_group = (
        df_recent[emask].groupby(["weekday_idx", "nth_dow"])["ratio"].mean()
        if df_recent[emask].size > 0 else pd.Series(dtype=float)
    )
    ratio_wend_dow = (
        df_recent[emask].groupby(["weekday_idx"])["ratio"].mean()
        if df_recent[emask].size > 0 else pd.Series(dtype=float)
    )

    d_wk_group_week = ratio_wk_group_week.to_dict()
    d_wk_group_overall = ratio_wk_group_overall.to_dict()
    d_wend_group = ratio_wend_group.to_dict()
    d_wend_dow = ratio_wend_dow.to_dict()

    # ── 대상월 프레임 ───────────────────────────
    last_day = calendar.monthrange(target_year, target_month)[1]
    date_range = pd.date_range(f"{target_year}-{target_month:02d}-01", periods=last_day, freq="D")

    df_target = pd.DataFrame({"일자": date_range})
    df_target["연"] = target_year
    df_target["월"] = target_month
    df_target["일"] = df_target["일자"].dt.day
    df_target["weekday_idx"] = df_target["일자"].dt.weekday
    df_target["요일"] = df_target["weekday_idx"].map(_korean_dow_name)
    df_target["week_of_month"] = _week_of_month(df_target["일자"])
    df_target["nth_dow"] = df_target.sort_values("일").groupby("weekday_idx").cumcount() + 1

    if cal_df is not None:
        df_target = df_target.merge(cal_df, on="일자", how="left")
        for col in ["공휴일여부", "명절여부"]:
            if col not in df_target.columns:
                df_target[col] = False
            df_target[col] = df_target[col].fillna(False).astype(bool)
    else:
        df_target["공휴일여부"] = False
        df_target["명절여부"] = False

    df_target["is_holiday"] = df_target["공휴일여부"] | df_target["명절여부"]
    df_target["is_weekend"] = (df_target["weekday_idx"] >= 5) | df_target["is_holiday"]

    df_target["구분(카테고리)"] = df_target.apply(
        lambda r: "주말/공휴일" if r["is_weekend"] else _classify_weekday_group(int(r["weekday_idx"])),
        axis=1
    )

    # raw 계산
    raw = []
    for _, r in df_target.iterrows():
        if bool(r["is_weekend"]):
            key = (int(r["weekday_idx"]), int(r["nth_dow"]))
            v = d_wend_group.get(key, np.nan)
            if pd.isna(v):
                v = d_wend_dow.get(int(r["weekday_idx"]), np.nan)
            raw.append(v)
        else:
            g = r["구분(카테고리)"]
            key = (g, int(r["week_of_month"]))
            v = d_wk_group_week.get(key, np.nan)
            if pd.isna(v):
                v = d_wk_group_overall.get(g, np.nan)
            raw.append(v)

    df_target["raw"] = raw

    # NaN 채우기(카테고리 평균 → 전체 평균)
    if df_target["raw"].notna().any():
        overall_mean = df_target["raw"].dropna().mean()
        df_target["raw"] = df_target.groupby("구분(카테고리)")["raw"].transform(
            lambda s: s.fillna(s.dropna().mean() if s.notna().any() else overall_mean)
        )
        df_target["raw"] = df_target["raw"].fillna(overall_mean)
    else:
        df_target["raw"] = 1.0

    # 정규화
    s = df_target["raw"].sum()
    if s <= 0:
        df_target["일별비율"] = 1.0 / last_day
    else:
        df_target["일별비율"] = df_target["raw"] / s

    # 최근 N년 총/평균(비율로 배분)
    month_total_all = df_recent["공급량(MJ)"].sum()
    df_target["최근N년_총공급량(MJ)"] = df_target["일별비율"] * month_total_all
    df_target["최근N년_평균공급량(MJ)"] = df_target["최근N년_총공급량(MJ)"] / len(recent_years)

    # 월 계획총량
    row_plan = df_plan[(df_plan["연"] == target_year) & (df_plan["월"] == target_month)]
    plan_total = float(row_plan["계획(사업계획제출_MJ)"].iloc[0]) if not row_plan.empty else np.nan

    df_target["예상공급량(MJ)"] = (df_target["일별비율"] * plan_total).round(0)

    df_target = df_target.sort_values("일").reset_index(drop=True)

    df_result = df_target[
        [
            "연", "월", "일", "일자", "요일",
            "구분(카테고리)", "공휴일여부", "명절여부",
            "최근N년_평균공급량(MJ)", "최근N년_총공급량(MJ)",
            "일별비율", "예상공급량(MJ)",
        ]
    ].copy()

    df_mat = (
        df_recent.pivot_table(index="일", columns="연도", values="공급량(MJ)", aggfunc="sum")
        .sort_index()
        .sort_index(axis=1)
    )

    return df_result, df_mat, recent_years


# ─────────────────────────────────────────────
# (B) 유효일수 기반 Daily 계획: 가중치로 일별비율 생성
# ─────────────────────────────────────────────
DEFAULT_WEIGHTS = {
    "평일_1(월/금)": 1.000,
    "평일_2(화/수/목)": 0.971,
    "토요일": 0.857,
    "일요일": 0.765,
    "공휴일_대체": 0.841,
    "명절_설날": 0.838,
    "명절_추석": 0.799,
}


def make_daily_plan_table_effective(
    df_daily: pd.DataFrame,
    df_plan: pd.DataFrame,
    target_year: int = 2026,
    target_month: int = 1,
    recent_window: int = 3,
    weights: dict[str, float] | None = None,
) -> tuple[pd.DataFrame | None, pd.DataFrame | None, list[int], pd.DataFrame | None]:
    if weights is None:
        weights = DEFAULT_WEIGHTS.copy()

    cal_df = load_effective_calendar()

    all_years = sorted(df_daily["연도"].unique())
    start_year = target_year - recent_window
    recent_years = [y for y in range(start_year, target_year) if y in all_years]
    if len(recent_years) == 0:
        return None, None, [], None

    df_recent = df_daily[(df_daily["연도"].isin(recent_years)) & (df_daily["월"] == target_month)].copy()
    if df_recent.empty:
        return None, None, recent_years, None

    df_recent = df_recent.sort_values(["연도", "일"]).copy()

    # 대상월 생성
    last_day = calendar.monthrange(target_year, target_month)[1]
    date_range = pd.date_range(f"{target_year}-{target_month:02d}-01", periods=last_day, freq="D")

    df_target = pd.DataFrame({"일자": date_range})
    df_target["연"] = target_year
    df_target["월"] = target_month
    df_target["일"] = df_target["일자"].dt.day
    df_target["weekday_idx"] = df_target["일자"].dt.weekday
    df_target["요일"] = df_target["weekday_idx"].map(_korean_dow_name)

    # 캘린더 merge
    if cal_df is not None:
        df_target = df_target.merge(cal_df, on="일자", how="left")
        for col in ["공휴일여부", "명절여부"]:
            if col not in df_target.columns:
                df_target[col] = False
            df_target[col] = df_target[col].fillna(False).astype(bool)
    else:
        df_target["공휴일여부"] = False
        df_target["명절여부"] = False

    df_target["구분(카테고리)"] = df_target.apply(_classify_effective_category, axis=1)
    df_target["유효가중치"] = df_target["구분(카테고리)"].map(lambda k: float(weights.get(k, 0.0)))

    # 월 가중치 합
    wsum = float(df_target["유효가중치"].sum())
    if wsum <= 0:
        df_target["일별비율"] = 1.0 / last_day
    else:
        df_target["일별비율"] = df_target["유효가중치"] / wsum

    # 최근 N년 총/평균(비율로 배분)
    month_total_all = df_recent["공급량(MJ)"].sum()
    df_target["최근N년_총공급량(MJ)"] = df_target["일별비율"] * month_total_all
    df_target["최근N년_평균공급량(MJ)"] = df_target["최근N년_총공급량(MJ)"] / len(recent_years)

    # 월 계획총량
    row_plan = df_plan[(df_plan["연"] == target_year) & (df_plan["월"] == target_month)]
    plan_total = float(row_plan["계획(사업계획제출_MJ)"].iloc[0]) if not row_plan.empty else np.nan
    df_target["예상공급량(MJ)"] = (df_target["일별비율"] * plan_total).round(0)

    df_result = df_target[
        [
            "연", "월", "일", "일자", "요일",
            "구분(카테고리)", "유효가중치",
            "공휴일여부", "명절여부",
            "최근N년_평균공급량(MJ)", "최근N년_총공급량(MJ)",
            "일별비율", "예상공급량(MJ)",
        ]
    ].copy()

    df_mat = (
        df_recent.pivot_table(index="일", columns="연도", values="공급량(MJ)", aggfunc="sum")
        .sort_index()
        .sort_index(axis=1)
    )

    # (옵션) matrix 요약표도 같이 보여주기
    mx = load_effective_days_matrix()
    mx_row = None
    if mx is not None:
        mx_row = mx[(mx["연"] == target_year) & (mx["월"] == target_month)].copy()
        if mx_row.empty:
            mx_row = None

    return df_result, df_mat, recent_years, mx_row


# ─────────────────────────────────────────────
# 공통 렌더링(표/그래프/매트릭스/요약/엑셀다운)
# ─────────────────────────────────────────────
def _render_daily_plan_ui(
    df_result: pd.DataFrame,
    df_mat: pd.DataFrame | None,
    recent_years: list[int],
    target_year: int,
    target_month: int,
    recent_window: int,
    plan_total_raw: float | np.floating | None,
    mode_name: str,
):
    st.markdown("#### 1. 일별 비율·예상 공급량 테이블")

    view = df_result.copy()

    total_row = {
        "연": "",
        "월": "",
        "일": "",
        "일자": "",
        "요일": "합계",
        "구분(카테고리)": "",
        "유효가중치": view["유효가중치"].sum() if "유효가중치" in view.columns else "",
        "공휴일여부": False,
        "명절여부": False,
        "최근N년_평균공급량(MJ)": view["최근N년_평균공급량(MJ)"].sum(),
        "최근N년_총공급량(MJ)": view["최근N년_총공급량(MJ)"].sum(),
        "일별비율": view["일별비율"].sum(),
        "예상공급량(MJ)": view["예상공급량(MJ)"].sum(),
    }
    view_with_total = pd.concat([view, pd.DataFrame([total_row])], ignore_index=True)

    # 표시 컬럼 구성
    cols = [
        "연", "월", "일", "요일", "구분(카테고리)",
        "공휴일여부", "명절여부",
        "최근N년_평균공급량(MJ)", "최근N년_총공급량(MJ)",
        "일별비율", "예상공급량(MJ)",
    ]
    if "유효가중치" in view_with_total.columns:
        cols.insert(5, "유효가중치")

    view_for_format = view_with_total[cols].copy()
    view_for_format = format_table_generic(view_for_format, percent_cols=["일별비율"])
    st.table(center_style(view_for_format))

    # ── 그래프 ─────────────────────────────────
    st.markdown("#### 2. 일별 예상 공급량 & 비율 그래프")

    fig = go.Figure()

    # 바: 카테고리별 분리
    cat_order = [
        "평일_1(월/금)", "평일_2(화/수/목)",
        "토요일", "일요일", "공휴일_대체", "명절_설날", "명절_추석",
        "주말/공휴일",
    ]
    cats = [c for c in cat_order if c in view["구분(카테고리)"].unique()]
    # 혹시 새로운 값이 있으면 뒤에 붙임
    for c in sorted(set(view["구분(카테고리)"].unique()) - set(cats)):
        cats.append(c)

    for c in cats:
        sub = view[view["구분(카테고리)"] == c]
        fig.add_bar(
            x=sub["일"],
            y=sub["예상공급량(MJ)"],
            name=c,
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
        title=f"{target_year}년 {target_month}월 일별 공급량 계획 ({mode_name})",
        xaxis_title="일",
        yaxis=dict(title="예상 공급량 (MJ)"),
        yaxis2=dict(title="일별비율", overlaying="y", side="right"),
        barmode="group",
        margin=dict(l=20, r=20, t=60, b=40),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
    )
    st.plotly_chart(fig, use_container_width=True)

    # ── 매트릭스 ───────────────────────────────
    st.markdown("#### 3. 최근 N년 일별 실적 매트릭스")
    if df_mat is not None and not df_mat.empty:
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

    # ── 요약 ───────────────────────────────────
    st.markdown("#### 4. 카테고리 비중 요약")
    summary = (
        view.groupby("구분(카테고리)", as_index=False)[["일별비율", "예상공급량(MJ)"]]
        .sum()
        .rename(columns={"일별비율": "일별비율합계"})
    )
    total_row_sum = {
        "구분(카테고리)": "합계",
        "일별비율합계": summary["일별비율합계"].sum(),
        "예상공급량(MJ)": summary["예상공급량(MJ)"].sum(),
    }
    summary = pd.concat([summary, pd.DataFrame([total_row_sum])], ignore_index=True)
    summary = format_table_generic(summary, percent_cols=["일별비율합계"])
    st.table(center_style(summary))

    # ── 엑셀 다운로드(월) ───────────────────────
    st.markdown("#### 5. 일별 계획 엑셀 다운로드")

    buffer = BytesIO()
    sheet_name = f"{target_year}_{target_month:02d}_일별계획"
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        view_with_total.to_excel(writer, index=False, sheet_name=sheet_name)

        # INPUT 시트(간단)
        wb = writer.book
        ws_in = wb.create_sheet("INPUT")
        ws_in["A1"] = "항목"
        ws_in["B1"] = "값"
        ws_in["C1"] = "비고"
        for cell in ("A1", "B1", "C1"):
            ws_in[cell].font = Font(bold=True)

        rows = [
            ("대상연도", target_year, ""),
            ("대상월", target_month, ""),
            ("최근N년(설정)", recent_window, ""),
            ("실제 사용된 연도", ", ".join([str(y) for y in recent_years]), ""),
            ("월 계획총량(MJ) (사업계획제출)", plan_total_raw if plan_total_raw is not None else "", "공급량(계획_실적).xlsx → 월별계획_실적"),
            ("모드", mode_name, ""),
        ]
        r0 = 2
        for i, (k, v, note) in enumerate(rows):
            rr = r0 + i
            ws_in.cell(rr, 1, k)
            ws_in.cell(rr, 2, v)
            ws_in.cell(rr, 3, note)

        _format_excel_sheet(
            wb[sheet_name],
            freeze="A2",
            center=True,
            width_map={
                "A": 6, "B": 4, "C": 4, "D": 14, "E": 6, "F": 18,
                "G": 12, "H": 12, "I": 20, "J": 20, "K": 12, "L": 18, "M": 18,
            },
        )
        _format_excel_sheet(ws_in, freeze="A2", center=True, width_map={"A": 22, "B": 28, "C": 50})

        # 헤더 bold
        ws_main = wb[sheet_name]
        for c in range(1, ws_main.max_column + 1):
            ws_main.cell(1, c).font = Font(bold=True)

    st.download_button(
        label=f"📥 {target_year}년 {target_month}월 일별공급계획 다운로드 (Excel)",
        data=buffer.getvalue(),
        file_name=f"{target_year}_{target_month:02d}_일별공급계획.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


def _build_year_daily_plan(
    df_daily: pd.DataFrame,
    df_plan: pd.DataFrame,
    target_year: int,
    recent_window: int,
    mode: str,
    weights: dict[str, float] | None = None,
):
    cal_df = load_effective_calendar()

    all_rows = []
    month_summary_rows = []

    for m in range(1, 13):
        row_plan = df_plan[(df_plan["연"] == target_year) & (df_plan["월"] == m)]
        plan_total = float(row_plan["계획(사업계획제출_MJ)"].iloc[0]) if not row_plan.empty else np.nan

        if mode == "pattern":
            df_res, _, used_years = make_daily_plan_table_pattern(
                df_daily=df_daily, df_plan=df_plan, target_year=target_year, target_month=m, recent_window=recent_window
            )
        else:
            df_res, _, used_years, _ = make_daily_plan_table_effective(
                df_daily=df_daily, df_plan=df_plan, target_year=target_year, target_month=m,
                recent_window=recent_window, weights=weights
            )

        if df_res is None:
            # fallback: 균등분배
            last_day = calendar.monthrange(target_year, m)[1]
            dr = pd.date_range(f"{target_year}-{m:02d}-01", periods=last_day, freq="D")
            tmp = pd.DataFrame({"일자": dr})
            tmp["연"] = target_year
            tmp["월"] = m
            tmp["일"] = tmp["일자"].dt.day
            tmp["weekday_idx"] = tmp["일자"].dt.weekday
            tmp["요일"] = tmp["weekday_idx"].map(_korean_dow_name)

            if cal_df is not None:
                tmp = tmp.merge(cal_df, on="일자", how="left")
                for col in ["공휴일여부", "명절여부"]:
                    if col not in tmp.columns:
                        tmp[col] = False
                    tmp[col] = tmp[col].fillna(False).astype(bool)
            else:
                tmp["공휴일여부"] = False
                tmp["명절여부"] = False

            if mode == "pattern":
                tmp["구분(카테고리)"] = tmp.apply(
                    lambda r: "주말/공휴일"
                    if ((int(r["weekday_idx"]) >= 5) or bool(r["공휴일여부"]) or bool(r["명절여부"]))
                    else _classify_weekday_group(int(r["weekday_idx"])),
                    axis=1
                )
            else:
                tmp["구분(카테고리)"] = tmp.apply(_classify_effective_category, axis=1)
                tmp["유효가중치"] = tmp["구분(카테고리)"].map(lambda k: float((weights or DEFAULT_WEIGHTS).get(k, 0.0)))

            tmp["일별비율"] = 1.0 / last_day
            tmp["최근N년_총공급량(MJ)"] = np.nan
            tmp["최근N년_평균공급량(MJ)"] = np.nan
            tmp["예상공급량(MJ)"] = (tmp["일별비율"] * plan_total).round(0) if pd.notna(plan_total) else np.nan

            base_cols = [
                "연", "월", "일", "일자", "요일",
                "구분(카테고리)", "공휴일여부", "명절여부",
                "최근N년_평균공급량(MJ)", "최근N년_총공급량(MJ)",
                "일별비율", "예상공급량(MJ)",
            ]
            if mode != "pattern":
                base_cols.insert(6, "유효가중치")

            df_res = tmp[base_cols].copy()

        all_rows.append(df_res)

        month_summary_rows.append({"월": m, "월간 계획(MJ)": plan_total})

    df_year = pd.concat(all_rows, ignore_index=True).sort_values(["월", "일"]).reset_index(drop=True)

    total_row = {
        "연": "",
        "월": "",
        "일": "",
        "일자": "",
        "요일": "합계",
        "구분(카테고리)": "",
        "유효가중치": df_year["유효가중치"].sum() if "유효가중치" in df_year.columns else "",
        "공휴일여부": False,
        "명절여부": False,
        "최근N년_평균공급량(MJ)": df_year["최근N년_평균공급량(MJ)"].sum(skipna=True),
        "최근N년_총공급량(MJ)": df_year["최근N년_총공급량(MJ)"].sum(skipna=True),
        "일별비율": df_year["일별비율"].sum(skipna=True),
        "예상공급량(MJ)": df_year["예상공급량(MJ)"].sum(skipna=True),
    }
    df_year_with_total = pd.concat([df_year, pd.DataFrame([total_row])], ignore_index=True)

    df_month_sum = pd.DataFrame(month_summary_rows).sort_values("월").reset_index(drop=True)
    df_month_sum_total = pd.DataFrame([{"월": "소계", "월간 계획(MJ)": df_month_sum["월간 계획(MJ)"].sum(skipna=True)}])
    df_month_sum = pd.concat([df_month_sum, df_month_sum_total], ignore_index=True)

    return df_year_with_total, df_month_sum


# ─────────────────────────────────────────────
# 탭: Daily 공급량 분석(패턴 기반)
# ─────────────────────────────────────────────
def tab_daily_plan_pattern(df_daily: pd.DataFrame):
    st.subheader("📅 Daily 공급량 분석 — 최근 N년 패턴 기반 (평일 2그룹 + 주말 nth_dow)")

    df_plan = load_monthly_plan()

    years_plan = sorted(df_plan["연"].unique())
    default_year_idx = years_plan.index(2026) if 2026 in years_plan else len(years_plan) - 1

    col_y, col_m, _ = st.columns([1, 1, 2])
    with col_y:
        target_year = st.selectbox("계획 연도 선택", years_plan, index=default_year_idx, key="pat_year")
    with col_m:
        months_plan = sorted(df_plan[df_plan["연"] == target_year]["월"].unique())
        default_month_idx = months_plan.index(1) if 1 in months_plan else 0
        target_month = st.selectbox("계획 월 선택", months_plan, index=default_month_idx, format_func=lambda m: f"{m}월", key="pat_month")

    all_years = sorted(df_daily["연도"].unique())
    hist_years = [y for y in all_years if y < target_year]
    if len(hist_years) < 1:
        st.warning("해당 연도는 직전 연도가 없어 최근 N년 분석을 할 수 없어.")
        return

    slider_min = 1
    slider_max = min(10, len(hist_years))
    col_slider, _ = st.columns([2, 3])
    with col_slider:
        recent_window = st.slider(
            "최근 몇 년 평균으로 비율을 계산할까?",
            min_value=slider_min,
            max_value=slider_max,
            value=min(3, slider_max),
            step=1,
            key="pat_recent",
        )

    st.caption(
        f"최근 {recent_window}년 ({target_year-recent_window}년 ~ {target_year-1}년) "
        f"{target_month}월 데이터를 사용. "
        f"평일은 (월/금) vs (화/수/목)로 나누고, 주말/공휴일은 '요일+n번째' 패턴을 사용해."
    )

    df_result, df_mat, recent_years = make_daily_plan_table_pattern(
        df_daily=df_daily,
        df_plan=df_plan,
        target_year=int(target_year),
        target_month=int(target_month),
        recent_window=int(recent_window),
    )
    if df_result is None or len(recent_years) == 0:
        st.warning("해당 연도/월에 대해 선택한 최근 N년 기준으로 계산할 수 있는 데이터가 없어.")
        return

    st.markdown(f"- 실제 사용된 과거 연도: {min(recent_years)}년 ~ {max(recent_years)}년 (총 {len(recent_years)}개)")

    row_plan = df_plan[(df_plan["연"] == target_year) & (df_plan["월"] == target_month)]
    plan_total_raw = float(row_plan["계획(사업계획제출_MJ)"].iloc[0]) if not row_plan.empty else None

    plan_total_sum = float(df_result["예상공급량(MJ)"].sum())
    st.markdown(f"**{target_year}년 {target_month}월 사업계획 제출 공급량 합계:** `{plan_total_sum:,.0f} MJ`")

    _render_daily_plan_ui(
        df_result=df_result,
        df_mat=df_mat,
        recent_years=recent_years,
        target_year=int(target_year),
        target_month=int(target_month),
        recent_window=int(recent_window),
        plan_total_raw=plan_total_raw,
        mode_name="패턴 기반(평일 2그룹 + 주말 nth_dow)",
    )

    # 연간 다운로드
    st.markdown("#### 6. 일일계획 다운로드(연간)")
    col_ay, col_btn = st.columns([1, 3])
    with col_ay:
        annual_year = st.selectbox("연간 계획 연도 선택", years_plan, index=years_plan.index(target_year), key="pat_annual_year")
    with col_btn:
        st.caption("선택한 연도(1/1~12/31) 일별계획을 '연간' 시트로, '월 요약 계획' 시트에 월합계를 내려받을 수 있어.")

    buffer_year = BytesIO()
    df_year_daily, df_month_summary = _build_year_daily_plan(
        df_daily=df_daily,
        df_plan=df_plan,
        target_year=int(annual_year),
        recent_window=int(recent_window),
        mode="pattern",
        weights=None,
    )

    with pd.ExcelWriter(buffer_year, engine="openpyxl") as writer:
        df_year_daily.to_excel(writer, index=False, sheet_name="연간")
        df_month_summary.to_excel(writer, index=False, sheet_name="월 요약 계획")

        wb = writer.book
        ws_y = wb["연간"]
        ws_m = wb["월 요약 계획"]

        _format_excel_sheet(ws_y, freeze="A2", center=True, width_map={"A": 6, "B": 4, "C": 4, "D": 14, "E": 6, "F": 18, "G": 12, "H": 12, "I": 20, "J": 20, "K": 12, "L": 18, "M": 18})
        _format_excel_sheet(ws_m, freeze="A2", center=True, width_map={"A": 10, "B": 18})

        for c in range(1, ws_y.max_column + 1):
            ws_y.cell(1, c).font = Font(bold=True)
        for c in range(1, ws_m.max_column + 1):
            ws_m.cell(1, c).font = Font(bold=True)

    st.download_button(
        label=f"📥 {annual_year}년 연간 일별공급계획 다운로드 (Excel)",
        data=buffer_year.getvalue(),
        file_name=f"{annual_year}_연간_일별공급계획(패턴).xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key="pat_download_annual_excel",
    )


# ─────────────────────────────────────────────
# 탭: 유효일수 사용
# ─────────────────────────────────────────────
def tab_daily_plan_effective(df_daily: pd.DataFrame):
    st.subheader("📅 유효일수 사용 — 카테고리 가중치 기반 일별 계획 (effective_days_matrix.xlsx 참고)")

    df_plan = load_monthly_plan()

    years_plan = sorted(df_plan["연"].unique())
    default_year_idx = years_plan.index(2026) if 2026 in years_plan else len(years_plan) - 1

    col_y, col_m, _ = st.columns([1, 1, 2])
    with col_y:
        target_year = st.selectbox("계획 연도 선택", years_plan, index=default_year_idx, key="eff_year")
    with col_m:
        months_plan = sorted(df_plan[df_plan["연"] == target_year]["월"].unique())
        default_month_idx = months_plan.index(1) if 1 in months_plan else 0
        target_month = st.selectbox("계획 월 선택", months_plan, index=default_month_idx, format_func=lambda m: f"{m}월", key="eff_month")

    all_years = sorted(df_daily["연도"].unique())
    hist_years = [y for y in all_years if y < target_year]
    if len(hist_years) < 1:
        st.warning("해당 연도는 직전 연도가 없어 최근 N년 분석을 할 수 없어.")
        return

    slider_min = 1
    slider_max = min(10, len(hist_years))

    col_slider, col_w = st.columns([2, 2])
    with col_slider:
        recent_window = st.slider(
            "최근 몇 년 합계(참고용: 최근N년 총/평균 계산)",
            min_value=slider_min,
            max_value=slider_max,
            value=min(3, slider_max),
            step=1,
            key="eff_recent",
        )

    # 가중치 조정 UI
    st.markdown("##### 카테고리 가중치(유효일수) 설정")
    w = {}
    with col_w:
        for k, v in DEFAULT_WEIGHTS.items():
            w[k] = st.number_input(k, value=float(v), step=0.001, format="%.3f", key=f"w_{k}")

    mx = load_effective_days_matrix()
    if mx is None:
        st.caption("effective_days_matrix.xlsx 파일이 없거나 포맷이 달라서, 매트릭스 요약표는 표시 못해.")
    else:
        mx_row = mx[(mx["연"] == int(target_year)) & (mx["월"] == int(target_month))].copy()
        if not mx_row.empty:
            st.markdown("##### (참고) 유효일수 매트릭스 요약")
            mx_show = mx_row.copy()
            mx_show = format_table_generic(mx_show, percent_cols=["적용_비율(유효/월일수)"])
            st.table(center_style(mx_show))

    st.caption(
        "이 탭은 최근 N년의 '일자별 패턴'을 직접 학습하지 않고, "
        "각 날짜의 카테고리(평일1/평일2/토/일/공휴일/명절)에 부여한 가중치로 일별비율을 만들고 "
        "월 계획총량을 배분해."
    )

    df_result, df_mat, recent_years, mx_row = make_daily_plan_table_effective(
        df_daily=df_daily,
        df_plan=df_plan,
        target_year=int(target_year),
        target_month=int(target_month),
        recent_window=int(recent_window),
        weights=w,
    )
    if df_result is None or len(recent_years) == 0:
        st.warning("해당 연도/월에 대해 계산할 수 있는 데이터가 없어.")
        return

    st.markdown(f"- 실제 사용된 과거 연도: {min(recent_years)}년 ~ {max(recent_years)}년 (총 {len(recent_years)}개)")

    row_plan = df_plan[(df_plan["연"] == target_year) & (df_plan["월"] == target_month)]
    plan_total_raw = float(row_plan["계획(사업계획제출_MJ)"].iloc[0]) if not row_plan.empty else None

    plan_total_sum = float(df_result["예상공급량(MJ)"].sum())
    st.markdown(f"**{target_year}년 {target_month}월 사업계획 제출 공급량 합계:** `{plan_total_sum:,.0f} MJ`")

    _render_daily_plan_ui(
        df_result=df_result,
        df_mat=df_mat,
        recent_years=recent_years,
        target_year=int(target_year),
        target_month=int(target_month),
        recent_window=int(recent_window),
        plan_total_raw=plan_total_raw,
        mode_name="유효일수(가중치) 기반",
    )

    # 연간 다운로드(유효일수)
    st.markdown("#### 6. 일일계획 다운로드(연간)")
    col_ay, col_btn = st.columns([1, 3])
    with col_ay:
        annual_year = st.selectbox("연간 계획 연도 선택", years_plan, index=years_plan.index(target_year), key="eff_annual_year")
    with col_btn:
        st.caption("선택한 연도(1/1~12/31) 일별계획을 '연간' 시트로, '월 요약 계획' 시트에 월합계를 내려받을 수 있어.")

    buffer_year = BytesIO()
    df_year_daily, df_month_summary = _build_year_daily_plan(
        df_daily=df_daily,
        df_plan=df_plan,
        target_year=int(annual_year),
        recent_window=int(recent_window),
        mode="effective",
        weights=w,
    )

    with pd.ExcelWriter(buffer_year, engine="openpyxl") as writer:
        df_year_daily.to_excel(writer, index=False, sheet_name="연간")
        df_month_summary.to_excel(writer, index=False, sheet_name="월 요약 계획")

        wb = writer.book
        ws_y = wb["연간"]
        ws_m = wb["월 요약 계획"]

        _format_excel_sheet(ws_y, freeze="A2", center=True, width_map={"A": 6, "B": 4, "C": 4, "D": 14, "E": 6, "F": 18, "G": 10, "H": 12, "I": 12, "J": 20, "K": 20, "L": 12, "M": 18, "N": 18})
        _format_excel_sheet(ws_m, freeze="A2", center=True, width_map={"A": 10, "B": 18})

        for c in range(1, ws_y.max_column + 1):
            ws_y.cell(1, c).font = Font(bold=True)
        for c in range(1, ws_m.max_column + 1):
            ws_m.cell(1, c).font = Font(bold=True)

    st.download_button(
        label=f"📥 {annual_year}년 연간 일별공급계획 다운로드 (Excel)",
        data=buffer_year.getvalue(),
        file_name=f"{annual_year}_연간_일별공급계획(유효일수).xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key="eff_download_annual_excel",
    )


# ─────────────────────────────────────────────
# 탭: Daily·Monthly 공급량 비교 (원 코드 유지)
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

    r2 = np.nan if ss_tot == 0 else 1 - ss_res / ss_tot
    return coef, y_pred, r2


def plot_poly_fit(x, y, coef, title, x_label, y_label):
    x = np.asarray(x, dtype="float64")
    y = np.asarray(y, dtype="float64")

    x_grid = np.linspace(x.min(), x.max(), 200)
    y_grid = np.polyval(coef, x_grid)

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=x, y=y, mode="markers", name="실적"))
    fig.add_trace(go.Scatter(x=x_grid, y=y_grid, mode="lines", name="3차 다항식 예측"))
    fig.update_layout(title=title, xaxis_title=x_label, yaxis_title=y_label, margin=dict(l=20, r=20, t=40, b=20))
    return fig


def tab_daily_monthly_compare(df: pd.DataFrame, df_temp_all: pd.DataFrame):
    min_year_model = int(df["연도"].min())
    max_year_model = int(df["연도"].max())

    min_year_temp = int(df_temp_all["연도"].min())
    max_year_temp = int(df_temp_all["연도"].max())

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
            text = corr.round(2).astype(str).values

            fig_corr = go.Figure(
                data=go.Heatmap(
                    z=z_display,
                    x=corr.columns,
                    y=corr.index,
                    colorscale="RdBu_r",
                    zmin=-0.7,
                    zmax=0.7,
                    zmid=0,
                    colorbar_title="상관계수",
                    text=text,
                    texttemplate="%{text}",
                )
            )
            fig_corr.update_layout(
                xaxis_title="변수",
                yaxis_title="변수",
                xaxis=dict(side="top", tickangle=45),
                yaxis=dict(autorange="reversed"),
                width=600,
                height=600,
                margin=dict(l=80, r=20, t=80, b=80),
            )

            target_col = None
            for c in num_cols:
                if "공급량" in str(c):
                    target_col = c
                    break
            if target_col is None:
                target_col = num_cols[0]

            if target_col in corr.columns:
                target_series = corr[target_col].drop(target_col)
                target_series = target_series.reindex(target_series.abs().sort_values(ascending=False).index)

                tbl_df = target_series.to_frame(name="상관계수")
                tbl_df_disp = tbl_df.copy()
                tbl_df_disp["상관계수"] = tbl_df_disp["상관계수"].map(lambda x: f"{x:.2f}")

                col_hm, col_tbl = st.columns([3, 2])
                with col_hm:
                    st.plotly_chart(fig_corr, use_container_width=True)
                with col_tbl:
                    st.markdown(f"**기준 변수: `{target_col}` 과(와) 다른 변수들의 상관계수**")
                    st.table(center_style(tbl_df_disp))
        else:
            st.caption("숫자 컬럼이 2개 미만이라 상관도 분석을 할 수 없어.")

    st.subheader("📚 ① 데이터 학습기간 선택 (3차 다항식 R² 계산용)")
    train_default_start = max(min_year_model, max_year_model - 4)
    col_train, _ = st.columns([1, 1])
    with col_train:
        train_start, train_end = st.slider(
            "학습에 사용할 연도 범위",
            min_value=min_year_model,
            max_value=max_year_model,
            value=(train_default_start, max_year_model),
            step=1,
        )
    st.caption(f"현재 학습 구간: **{train_start}년 ~ {train_end}년**")
    df_window = df[df["연도"].between(train_start, train_end)].copy()

    df_month = (
        df_window.groupby(["연도", "월"], as_index=False)
        .agg(공급량_MJ=("공급량(MJ)", "sum"), 평균기온=("평균기온(℃)", "mean"))
    )

    coef_m, y_pred_m, r2_m = fit_poly3_and_r2(df_month["평균기온"], df_month["공급량_MJ"])
    df_month["예측공급량_MJ"] = y_pred_m if y_pred_m is not None else np.nan

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
            st.plotly_chart(
                plot_poly_fit(
                    df_month["평균기온"], df_month["공급량_MJ"], coef_m,
                    title="월단위: 월평균 기온 vs 월별 공급량(MJ)",
                    x_label="월평균 기온 (℃)", y_label="월별 공급량 합계 (MJ)",
                ),
                use_container_width=True,
            )
    with col4:
        if coef_d is not None:
            st.plotly_chart(
                plot_poly_fit(
                    df_window["평균기온(℃)"], df_window["공급량(MJ)"], coef_d,
                    title="일단위: 일평균 기온 vs 일별 공급량(MJ)",
                    x_label="일평균 기온 (℃)", y_label="일별 공급량 (MJ)",
                ),
                use_container_width=True,
            )

    st.subheader("🧊 ② 기온 시나리오 연도 범위 선택 (월평균 vs 일평균 예측 비교용)")
    scen_default_start = max(min_year_temp, max_year_temp - 4)
    col_scen, _ = st.columns([1, 1])
    with col_scen:
        scen_start, scen_end = st.slider(
            "기온 시나리오에 사용할 연도 범위",
            min_value=min_year_temp,
            max_value=max_year_temp,
            value=(scen_default_start, max_year_temp),
            step=1,
        )
    st.caption(f"선택한 기온 시나리오 연도: **{scen_start}년 ~ {scen_end}년** (각 월별 평균기온 사용)")

    df_scen = df_temp_all[df_temp_all["연도"].between(scen_start, scen_end)].copy()
    if df_scen.empty:
        st.write("선택한 기온 시나리오 구간에 데이터가 없어.")
        return

    temp_month = df_scen.groupby("월")["평균기온(℃)"].mean().sort_index()

    monthly_pred_from_month_model = None
    if coef_m is not None:
        monthly_pred_vals = np.polyval(coef_m, temp_month.values)
        monthly_pred_from_month_model = pd.Series(monthly_pred_vals, index=temp_month.index, name=f"월단위 Poly-3 예측(MJ) - 기온 {scen_start}~{scen_end}년 평균")

    monthly_pred_from_daily_model = None
    if coef_d is not None:
        df_scen = df_scen.copy()
        df_scen["예측일공급량_MJ_from_daily"] = np.polyval(coef_d, df_scen["평균기온(℃)"].to_numpy())
        monthly_daily_by_year = df_scen.groupby(["연도", "월"])["예측일공급량_MJ_from_daily"].sum().reset_index()
        monthly_pred_from_daily_model = monthly_daily_by_year.groupby("월")["예측일공급량_MJ_from_daily"].mean().sort_index()
        monthly_pred_from_daily_model.name = f"일단위 Poly-3 예측합(MJ) - 기온 {scen_start}~{scen_end}년 평균"

    st.markdown("##### 예측/실적 연도 선택")
    year_options = sorted(df["연도"].unique())
    col_pred_year, _ = st.columns([1, 3])
    with col_pred_year:
        pred_year = st.selectbox("실제 월별 공급량을 확인할 연도", options=year_options, index=len(year_options) - 1)

    df_actual_year = df[df["연도"] == pred_year].copy()
    monthly_actual = None
    if not df_actual_year.empty:
        monthly_actual = df_actual_year.groupby("월")["공급량(MJ)"].sum().sort_index()
        monthly_actual.name = f"{pred_year}년 실적(MJ)"

    st.subheader("🔥 월별 예측 vs 실적 — 월단위 Poly-3 vs 일단위 Poly-3(합산)")
    month_index = list(range(1, 13))
    compare_dict = {}
    if monthly_actual is not None:
        compare_dict[monthly_actual.name] = monthly_actual
    if monthly_pred_from_month_model is not None:
        compare_dict[monthly_pred_from_month_model.name] = monthly_pred_from_month_model
    if monthly_pred_from_daily_model is not None:
        compare_dict[monthly_pred_from_daily_model.name] = monthly_pred_from_daily_model
    df_compare = pd.DataFrame(compare_dict, index=month_index)

    r2_m_txt = f"{r2_m:.3f}" if r2_m is not None else "N/A"
    r2_d_txt = f"{r2_d:.3f}" if r2_d is not None else "N/A"

    fig_line = go.Figure()
    for col in df_compare.columns:
        fig_line.add_trace(go.Scatter(x=list(df_compare.index), y=df_compare[col], mode="lines+markers", name=col))

    fig_line.update_layout(
        title=(f"{pred_year}년 월별 공급량: 실적 vs 예측 (기온 시나리오 {scen_start}~{scen_end}년 평균, Poly-3)"
               f"<br><sup>월평균 R²={r2_m_txt}, 일평균 R²={r2_d_txt}</sup>"),
        xaxis_title="월",
        yaxis_title="공급량 (MJ)",
        xaxis=dict(tickmode="array", tickvals=month_index, ticktext=[f"{m}월" for m in month_index]),
        margin=dict(l=20, r=20, t=40, b=20),
    )
    st.plotly_chart(fig_line, use_container_width=True)

    st.markdown("##### 월별 실적/예측 수치표")
    df_compare_view = df_compare.copy()
    df_compare_view.index = [f"{m}월" for m in df_compare_view.index]
    df_compare_view = format_table_generic(df_compare_view)
    st.table(center_style(df_compare_view))


# ─────────────────────────────────────────────
# 메인
# ─────────────────────────────────────────────
def main():
    df, df_temp_all = load_daily_data()

    mode = st.sidebar.radio(
        "좌측 탭 선택",
        ("📅 Daily 공급량 분석", "📅 유효일수 사용", "📊 Daily·Monthly 공급량 비교"),
        index=0,
    )

    if mode == "📅 Daily 공급량 분석":
        st.title("도시가스 공급량 — 일별계획 예측")
        tab_daily_plan_pattern(df_daily=df)
    elif mode == "📅 유효일수 사용":
        st.title("도시가스 공급량 — 유효일수(가중치) 기반 일별계획")
        tab_daily_plan_effective(df_daily=df)
    else:
        st.title("도시가스 공급량 — 일별 vs 월별 예측 검증")
        tab_daily_monthly_compare(df=df, df_temp_all=df_temp_all)


if __name__ == "__main__":
    main()
