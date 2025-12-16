import calendar
import datetime as dt
from io import BytesIO
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from openpyxl.styles import Alignment, Font
from openpyxl.utils import get_column_letter


# ─────────────────────────────────────────────
# 단위/환산 상수
# ─────────────────────────────────────────────
MJ_PER_GJ = 1000.0
AVG_HEAT_MJ_PER_NM3 = 42.563  # 연평균 열량 (MJ / N㎥)

def mj_to_gj(x):
    return x / MJ_PER_GJ

def mj_to_nm3(x):
    return x / AVG_HEAT_MJ_PER_NM3


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
    fig.update_layout(
        title=title,
        xaxis_title=x_label,
        yaxis_title=y_label,
        margin=dict(l=20, r=20, t=40, b=20),
    )
    return fig


def format_table_generic(df, percent_cols=None, temp_cols=None):
    df = df.copy()
    percent_cols = percent_cols or []
    temp_cols = temp_cols or []

    def _fmt_no_comma(x):
        if pd.isna(x):
            return ""
        try:
            return f"{int(x)}"
        except Exception:
            return str(x)

    for col in df.columns:
        if df[col].dtype == bool:
            df[col] = df[col].map(lambda x: "공휴일" if x else "")
            continue

        if col in percent_cols:
            df[col] = df[col].map(lambda x: f"{x:.4f}" if pd.notna(x) else "")
        elif col in temp_cols:
            df[col] = df[col].map(lambda x: f"{x:.2f}" if pd.notna(x) else "")
        elif pd.api.types.is_numeric_dtype(df[col]):
            if col in ["연", "연도", "월", "일"]:
                df[col] = df[col].map(_fmt_no_comma)
            else:
                df[col] = df[col].map(lambda x: f"{x:,.0f}" if pd.notna(x) else "")
    return df


def show_table_no_index(df: pd.DataFrame, height: int = 260):
    df_to_show = df.copy()
    try:
        st.dataframe(df_to_show, use_container_width=True, hide_index=True, height=height)
        return
    except TypeError:
        pass

    try:
        st.table(df_to_show.style.hide(axis="index"))
        return
    except Exception:
        pass

    st.table(df_to_show)


