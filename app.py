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
    page_title="도시가스 공급량: 일별 계획/누적(목표 vs 실적)",
    layout="wide",
)


# ─────────────────────────────────────────────
# 데이터 로더
# ─────────────────────────────────────────────
@st.cache_data
def load_daily_supply_all():
    """
    ✅ 실적/패턴/누적용: '공급량(MJ)'만 있으면 포함 (온도 없어도 포함)
    """
    excel_path = Path(__file__).parent / "공급량(일일실적).xlsx"
    df_raw = pd.read_excel(excel_path)

    # 온도 없는 날도 실적엔 필요하니까 dropna 하지 않음
    use_cols = [c for c in ["일자", "공급량(MJ)", "공급량(M3)", "평균기온(℃)"] if c in df_raw.columns]
    df_raw = df_raw[use_cols].copy()
    df_raw["일자"] = pd.to_datetime(df_raw["일자"])

    df_raw["연도"] = df_raw["일자"].dt.year
    df_raw["월"] = df_raw["일자"].dt.month
    df_raw["일"] = df_raw["일자"].dt.day
    return df_raw


@st.cache_data
def load_daily_model_only():
    """
    ✅ 기온-공급량 회귀/비교용: 온도 & 공급량 모두 있는 구간만
    """
    df_all = load_daily_supply_all().copy()
    need = []
    if "평균기온(℃)" in df_all.columns:
        need.append("평균기온(℃)")
    if "공급량(MJ)" in df_all.columns:
        need.append("공급량(MJ)")
    df_model = df_all.dropna(subset=need).copy() if need else df_all.copy()
    df_temp_all = df_all.dropna(subset=["평균기온(℃)"]).copy() if "평균기온(℃)" in df_all.columns else df_all.copy()
    return df_model, df_temp_all


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
# 포맷/엑셀 유틸
# ─────────────────────────────────────────────
def format_table_generic(df, percent_cols=None):
    df = df.copy()
    percent_cols = percent_cols or []

    def _fmt_int(x):
        if pd.isna(x):
            return ""
        try:
            return f"{int(x)}"
        except Exception:
            return str(x)

    for col in df.columns:
        if col in percent_cols:
            df[col] = df[col].map(lambda x: f"{x:.4f}" if pd.notna(x) else "")
        elif pd.api.types.is_numeric_dtype(df[col]):
            if col in ["연", "연도", "월", "일"]:
                df[col] = df[col].map(_fmt_int)
            else:
                df[col] = df[col].map(lambda x: f"{x:,.0f}" if pd.notna(x) else "")
        elif df[col].dtype == bool:
            df[col] = df[col].map(lambda x: "공휴일" if x else "")
    return df


def show_table_no_index(df: pd.DataFrame, height: int = 260):
    try:
        st.dataframe(df, use_container_width=True, hide_index=True, height=height)
    except TypeError:
        st.table(df)


def _format_excel_sheet(ws, freeze="A2"):
    if freeze:
        ws.freeze_panes = freeze
    for row in ws.iter_rows(min_row=1, max_row=ws.max_row, min_col=1, max_col=ws.max_column):
        for c in row:
            c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)


def _excel_find_col_letter(ws, header_name: str) -> str | None:
    header = [c.value for c in ws[1]]
    for idx, name in enumerate(header, start=1):
        if str(name).strip() == header_name:
            return get_column_letter(idx)
    return None


def _find_plan_col(df_plan: pd.DataFrame) -> str:
    candidates = ["계획(사업계획제출_MJ)", "계획(사업계획제출)", "계획_MJ", "계획"]
    for c in candidates:
        if c in df_plan.columns:
            return c
    nums = [c for c in df_plan.columns if pd.api.types.is_numeric_dtype(df_plan[c])]
    return nums[0] if nums else "계획(사업계획제출_MJ)"


