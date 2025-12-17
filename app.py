# app.py ─ 도시가스 공급량: Daily 계획 + Daily·Monthly 비교 (GJ + ㎥ 표기)

import calendar
from io import BytesIO
from pathlib import Path
from typing import List, Tuple, Optional

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from openpyxl.styles import Font, Alignment
from openpyxl.utils import get_column_letter


# ─────────────────────────────────────────────
# 단위/환산
# ─────────────────────────────────────────────
MJ_PER_NM3 = 42.563  # MJ/Nm3 (고정)

def to_num(x):
    try:
        if pd.isna(x):
            return np.nan
        return float(str(x).replace(",", "").strip())
    except Exception:
        return np.nan

def mj_to_gj(mj: float) -> float:
    if mj is None or pd.isna(mj):
        return np.nan
    return float(mj) / 1000.0

def gj_to_mj(gj: float) -> float:
    if gj is None or pd.isna(gj):
        return np.nan
    return float(gj) * 1000.0

def mj_to_m3(mj: float) -> float:
    if mj is None or pd.isna(mj):
        return np.nan
    return float(mj) / MJ_PER_NM3

def gj_to_m3(gj: float) -> float:
    if gj is None or pd.isna(gj):
        return np.nan
    return mj_to_m3(gj_to_mj(gj))