def _format_excel_sheet(ws, freeze="A2", center=True):
    if freeze:
        ws.freeze_panes = freeze

    if center:
        for row in ws.iter_rows(min_row=1, max_row=ws.max_row, min_col=1, max_col=ws.max_column):
            for c in row:
                c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)


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
    월별 계획 표(가로) : 1행=GJ, 2행=㎥
    """
    df_year = df_plan[df_plan["연"] == target_year][["월", plan_col]].copy()
    base = pd.DataFrame({"월": list(range(1, 13))})
    df_year = base.merge(df_year, on="월", how="left")

    df_year["월별 계획(GJ)"] = mj_to_gj(df_year[plan_col])
    df_year["월별 계획(㎥)"] = mj_to_nm3(df_year[plan_col])

    total_gj = df_year["월별 계획(GJ)"].sum(skipna=True)
    total_m3 = df_year["월별 계획(㎥)"].sum(skipna=True)

    row_gj = {f"{m}월": df_year.loc[df_year["월"] == m, "월별 계획(GJ)"].iloc[0] for m in range(1, 13)}
    row_gj["연간합계"] = total_gj

    row_m3 = {f"{m}월": df_year.loc[df_year["월"] == m, "월별 계획(㎥)"].iloc[0] for m in range(1, 13)}
    row_m3["연간합계"] = total_m3

    out = pd.DataFrame([row_gj, row_m3])
    out.insert(0, "구분", ["사업계획(월별 계획, GJ)", "사업계획(월별 계획, ㎥)"])
    return out


def _excel_find_col_letter(ws, header_name: str) -> str | None:
    header = [c.value for c in ws[1]]
    for idx, name in enumerate(header, start=1):
        if str(name).strip() == header_name:
            return get_column_letter(idx)
    return None


def _add_cumulative_plan_sheet(wb, asof_date: dt.date):
    """
    누적계획량 시트
    - 진행률(일대비) = (기준일까지 실적) / (기준일까지 목표)
    - 일/월/연 모두 같은 방식
    """
    if "연간" not in wb.sheetnames:
        return

    ws_y = wb["연간"]

    date_col = _excel_find_col_letter(ws_y, "일자")
    plan_gj_col = _excel_find_col_letter(ws_y, "예상공급량(GJ)")
    plan_m3_col = _excel_find_col_letter(ws_y, "예상공급량(㎥)")
    act_gj_col = _excel_find_col_letter(ws_y, "실적공급량(GJ)")
    act_m3_col = _excel_find_col_letter(ws_y, "실적공급량(㎥)")

    if not all([date_col, plan_gj_col, plan_m3_col, act_gj_col, act_m3_col]):
        return

    ws_c = wb.create_sheet("누적계획량")

    ws_c["A1"].value = "기준일"
    ws_c["B1"].value = asof_date
    ws_c["B1"].number_format = "yyyy-mm-dd"

    ws_c["A3"].value = "구분"
    ws_c["B3"].value = "목표(GJ)"   # 기준일까지 목표(=일대비 목표 누적)
    ws_c["C3"].value = "누적(GJ)"   # 기준일까지 실적(=일대비 실적 누적)
    ws_c["D3"].value = "목표(㎥)"
    ws_c["E3"].value = "누적(㎥)"
    ws_c["F3"].value = "진행률(일대비, GJ)"

    for c in range(1, 7):
        ws_c.cell(3, c).font = Font(bold=True)
        ws_c.cell(3, c).alignment = Alignment(horizontal="center", vertical="center")

    ws_c["A4"].value = "일"
    ws_c["A5"].value = "월"
    ws_c["A6"].value = "연"

    rng_plan_gj = f"연간!${plan_gj_col}:${plan_gj_col}"
    rng_plan_m3 = f"연간!${plan_m3_col}:${plan_m3_col}"
    rng_act_gj = f"연간!${act_gj_col}:${act_gj_col}"
    rng_act_m3 = f"연간!${act_m3_col}:${act_m3_col}"
    rng_date = f"연간!${date_col}:${date_col}"

    # ── 일 (해당일 실적 / 해당일 목표)
    ws_c["B4"].value = f'=SUMIFS({rng_plan_gj},{rng_date},$B$1)'
    ws_c["C4"].value = f'=SUMIFS({rng_act_gj},{rng_date},$B$1)'
    ws_c["D4"].value = f'=SUMIFS({rng_plan_m3},{rng_date},$B$1)'
    ws_c["E4"].value = f'=SUMIFS({rng_act_m3},{rng_date},$B$1)'
    ws_c["F4"].value = "=IFERROR(C4/B4,0)"

    # ── 월 (기준일까지 실적 누적 / 기준일까지 목표 누적)
    ws_c["B5"].value = (
        f'=SUMIFS({rng_plan_gj},{rng_date},"<="&$B$1,{rng_date},">="&DATE(YEAR($B$1),MONTH($B$1),1))'
    )
    ws_c["C5"].value = (
        f'=SUMIFS({rng_act_gj},{rng_date},"<="&$B$1,{rng_date},">="&DATE(YEAR($B$1),MONTH($B$1),1))'
    )
    ws_c["D5"].value = (
        f'=SUMIFS({rng_plan_m3},{rng_date},"<="&$B$1,{rng_date},">="&DATE(YEAR($B$1),MONTH($B$1),1))'
    )
    ws_c["E5"].value = (
        f'=SUMIFS({rng_act_m3},{rng_date},"<="&$B$1,{rng_date},">="&DATE(YEAR($B$1),MONTH($B$1),1))'
    )
    ws_c["F5"].value = "=IFERROR(C5/B5,0)"

    # ── 연 (기준일까지 실적 누적 / 기준일까지 목표 누적)
    ws_c["B6"].value = (
        f'=SUMIFS({rng_plan_gj},{rng_date},"<="&$B$1,{rng_date},">="&DATE(YEAR($B$1),1,1))'
    )
    ws_c["C6"].value = (
        f'=SUMIFS({rng_act_gj},{rng_date},"<="&$B$1,{rng_date},">="&DATE(YEAR($B$1),1,1))'
    )
    ws_c["D6"].value = (
        f'=SUMIFS({rng_plan_m3},{rng_date},"<="&$B$1,{rng_date},">="&DATE(YEAR($B$1),1,1))'
    )
    ws_c["E6"].value = (
        f'=SUMIFS({rng_act_m3},{rng_date},"<="&$B$1,{rng_date},">="&DATE(YEAR($B$1),1,1))'
    )
    ws_c["F6"].value = "=IFERROR(C6/B6,0)"

    ws_c.freeze_panes = "A4"
    ws_c.column_dimensions["A"].width = 10
    ws_c.column_dimensions["B"].width = 14
    ws_c.column_dimensions["C"].width = 14
    ws_c.column_dimensions["D"].width = 16
    ws_c.column_dimensions["E"].width = 16
    ws_c.column_dimensions["F"].width = 18

    for r in range(4, 7):
        ws_c[f"A{r}"].alignment = Alignment(horizontal="center", vertical="center")
        ws_c[f"B{r}"].number_format = "#,##0"
        ws_c[f"C{r}"].number_format = "#,##0"
        ws_c[f"D{r}"].number_format = "#,##0"
        ws_c[f"E{r}"].number_format = "#,##0"
        ws_c[f"F{r}"].number_format = "0.00%"
        for col in ["B", "C", "D", "E", "F"]:
            ws_c[f"{col}{r}"].alignment = Alignment(horizontal="center", vertical="center")


# ─────────────────────────────────────────────
# Daily 공급량 분석용 함수
# ─────────────────────────────────────────────
def make_daily_plan_table(
    df_daily: pd.DataFrame,
    df_plan: pd.DataFrame,
    target_year: int = 2026,
    target_month: int = 1,
    recent_window: int = 3,
) -> tuple[pd.DataFrame | None, pd.DataFrame | None, list[int], pd.DataFrame]:
    cal_df = load_effective_calendar()
    plan_col = _find_plan_col(df_plan)

    all_years = sorted(df_daily["연도"].unique())
    start_year = target_year - recent_window
    candidate_years = [y for y in range(start_year, target_year) if y in all_years]
    if len(candidate_years) == 0:
        return None, None, [], pd.DataFrame()

    df_pool = df_daily[(df_daily["연도"].isin(candidate_years)) & (df_daily["월"] == target_month)].copy()
    df_pool = df_pool.dropna(subset=["공급량(MJ)"])
    used_years = sorted(df_pool["연도"].unique().tolist())
    if len(used_years) == 0:
        return None, None, [], pd.DataFrame()

    df_recent = df_daily[(df_daily["연도"].isin(used_years)) & (df_daily["월"] == target_month)].copy()
    df_recent = df_recent.dropna(subset=["공급량(MJ)"])
    if df_recent.empty:
        return None, None, used_years, pd.DataFrame()

    df_recent = df_recent.sort_values(["연도", "일"]).copy()
    df_recent["weekday_idx"] = df_recent["일자"].dt.weekday  # 0=월, 6=일

    if cal_df is not None:
        df_recent = df_recent.merge(cal_df, on="일자", how="left")
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

    # 주말/공휴일/명절을 먼저 주말로 확정
    df_recent["is_weekend"] = (df_recent["weekday_idx"] >= 5) | df_recent["is_holiday"]

    # 평일1/2는 주말 제외 조건
    df_recent["is_weekday1"] = (~df_recent["is_weekend"]) & (df_recent["weekday_idx"].isin([0, 4]))  # 월,금
    df_recent["is_weekday2"] = (~df_recent["is_weekend"]) & (df_recent["weekday_idx"].isin([1, 2, 3]))  # 화수목

    df_recent["month_total"] = df_recent.groupby("연도")["공급량(MJ)"].transform("sum")
    df_recent["ratio"] = df_recent["공급량(MJ)"] / df_recent["month_total"]

    df_recent["nth_dow"] = (
        df_recent.sort_values(["연도", "일"])
        .groupby(["연도", "weekday_idx"])
        .cumcount()
        + 1
    )

    weekend_mask = df_recent["is_weekend"]
    w1_mask = df_recent["is_weekday1"]
    w2_mask = df_recent["is_weekday2"]

    ratio_weekend_group = (
        df_recent[weekend_mask].groupby(["weekday_idx", "nth_dow"])["ratio"].mean()
        if df_recent[weekend_mask].size > 0 else pd.Series(dtype=float)
    )
    ratio_weekend_by_dow = (
        df_recent[weekend_mask].groupby("weekday_idx")["ratio"].mean()
        if df_recent[weekend_mask].size > 0 else pd.Series(dtype=float)
    )

    ratio_w1_group = (
        df_recent[w1_mask].groupby(["weekday_idx", "nth_dow"])["ratio"].mean()
        if df_recent[w1_mask].size > 0 else pd.Series(dtype=float)
    )
    ratio_w1_by_dow = (
        df_recent[w1_mask].groupby("weekday_idx")["ratio"].mean()
        if df_recent[w1_mask].size > 0 else pd.Series(dtype=float)
    )

    ratio_w2_group = (
        df_recent[w2_mask].groupby(["weekday_idx", "nth_dow"])["ratio"].mean()
        if df_recent[w2_mask].size > 0 else pd.Series(dtype=float)
    )
    ratio_w2_by_dow = (
        df_recent[w2_mask].groupby("weekday_idx")["ratio"].mean()
        if df_recent[w2_mask].size > 0 else pd.Series(dtype=float)
    )

    ratio_weekend_group_dict = ratio_weekend_group.to_dict()
    ratio_weekend_by_dow_dict = ratio_weekend_by_dow.to_dict()
    ratio_w1_group_dict = ratio_w1_group.to_dict()
    ratio_w1_by_dow_dict = ratio_w1_by_dow.to_dict()
    ratio_w2_group_dict = ratio_w2_group.to_dict()
    ratio_w2_by_dow_dict = ratio_w2_by_dow_dict = ratio_w2_by_dow.to_dict()

    last_day = calendar.monthrange(target_year, target_month)[1]
    date_range = pd.date_range(f"{target_year}-{target_month:02d}-01", periods=last_day, freq="D")

    df_target = pd.DataFrame({"일자": date_range})
    df_target["연"] = target_year
    df_target["월"] = target_month
    df_target["일"] = df_target["일자"].dt.day
    df_target["weekday_idx"] = df_target["일자"].dt.weekday

    if cal_df is not None:
        df_target = df_target.merge(cal_df, on="일자", how="left")
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
    df_target["is_weekday1"] = (~df_target["is_weekend"]) & (df_target["weekday_idx"].isin([0, 4]))
    df_target["is_weekday2"] = (~df_target["is_weekend"]) & (df_target["weekday_idx"].isin([1, 2, 3]))

    weekday_names = ["월", "화", "수", "목", "금", "토", "일"]
    df_target["요일"] = df_target["weekday_idx"].map(lambda i: weekday_names[i])

    df_target["nth_dow"] = df_target.sort_values("일").groupby("weekday_idx").cumcount() + 1

    def _label(row):
        if row["is_weekend"]:
            return "주말/공휴일"
        if row["is_weekday1"]:
            return "평일1(월·금)"
        return "평일2(화·수·목)"

    df_target["구분"] = df_target.apply(_label, axis=1)

    def _pick_ratio(row):
        dow = int(row["weekday_idx"])
        nth = int(row["nth_dow"])
        key = (dow, nth)

        if bool(row["is_weekend"]):
            v = ratio_weekend_group_dict.get(key, None)
            if v is None or pd.isna(v):
                v = ratio_weekend_by_dow_dict.get(dow, None)
            return v

        if bool(row["is_weekday1"]):
            v = ratio_w1_group_dict.get(key, None)
            if v is None or pd.isna(v):
                v = ratio_w1_by_dow_dict.get(dow, None)
            return v

        v = ratio_w2_group_dict.get(key, None)
        if v is None or pd.isna(v):
            v = ratio_w2_by_dow_dict.get(dow, None)
        return v

    df_target["raw"] = df_target.apply(_pick_ratio, axis=1).astype("float64")

    overall_mean = df_target["raw"].dropna().mean() if df_target["raw"].notna().any() else np.nan

    for cat in ["주말/공휴일", "평일1(월·금)", "평일2(화·수·목)"]:
        mask = df_target["구분"] == cat
        if mask.any():
            m = df_target.loc[mask, "raw"].dropna().mean()
            if pd.isna(m):
                m = overall_mean
            df_target.loc[mask, "raw"] = df_target.loc[mask, "raw"].fillna(m)

    if df_target["raw"].isna().all():
        df_target["raw"] = 1.0

    raw_sum = df_target["raw"].sum()
    df_target["일별비율"] = (df_target["raw"] / raw_sum) if raw_sum > 0 else (1.0 / last_day)

    month_total_all_mj = df_recent["공급량(MJ)"].sum()
    df_target["최근N년_총공급량(GJ)"] = mj_to_gj(df_target["일별비율"] * month_total_all_mj)
    df_target["최근N년_평균공급량(GJ)"] = df_target["최근N년_총공급량(GJ)"] / len(used_years)

    df_target["최근N년_총공급량(㎥)"] = mj_to_nm3(df_target["일별비율"] * month_total_all_mj)
    df_target["최근N년_평균공급량(㎥)"] = df_target["최근N년_총공급량(㎥)"] / len(used_years)

    row_plan = df_plan[(df_plan["연"] == target_year) & (df_plan["월"] == target_month)]
    plan_total_mj = float(row_plan[plan_col].iloc[0]) if not row_plan.empty else np.nan

    df_target["예상공급량(GJ)"] = (mj_to_gj(df_target["일별비율"] * plan_total_mj)).round(0)
    df_target["예상공급량(㎥)"] = (mj_to_nm3(df_target["일별비율"] * plan_total_mj)).round(0)

    df_target = df_target.sort_values("일").reset_index(drop=True)

    df_result = df_target[
        [
            "연",
            "월",
            "일",
            "일자",
            "요일",
            "weekday_idx",
            "nth_dow",
            "구분",
            "공휴일여부",
            "명절여부",
            "최근N년_평균공급량(GJ)",
            "최근N년_총공급량(GJ)",
            "최근N년_평균공급량(㎥)",
            "최근N년_총공급량(㎥)",
            "일별비율",
            "예상공급량(GJ)",
            "예상공급량(㎥)",
        ]
    ].copy()

    df_mat_mj = (
        df_recent.pivot_table(index="일", columns="연도", values="공급량(MJ)", aggfunc="sum")
        .sort_index()
        .sort_index(axis=1)
    )
    df_mat_gj = df_mat_mj / MJ_PER_GJ

    df_debug_target = df_target[
        ["일", "일자", "요일", "weekday_idx", "nth_dow", "공휴일여부", "명절여부", "is_weekend", "구분", "raw", "일별비율"]
    ].copy()

    return df_result, df_mat_gj, used_years, df_debug_target


def _build_year_daily_plan(df_daily: pd.DataFrame, df_plan: pd.DataFrame, target_year: int, recent_window: int):
    """
    연간 시트 만들 때:
    - 예측(계획) GJ/㎥
    - 실적 GJ/㎥ (해당 날짜 실적이 있으면 매칭, 없으면 NaN)
    """
    plan_col = _find_plan_col(df_plan)

    # 타겟연도 실적 맵 (MJ 기준)
    df_act_year = df_daily[df_daily["연도"] == target_year][["일자", "공급량(MJ)"]].dropna().copy()
    act_map_mj = dict(zip(df_act_year["일자"].dt.normalize(), df_act_year["공급량(MJ)"]))

    all_rows = []
    month_summary_rows = []

    for m in range(1, 13):
        df_res, _mat, _used_years, _debug = make_daily_plan_table(
            df_daily=df_daily,
            df_plan=df_plan,
            target_year=target_year,
            target_month=m,
            recent_window=recent_window,
        )

        row_plan = df_plan[(df_plan["연"] == target_year) & (df_plan["월"] == m)]
        plan_total_mj = float(row_plan[plan_col].iloc[0]) if not row_plan.empty else np.nan

        if df_res is None:
            last_day = calendar.monthrange(target_year, m)[1]
            dr = pd.date_range(f"{target_year}-{m:02d}-01", periods=last_day, freq="D")
            tmp = pd.DataFrame({"일자": dr})
            tmp["연"] = target_year
            tmp["월"] = m
            tmp["일"] = tmp["일자"].dt.day
            tmp["weekday_idx"] = tmp["일자"].dt.weekday
            weekday_names = ["월", "화", "수", "목", "금", "토", "일"]
            tmp["요일"] = tmp["weekday_idx"].map(lambda i: weekday_names[i])
            tmp["nth_dow"] = tmp.groupby("weekday_idx").cumcount() + 1
            tmp["공휴일여부"] = False
            tmp["명절여부"] = False

            tmp["is_holiday"] = tmp["공휴일여부"] | tmp["명절여부"]
            tmp["is_weekend"] = (tmp["weekday_idx"] >= 5) | tmp["is_holiday"]
            tmp["구분"] = np.where(
                tmp["is_weekend"], "주말/공휴일",
                np.where(tmp["weekday_idx"].isin([0, 4]), "평일1(월·금)", "평일2(화·수·목)")
            )

            tmp["일별비율"] = 1.0 / last_day if last_day > 0 else 0.0
            tmp["예상공급량(GJ)"] = (mj_to_gj(tmp["일별비율"] * plan_total_mj)).round(0) if pd.notna(plan_total_mj) else np.nan
            tmp["예상공급량(㎥)"] = (mj_to_nm3(tmp["일별비율"] * plan_total_mj)).round(0) if pd.notna(plan_total_mj) else np.nan

            df_res = tmp[
                [
                    "연", "월", "일", "일자", "요일", "weekday_idx", "nth_dow", "구분",
                    "공휴일여부", "명절여부",
                    "일별비율", "예상공급량(GJ)", "예상공급량(㎥)",
                ]
            ].copy()
        else:
            # 화면용 df_res는 컬럼이 많으니까 연간에는 핵심만 쓰되, 아래서 실적 컬럼 추가
            pass

        # 실적 컬럼 추가 (연간 시트용)
        df_res = df_res.copy()
        norm_date = pd.to_datetime(df_res["일자"]).dt.normalize()
        df_res["실적공급량(MJ)"] = norm_date.map(act_map_mj)  # 없으면 NaN
        df_res["실적공급량(GJ)"] = mj_to_gj(df_res["실적공급량(MJ)"])
        df_res["실적공급량(㎥)"] = mj_to_nm3(df_res["실적공급량(MJ)"])

        # 연간 시트에 필요한 컬럼 정리
        keep_cols = [
            "연", "월", "일", "일자", "요일", "구분", "공휴일여부", "명절여부",
            "일별비율",
            "예상공급량(GJ)", "예상공급량(㎥)",
            "실적공급량(GJ)", "실적공급량(㎥)",
        ]
        df_res = df_res[[c for c in keep_cols if c in df_res.columns]].copy()

        all_rows.append(df_res)

        month_summary_rows.append({
            "월": m,
            "월간 계획(GJ)": mj_to_gj(plan_total_mj) if pd.notna(plan_total_mj) else np.nan,
            "월간 계획(㎥)": mj_to_nm3(plan_total_mj) if pd.notna(plan_total_mj) else np.nan,
        })

    df_year = pd.concat(all_rows, ignore_index=True)
    df_year = df_year.sort_values(["월", "일"]).reset_index(drop=True)

    # 합계행
    total_row = {c: "" for c in df_year.columns}
    total_row["요일"] = "합계"

    for c in ["일별비율", "예상공급량(GJ)", "예상공급량(㎥)", "실적공급량(GJ)", "실적공급량(㎥)"]:
        if c in df_year.columns:
            total_row[c] = df_year[c].sum(skipna=True)

    df_year_with_total = pd.concat([df_year, pd.DataFrame([total_row])], ignore_index=True)

    df_month_sum = pd.DataFrame(month_summary_rows).sort_values("월").reset_index(drop=True)
    df_month_sum_total = pd.DataFrame([{
        "월": "연간합계",
        "월간 계획(GJ)": df_month_sum["월간 계획(GJ)"].sum(skipna=True),
        "월간 계획(㎥)": df_month_sum["월간 계획(㎥)"].sum(skipna=True),
    }])
    df_month_sum = pd.concat([df_month_sum, df_month_sum_total], ignore_index=True)

    return df_year_with_total, df_month_sum


# ─────────────────────────────────────────────
# 탭1: Daily 공급량 분석
# ─────────────────────────────────────────────
def tab_daily_plan(df_daily: pd.DataFrame):
    st.subheader("📅 Daily 공급량 분석 — 최근 N년 패턴 기반 일별 계획")

    df_plan = load_monthly_plan()
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
        )

    df_result, df_mat_gj, used_years, df_debug = make_daily_plan_table(
        df_daily=df_daily,
        df_plan=df_plan,
        target_year=target_year,
        target_month=target_month,
        recent_window=recent_window,
    )

    if df_result is None or len(used_years) == 0:
        st.warning("해당 연도/월에 대해 선택한 최근 N년 기준으로 계산할 수 있는 데이터가 없어.")
        return

    st.markdown("#### 📌 월별 계획량(1~12월) & 연간 총량")
    # ✅ 여기서 우측 상단 [단위:GJ] 표기는 완전히 제거
    df_plan_h = make_month_plan_horizontal(df_plan, target_year=int(target_year), plan_col=plan_col)
    df_plan_h_disp = format_table_generic(df_plan_h)
    show_table_no_index(df_plan_h_disp, height=140)

    st.markdown("#### 📋 1. 일별 비율, 예상 공급량 테이블")
    view = df_result.copy()

    view_for_format = view[
        [
            "연", "월", "일", "요일", "weekday_idx", "nth_dow", "구분", "공휴일여부",
            "예상공급량(GJ)", "예상공급량(㎥)",
            "일별비율",
        ]
    ]
    view_for_format = format_table_generic(view_for_format, percent_cols=["일별비율"])
    show_table_no_index(view_for_format, height=520)

    with st.expander("🔎 (검증) 대상월 '1째 월요일/2째 월요일...' 계산 확인"):
        dbg_disp = format_table_generic(df_debug.copy(), percent_cols=["일별비율"])
        show_table_no_index(dbg_disp, height=420)

    st.markdown("#### 📊 2. 일별 예상 공급량(GJ) & 비율 그래프(평일1/평일2/주말 분리)")

    w1_df = view[view["구분"] == "평일1(월·금)"]
    w2_df = view[view["구분"] == "평일2(화·수·목)"]

    wend_df = view[view["구분"] == "주말/공휴일"]
    wend_major = wend_df[wend_df["명절여부"]].copy()
    wend_other = wend_df[~wend_df["명절여부"]].copy()

    fig = go.Figure()
    fig.add_bar(x=w1_df["일"], y=w1_df["예상공급량(GJ)"], name="평일1(월·금) 예상공급량(GJ)")
    fig.add_bar(x=w2_df["일"], y=w2_df["예상공급량(GJ)"], name="평일2(화·수·목) 예상공급량(GJ)")

    fig.add_bar(
        x=wend_other["일"],
        y=wend_other["예상공급량(GJ)"],
        name="주말/공휴일 예상공급량(GJ)",
        marker=dict(color="rgba(160,160,160,1.0)"),
    )

    if not wend_major.empty:
        fig.add_bar(
            x=wend_major["일"],
            y=wend_major["예상공급량(GJ)"],
            name="설날/추석(명절) 예상공급량(GJ)",
            marker=dict(color="rgba(160,160,160,0.35)"),
        )

    fig.add_trace(
        go.Scatter(
            x=view["일"],
            y=view["일별비율"],
            mode="lines+markers",
            name=f"일별비율 (최근{len(used_years)}년)",
            yaxis="y2",
        )
    )

    fig.update_layout(
        xaxis_title="일",
        yaxis=dict(title="예상 공급량 (GJ)"),
        yaxis2=dict(title="일별비율", overlaying="y", side="right"),
        barmode="group",
        margin=dict(l=20, r=20, t=40, b=40),
    )
    st.plotly_chart(fig, use_container_width=True)

    st.markdown("#### 🗂️ 6. 일일계획 다운로드(연간)")

    years_plan = sorted(df_plan["연"].unique())
    annual_year = st.selectbox(
        "연간 계획 연도 선택",
        years_plan,
        index=years_plan.index(target_year) if target_year in years_plan else 0,
        key="annual_year_select",
    )

    default_asof = dt.date(int(annual_year), 1, 16)
    asof_date = st.date_input(
        "누적 기준일 선택(누적계획량 시트 계산용)",
        value=default_asof,
        min_value=dt.date(int(annual_year), 1, 1),
        max_value=dt.date(int(annual_year), 12, 31),
        key="asof_date_select",
    )

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

        for c in range(1, ws_y.max_column + 1):
            ws_y.cell(1, c).font = Font(bold=True)
        for c in range(1, ws_m.max_column + 1):
            ws_m.cell(1, c).font = Font(bold=True)

        _format_excel_sheet(ws_y, freeze="A2", center=True)
        _format_excel_sheet(ws_m, freeze="A2", center=True)

        # ✅ 누적계획량(일대비 진행률) 시트 생성
        _add_cumulative_plan_sheet(wb, asof_date=asof_date)

    st.download_button(
        label=f"📥 {annual_year}년 연간 일별공급계획 다운로드 (Excel)",
        data=buffer_year.getvalue(),
        file_name=f"{annual_year}_연간_일별공급계획_GJ_㎥(일대비진행률).xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key="download_annual_excel",
    )


# ─────────────────────────────────────────────
# 탭2: Daily·Monthly 공급량 비교 (표시 단위만 GJ로)
# ─────────────────────────────────────────────
def tab_daily_monthly_compare(df: pd.DataFrame, df_temp_all: pd.DataFrame):
    min_year_model = int(df["연도"].min())
    max_year_model = int(df["연도"].max())

    st.subheader("📚 데이터 학습기간 선택 (3차 다항식 R² 계산용)")

    train_default_start = max(min_year_model, max_year_model - 4)
    train_start, train_end = st.slider(
        "학습에 사용할 연도 범위",
        min_value=min_year_model,
        max_value=max_year_model,
        value=(train_default_start, max_year_model),
        step=1,
    )

    df_window = df[df["연도"].between(train_start, train_end)].copy()

    df_month = (
        df_window
        .groupby(["연도", "월"], as_index=False)
        .agg(공급량_MJ=("공급량(MJ)", "sum"), 평균기온=("평균기온(℃)", "mean"))
    )
    df_month["공급량_GJ"] = df_month["공급량_MJ"] / MJ_PER_GJ

    coef_m, y_pred_m, r2_m = fit_poly3_and_r2(df_month["평균기온"], df_month["공급량_GJ"])
    df_month["예측공급량_GJ"] = y_pred_m if y_pred_m is not None else np.nan

    df_window["공급량_GJ"] = df_window["공급량(MJ)"] / MJ_PER_GJ
    coef_d, y_pred_d, r2_d = fit_poly3_and_r2(df_window["평균기온(℃)"], df_window["공급량_GJ"])
    df_window["예측공급량_GJ"] = y_pred_d if y_pred_d is not None else np.nan

    col1, col2 = st.columns(2)
    with col1:
        st.metric("R² (월평균 기온, GJ)", f"{r2_m:.3f}" if r2_m is not None else "-")
    with col2:
        st.metric("R² (일평균 기온, GJ)", f"{r2_d:.3f}" if r2_d is not None else "-")


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