def make_month_plan_horizontal(df_plan: pd.DataFrame, target_year: int, plan_col: str) -> pd.DataFrame:
    df_year = df_plan[df_plan["연"] == target_year][["월", plan_col]].copy()
    base = pd.DataFrame({"월": list(range(1, 13))})
    df_year = base.merge(df_year, on="월", how="left")

    df_year["GJ"] = mj_to_gj(df_year[plan_col])
    df_year["m3"] = mj_to_nm3(df_year[plan_col])

    row_gj = {f"{m}월": df_year.loc[df_year["월"] == m, "GJ"].iloc[0] for m in range(1, 13)}
    row_m3 = {f"{m}월": df_year.loc[df_year["월"] == m, "m3"].iloc[0] for m in range(1, 13)}
    row_gj["연간합계"] = df_year["GJ"].sum(skipna=True)
    row_m3["연간합계"] = df_year["m3"].sum(skipna=True)

    out = pd.DataFrame([row_gj, row_m3])
    out.insert(0, "구분", ["사업계획(월별 계획, GJ)", "사업계획(월별 계획, ㎥)"])
    return out


# ─────────────────────────────────────────────
# ✅ 누적계획량 시트: (기준일까지 실적 누적) / (기준일까지 목표 누적)
# ─────────────────────────────────────────────
def _add_cumulative_plan_sheet(wb, asof_date: dt.date):
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
    ws_c["B3"].value = "목표(GJ)"          # ✅ 기준일까지 목표 누적
    ws_c["C3"].value = "누적(GJ)"          # ✅ 기준일까지 실적 누적
    ws_c["D3"].value = "목표(㎥)"
    ws_c["E3"].value = "누적(㎥)"
    ws_c["F3"].value = "진행률(일대비, GJ)"  # ✅ (기준일까지 실적)/(기준일까지 목표)

    for c in range(1, 7):
        ws_c.cell(3, c).font = Font(bold=True)
        ws_c.cell(3, c).alignment = Alignment(horizontal="center", vertical="center")

    ws_c["A4"].value = "일"
    ws_c["A5"].value = "월"
    ws_c["A6"].value = "연"

    rng_date = f"연간!${date_col}:${date_col}"
    rng_plan_gj = f"연간!${plan_gj_col}:${plan_gj_col}"
    rng_plan_m3 = f"연간!${plan_m3_col}:${plan_m3_col}"
    rng_act_gj = f"연간!${act_gj_col}:${act_gj_col}"
    rng_act_m3 = f"연간!${act_m3_col}:${act_m3_col}"

    # 일: 해당일 실적 / 해당일 목표
    ws_c["B4"].value = f'=SUMIFS({rng_plan_gj},{rng_date},$B$1)'
    ws_c["C4"].value = f'=SUMIFS({rng_act_gj},{rng_date},$B$1)'
    ws_c["D4"].value = f'=SUMIFS({rng_plan_m3},{rng_date},$B$1)'
    ws_c["E4"].value = f'=SUMIFS({rng_act_m3},{rng_date},$B$1)'
    ws_c["F4"].value = "=IFERROR(C4/B4,0)"

    # 월: 기준일까지 누적 실적 / 기준일까지 누적 목표
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

    # 연: 기준일까지 누적 실적 / 기준일까지 누적 목표
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
    for col, w in {"A": 10, "B": 14, "C": 14, "D": 16, "E": 16, "F": 18}.items():
        ws_c.column_dimensions[col].width = w

    for r in range(4, 7):
        for col in ["A", "B", "C", "D", "E", "F"]:
            ws_c[f"{col}{r}"].alignment = Alignment(horizontal="center", vertical="center")
        for col in ["B", "C", "D", "E"]:
            ws_c[f"{col}{r}"].number_format = "#,##0"
        ws_c[f"F{r}"].number_format = "0.00%"


