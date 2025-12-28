import calendar
from io import BytesIO
from pathlib import Path
	
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from openpyxl.utils import get_column_letter
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


def _format_excel_sheet(ws, freeze="A2", center=True, width_map=None):
    """엑셀 시트 가독성용: 상단 고정, 중앙정렬, 컬럼폭."""
    if freeze:
        ws.freeze_panes = freeze

    if center:
        for row in ws.iter_rows(min_row=1, max_row=ws.max_row, min_col=1, max_col=ws.max_column):
            for c in row:
                c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    if width_map:
        for col_letter, w in width_map.items():
            ws.column_dimensions[col_letter].width = w


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

    # ── 캘린더 정보를 머지해서 공휴일/명절 붙이기 ──
    if cal_df is not None:
        df_recent = df_recent.merge(
            cal_df,
            on="일자",
            how="left",
        )
        # (오타 케이스 안전처리) 공휴일여버 → 공휴일여부
        if ("공휴일여부" not in df_recent.columns) and ("공휴일여버" in df_recent.columns):
            df_recent = df_recent.rename(columns={"공휴일여버": "공휴일여부"})
        if "공휴일여부" not in df_recent.columns:
            df_recent["공휴일여부"] = False

        df_recent["공휴일여부"] = df_recent["공휴일여부"].fillna(False).astype(bool)
        df_recent["명절여부"] = df_recent["명절여부"].fillna(False).astype(bool)
    else:
        df_recent["공휴일여부"] = False
        df_recent["명절여부"] = False

    df_recent["is_holiday"] = df_recent["공휴일여부"] | df_recent["명절여부"]
    # 주말 정의: 토/일 OR 공휴일/명절
    df_recent["is_weekend"] = (df_recent["weekday_idx"] >= 5) | df_recent["is_holiday"]

    # 연도별 월 합계
    df_recent["month_total"] = df_recent.groupby("연도")["공급량(MJ)"].transform("sum")
    df_recent["ratio"] = df_recent["공급량(MJ)"] / df_recent["month_total"]

    # 같은 연도·요일(월~일) 내에서 몇 번째 요일인지 (1번째 토요일, 2번째 토요일 ...)
    df_recent["nth_dow"] = (
        df_recent.sort_values(["연도", "일"])
        .groupby(["연도", "weekday_idx"])
        .cumcount()
        + 1
    )

    weekday_mask = ~df_recent["is_weekend"]
    weekend_mask = df_recent["is_weekend"]

    # 평일: 일자 기준 평균 비율 / 요일 기준 백업 비율
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

    # 주말(토·일 + 공휴일/명절): (요일, nth_dow) 기준 평균 비율 / 요일 기준 백업 비율
    ratio_weekend_group = (
        df_recent[weekend_mask].groupby(["weekday_idx", "nth_dow"])["ratio"].mean()
        if df_recent[weekend_mask].size > 0
        else pd.Series(dtype=float)
    )
    ratio_weekend_by_dow = (
        df_recent[weekend_mask].groupby("weekday_idx")["ratio"].mean()
        if df_recent[weekend_mask].size > 0
        else pd.Series(dtype=float)
    )

    # dict 로 변환
    ratio_by_day_dict = ratio_by_day.to_dict()
    ratio_weekday_by_dow_dict = ratio_weekday_by_dow.to_dict()
    ratio_weekend_group_dict = ratio_weekend_group.to_dict()
    ratio_weekend_by_dow_dict = ratio_weekend_by_dow.to_dict()

    # 대상 연·월 날짜 생성
    last_day = calendar.monthrange(target_year, target_month)[1]
    date_range = pd.date_range(f"{target_year}-{target_month:02d}-01", periods=last_day, freq="D")

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
        if ("공휴일여부" not in df_target.columns) and ("공휴일여버" in df_target.columns):
            df_target = df_target.rename(columns={"공휴일여버": "공휴일여부"})
        if "공휴일여부" not in df_target.columns:
            df_target["공휴일여부"] = False

        df_target["공휴일여부"] = df_target["공휴일여부"].fillna(False).astype(bool)
        df_target["명절여부"] = df_target["명절여부"].fillna(False).astype(bool)
    else:
        df_target["공휴일여부"] = False
        df_target["명절여부"] = False

    df_target["is_holiday"] = df_target["공휴일여부"] | df_target["명절여부"]
    df_target["is_weekend"] = (df_target["weekday_idx"] >= 5) | df_target["is_holiday"]

    weekday_names = ["월", "화", "수", "목", "금", "토", "일"]
    df_target["요일"] = df_target["weekday_idx"].map(lambda i: weekday_names[i])

    # 대상 월에서도 요일별로 몇 번째인지 계산
    df_target["nth_dow"] = df_target.sort_values("일").groupby("weekday_idx").cumcount() + 1

    def _label(row):
        return "주말" if row["is_weekend"] else "평일"

    df_target["구분(평일/주말)"] = df_target.apply(_label, axis=1)

    # 1단계: 주말 비율 확정
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

        df_target["일별비율"] = df_target["weekend_scaled"] + df_target["weekday_scaled"]

        total_ratio = df_target["일별비율"].sum()
        if total_ratio > 0:
            df_target["일별비율"] = df_target["일별비율"] / total_ratio
        else:
            df_target["일별비율"] = 1.0 / last_day

    # 최근 N년 기준 총·평균 공급량
    month_total_all = df_recent["공급량(MJ)"].sum()
    df_target["최근N년_총공급량(MJ)"] = df_target["일별비율"] * month_total_all
    df_target["최근N년_평균공급량(MJ)"] = df_target["최근N년_총공급량(MJ)"] / len(recent_years)

    # 대상 연도의 월 계획 총량
    row_plan = df_plan[(df_plan["연"] == target_year) & (df_plan["월"] == target_month)]
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
        df_recent.pivot_table(index="일", columns="연도", values="공급량(MJ)", aggfunc="sum")
        .sort_index()
        .sort_index(axis=1)
    )

    return df_result, df_mat, recent_years