# ─────────────────────────────────────────────
# 데이터 로딩
# ─────────────────────────────────────────────
@st.cache_data(show_spinner=False)
def load_daily_data() -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    반환:
      df_model     : 공급량(MJ) + 평균기온 있는 구간(예측/R² 계산용)
      df_temp_all  : 평균기온만 있어도 되는 전체 구간(히트맵/선택용)
    """
    excel_path = Path(__file__).parent / "공급량(일일실적).xlsx"
    df_raw = pd.read_excel(excel_path)

    cols = df_raw.columns.astype(str).tolist()

    def pick(cands: List[str], default_idx=0):
        for k in cands:
            for c in cols:
                if k in c:
                    return c
        return cols[default_idx]

    c_date = pick(["일자", "날짜", "date"], 0)
    c_mj   = pick(["공급량(MJ)", "공급량", "MJ"], 1)
    c_temp = pick(["평균기온", "기온", "temp"], 2)

    df = df_raw.copy()
    df["일자"] = pd.to_datetime(df[c_date])
    df["공급량(MJ)"] = df[c_mj].apply(to_num)
    df["평균기온(℃)"] = df[c_temp].apply(to_num)

    df["연도"] = df["일자"].dt.year
    df["월"] = df["일자"].dt.month
    df["일"] = df["일자"].dt.day
    df["weekday_idx"] = df["일자"].dt.weekday  # 월0~일6

    df_model = df.dropna(subset=["공급량(MJ)", "평균기온(℃)"]).copy()
    df_temp_all = df.dropna(subset=["평균기온(℃)"]).copy()

    df_model["공급량_GJ"] = df_model["공급량(MJ)"].apply(mj_to_gj)
    df_model["공급량_㎥"] = df_model["공급량(MJ)"].apply(mj_to_m3)

    df_temp_all = df_temp_all.sort_values("일자").reset_index(drop=True)
    df_model = df_model.sort_values("일자").reset_index(drop=True)
    return df_model, df_temp_all


def _auto_find_plan_file() -> Optional[Path]:
    """
    월별 계획 파일을 폴더에서 자동 탐색.
    """
    base = Path(__file__).parent

    candidates = [
        "공급계획_월별.xlsx",
        "공급량(계획_실적).xlsx",
        "공급계획.xlsx",
        "월별계획.xlsx",
        "사업계획.xlsx",
    ]
    for name in candidates:
        p = base / name
        if p.exists():
            return p

    xlsx = sorted(base.glob("*.xlsx"), key=lambda x: x.stat().st_mtime, reverse=True)
    for p in xlsx:
        nm = p.name.lower()
        if any(k in nm for k in ["계획", "plan", "월별", "공급"]):
            return p

    return None


def _read_plan_excel(src, preferred_sheets: Optional[List[str]] = None) -> pd.DataFrame:
    """
    src: Path 또는 업로드 파일(BytesIO)
    """
    preferred_sheets = preferred_sheets or ["월별계획_실적", "월별계획", "계획", "Plan", "월별"]

    try:
        df = pd.read_excel(src, sheet_name=0)
        if isinstance(df, pd.DataFrame) and not df.empty:
            return df
    except Exception:
        pass

    for sh in preferred_sheets:
        try:
            df = pd.read_excel(src, sheet_name=sh)
            if isinstance(df, pd.DataFrame) and not df.empty:
                return df
        except Exception:
            continue

    return pd.read_excel(src)


def _promote_first_row_to_header_if_needed(df: pd.DataFrame) -> pd.DataFrame:
    """
    헤더가 2행에 있거나, Unnamed 컬럼이 대부분인 케이스 처리:
    - 첫 행에 '연/월/계획' 같은 키워드가 보이면 첫 행을 헤더로 승격
    """
    if df is None or df.empty:
        return df

    cols = [str(c) for c in df.columns]
    unnamed_ratio = np.mean([("unnamed" in c.lower()) for c in cols])

    first_row = df.iloc[0].astype(str).tolist()
    hit = sum(("연" in v or "년도" in v or "연도" in v or "월" in v or "계획" in v or "사업" in v or "plan" in v.lower()) for v in first_row)

    if unnamed_ratio >= 0.5 and hit >= 2:
        df2 = df.copy()
        df2.columns = df2.iloc[0].astype(str)
        df2 = df2.iloc[1:].reset_index(drop=True)
        return df2

    return df


def _normalize_year_month_columns(df: pd.DataFrame) -> Tuple[pd.DataFrame, Optional[str], Optional[str]]:
    """
    1) 컬럼명으로 연/월 찾기
    2) 없으면 값 패턴(연: 1990~2100 / 월: 1~12)으로 찾기
    """
    df = _promote_first_row_to_header_if_needed(df)

    cols = [str(c) for c in df.columns]

    # 1) 이름 기반 탐색
    year_keys = ["연도", "년도", "연", "year", "yyyy"]
    month_keys = ["월", "month", "mm"]

    year_cands = [c for c in cols if any(k in c.lower() for k in [k.lower() for k in year_keys])]
    month_cands = [c for c in cols if any(k in c.lower() for k in [k.lower() for k in month_keys])]

    year_col = None
    month_col = None

    if year_cands:
        # '연' 단독/짧은 컬럼 우선
        year_col = sorted(year_cands, key=lambda x: (len(x), x))[0]
    if month_cands:
        month_col = sorted(month_cands, key=lambda x: (len(x), x))[0]

    # 2) 값 패턴 기반 탐색(이름으로 못 찾은 경우)
    def score_year(s: pd.Series) -> float:
        x = s.apply(to_num)
        x = x.dropna()
        if x.empty:
            return 0.0
        ok = ((x >= 1990) & (x <= 2100)).mean()
        return float(ok)

    def score_month(s: pd.Series) -> float:
        x = s.apply(to_num)
        x = x.dropna()
        if x.empty:
            return 0.0
        ok = ((x >= 1) & (x <= 12)).mean()
        return float(ok)

    if year_col is None:
        best = (0.0, None)
        for c in cols:
            sc = score_year(df[c])
            if sc > best[0]:
                best = (sc, c)
        if best[0] >= 0.4:
            year_col = best[1]

    if month_col is None:
        best = (0.0, None)
        for c in cols:
            sc = score_month(df[c])
            if sc > best[0]:
                best = (sc, c)
        if best[0] >= 0.4:
            month_col = best[1]

    # rename
    out = df.copy()
    if year_col is not None and year_col != "연":
        out = out.rename(columns={year_col: "연"})
    if month_col is not None and month_col != "월":
        out = out.rename(columns={month_col: "월"})

    return out, year_col, month_col


def _find_plan_col(df_plan: pd.DataFrame) -> str:
    """
    계획량 컬럼 자동 탐색
    """
    candidates = ["사업", "제출", "월별", "계획", "공급", "물량", "plan", "total", "GJ", "MJ"]
    cols = df_plan.columns.astype(str).tolist()

    for c in cols:
        if any(k.lower() in c.lower() for k in candidates):
            s = df_plan[c].apply(to_num)
            if s.notna().any():
                return c

    for c in reversed(cols):
        s = df_plan[c].apply(to_num)
        if s.notna().any():
            return c

    return cols[-1]


def _normalize_plan_to_mj(df_plan: pd.DataFrame, plan_col: str) -> pd.DataFrame:
    """
    계획량 컬럼이 MJ인지 GJ인지 섞여도 내부는 MJ로 통일.
    - 월 계획 중앙값이 1e8 이상이면 MJ로 간주
    - 그보다 작으면 GJ로 간주하고 *1000 해서 MJ로 변환
    """
    out = df_plan.copy()
    v = out[plan_col].apply(to_num)
    med = float(np.nanmedian(v.values)) if np.isfinite(np.nanmedian(v.values)) else np.nan

    out[plan_col] = v
    if pd.isna(med):
        return out

    if med >= 1e8:
        return out

    out[plan_col] = out[plan_col] * 1000.0
    return out


# ─────────────────────────────────────────────
# 표/엑셀 유틸
# ─────────────────────────────────────────────
def show_table_no_index(df: pd.DataFrame, height=260):
    st.dataframe(df, use_container_width=True, hide_index=True, height=height)

def _format_excel_sheet(ws, freeze="A2", center=True):
    ws.freeze_panes = freeze
    if center:
        for row in ws.iter_rows(min_row=1, max_row=ws.max_row, min_col=1, max_col=ws.max_column):
            for cell in row:
                cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    for col in range(1, ws.max_column + 1):
        letter = get_column_letter(col)
        ws.column_dimensions[letter].width = max(10, min(22, ws.column_dimensions[letter].width or 12))

def _add_cumulative_status_sheet(wb, annual_year: int):
    ws = wb.create_sheet("누적계획현황")

    ws["A1"] = "기준일"
    ws["B1"] = f"{annual_year}-01-01"
    ws["A3"] = "구분"
    ws["B3"] = "목표(GJ)"
    ws["C3"] = "누적(GJ)"
    ws["D3"] = "목표(㎥)"
    ws["E3"] = "누적(㎥)"
    ws["F3"] = "진행률(GJ)"

    for cell in ["A1","A3","B3","C3","D3","E3","F3"]:
        ws[cell].font = Font(bold=True)

    ws["A4"] = "일"
    ws["A5"] = "월"
    ws["A6"] = "연"

    ws["B4"] = '=SUMIFS(연간!$F:$F, 연간!$A:$A, $B$1)'
    ws["C4"] = '=SUMIFS(연간!$F:$F, 연간!$A:$A, $B$1)'
    ws["D4"] = '=SUMIFS(연간!$G:$G, 연간!$A:$A, $B$1)'
    ws["E4"] = '=SUMIFS(연간!$G:$G, 연간!$A:$A, $B$1)'
    ws["F4"] = '=IFERROR(C4/B4,0)'

    ws["B5"] = '=SUMIFS(연간!$F:$F, 연간!$A:$A, ">="&EOMONTH($B$1,-1)+1, 연간!$A:$A, "<="&EOMONTH($B$1,0))'
    ws["C5"] = '=SUMIFS(연간!$F:$F, 연간!$A:$A, ">="&EOMONTH($B$1,-1)+1, 연간!$A:$A, "<="&$B$1)'
    ws["D5"] = '=SUMIFS(연간!$G:$G, 연간!$A:$A, ">="&EOMONTH($B$1,-1)+1, 연간!$A:$A, "<="&EOMONTH($B$1,0))'
    ws["E5"] = '=SUMIFS(연간!$G:$G, 연간!$A:$A, ">="&EOMONTH($B$1,-1)+1, 연간!$A:$A, "<="&$B$1)'
    ws["F5"] = '=IFERROR(C5/B5,0)'

    ws["B6"] = '=SUM(연간!$F:$F)'
    ws["C6"] = '=SUMIFS(연간!$F:$F, 연간!$A:$A, "<="&$B$1)'
    ws["D6"] = '=SUM(연간!$G:$G)'
    ws["E6"] = '=SUMIFS(연간!$G:$G, 연간!$A:$A, "<="&$B$1)'
    ws["F6"] = '=IFERROR(C6/B6,0)'

    _format_excel_sheet(ws, freeze="A4", center=True)
    ws["B1"].number_format = "yyyy-mm-dd"


# ─────────────────────────────────────────────
# 탭1: 일별 계획 생성(최근 N년 패턴)
# ─────────────────────────────────────────────
def make_daily_plan_table(
    df_daily: pd.DataFrame,
    df_plan: pd.DataFrame,
    plan_col: str,
    target_year: int,
    target_month: int,
    recent_window: int,
):
    last_day = calendar.monthrange(target_year, target_month)[1]

    all_years = sorted(df_daily["연도"].unique())
    hist_years = [y for y in all_years if y < target_year]
    used_years = hist_years[-recent_window:]
    df_recent = df_daily[(df_daily["연도"].isin(used_years)) & (df_daily["월"] == target_month)].copy()

    if "공휴일여부" not in df_recent.columns:
        df_recent["공휴일여부"] = False

    days = pd.date_range(f"{target_year}-{target_month:02d}-01", f"{target_year}-{target_month:02d}-{last_day:02d}", freq="D")
    df_target = pd.DataFrame({"일자": days})
    df_target["연"] = target_year
    df_target["월"] = target_month
    df_target["일"] = df_target["일자"].dt.day
    df_target["weekday_idx"] = df_target["일자"].dt.weekday
    if "공휴일여부" not in df_target.columns:
        df_target["공휴일여부"] = False

    df_target["is_weekend"] = (df_target["weekday_idx"] >= 5) | (df_target["공휴일여부"] == True)
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

    df_recent = df_recent.copy()
    df_recent["day"] = df_recent["일자"].dt.day
    df_recent["nth_dow"] = df_recent.sort_values("day").groupby(["연도", "weekday_idx"]).cumcount() + 1

    ratio_weekend_group = (
        df_recent[df_recent["weekday_idx"].isin([5, 6]) | (df_recent["공휴일여부"] == True)]
        .groupby(["weekday_idx", "nth_dow"])["공급량(MJ)"].mean()
    )
    ratio_weekend_by_dow = (
        df_recent[df_recent["weekday_idx"].isin([5, 6]) | (df_recent["공휴일여부"] == True)]
        .groupby(["weekday_idx"])["공급량(MJ)"].mean()
    )

    ratio_w1_group = df_recent[df_recent["weekday_idx"].isin([0, 4])].groupby(["weekday_idx", "nth_dow"])["공급량(MJ)"].mean()
    ratio_w1_by_dow = df_recent[df_recent["weekday_idx"].isin([0, 4])].groupby(["weekday_idx"])["공급량(MJ)"].mean()

    ratio_w2_group = df_recent[df_recent["weekday_idx"].isin([1, 2, 3])].groupby(["weekday_idx", "nth_dow"])["공급량(MJ)"].mean()
    ratio_w2_by_dow = df_recent[df_recent["weekday_idx"].isin([1, 2, 3])].groupby(["weekday_idx"])["공급량(MJ)"].mean()

    ratio_weekend_group_dict = ratio_weekend_group.to_dict()
    ratio_weekend_by_dow_dict = ratio_weekend_by_dow.to_dict()
    ratio_w1_group_dict = ratio_w1_group.to_dict()
    ratio_w1_by_dow_dict = ratio_w1_by_dow.to_dict()
    ratio_w2_group_dict = ratio_w2_group.to_dict()
    ratio_w2_by_dow_dict = ratio_w2_by_dow.to_dict()

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

    month_total_all = df_recent["공급량(MJ)"].sum()
    df_target["최근N년_총공급량(MJ)"] = df_target["일별비율"] * month_total_all
    df_target["최근N년_평균공급량(MJ)"] = df_target["최근N년_총공급량(MJ)"] / max(1, len(used_years))

    row_plan = df_plan[(df_plan["연"] == target_year) & (df_plan["월"] == target_month)]
    plan_total = float(row_plan[plan_col].apply(to_num).iloc[0]) if not row_plan.empty else np.nan

    df_target["예상공급량(MJ)"] = (df_target["일별비율"] * plan_total).round(0)
    df_target = df_target.sort_values("일").reset_index(drop=True)

    df_result = df_target[
        ["연", "월", "일", "일자", "요일", "weekday_idx", "nth_dow", "구분", "공휴일여부",
         "최근N년_평균공급량(MJ)", "최근N년_총공급량(MJ)", "일별비율", "예상공급량(MJ)"]
    ].copy()

    df_mat = (
        df_recent.pivot_table(index="day", columns="연도", values="공급량(MJ)", aggfunc="sum")
        .sort_index()
        .reindex(range(1, last_day + 1))
    )

    return df_result, df_mat, used_years


def _make_display_table_gj_m3(df_mj: pd.DataFrame) -> pd.DataFrame:
    df = df_mj.copy()
    base_col = "예상공급량(MJ)"
    df["예상공급량(GJ)"] = df[base_col].apply(mj_to_gj).round(0)
    df["예상공급량(㎥)"] = df[base_col].apply(mj_to_m3).round(0)
    keep = ["일자", "요일", "구분", "공휴일여부", "일별비율", "예상공급량(GJ)", "예상공급량(㎥)"]
    return df[keep].copy()


# ─────────────────────────────────────────────
# 탭1 UI
# ─────────────────────────────────────────────
def tab_daily_plan(df_daily: pd.DataFrame):
    st.subheader("📅 Daily 공급량 분석 — 최근 N년 패턴 기반 일별 계획")

    plan_path = _auto_find_plan_file()
    uploaded = st.file_uploader(
        "월별 계획 엑셀 업로드(XLSX) (없으면 폴더에서 자동 탐색)",
        type=["xlsx"],
        key="plan_uploader",
    )

    if uploaded is not None:
        df_plan = _read_plan_excel(uploaded)
    else:
        if plan_path is None:
            st.error("월별 계획 파일을 폴더에서 찾지 못했어. 위에 업로드로 넣어주면 돼.")
            st.stop()
        df_plan = _read_plan_excel(plan_path)

    # ✅ 여기서 연/월 컬럼을 ‘무조건 찾도록’ 보강
    df_plan, ycol, mcol = _normalize_year_month_columns(df_plan)

    if "연" not in df_plan.columns or "월" not in df_plan.columns:
        st.error("계획 파일에서 연/월 컬럼을 인식하지 못했어. 아래 컬럼명을 확인해줘.")
        st.write("컬럼:", list(df_plan.columns))
        st.dataframe(df_plan.head(20), use_container_width=True)
        st.stop()

    df_plan["연"] = df_plan["연"].apply(to_num).astype("Int64")
    df_plan["월"] = df_plan["월"].apply(to_num).astype("Int64")

    plan_col = _find_plan_col(df_plan)
    df_plan = _normalize_plan_to_mj(df_plan, plan_col)

    years_plan = sorted([int(x) for x in df_plan["연"].dropna().unique().tolist()])
    if not years_plan:
        st.error("계획파일에서 '연/월/계획량'을 읽지 못했어. 컬럼명을 확인해줘.")
        st.stop()

    default_year_idx = years_plan.index(2026) if 2026 in years_plan else len(years_plan) - 1

    col_y, col_m, col_n = st.columns([1, 1, 2])
    with col_y:
        target_year = st.selectbox("계획 연도 선택", years_plan, index=default_year_idx)
    with col_m:
        months_plan = sorted([int(x) for x in df_plan[df_plan["연"] == target_year]["월"].dropna().unique().tolist()])
        default_month_idx = months_plan.index(1) if 1 in months_plan else 0
        target_month = st.selectbox("계획 월 선택", months_plan, index=default_month_idx, format_func=lambda m: f"{m}월")
    with col_n:
        recent_window = st.slider("최근 몇 년 평균으로 비율을 계산할까?", 1, 10, 3, step=1)

    all_years = sorted(df_daily["연도"].unique())
    hist_years = [y for y in all_years if y < int(target_year)]
    used_years = hist_years[-int(recent_window):]
    if used_years:
        st.markdown(f"- **실제 학습에 사용된 연도(해당월 실적 존재)**: {used_years[0]}년 ~ {used_years[-1]}년 (총 {len(used_years)}개)")
    else:
        st.markdown("- 학습 연도 없음")

    row_plan = df_plan[(df_plan["연"] == target_year) & (df_plan["월"] == target_month)]
    plan_total_mj = float(row_plan[plan_col].apply(to_num).iloc[0]) if not row_plan.empty else np.nan
    st.markdown(f"**{target_year}년 {target_month}월 사업계획 제출 공급량 합계**:  {mj_to_gj(plan_total_mj):,.0f} GJ")

    st.markdown("### 🧩 일별 공급량 분배 기준")
    st.markdown(
        "- 주말/공휴일/명절: 요일(토/일) + 그 달의 n번째 기준 평균(공휴일/명절도 주말 패턴으로 묶음)\n"
        "- 평일: '평일1(월·금)' / '평일2(화·수·목)' 구분\n"
        "- 기본은 요일 + 그 달의 n번째(1째 월요일, 2째 월요일…) 기준 평균\n"
        "- 일부 케이스 데이터가 부족하면 요일 평균으로 보정\n"
        "- 마지막에 일별비율 합계가 1이 되도록 정규화(raw / SUM(raw))"
    )

    st.markdown("### 📌 월별 계획량(1~12월) & 연간 총량")
    df_year_plan = df_plan[df_plan["연"] == target_year].copy()
    df_year_plan["계획_MJ"] = df_year_plan[plan_col].apply(to_num)

    month_map = {m: (df_year_plan[df_year_plan["월"] == m]["계획_MJ"].iloc[0] if ((df_year_plan["월"] == m).any()) else np.nan) for m in range(1, 13)}
    annual_sum = np.nansum(list(month_map.values()))

    header = ["구분"] + [f"{m}월" for m in range(1, 13)] + ["연간합계"]
    row_gj = ["사업계획(월별 계획)"] + [mj_to_gj(month_map[m]) if not pd.isna(month_map[m]) else np.nan for m in range(1, 13)] + [mj_to_gj(annual_sum)]
    row_m3 = ["(하단) ㎥ 환산"] + [mj_to_m3(month_map[m]) if not pd.isna(month_map[m]) else np.nan for m in range(1, 13)] + [mj_to_m3(annual_sum)]
    df_month_table = pd.DataFrame([row_gj, row_m3], columns=header)
    df_month_show = df_month_table.copy()
    for c in df_month_show.columns[1:]:
        df_month_show[c] = df_month_show[c].apply(lambda x: "" if pd.isna(x) else f"{x:,.0f}")
    show_table_no_index(df_month_show, height=120)

    df_result, df_mat, _ = make_daily_plan_table(
        df_daily=df_daily,
        df_plan=df_plan,
        plan_col=plan_col,
        target_year=int(target_year),
        target_month=int(target_month),
        recent_window=int(recent_window),
    )

    view = df_result.copy()
    view["예상공급량(GJ)"] = view["예상공급량(MJ)"].apply(mj_to_gj).round(0)
    view["예상공급량(㎥)"] = view["예상공급량(MJ)"].apply(mj_to_m3).round(0)

    st.markdown("### 📊 일별 계획(표)")
    show_cols = ["일자", "요일", "구분", "공휴일여부", "일별비율", "예상공급량(GJ)", "예상공급량(㎥)"]
    view_show = view[show_cols].copy()
    view_show["일별비율"] = view_show["일별비율"].apply(lambda x: "" if pd.isna(x) else f"{x:.2%}")
    for c in ["예상공급량(GJ)", "예상공급량(㎥)"]:
        view_show[c] = view_show[c].apply(lambda x: "" if pd.isna(x) else f"{x:,.0f}")
    show_table_no_index(view_show, height=420)

    st.markdown("### 🧊 (복구) 과거연도 일별 공급량 매트릭스")
    if not df_mat.empty:
        df_mat_show = df_mat.applymap(lambda x: np.nan if pd.isna(x) else mj_to_gj(x))
        st.dataframe(df_mat_show, use_container_width=True, height=320)
    else:
        st.info("매트릭스 생성용 과거 데이터가 부족해.")

    st.markdown("#### 💾 5. 일별 계획 엑셀 다운로드")
    buffer = BytesIO()
    sheet_name = f"{target_year}_{int(target_month):02d}_일별계획"
    excel_df = _make_display_table_gj_m3(df_result)

    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        excel_df.to_excel(writer, index=False, sheet_name=sheet_name)
        wb = writer.book
        ws = wb[sheet_name]
        for c in range(1, ws.max_column + 1):
            ws.cell(1, c).font = Font(bold=True)
        _format_excel_sheet(ws, freeze="A2", center=True)

    st.download_button(
        label=f"📥 {target_year}년 {int(target_month)}월 일별공급계획 다운로드 (Excel)",
        data=buffer.getvalue(),
        file_name=f"{target_year}_{int(target_month):02d}_일별공급계획.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key="download_month_excel",
    )


# ─────────────────────────────────────────────
# 탭2: (그대로 유지) 3차 다항 회귀 + 비교 + 하단 히트맵
# ─────────────────────────────────────────────
def fit_poly3_and_r2(x: pd.Series, y: pd.Series):
    x = pd.Series(x).astype(float)
    y = pd.Series(y).astype(float)
    df = pd.DataFrame({"x": x, "y": y}).dropna()
    if len(df) < 8:
        return None, None, None, df.index
    coef = np.polyfit(df["x"].values, df["y"].values, 3)
    p = np.poly1d(coef)
    y_pred = p(df["x"].values)
    ss_res = np.sum((df["y"].values - y_pred) ** 2)
    ss_tot = np.sum((df["y"].values - np.mean(df["y"].values)) ** 2)
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else np.nan
    return coef, y_pred, r2, df.index

def plot_poly_fit(x, y, coef, title, x_label, y_label):
    p = np.poly1d(coef)
    x_clean = pd.Series(x).dropna().astype(float)
    if x_clean.empty:
        return go.Figure()
    xmin, xmax = float(x_clean.min()), float(x_clean.max())
    xs = np.linspace(xmin, xmax, 200)
    ys = p(xs)

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=x, y=y, mode="markers", name="실적"))
    fig.add_trace(go.Scatter(x=xs, y=ys, mode="lines", name="3차 다항식"))
    fig.update_layout(title=title, xaxis_title=x_label, yaxis_title=y_label, template="simple_white")
    return fig

def tab_daily_monthly_compare(df: pd.DataFrame, df_temp_all: pd.DataFrame):
    st.subheader("📊 Daily·Monthly 공급량 비교 — 기온 기반 3차 다항 회귀")

    df_m = df.copy()
    df_m["연"] = df_m["일자"].dt.year
    df_m["월"] = df_m["일자"].dt.month

    df_month = df_m.groupby(["연", "월"], as_index=False).agg(
        평균기온=("평균기온(℃)", "mean"),
        공급량_MJ=("공급량(MJ)", "sum"),
    )
    df_month["공급량_GJ"] = df_month["공급량_MJ"].apply(mj_to_gj)

    df_window = df_m.dropna(subset=["평균기온(℃)", "공급량(MJ)"]).copy()
    df_window["공급량_GJ"] = df_window["공급량(MJ)"].apply(mj_to_gj)

    coef_m, y_pred_m, r2_m, idx_m = fit_poly3_and_r2(df_month["평균기온"], df_month["공급량_GJ"])
    df_month["예측공급량_GJ"] = np.nan
    if y_pred_m is not None and len(idx_m) == len(y_pred_m):
        df_month.loc[idx_m, "예측공급량_GJ"] = y_pred_m

    coef_d, y_pred_d, r2_d, idx_d = fit_poly3_and_r2(df_window["평균기온(℃)"], df_window["공급량_GJ"])
    df_window["예측공급량_GJ"] = np.nan
    if y_pred_d is not None and len(idx_d) == len(y_pred_d):
        df_window.loc[idx_d, "예측공급량_GJ"] = y_pred_d

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
                df_month["평균기온"], df_month["공급량_GJ"], coef_m,
                title="월단위: 월평균 기온 vs 월별 공급량(GJ)",
                x_label="월평균 기온 (℃)", y_label="월별 공급량 합계 (GJ)"
            )
            st.plotly_chart(fig_m, use_container_width=True)
    with col4:
        if coef_d is not None:
            fig_d = plot_poly_fit(
                df_window["평균기온(℃)"], df_window["공급량_GJ"], coef_d,
                title="일단위: 일평균 기온 vs 일별 공급량(GJ)",
                x_label="일평균 기온 (℃)", y_label="일별 공급량 (GJ)"
            )
            st.plotly_chart(fig_d, use_container_width=True)

    st.markdown("---")
    st.subheader("🧊 기온분석 — 일일 평균기온 히트맵")

    up = st.file_uploader("일일기온 파일 업로드(XLSX) (없으면 앱 데이터(df_temp_all) 사용)", type=["xlsx"], key="heatmap_uploader")

    if up is not None:
        raw = pd.read_excel(up)
        cols = raw.columns.astype(str).tolist()

        def pick(cands, default_idx=0):
            for k in cands:
                for c in cols:
                    if k in c:
                        return c
            return cols[default_idx]

        c_date = pick(["일자", "날짜", "date"], 0)
        c_temp = pick(["평균기온", "기온", "tmean", "temp"], 1)

        dt = raw.copy()
        dt["date"] = pd.to_datetime(dt[c_date])
        dt["tmean"] = dt[c_temp].apply(to_num)
        dt = dt.dropna(subset=["date", "tmean"]).sort_values("date")
    else:
        dt = df_temp_all.copy()
        dt = dt.rename(columns={"일자": "date", "평균기온(℃)": "tmean"})
        dt = dt.dropna(subset=["date", "tmean"]).sort_values("date")

    if dt.empty:
        st.info("히트맵 표시할 기온 데이터가 없어.")
        return

    dt["year"] = dt["date"].dt.year
    dt["month"] = dt["date"].dt.month
    dt["day"] = dt["date"].dt.day

    y_min, y_max = int(dt["year"].min()), int(dt["year"].max())
    months_all = list(range(1, 13))
    month_names = {m: calendar.month_name[m] for m in range(1, 13)}

    c1, c2 = st.columns([2, 1])
    with c1:
        year_range = st.slider("연도 범위", min_value=y_min, max_value=y_max, value=(y_min, y_max), step=1, key="hm_year_range")
    with c2:
        default_month = int(dt["month"].iloc[-1])
        sel_month = st.selectbox(
            "월 선택",
            options=months_all,
            index=months_all.index(default_month),
            format_func=lambda m: f"{m:02d} ({month_names[m]})",
            key="hm_month",
        )

    sel_years = [y for y in sorted(dt["year"].unique()) if year_range[0] <= y <= year_range[1]]
    dsel = dt[(dt["year"].isin(sel_years)) & (dt["month"] == sel_month)].copy()
    if dsel.empty:
        st.info("선택한 연·월에 데이터가 없습니다.")
        return

    last_day = int(dsel["day"].max())
    pivot = (
        dsel.pivot_table(index="day", columns="year", values="tmean", aggfunc="mean")
        .reindex(range(1, last_day + 1))
    )

    avg_row = pivot.mean(axis=0, skipna=True)
    pivot_with_avg = pd.concat([pivot, pd.DataFrame([avg_row], index=["평균"])])

    y_labels = [f"{sel_month:02d}-{int(d):02d}" for d in pivot.index]
    y_labels.append("평균")

    Z = pivot_with_avg.values.astype(float)
    X = pivot_with_avg.columns.tolist()
    Y = y_labels
    zmid = float(np.nanmean(pivot.values))

    text = np.full_like(Z, "", dtype=object)
    last_idx = Z.shape[0] - 1
    text[last_idx, :] = [f"{v:.1f}" if np.isfinite(v) else "" for v in Z[last_idx, :]]

    heat = go.Figure(
        data=go.Heatmap(
            z=Z,
            x=X,
            y=Y,
            colorscale="RdBu_r",
            zmid=zmid,
            colorbar=dict(title="°C"),
            hoverongaps=False,
            hovertemplate="연도=%{x}<br>일자=%{y}<br>평균기온=%{z:.1f}℃<extra></extra>",
            text=text,
            texttemplate="%{text}",
            textfont={"size": 12},
        )
    )
    heat.update_layout(
        template="simple_white",
        height=max(360, 120 + len(Y) * 18),
        margin=dict(l=40, r=20, t=40, b=60),
        xaxis=dict(title="Year"),
        yaxis=dict(title="Day"),
    )
    st.plotly_chart(heat, use_container_width=True)


# ─────────────────────────────────────────────
# 메인
# ─────────────────────────────────────────────
def main():
    st.set_page_config(page_title="도시가스 공급량 분석", layout="wide")
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