# ─────────────────────────────────────────────
# 일별 계획 생성 (최근 N년 패턴)
# ─────────────────────────────────────────────
def make_daily_plan_table(df_supply_all: pd.DataFrame, df_plan: pd.DataFrame, target_year: int, target_month: int, recent_window: int):
    cal_df = load_effective_calendar()
    plan_col = _find_plan_col(df_plan)

    # ✅ 패턴 산출도 온도 필요 없으니까 공급량 있는 전체에서 계산
    all_years = sorted(df_supply_all["연도"].unique())
    candidate_years = [y for y in range(target_year - recent_window, target_year) if y in all_years]

    df_pool = df_supply_all[(df_supply_all["연도"].isin(candidate_years)) & (df_supply_all["월"] == target_month)].copy()
    df_pool = df_pool.dropna(subset=["공급량(MJ)"])
    used_years = sorted(df_pool["연도"].unique().tolist())
    if len(used_years) == 0:
        return None, None, [], pd.DataFrame()

    df_recent = df_supply_all[(df_supply_all["연도"].isin(used_years)) & (df_supply_all["월"] == target_month)].copy()
    df_recent = df_recent.dropna(subset=["공급량(MJ)"])
    df_recent = df_recent.sort_values(["연도", "일"]).copy()
    df_recent["weekday_idx"] = df_recent["일자"].dt.weekday

    if cal_df is not None:
        df_recent = df_recent.merge(cal_df, on="일자", how="left")
        df_recent["공휴일여부"] = df_recent["공휴일여부"].fillna(False).astype(bool)
        df_recent["명절여부"] = df_recent["명절여부"].fillna(False).astype(bool)
    else:
        df_recent["공휴일여부"] = False
        df_recent["명절여부"] = False

    df_recent["is_holiday"] = df_recent["공휴일여부"] | df_recent["명절여부"]
    df_recent["is_weekend"] = (df_recent["weekday_idx"] >= 5) | df_recent["is_holiday"]
    df_recent["is_weekday1"] = (~df_recent["is_weekend"]) & (df_recent["weekday_idx"].isin([0, 4]))
    df_recent["is_weekday2"] = (~df_recent["is_weekend"]) & (df_recent["weekday_idx"].isin([1, 2, 3]))

    df_recent["month_total"] = df_recent.groupby("연도")["공급량(MJ)"].transform("sum")
    df_recent["ratio"] = df_recent["공급량(MJ)"] / df_recent["month_total"]
    df_recent["nth_dow"] = df_recent.sort_values(["연도", "일"]).groupby(["연도", "weekday_idx"]).cumcount() + 1

    def _mean_dict(mask, keys):
        if df_recent[mask].empty:
            return {}, {}
        g = df_recent[mask].groupby(keys)["ratio"].mean().to_dict()
        d = df_recent[mask].groupby("weekday_idx")["ratio"].mean().to_dict()
        return g, d

    weekend_group, weekend_dow = _mean_dict(df_recent["is_weekend"], ["weekday_idx", "nth_dow"])
    w1_group, w1_dow = _mean_dict(df_recent["is_weekday1"], ["weekday_idx", "nth_dow"])
    w2_group, w2_dow = _mean_dict(df_recent["is_weekday2"], ["weekday_idx", "nth_dow"])

    last_day = calendar.monthrange(target_year, target_month)[1]
    date_range = pd.date_range(f"{target_year}-{target_month:02d}-01", periods=last_day, freq="D")

    df_target = pd.DataFrame({"일자": date_range})
    df_target["연"] = target_year
    df_target["월"] = target_month
    df_target["일"] = df_target["일자"].dt.day
    df_target["weekday_idx"] = df_target["일자"].dt.weekday

    if cal_df is not None:
        df_target = df_target.merge(cal_df, on="일자", how="left")
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
        dow, nth = int(row["weekday_idx"]), int(row["nth_dow"])
        key = (dow, nth)
        if row["is_weekend"]:
            return weekend_group.get(key, weekend_dow.get(dow, np.nan))
        if row["is_weekday1"]:
            return w1_group.get(key, w1_dow.get(dow, np.nan))
        return w2_group.get(key, w2_dow.get(dow, np.nan))

    df_target["raw"] = df_target.apply(_pick_ratio, axis=1).astype("float64")
    overall_mean = df_target["raw"].dropna().mean()
    df_target["raw"] = df_target["raw"].fillna(overall_mean if pd.notna(overall_mean) else 1.0)

    df_target["일별비율"] = df_target["raw"] / df_target["raw"].sum()

    row_plan = df_plan[(df_plan["연"] == target_year) & (df_plan["월"] == target_month)]
    plan_total_mj = float(row_plan[plan_col].iloc[0]) if not row_plan.empty else np.nan

    df_target["예상공급량(GJ)"] = (mj_to_gj(df_target["일별비율"] * plan_total_mj)).round(0)
    df_target["예상공급량(㎥)"] = (mj_to_nm3(df_target["일별비율"] * plan_total_mj)).round(0)

    df_result = df_target[
        ["연", "월", "일", "일자", "요일", "weekday_idx", "nth_dow", "구분",
         "공휴일여부", "명절여부", "일별비율", "예상공급량(GJ)", "예상공급량(㎥)"]
    ].copy()

    return df_result, None, used_years, df_target[["일", "일자", "요일", "weekday_idx", "nth_dow", "raw", "일별비율"]].copy()