def _build_year_daily_plan(df_daily: pd.DataFrame, df_plan: pd.DataFrame, target_year: int, recent_window: int):
    """
    (추가) 연간(1~12월) 일별 계획을 한 번에 만들기.
    - 월별로 make_daily_plan_table 호출해서 concat
    - 특정 월 계산 불가하면: '균등분배'로 fallback
    """
    cal_df = load_effective_calendar()

    all_rows = []
    month_summary_rows = []

    for m in range(1, 13):
        df_res, _, used_years = make_daily_plan_table(
            df_daily=df_daily,
            df_plan=df_plan,
            target_year=target_year,
            target_month=m,
            recent_window=recent_window,
        )

        # 월 계획총량
        row_plan = df_plan[(df_plan["연"] == target_year) & (df_plan["월"] == m)]
        plan_total = float(row_plan["계획(사업계획제출_MJ)"].iloc[0]) if not row_plan.empty else np.nan

        if df_res is None:
            # fallback: 균등 분배
            last_day = calendar.monthrange(target_year, m)[1]
            dr = pd.date_range(f"{target_year}-{m:02d}-01", periods=last_day, freq="D")
            tmp = pd.DataFrame({"일자": dr})
            tmp["연"] = target_year
            tmp["월"] = m
            tmp["일"] = tmp["일자"].dt.day
            tmp["weekday_idx"] = tmp["일자"].dt.weekday
            weekday_names = ["월", "화", "수", "목", "금", "토", "일"]
            tmp["요일"] = tmp["weekday_idx"].map(lambda i: weekday_names[i])

            if cal_df is not None:
                tmp = tmp.merge(cal_df, on="일자", how="left")
                if ("공휴일여부" not in tmp.columns) and ("공휴일여버" in tmp.columns):
                    tmp = tmp.rename(columns={"공휴일여버": "공휴일여부"})
                if "공휴일여부" not in tmp.columns:
                    tmp["공휴일여부"] = False
                tmp["공휴일여부"] = tmp["공휴일여부"].fillna(False).astype(bool)
                tmp["명절여부"] = tmp["명절여부"].fillna(False).astype(bool)
            else:
                tmp["공휴일여부"] = False
                tmp["명절여부"] = False

            tmp["is_holiday"] = tmp["공휴일여부"] | tmp["명절여부"]
            tmp["is_weekend"] = (tmp["weekday_idx"] >= 5) | tmp["is_holiday"]
            tmp["구분(평일/주말)"] = tmp["is_weekend"].map(lambda x: "주말" if x else "평일")

            tmp["일별비율"] = 1.0 / last_day if last_day > 0 else 0.0
            tmp["최근N년_총공급량(MJ)"] = np.nan
            tmp["최근N년_평균공급량(MJ)"] = np.nan
            tmp["예상공급량(MJ)"] = (tmp["일별비율"] * plan_total).round(0) if pd.notna(plan_total) else np.nan

            df_res = tmp[
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

        all_rows.append(df_res)

        month_summary_rows.append(
            {
                "월": m,
                "월간 계획(MJ)": plan_total,
            }
        )

    df_year = pd.concat(all_rows, ignore_index=True)
    df_year = df_year.sort_values(["월", "일"]).reset_index(drop=True)

    # 연간 합계행
    total_row = {
        "연": "",
        "월": "",
        "일": "",
        "일자": "",
        "요일": "합계",
        "구분(평일/주말)": "",
        "공휴일여부": False,
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

    # (설명시트용) 월 계획총량 원값
    row_plan = df_plan[(df_plan["연"] == target_year) & (df_plan["월"] == target_month)]
    plan_total_raw = float(row_plan["계획(사업계획제출_MJ)"].iloc[0]) if not row_plan.empty else np.nan

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
    view_with_total = pd.concat([view, pd.DataFrame([total_row])], ignore_index=True)

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

    # 5. 엑셀 다운로드 (월 단위)
    st.markdown("#### 5. 일별 계획 엑셀 다운로드")

    buffer = BytesIO()
    sheet_name = f"{target_year}_{target_month:02d}_일별계획"
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        # 기본 데이터 먼저 기록 (메인 시트)
        view_with_total.to_excel(
            writer,
            index=False,
            sheet_name=sheet_name,
        )

        wb = writer.book
        ws = wb[sheet_name]

        # ─────────────────────────────────────────────
        # (다운로드 엑셀 내 설명/산식이 보이도록) 헤더 기반 컬럼 찾기
        # ─────────────────────────────────────────────
        def _header_map(_ws):
            m = {}
            for c in range(1, _ws.max_column + 1):
                v = _ws.cell(row=1, column=c).value
                if isinstance(v, str) and v.strip():
                    m[v.strip()] = c
            return m

        hmap = _header_map(ws)
        ratio_col_idx = hmap.get("일별비율", None)
        pred_col_idx = hmap.get("예상공급량(MJ)", None)
        day_col_idx = hmap.get("일", None)  # 메인시트에서 '일' 컬럼 위치

        last_row = ws.max_row
        last_col = ws.max_column

        # ─────────────────────────────────────────────
        # 5-0) 최근N년(연도별) 근거 시트 생성
        # ─────────────────────────────────────────────
        year_total_cells = []
        year_data_ranges = {}
        cal_df = load_effective_calendar()

        if df_mat is not None and len(recent_years) > 0:
            for y in recent_years:
                if y not in df_mat.columns:
                    continue

                sheet_y = str(y)

                df_year = pd.DataFrame(
                    {
                        "일": df_mat.index,
                        "공급량(MJ)": df_mat[y].values,
                    }
                )
                df_year.to_excel(
                    writer,
                    index=False,
                    sheet_name=sheet_y,
                )

                ws_y = wb[sheet_y]
                data_last_row = ws_y.max_row
                total_row_y = data_last_row + 1

                ws_y.cell(row=1, column=3, value="일별비율(해당연도)=B/월합계")
                ws_y.cell(row=1, column=4, value="일자")
                ws_y.cell(row=1, column=5, value="weekday_idx(0=월)")
                ws_y.cell(row=1, column=6, value="nth_dow(해당요일 n번째)")
                ws_y.cell(row=1, column=7, value="공휴일여부")
                ws_y.cell(row=1, column=8, value="명절여부")
                ws_y.cell(row=1, column=9, value="is_weekend(토/일+공휴일+명절)")

                last_day_y = calendar.monthrange(y, target_month)[1]
                date_range_y = pd.date_range(f"{y}-{target_month:02d}-01", periods=last_day_y, freq="D")
                tmp = pd.DataFrame({"일자": date_range_y})
                if cal_df is not None:
                    tmp = tmp.merge(cal_df, on="일자", how="left")
                    if ("공휴일여부" not in tmp.columns) and ("공휴일여버" in tmp.columns):
                        tmp = tmp.rename(columns={"공휴일여버": "공휴일여부"})
                    if "공휴일여부" not in tmp.columns:
                        tmp["공휴일여부"] = False
                    tmp["공휴일여부"] = tmp["공휴일여부"].fillna(False).astype(bool)
                    tmp["명절여부"] = tmp["명절여부"].fillna(False).astype(bool)
                else:
                    tmp["공휴일여부"] = False
                    tmp["명절여부"] = False

                holiday_map = {d.date(): bool(v) for d, v in zip(tmp["일자"], tmp["공휴일여부"])}
                seollal_map = {d.date(): bool(v) for d, v in zip(tmp["일자"], tmp["명절여부"])}

                for r in range(2, data_last_row + 1):
                    day_val = ws_y.cell(row=r, column=1).value
                    try:
                        day_int = int(day_val)
                    except Exception:
                        day_int = None

                    if day_int is not None and 1 <= day_int <= last_day_y:
                        dt = pd.Timestamp(year=y, month=target_month, day=day_int).to_pydatetime()
                        ws_y.cell(row=r, column=4, value=dt)

                        h = holiday_map.get(dt.date(), False)
                        m = seollal_map.get(dt.date(), False)
                        ws_y.cell(row=r, column=7, value=bool(h))
                        ws_y.cell(row=r, column=8, value=bool(m))

                        ws_y.cell(row=r, column=5, value=f"=WEEKDAY(D{r},2)-1")
                        ws_y.cell(row=r, column=6, value=f"=COUNTIFS($E$2:E{r},E{r})")
                        ws_y.cell(row=r, column=9, value=f"=OR(E{r}>=5,G{r}=TRUE,H{r}=TRUE)")
                    else:
                        ws_y.cell(row=r, column=4, value="")
                        ws_y.cell(row=r, column=7, value="")
                        ws_y.cell(row=r, column=8, value="")
                        ws_y.cell(row=r, column=5, value="")
                        ws_y.cell(row=r, column=6, value="")
                        ws_y.cell(row=r, column=9, value="")

                ws_y.cell(row=total_row_y, column=1, value="합계")
                ws_y.cell(
                    row=total_row_y,
                    column=2,
                    value=f"=SUM(B2:B{data_last_row})",
                )

                for r in range(2, data_last_row + 1):
                    ws_y.cell(
                        row=r,
                        column=3,
                        value=f"=IFERROR(B{r}/$B${total_row_y},\"\")",
                    )
                ws_y.cell(
                    row=total_row_y,
                    column=3,
                    value=f"=SUM(C2:C{data_last_row})",
                )

                year_total_cells.append(f"'{sheet_y}'!$B${total_row_y}")
                year_data_ranges[y] = {"last": data_last_row}

        # ─────────────────────────────────────────────
        # 5-1) 예상공급량(MJ)_수식 열 추가
        # ─────────────────────────────────────────────
        formula_col = last_col + 1
        ws.cell(row=1, column=formula_col, value="예상공급량(MJ)_수식(비율*월합계)")

        if ratio_col_idx is None:
            ratio_col_idx = 10
        ratio_col_letter = get_column_letter(ratio_col_idx)

        if pred_col_idx is None:
            pred_col_idx = 11
        pred_col_letter = get_column_letter(pred_col_idx)

        for r in range(2, last_row):
            ws.cell(
                row=r,
                column=formula_col,
                value=f"=ROUND(${ratio_col_letter}{r}*${pred_col_letter}${last_row},0)",
            )
        ws.cell(
            row=last_row,
            column=formula_col,
            value=f"=SUM({get_column_letter(formula_col)}2:{get_column_letter(formula_col)}{last_row-1})",
        )

        # ─────────────────────────────────────────────
        # 5-2) 최근N년_총공급량/평균공급량 수식 열 추가
        # ─────────────────────────────────────────────
        recent_total_col = formula_col + 1
        recent_avg_col = formula_col + 2
        recent_total_col_letter = get_column_letter(recent_total_col)
        recent_avg_col_letter = get_column_letter(recent_avg_col)

        ws.cell(row=1, column=recent_total_col, value="최근N년_총공급량(MJ)_수식(비율*최근N년월합)")
        ws.cell(row=1, column=recent_avg_col, value="최근N년_평균공급량(MJ)_수식(총/N)")

        if year_total_cells:
            recent_total_expr = "+".join(year_total_cells)
            n_years = len(year_total_cells)

            for r in range(2, last_row):
                ws.cell(
                    row=r,
                    column=recent_total_col,
                    value=f"=ROUND(${ratio_col_letter}{r}*({recent_total_expr}),0)",
                )
                ws.cell(
                    row=r,
                    column=recent_avg_col,
                    value=f"=ROUND({recent_total_col_letter}{r}/{n_years},0)",
                )

            ws.cell(
                row=last_row,
                column=recent_total_col,
                value=f"=SUM({recent_total_col_letter}2:{recent_total_col_letter}{last_row-1})",
            )
            ws.cell(
                row=last_row,
                column=recent_avg_col,
                value=f"=SUM({recent_avg_col_letter}2:{recent_avg_col_letter}{last_row-1})",
            )

        # ─────────────────────────────────────────────
        # 5-3) INPUT 시트
        # ─────────────────────────────────────────────
        input_sheet = "INPUT"
        if input_sheet in wb.sheetnames:
            wb.remove(wb[input_sheet])
        ws_in = wb.create_sheet(input_sheet)

        ws_in["A1"] = "항목"
        ws_in["B1"] = "값"
        ws_in["C1"] = "비고(셀/참조)"
        for cell in ("A1", "B1", "C1"):
            ws_in[cell].font = Font(bold=True)

        rows = [
            ("대상연도", target_year, ""),
            ("대상월", target_month, ""),
            ("최근N년(설정)", recent_window, ""),
            ("실제 사용된 연도", ", ".join([str(y) for y in recent_years]), ""),
            ("월 계획총량(MJ) (사업계획제출)", plan_total_raw, "공급량(계획_실적).xlsx → 월별계획_실적"),
        ]

        r0 = 2
        for i, (k, v, note) in enumerate(rows):
            rr = r0 + i
            ws_in.cell(rr, 1, k)
            ws_in.cell(rr, 2, v)
            ws_in.cell(rr, 3, note)

        base = r0 + len(rows) + 1
        ws_in.cell(base, 1, "최근N년 연도별 월합계(MJ) 참조")
        ws_in.cell(base, 1).font = Font(bold=True)

        rr = base + 1
        for idx, ref in enumerate(year_total_cells, start=1):
            ws_in.cell(rr, 1, f"연도합계{idx}")
            ws_in.cell(rr, 2, f"={ref}")
            ws_in.cell(rr, 3, ref)
            rr += 1

        ws_in.cell(rr, 1, "최근N년 월합계(MJ) 합산")
        if year_total_cells:
            ws_in.cell(rr, 2, "=" + "+".join([ref for ref in year_total_cells]))
        else:
            ws_in.cell(rr, 2, "")
        ws_in.cell(rr, 3, "연도별 월합계의 합")

        plan_cell_addr = "B6"
        ws_in["E1"] = "고정참조"
        ws_in["E2"] = "월계획총량셀"
        ws_in["F2"] = f"={input_sheet}!${plan_cell_addr}"
        ws_in["E1"].font = Font(bold=True)

        for row in ws_in.iter_rows(min_row=1, max_row=ws_in.max_row, min_col=1, max_col=3):
            for c in row:
                c.alignment = Alignment(vertical="top", wrap_text=True)

        # ─────────────────────────────────────────────
        # 5-3-1) 일별비율_산정근거 시트
        # ─────────────────────────────────────────────
        ratio_basis_sheet = "일별비율_산정근거"
        if ratio_basis_sheet in wb.sheetnames:
            wb.remove(wb[ratio_basis_sheet])
        ws_rb = wb.create_sheet(ratio_basis_sheet)

        ws_rb["A1"] = "일별비율 산정근거(엑셀 수식으로 추적)"
        ws_rb["A1"].font = Font(bold=True, size=13)

        headers = [
            "일", "일자", "weekday_idx(0=월)", "nth_dow", "is_weekend(토/일+공휴일+명절)",
            "평일_기본(일자별 평균)", "평일_대체(요일 평균)",
            "주말_기본(요일+n번째 평균)", "주말_대체(요일 평균)",
            "raw(선택)", "일별비율(raw/합)", "예상공급량(MJ)=비율*월계획"
        ]
        start_row = 3
        for j, h in enumerate(headers, start=1):
            cell = ws_rb.cell(row=start_row, column=j, value=h)
            cell.font = Font(bold=True)

        last_day_target = calendar.monthrange(target_year, target_month)[1]
        dr_t = pd.date_range(f"{target_year}-{target_month:02d}-01", periods=last_day_target, freq="D")
        cal_df2 = load_effective_calendar()
        tmp_t = pd.DataFrame({"일자": dr_t})
        if cal_df2 is not None:
            tmp_t = tmp_t.merge(cal_df2, on="일자", how="left")
            if ("공휴일여부" not in tmp_t.columns) and ("공휴일여버" in tmp_t.columns):
                tmp_t = tmp_t.rename(columns={"공휴일여버": "공휴일여부"})
            if "공휴일여부" not in tmp_t.columns:
                tmp_t["공휴일여부"] = False
            tmp_t["공휴일여부"] = tmp_t["공휴일여부"].fillna(False).astype(bool)
            tmp_t["명절여부"] = tmp_t["명절여부"].fillna(False).astype(bool)
        else:
            tmp_t["공휴일여부"] = False
            tmp_t["명절여부"] = False
        tmp_t["weekday_idx"] = tmp_t["일자"].dt.weekday
        tmp_t["is_weekend"] = (tmp_t["weekday_idx"] >= 5) | (tmp_t["공휴일여부"] | tmp_t["명절여부"])
        tmp_t["nth_dow"] = tmp_t.groupby(tmp_t["weekday_idx"]).cumcount() + 1
        tmp_t["일"] = tmp_t["일자"].dt.day

        year_terms = [y for y in recent_years if str(y) in wb.sheetnames]

        def _avg_of_years(expr_builder):
            parts = []
            for y in year_terms:
                sh = str(y)
                data_last = year_data_ranges.get(y, {}).get("last", None)
                if not data_last:
                    continue
                parts.append(f'IFERROR({expr_builder(sh, data_last)},"")')
            if not parts:
                return '""'
            return "=AVERAGE(" + ",".join(parts) + ")"

        for i in range(last_day_target):
            rr = start_row + 1 + i
            day_i = int(tmp_t.loc[i, "일"])
            dt_i = tmp_t.loc[i, "일자"].to_pydatetime()
            widx = int(tmp_t.loc[i, "weekday_idx"])
            nth = int(tmp_t.loc[i, "nth_dow"])
            is_wend = bool(tmp_t.loc[i, "is_weekend"])

            ws_rb.cell(rr, 1, day_i)
            ws_rb.cell(rr, 2, dt_i)
            ws_rb.cell(rr, 3, widx)
            ws_rb.cell(rr, 4, nth)
            ws_rb.cell(rr, 5, is_wend)

            f_weekday_day = _avg_of_years(
                lambda sh, last: (
                    f"AVERAGEIFS('{sh}'!$C$2:$C${last},'{sh}'!$A$2:$A${last},$A{rr},'{sh}'!$I$2:$I${last},FALSE)"
                )
            )
            ws_rb.cell(rr, 6, value=f_weekday_day)

            f_weekday_dow = _avg_of_years(
                lambda sh, last: (
                    f"AVERAGEIFS('{sh}'!$C$2:$C${last},'{sh}'!$E$2:$E${last},$C{rr},'{sh}'!$I$2:$I${last},FALSE)"
                )
            )
            ws_rb.cell(rr, 7, value=f_weekday_dow)

            f_weekend_group = _avg_of_years(
                lambda sh, last: (
                    f"AVERAGEIFS('{sh}'!$C$2:$C${last},'{sh}'!$E$2:$E${last},$C{rr},'{sh}'!$F$2:$F${last},$D{rr},'{sh}'!$I$2:$I${last},TRUE)"
                )
            )
            ws_rb.cell(rr, 8, value=f_weekend_group)

            f_weekend_dow = _avg_of_years(
                lambda sh, last: (
                    f"AVERAGEIFS('{sh}'!$C$2:$C${last},'{sh}'!$E$2:$E${last},$C{rr},'{sh}'!$I$2:$I${last},TRUE)"
                )
            )
            ws_rb.cell(rr, 9, value=f_weekend_dow)

            ws_rb.cell(rr, 10, value=f"=IF($E{rr},IFERROR($H{rr},$I{rr}),IFERROR($F{rr},$G{rr}))")

        raw_sum_row_start = start_row + 1
        raw_sum_row_end = start_row + last_day_target
        for rr in range(raw_sum_row_start, raw_sum_row_end + 1):
            ws_rb.cell(rr, 11, value=f"=IFERROR($J{rr}/SUM($J${raw_sum_row_start}:$J${raw_sum_row_end}),0)")
            ws_rb.cell(rr, 12, value=f"=ROUND($K{rr}*INPUT!${plan_cell_addr},0)")

        check_row = raw_sum_row_end + 2
        ws_rb.cell(check_row, 9, "검증(합계)")
        ws_rb.cell(check_row, 10, value=f"=SUM($J${raw_sum_row_start}:$J${raw_sum_row_end})")
        ws_rb.cell(check_row, 11, value=f"=SUM($K${raw_sum_row_start}:$K${raw_sum_row_end})")
        ws_rb.cell(check_row, 12, value=f"=SUM($L${raw_sum_row_start}:$L${raw_sum_row_end})")
        for c in range(9, 13):
            ws_rb.cell(check_row, c).font = Font(bold=True)

        ws_rb.freeze_panes = f"A{start_row+1}"
        ws_rb.column_dimensions["A"].width = 6
        ws_rb.column_dimensions["B"].width = 14
        ws_rb.column_dimensions["C"].width = 16
        ws_rb.column_dimensions["D"].width = 10
        ws_rb.column_dimensions["E"].width = 26
        for col in ["F", "G", "H", "I", "J", "K", "L"]:
            ws_rb.column_dimensions[col].width = 22

        # ─────────────────────────────────────────────
        # 5-3-2) 메인 시트의 '일별비율'을 산정근거 참조로 교체
        # ─────────────────────────────────────────────
        if day_col_idx is None:
            day_col_idx = 3
        day_col_letter = get_column_letter(day_col_idx)

        if ratio_col_idx is not None:
            for r in range(2, last_row):
                ws.cell(
                    row=r,
                    column=ratio_col_idx,
                    value=f"=IFERROR(INDEX('{ratio_basis_sheet}'!$K$4:$K${3+last_day_target}, MATCH(${day_col_letter}{r}, '{ratio_basis_sheet}'!$A$4:$A${3+last_day_target}, 0)),0)"
                )
            ws.cell(
                row=last_row,
                column=ratio_col_idx,
                value=f"=SUM({ratio_col_letter}2:{ratio_col_letter}{last_row-1})"
            )

        # ─────────────────────────────────────────────
        # 5-4) 설명(README) 시트
        # ─────────────────────────────────────────────
        readme_sheet = "설명_README"
        if readme_sheet in wb.sheetnames:
            wb.remove(wb[readme_sheet])
        ws_rd = wb.create_sheet(readme_sheet)

        ws_rd["A1"] = "일별계획 산식/로직 설명"
        ws_rd["A1"].font = Font(bold=True, size=14)

        main_sheet_ref = sheet_name

        lines = [
            "1) 필요한 입력 데이터",
            "   - 최근 N년 동일 월의 '일별 실적 공급량(MJ)'",
            "   - 대상 월의 '월 계획총량(MJ)' (사업계획 제출값)",
            "",
            "2) 연도별 월합계 및 연도별 일별비율(근거) — 각 연도 시트(예: '2023')",
            "   - 월합계(MJ) = SUM(해당월 일별실적)",
            "   - 연도별 일별비율(해당연도) = (해당일 실적) / (월합계)",
            "",
            "3) 최종 '일별비율'(메인 시트) 산정 로직(요약) — '일별비율_산정근거' 시트에서 수식으로 계산",
            "   - 평일(월~금): '일자(1~31)별' 비율을 최근 N년 평균",
            "   - 주말/공휴일/명절: '요일 + 그 달의 n번째' 기준 평균",
            "   - raw 선택 후, raw / SUM(raw) 로 정규화",
            "",
            "4) 최종 '예상공급량(MJ)' 계산식",
            "   예상공급량(MJ) = 월 계획총량(MJ) × 일별비율",
            "",
            "5) 엑셀에서 바로 검증하는 방법",
            f"   - 메인 시트({main_sheet_ref})에서",
            "     · 일별비율 합계(마지막 행)가 1.0000인지 확인",
            "     · 예상공급량(MJ) 합계(마지막 행)가 월 계획총량과 동일(반올림 차이 ±몇 MJ 가능)한지 확인",
            "",
            "6) 셀/시트 참조",
            f"   - 메인 시트: '{main_sheet_ref}'",
            f"     · 일별비율 = '{ratio_basis_sheet}' 시트의 K열을 INDEX/MATCH로 참조",
            "   - INPUT 시트:",
            f"     · 월 계획총량(MJ) = INPUT!{plan_cell_addr}",
        ]

        ws_rd["A3"] = "\n".join(lines)
        ws_rd["A3"].alignment = Alignment(vertical="top", wrap_text=True)
        ws_rd.column_dimensions["A"].width = 110
        ws_rd.row_dimensions[3].height = 560

        add_col_1 = recent_avg_col + 1
        add_col_2 = recent_avg_col + 2
        ws.cell(row=1, column=add_col_1, value="월계획총량(MJ)_참조(INPUT)")
        ws.cell(row=1, column=add_col_2, value="예상공급량(MJ)_산식(비율*월계획)")

        add_col_2_letter = get_column_letter(add_col_2)
        add_col_1_letter = get_column_letter(add_col_1)

        for r in range(2, last_row):
            ws.cell(row=r, column=add_col_1, value=f"=INPUT!${plan_cell_addr}")
            ws.cell(
                row=r,
                column=add_col_2,
                value=f"=ROUND(${ratio_col_letter}{r}*{add_col_1_letter}{r},0)",
            )

        ws.cell(row=last_row, column=add_col_1, value="")
        ws.cell(
            row=last_row,
            column=add_col_2,
            value=f"=SUM({add_col_2_letter}2:{add_col_2_letter}{last_row-1})",
        )

        # 가독성
        ws.freeze_panes = "A2"
        ws_in.freeze_panes = "A2"
        ws_rd.freeze_panes = "A2"

    st.download_button(
        label=f"📥 {target_year}년 {target_month}월 일별공급계획 다운로드 (Excel)",
        data=buffer.getvalue(),
        file_name=f"{target_year}_{target_month:02d}_일별공급계획.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    # ─────────────────────────────────────────────
    # 6. (추가) 일일계획 다운로드(연간)
    # ─────────────────────────────────────────────
    st.markdown("#### 6. 일일계획 다운로드(연간)")

    col_ay, col_btn = st.columns([1, 3])
    with col_ay:
        annual_year = st.selectbox(
            "연간 계획 연도 선택",
            years_plan,
            index=years_plan.index(target_year) if target_year in years_plan else 0,
            key="annual_year_select",
        )
    with col_btn:
        st.caption("선택한 연도(1/1~12/31) 일별계획을 한 시트(연간)로, 다음 시트에 월 요약 계획(소계 포함)으로 내려받을 수 있어.")

    # 연간 파일 생성
    buffer_year = BytesIO()
    df_year_daily, df_month_summary = _build_year_daily_plan(
        df_daily=df_daily,
        df_plan=df_plan,
        target_year=int(annual_year),
        recent_window=int(recent_window),
    )

    with pd.ExcelWriter(buffer_year, engine="openpyxl") as writer:
        df_year_daily.to_excel(writer, index=False, sheet_name="연간")
        df_month_summary.to_excel(writer, index=False, sheet_name="월 요약 계획")

        wb = writer.book
        ws_y = wb["연간"]
        ws_m = wb["월 요약 계획"]

        # 간단 서식
        _format_excel_sheet(
            ws_y,
            freeze="A2",
            center=True,
            width_map={
                "A": 6,   # 연
                "B": 4,   # 월
                "C": 4,   # 일
                "D": 14,  # 일자
                "E": 6,   # 요일
                "F": 14,  # 구분
                "G": 10,  # 공휴일여부
                "H": 20,  # 최근N년 평균
                "I": 20,  # 최근N년 총
                "J": 12,  # 일별비율
                "K": 18,  # 예상공급량
            },
        )
        _format_excel_sheet(
            ws_m,
            freeze="A2",
            center=True,
            width_map={
                "A": 10,
                "B": 18,
            },
        )

        # 헤더 bold
        for c in range(1, ws_y.max_column + 1):
            ws_y.cell(1, c).font = Font(bold=True)
        for c in range(1, ws_m.max_column + 1):
            ws_m.cell(1, c).font = Font(bold=True)

    st.download_button(
        label=f"📥 {annual_year}년 연간 일별공급계획 다운로드 (Excel)",
        data=buffer_year.getvalue(),
        file_name=f"{annual_year}_연간_일별공급계획.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key="download_annual_excel",
    )


# ─────────────────────────────────────────────
# 탭2: Daily·Monthly 공급량 비교
# ─────────────────────────────────────────────
def tab_daily_monthly_compare(df: pd.DataFrame, df_temp_all: pd.DataFrame):
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
            text = corr.round(2).astype(str).values

            side = 600

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
                tbl_df_disp["상관계수"] = tbl_df_disp["상관계수"].map(lambda x: f"{x:.2f}")

                col_hm, col_tbl = st.columns([3, 2])
                with col_hm:
                    st.plotly_chart(fig_corr, use_container_width=True)
                with col_tbl:
                    st.markdown(f"**기준 변수: `{target_col}` 과(와) 다른 변수들의 상관계수**")
                    st.table(center_style(tbl_df_disp))
        else:
            st.caption("숫자 컬럼이 2개 미만이라 상관도 분석을 할 수 없어.")

    # ① 데이터 학습기간 선택
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
        df_window
        .groupby(["연도", "월"], as_index=False)
        .agg(
            공급량_MJ=("공급량(MJ)", "sum"),
            평균기온=("평균기온(℃)", "mean"),
        )
    )

    coef_m, y_pred_m, r2_m = fit_poly3_and_r2(
        df_month["평균기온"],
        df_month["공급량_MJ"],
    )
    if y_pred_m is not None:
        df_month["예측공급량_MJ"] = y_pred_m
    else:
        df_month["예측공급량_MJ"] = np.nan

    coef_d, y_pred_d, r2_d = fit_poly3_and_r2(
        df_window["평균기온(℃)"],
        df_window["공급량(MJ)"],
    )
    if y_pred_d is not None:
        df_window["예측공급량_MJ"] = y_pred_d
    else:
        df_window["예측공급량_MJ"] = np.nan

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

    # ② 기온 시나리오 연도 범위 선택
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

    st.caption(
        f"선택한 기온 시나리오 연도: **{scen_start}년 ~ {scen_end}년** "
        "(각 월별로 이 기간의 평균기온을 사용)"
    )

    df_scen = df_temp_all[df_temp_all["연도"].between(scen_start, scen_end)].copy()
    if df_scen.empty:
        st.write("선택한 기온 시나리오 구간에 데이터가 없어.")
        return

    temp_month = (
        df_scen.groupby("월")["평균기온(℃)"]
        .mean()
        .sort_index()
    )

    monthly_pred_from_month_model = None
    if coef_m is not None:
        monthly_pred_vals = np.polyval(coef_m, temp_month.values)
        monthly_pred_from_month_model = pd.Series(
            monthly_pred_vals,
            index=temp_month.index,
            name=f"월단위 Poly-3 예측(MJ) - 기온 {scen_start}~{scen_end}년 평균",
        )

    monthly_pred_from_daily_model = None
    if coef_d is not None:
        df_scen = df_scen.copy()
        df_scen["예측일공급량_MJ_from_daily"] = np.polyval(
            coef_d,
            df_scen["평균기온(℃)"].to_numpy(),
        )

        monthly_daily_by_year = (
            df_scen
            .groupby(["연도", "월"])["예측일공급량_MJ_from_daily"]
            .sum()
            .reset_index()
        )

        monthly_pred_from_daily_model = (
            monthly_daily_by_year
            .groupby("월")["예측일공급량_MJ_from_daily"]
            .mean()
            .sort_index()
        )
        monthly_pred_from_daily_model.name = (
            f"일단위 Poly-3 예측합(MJ) - 기온 {scen_start}~{scen_end}년 평균"
        )

    st.markdown("##### 예측/실적 연도 선택")

    year_options = sorted(df["연도"].unique())
    col_pred_year, _ = st.columns([1, 3])
    with col_pred_year:
        pred_year = st.selectbox(
            "실제 월별 공급량을 확인할 연도",
            options=year_options,
            index=len(year_options) - 1,
        )

    df_actual_year = df[df["연도"] == pred_year].copy()
    monthly_actual = None
    if not df_actual_year.empty:
        monthly_actual = (
            df_actual_year
            .groupby("월")["공급량(MJ)"]
            .sum()
            .sort_index()
        )
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

    colors = {}
    if monthly_actual is not None:
        colors[monthly_actual.name] = "red"
    if monthly_pred_from_month_model is not None:
        colors[monthly_pred_from_month_model.name] = "#1f77b4"
    if monthly_pred_from_daily_model is not None:
        colors[monthly_pred_from_daily_model.name] = "#ff7f0e"

    fig_line = go.Figure()
    for col in df_compare.columns:
        if monthly_actual is not None and col == monthly_actual.name:
            line_style = dict(color=colors.get(col, None), width=3)
        else:
            line_style = dict(color=colors.get(col, None), width=2, dash="dot")

        fig_line.add_trace(
            go.Scatter(
                x=list(df_compare.index),
                y=df_compare[col],
                mode="lines+markers",
                name=col,
                line=line_style,
            )
        )

    fig_line.update_layout(
        title=(
            f"{pred_year}년 월별 공급량: 실적 vs 예측 "
            f"(기온 시나리오 {scen_start}~{scen_end}년 평균, Poly-3)"
            f"<br><sup>월평균 기온 기반 R²={r2_m_txt}, "
            f"일평균 기온 기반 R²={r2_d_txt}</sup>"
        ),
        xaxis_title="월",
        yaxis_title="공급량 (MJ)",
        xaxis=dict(
            tickmode="array",
            tickvals=month_index,
            ticktext=[f"{m}월" for m in month_index],
        ),
        margin=dict(l=20, r=20, t=40, b=20),
    )

    st.plotly_chart(fig_line, use_container_width=True)

    st.markdown("##### 월별 실적/예측 수치표")
    df_compare_view = df_compare.copy()
    df_compare_view.index = [f"{m}월" for m in df_compare_view.index]
    df_compare_view = format_table_generic(df_compare_view)
    st.table(center_style(df_compare_view))

    if (
        (monthly_actual is not None)
        and (monthly_pred_from_month_model is not None)
        and (monthly_pred_from_daily_model is not None)
    ):
        total_actual = monthly_actual.sum()
        total_month_pred = monthly_pred_from_month_model.sum()
        total_daily_pred = monthly_pred_from_daily_model.sum()

        summary_df = pd.DataFrame(
            {
                "구분": ["실적", "월단위 Poly-3 예측", "일단위 Poly-3 예측합"],
                "연간 공급량(MJ)": [total_actual, total_month_pred, total_daily_pred],
            }
        )
        summary_df["실적대비 차이(MJ)"] = summary_df["연간 공급량(MJ)"] - total_actual
        summary_df["실적대비 오차율(%)"] = summary_df["실적대비 차이(MJ)"] / total_actual * 100

        st.markdown("###### 연간 소계 (실적 vs 예측, 실적대비 차이·오차율)")
        summary_view = format_table_generic(summary_df, percent_cols=["실적대비 오차율(%)"])
        st.table(center_style(summary_view))

    # ③ 기온 매트릭스
    st.subheader("🌡️ ③ 기온 매트릭스 (일별 평균기온)")

    mat_slider_min = min_year_temp
    mat_slider_max = max_year_temp
    mat_default_start = mat_slider_min

    col_mat_slider, col_mat_month = st.columns([2, 1])
    with col_mat_slider:
        mat_start, mat_end = st.slider(
            "연도 범위 (실제 데이터가 있는 연도만 표시됨)",
            min_value=mat_slider_min,
            max_value=mat_slider_max,
            value=(mat_default_start, mat_slider_max),
            step=1,
        )
    with col_mat_month:
        month_sel = st.selectbox(
            "월 선택",
            list(range(1, 12 + 1)),
            index=9,
        )

    df_mat_temp = df_temp_all[
        (df_temp_all["연도"].between(mat_start, mat_end))
        & (df_temp_all["월"] == month_sel)
    ].copy()
    if df_mat_temp.empty:
        st.write("선택한 연도/월 범위에 대한 기온 데이터가 없어.")
        return

    pivot = (
        df_mat_temp.pivot_table(
            index="일",
            columns="연도",
            values="평균기온(℃)",
            aggfunc="mean",
        )
        .sort_index()
        .sort_index(axis=1)
    )

    side_hm = int(700 * 1.2)

    fig_hm = go.Figure(
        data=go.Heatmap(
            z=pivot.values,
            x=pivot.columns,
            y=pivot.index,
            colorscale="RdBu_r",
            colorbar_title="℃",
        )
    )
    fig_hm.update_layout(
        title=f"기온 매트릭스 — {month_sel}월 기준 (선택 연도 {mat_start}~{mat_end})",
        xaxis_title="연도",
        yaxis=dict(title="일", autorange="reversed"),
        width=side_hm,
        height=side_hm,
        margin=dict(l=20, r=20, t=40, b=40),
    )

    st.plotly_chart(fig_hm, use_container_width=False)


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