def _build_year_daily_plan(df_supply_all: pd.DataFrame, df_plan: pd.DataFrame, target_year: int, recent_window: int):
    """
    ✅ 연간 시트에 '실적공급량(GJ/㎥)' 채움:
    - 공급량(일일실적).xlsx의 해당 날짜 실적을 그대로 매칭 (온도 무관)
    """
    df_act_year = df_supply_all[df_supply_all["연도"] == target_year][["일자", "공급량(MJ)"]].dropna().copy()
    act_map_mj = dict(zip(df_act_year["일자"].dt.normalize(), df_act_year["공급량(MJ)"]))

    all_rows = []
    df_plan_col = _find_plan_col(df_plan)

    for m in range(1, 13):
        df_res, _, _, _ = make_daily_plan_table(df_supply_all, df_plan, target_year, m, recent_window)
        if df_res is None:
            # 데이터가 없으면 균등분배(최소 안전장치)
            last_day = calendar.monthrange(target_year, m)[1]
            dr = pd.date_range(f"{target_year}-{m:02d}-01", periods=last_day, freq="D")
            row_plan = df_plan[(df_plan["연"] == target_year) & (df_plan["월"] == m)]
            plan_total_mj = float(row_plan[df_plan_col].iloc[0]) if not row_plan.empty else np.nan

            tmp = pd.DataFrame({"일자": dr})
            tmp["연"] = target_year
            tmp["월"] = m
            tmp["일"] = tmp["일자"].dt.day
            tmp["요일"] = tmp["일자"].dt.weekday.map(lambda i: ["월","화","수","목","금","토","일"][i])
            tmp["구분"] = np.where(tmp["일자"].dt.weekday >= 5, "주말/공휴일", "평일")
            tmp["공휴일여부"] = False
            tmp["명절여부"] = False
            tmp["일별비율"] = 1.0 / last_day
            tmp["예상공급량(GJ)"] = (mj_to_gj(tmp["일별비율"] * plan_total_mj)).round(0)
            tmp["예상공급량(㎥)"] = (mj_to_nm3(tmp["일별비율"] * plan_total_mj)).round(0)
            df_res = tmp[["연","월","일","일자","요일","구분","공휴일여부","명절여부","일별비율","예상공급량(GJ)","예상공급량(㎥)"]].copy()

        norm_date = pd.to_datetime(df_res["일자"]).dt.normalize()
        df_res["실적공급량(MJ)"] = norm_date.map(act_map_mj)
        df_res["실적공급량(GJ)"] = mj_to_gj(df_res["실적공급량(MJ)"])
        df_res["실적공급량(㎥)"] = mj_to_nm3(df_res["실적공급량(MJ)"])

        all_rows.append(df_res)

    df_year = pd.concat(all_rows, ignore_index=True).sort_values(["월","일"]).reset_index(drop=True)

    # 합계행(선택)
    total = {c: "" for c in df_year.columns}
    total["요일"] = "합계"
    for c in ["일별비율", "예상공급량(GJ)", "예상공급량(㎥)", "실적공급량(GJ)", "실적공급량(㎥)"]:
        if c in df_year.columns:
            total[c] = df_year[c].sum(skipna=True)
    df_year = pd.concat([df_year, pd.DataFrame([total])], ignore_index=True)

    return df_year


# ─────────────────────────────────────────────
# 메인 탭: Daily 계획 + 연간 다운로드
# ─────────────────────────────────────────────
def tab_daily_plan():
    st.title("도시가스 공급량 — 일별계획 & 누적(목표 vs 실적)")

    df_supply_all = load_daily_supply_all()
    df_plan = load_monthly_plan()
    plan_col = _find_plan_col(df_plan)

    years_plan = sorted(df_plan["연"].unique())
    default_year_idx = years_plan.index(2026) if 2026 in years_plan else len(years_plan)-1

    c1, c2, c3 = st.columns([1,1,2])
    with c1:
        target_year = st.selectbox("계획 연도", years_plan, index=default_year_idx)
    with c2:
        months_plan = sorted(df_plan[df_plan["연"] == target_year]["월"].unique())
        target_month = st.selectbox("계획 월", months_plan, index=0, format_func=lambda m: f"{m}월")
    with c3:
        recent_window = st.slider("최근 몇 년 패턴 사용?", 1, 10, 3, 1)

    # 월별 계획(가로) — ✅ 우측 상단 단위표기 삭제
    st.subheader("📌 월별 계획량(1~12월) & 연간 총량")
    df_plan_h = make_month_plan_horizontal(df_plan, int(target_year), plan_col)
    show_table_no_index(format_table_generic(df_plan_h), height=140)

    # 일별 테이블 + 그래프
    df_result, _, used_years, _dbg = make_daily_plan_table(df_supply_all, df_plan, int(target_year), int(target_month), int(recent_window))
    if df_result is None:
        st.warning("선택한 조건으로 일별 계획 생성이 안됨(해당 월 실적이 있는 과거년도 부족).")
        return

    st.caption(f"패턴 사용 연도(해당월 실적 존재): {used_years}")

    st.subheader("📋 일별 계획(GJ/㎥)")
    show_table_no_index(format_table_generic(df_result, percent_cols=["일별비율"]), height=520)

    wend = df_result[df_result["구분"] == "주말/공휴일"]
    major = wend[wend["명절여부"]]
    other = wend[~wend["명절여부"]]
    w1 = df_result[df_result["구분"] == "평일1(월·금)"]
    w2 = df_result[df_result["구분"] == "평일2(화·수·목)"]

    fig = go.Figure()
    fig.add_bar(x=w1["일"], y=w1["예상공급량(GJ)"], name="평일1(월·금)")
    fig.add_bar(x=w2["일"], y=w2["예상공급량(GJ)"], name="평일2(화·수·목)")
    fig.add_bar(x=other["일"], y=other["예상공급량(GJ)"], name="주말/공휴일", marker=dict(color="rgba(160,160,160,1.0)"))
    if not major.empty:
        fig.add_bar(x=major["일"], y=major["예상공급량(GJ)"], name="설/추석(명절)", marker=dict(color="rgba(160,160,160,0.35)"))
    fig.update_layout(barmode="group", xaxis_title="일", yaxis_title="예상공급량(GJ)", margin=dict(l=20,r=20,t=30,b=30))
    st.plotly_chart(fig, use_container_width=True)

    # ── 연간 다운로드 (+ 누적계획량: 일대비 진행률)
    st.subheader("🗂️ 일일계획 다운로드(연간)")

    annual_year = st.selectbox("연간 계획 연도 선택", years_plan, index=years_plan.index(target_year) if target_year in years_plan else 0)
    asof_date = st.date_input(
        "누적 기준일(기준일까지 실적/목표 누적 후 진행률 계산)",
        value=dt.date(int(annual_year), 1, 17),
        min_value=dt.date(int(annual_year), 1, 1),
        max_value=dt.date(int(annual_year), 12, 31),
    )

    df_year = _build_year_daily_plan(df_supply_all, df_plan, int(annual_year), int(recent_window))

    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df_year.to_excel(writer, index=False, sheet_name="연간")

        wb = writer.book
        ws = wb["연간"]
        for c in range(1, ws.max_column + 1):
            ws.cell(1, c).font = Font(bold=True)
        _format_excel_sheet(ws, freeze="A2")

        _add_cumulative_plan_sheet(wb, asof_date)

    st.download_button(
        "📥 연간 일별공급계획 다운로드(누적=기준일까지 실적/목표)",
        data=buf.getvalue(),
        file_name=f"{annual_year}_연간_일별공급계획_GJ_㎥_누적(실적대비).xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


def main():
    tab_daily_plan()


if __name__ == "__main__":
    main()
