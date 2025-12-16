# app.py ─ 도시가스 공급량: Daily 계획 + Daily·Monthly 비교 (GJ + ㎥ 표기)
import calendar
from io import BytesIO
from pathlib import Path
from typing import List, Tuple, Optional, Dict

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
    # ㎥(Nm3) = MJ / (MJ/Nm3)
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

    # 컬럼 자동 매핑(최대한 기존 포맷 유지)
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

    # 모델용(공급량+기온 모두 있는 곳)
    df_model = df.dropna(subset=["공급량(MJ)", "평균기온(℃)"]).copy()

    # 히트맵용(기온만 있으면 OK)
    df_temp_all = df.dropna(subset=["평균기온(℃)"]).copy()

    # 보기 편의: GJ 컬럼도 기본 생성(표/그래프에서 사용)
    df_model["공급량_GJ"] = df_model["공급량(MJ)"].apply(mj_to_gj)
    df_model["공급량_㎥"] = df_model["공급량(MJ)"].apply(mj_to_m3)

    df_temp_all = df_temp_all.sort_values("일자").reset_index(drop=True)
    df_model = df_model.sort_values("일자").reset_index(drop=True)
    return df_model, df_temp_all


@st.cache_data(show_spinner=False)
def load_monthly_plan() -> pd.DataFrame:
    """
    월별 계획 파일은 기존 너 포맷 그대로 쓴다고 가정.
    (연/월/계획량 컬럼 존재)
    """
    excel_path = Path(__file__).parent / "공급계획_월별.xlsx"
    df = pd.read_excel(excel_path)
    # 최소 정리
    if "연" not in df.columns:
        # 혹시 '년도' 같은 경우
        for c in df.columns:
            if "연" in str(c):
                df = df.rename(columns={c: "연"})
                break
    if "월" not in df.columns:
        for c in df.columns:
            if "월" in str(c):
                df = df.rename(columns={c: "월"})
                break
    return df


def _find_plan_col(df_plan: pd.DataFrame) -> str:
    """
    계획량 컬럼 찾기 (월별계획/사업계획 등)
    """
    candidates = [
        "계획", "월별", "사업", "제출", "공급", "물량", "total"
    ]
    cols = df_plan.columns.astype(str).tolist()
    # 숫자형이면서 후보 단어 포함된 컬럼 우선
    for c in cols:
        if any(k in c for k in candidates):
            # 숫자형으로 변환 가능하면 채택
            s = df_plan[c].apply(to_num)
            if s.notna().any():
                return c
    # fallback: 마지막 숫자형 컬럼
    for c in reversed(cols):
        s = df_plan[c].apply(to_num)
        if s.notna().any():
            return c
    return cols[-1]


# ─────────────────────────────────────────────
# 표/엑셀 유틸
# ─────────────────────────────────────────────
def format_table_generic(df: pd.DataFrame, percent_cols: Optional[List[str]] = None) -> pd.DataFrame:
    out = df.copy()
    percent_cols = percent_cols or []
    for c in out.columns:
        if c in percent_cols:
            out[c] = out[c].apply(lambda x: "" if pd.isna(x) else f"{x:.2%}")
        else:
            # 숫자면 천단위 콤마
            if pd.api.types.is_numeric_dtype(out[c]):
                out[c] = out[c].apply(lambda x: "" if pd.isna(x) else f"{x:,.0f}")
    return out

def show_table_no_index(df: pd.DataFrame, height=260):
    st.dataframe(df, use_container_width=True, hide_index=True, height=height)

def _format_excel_sheet(ws, freeze="A2", center=True):
    ws.freeze_panes = freeze
    if center:
        for row in ws.iter_rows(min_row=1, max_row=ws.max_row, min_col=1, max_col=ws.max_column):
            for cell in row:
                cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    # 컬럼 폭 자동(대충)
    for col in range(1, ws.max_column + 1):
        letter = get_column_letter(col)
        ws.column_dimensions[letter].width = max(10, min(22, ws.column_dimensions[letter].width or 12))

def _add_cumulative_status_sheet(wb, annual_year: int):
    """
    연간 다운로드(엑셀) 마지막 시트에 '누적계획현황' 추가
    - 기준일 입력(셀 B1) → 일/월/연 목표 & 누적 & 진행률 자동
    """
    ws = wb.create_sheet("누적계획현황")

    # 헤더
    ws["A1"] = "기준일"
    ws["B1"] = f"{annual_year}-01-01"  # 사용자가 바꿀 수 있게 기본값
    ws["A3"] = "구분"
    ws["B3"] = "목표(GJ)"
    ws["C3"] = "누적(GJ)"
    ws["D3"] = "목표(㎥)"
    ws["E3"] = "누적(㎥)"
    ws["F3"] = "진행률(GJ)"

    for cell in ["A1","A3","B3","C3","D3","E3","F3"]:
        ws[cell].font = Font(bold=True)

    # 연간 시트는 "연간"으로 저장되어 있다고 가정
    # 연간 시트 컬럼 중 날짜/예상공급량(GJ)/(㎥) 찾아서 SUMIFS 구성
    # (여기서는 우리가 export할 때 컬럼명을 고정해 줄 거라 그대로 사용 가능)
    # 날짜: "일자", 계획GJ: "예상공급량(GJ)", 계획㎥: "예상공급량(㎥)"

    # SUMIFS 범위(전체 열)로 잡기
    # 일 누적: 해당 기준일 = 일자
    # 월 누적: 해당 기준일의 월 1일~기준일
    # 연 누적: 1/1~기준일

    # Excel 수식에서 DATEVALUE/DATE, EOMONTH 활용
    # 기준일: $B$1

    # 행 라벨
    ws["A4"] = "일"
    ws["A5"] = "월"
    ws["A6"] = "연"

    # 목표(GJ): 일 = 기준일 당일 계획, 월 = 해당월 계획 합, 연 = 연간 계획 합
    # 누적(GJ): 일 = 당일 실적? 여기서는 "연간" 시트가 '계획'이므로 누적도 계획 누적(요청하신 2번째 사진 형태)
    # 즉: 목표=일/월/연 총 계획, 누적=기준일까지 계획 누적

    # 연간시트 참조
    # '연간'!A:A 에 '일자', '예상공급량(GJ)', '예상공급량(㎥)'가 있다고 가정하고, 실제 열은 헤더 위치로 MATCH 사용
    # 간단히: export 시 열을 A=일자, ... 로 고정하므로 아래는 고정열로 작성
    # A: 일자 / B.. 중에 예상공급량(GJ), 예상공급량(㎥)를 D/E로 배치할 거라서 여기선 MATCH 없이 고정열로 간다.

    # 우리가 export할 연간 시트 형식:
    # [일자, 요일, 구분, 공휴일여부, 일별비율, 예상공급량(GJ), 예상공급량(㎥), ...]
    # → 예상공급량(GJ)=F열, 예상공급량(㎥)=G열 로 맞출 예정

    # 1) 일 목표/누적: 해당일 계획
    ws["B4"] = '=SUMIFS(연간!$F:$F, 연간!$A:$A, $B$1)'
    ws["C4"] = '=SUMIFS(연간!$F:$F, 연간!$A:$A, $B$1)'
    ws["D4"] = '=SUMIFS(연간!$G:$G, 연간!$A:$A, $B$1)'
    ws["E4"] = '=SUMIFS(연간!$G:$G, 연간!$A:$A, $B$1)'
    ws["F4"] = '=IFERROR(C4/B4,0)'

    # 2) 월 목표/누적
    ws["B5"] = '=SUMIFS(연간!$F:$F, 연간!$A:$A, ">="&EOMONTH($B$1,-1)+1, 연간!$A:$A, "<="&EOMONTH($B$1,0))'
    ws["C5"] = '=SUMIFS(연간!$F:$F, 연간!$A:$A, ">="&EOMONTH($B$1,-1)+1, 연간!$A:$A, "<="&$B$1)'
    ws["D5"] = '=SUMIFS(연간!$G:$G, 연간!$A:$A, ">="&EOMONTH($B$1,-1)+1, 연간!$A:$A, "<="&EOMONTH($B$1,0))'
    ws["E5"] = '=SUMIFS(연간!$G:$G, 연간!$A:$A, ">="&EOMONTH($B$1,-1)+1, 연간!$A:$A, "<="&$B$1)'
    ws["F5"] = '=IFERROR(C5/B5,0)'

    # 3) 연 목표/누적
    ws["B6"] = '=SUM(연간!$F:$F)'
    ws["C6"] = '=SUMIFS(연간!$F:$F, 연간!$A:$A, "<="&$B$1)'
    ws["D6"] = '=SUM(연간!$G:$G)'
    ws["E6"] = '=SUMIFS(연간!$G:$G, 연간!$A:$A, "<="&$B$1)'
    ws["F6"] = '=IFERROR(C6/B6,0)'

    _format_excel_sheet(ws, freeze="A4", center=True)
    ws["B1"].number_format = "yyyy-mm-dd"


def _make_display_table_gj_m3(df_mj: pd.DataFrame) -> pd.DataFrame:
    """
    다운로드 엑셀에 GJ/㎥ 컬럼이 반드시 나오게 변환
    """
    df = df_mj.copy()

    for base_col in ["최근N년_평균공급량(MJ)", "최근N년_총공급량(MJ)", "예상공급량(MJ)"]:
        if base_col not in df.columns:
            continue
        gj_col = base_col.replace("(MJ)", "(GJ)")
        m3_col = base_col.replace("(MJ)", "(㎥)")
        df[gj_col] = df[base_col].apply(mj_to_gj).round(0)
        df[m3_col] = df[base_col].apply(mj_to_m3).round(0)

    keep_cols = [
        "연", "월", "일", "일자", "요일", "weekday_idx", "nth_dow", "구분", "공휴일여부",
        "최근N년_평균공급량(GJ)", "최근N년_평균공급량(㎥)",
        "최근N년_총공급량(GJ)", "최근N년_총공급량(㎥)",
        "일별비율",
        "예상공급량(GJ)", "예상공급량(㎥)",
    ]
    keep_cols = [c for c in keep_cols if c in df.columns]
    return df[keep_cols].copy()


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
) -> Tuple[pd.DataFrame, pd.DataFrame, List[int]]:
    """
    기존 구조 유지:
    - 주말/공휴일/명절 + 평일1(월·금) + 평일2(화·수·목)
    - nth_dow(해당 월의 n번째 요일) 기반 평균 비율
    - raw 정규화하여 일별비율 합=1
    - 월 계획량(plan_total)을 일별비율로 분배 → 예상공급량(MJ)
    - 매트릭스(과거연도×일자)도 반환
    """
    last_day = calendar.monthrange(target_year, target_month)[1]

    # 최근 N년(해당월) 데이터
    all_years = sorted(df_daily["연도"].unique())
    hist_years = [y for y in all_years if y < target_year]
    used_years = hist_years[-recent_window:]
    df_recent = df_daily[(df_daily["연도"].isin(used_years)) & (df_daily["월"] == target_month)].copy()

    # 공휴일/명절 여부 컬럼이 따로 없다면 False로
    if "공휴일여부" not in df_recent.columns:
        df_recent["공휴일여부"] = False

    # 대상 월 날짜 프레임
    days = pd.date_range(f"{target_year}-{target_month:02d}-01", f"{target_year}-{target_month:02d}-{last_day:02d}", freq="D")
    df_target = pd.DataFrame({"일자": days})
    df_target["연"] = target_year
    df_target["월"] = target_month
    df_target["일"] = df_target["일자"].dt.day
    df_target["weekday_idx"] = df_target["일자"].dt.weekday

    # 공휴일여부가 별도 파일/로직이면 여기서 merge 하는 구조인데,
    # 기존 유지 차원에서 target에 없으면 False로 둠
    if "공휴일여부" not in df_target.columns:
        df_target["공휴일여부"] = False

    # 분류
    df_target["is_weekend"] = (df_target["weekday_idx"] >= 5) | (df_target["공휴일여부"] == True)
    df_target["is_weekday1"] = (~df_target["is_weekend"]) & (df_target["weekday_idx"].isin([0, 4]))   # 월/금
    df_target["is_weekday2"] = (~df_target["is_weekend"]) & (df_target["weekday_idx"].isin([1, 2, 3])) # 화수목

    weekday_names = ["월", "화", "수", "목", "금", "토", "일"]
    df_target["요일"] = df_target["weekday_idx"].map(lambda i: weekday_names[i])

    # n번째 요일
    df_target["nth_dow"] = df_target.sort_values("일").groupby("weekday_idx").cumcount() + 1

    def _label(row):
        if row["is_weekend"]:
            return "주말/공휴일"
        if row["is_weekday1"]:
            return "평일1(월·금)"
        return "평일2(화·수·목)"

    df_target["구분"] = df_target.apply(_label, axis=1)

    # 최근 데이터에도 nth_dow 생성
    df_recent = df_recent.copy()
    df_recent["day"] = df_recent["일자"].dt.day
    df_recent["nth_dow"] = df_recent.sort_values("day").groupby(["연도", "weekday_idx"]).cumcount() + 1

    # 각 그룹별 raw 비율(공급량 기반)
    # 주말/공휴일
    ratio_weekend_group = (
        df_recent[df_recent["weekday_idx"].isin([5, 6]) | (df_recent["공휴일여부"] == True)]
        .groupby(["weekday_idx", "nth_dow"])["공급량(MJ)"].mean()
    )
    ratio_weekend_by_dow = (
        df_recent[df_recent["weekday_idx"].isin([5, 6]) | (df_recent["공휴일여부"] == True)]
        .groupby(["weekday_idx"])["공급량(MJ)"].mean()
    )

    # 평일1/평일2
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

    # 결측 보정(구분 평균 → 전체 평균 → 1.0)
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

    # 과거연도×일자 매트릭스(기존 있던 표 복구용)
    df_mat = (
        df_recent.pivot_table(index="day", columns="연도", values="공급량(MJ)", aggfunc="sum")
        .sort_index()
        .reindex(range(1, last_day + 1))
    )

    return df_result, df_mat, used_years


def _build_year_daily_plan(
    df_daily: pd.DataFrame,
    df_plan: pd.DataFrame,
    target_year: int,
    recent_window: int,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    plan_col = _find_plan_col(df_plan)
    rows = []
    month_summary_rows = []

    for m in range(1, 13):
        if not ((df_plan["연"] == target_year) & (df_plan["월"] == m)).any():
            continue
        df_res, _, used_years = make_daily_plan_table(df_daily, df_plan, plan_col, target_year, m, recent_window)
        # GJ/㎥ 추가
        df_res["예상공급량(GJ)"] = df_res["예상공급량(MJ)"].apply(mj_to_gj).round(0)
        df_res["예상공급량(㎥)"] = df_res["예상공급량(MJ)"].apply(mj_to_m3).round(0)

        rows.append(df_res)

        month_plan_mj = float(df_plan[(df_plan["연"] == target_year) & (df_plan["월"] == m)][plan_col].apply(to_num).iloc[0])
        month_summary_rows.append({
            "월": m,
            "월간 계획(GJ)": round(mj_to_gj(month_plan_mj), 0),
            "월간 계획(㎥)": round(mj_to_m3(month_plan_mj), 0),
        })

    if rows:
        df_year = pd.concat(rows, ignore_index=True)
    else:
        df_year = pd.DataFrame()

    # 합계행
    if not df_year.empty:
        total_row = {
            "연": target_year,
            "월": "",
            "일": "",
            "일자": "",
            "요일": "",
            "weekday_idx": "",
            "nth_dow": "",
            "구분": "합계",
            "공휴일여부": "",
            "최근N년_평균공급량(MJ)": np.nan,
            "최근N년_총공급량(MJ)": np.nan,
            "일별비율": df_year["일별비율"].sum(skipna=True),
            "예상공급량(MJ)": df_year["예상공급량(MJ)"].sum(skipna=True),
            "예상공급량(GJ)": df_year["예상공급량(GJ)"].sum(skipna=True),
            "예상공급량(㎥)": df_year["예상공급량(㎥)"].sum(skipna=True),
        }
        df_year = pd.concat([df_year, pd.DataFrame([total_row])], ignore_index=True)

    df_month_sum = pd.DataFrame(month_summary_rows).sort_values("월").reset_index(drop=True)
    if not df_month_sum.empty:
        df_month_sum = pd.concat([df_month_sum, pd.DataFrame([{
            "월": "연간합계",
            "월간 계획(GJ)": df_month_sum["월간 계획(GJ)"].sum(skipna=True),
            "월간 계획(㎥)": df_month_sum["월간 계획(㎥)"].sum(skipna=True),
        }])], ignore_index=True)

    return df_year, df_month_sum


# ─────────────────────────────────────────────
# 탭1 UI
# ─────────────────────────────────────────────
def tab_daily_plan(df_daily: pd.DataFrame):
    st.subheader("📅 Daily 공급량 분석 — 최근 N년 패턴 기반 일별 계획")

    df_plan = load_monthly_plan()
    plan_col = _find_plan_col(df_plan)

    years_plan = sorted(df_plan["연"].unique())
    default_year_idx = years_plan.index(2026) if 2026 in years_plan else len(years_plan) - 1

    col_y, col_m, col_n = st.columns([1, 1, 2])
    with col_y:
        target_year = st.selectbox("계획 연도 선택", years_plan, index=default_year_idx)
    with col_m:
        months_plan = sorted(df_plan[df_plan["연"] == target_year]["월"].unique())
        default_month_idx = months_plan.index(1) if 1 in months_plan else 0
        target_month = st.selectbox("계획 월 선택", months_plan, index=default_month_idx, format_func=lambda m: f"{m}월")
    with col_n:
        recent_window = st.slider("최근 몇 년 평균으로 비율을 계산할까?", 1, 10, 3, step=1)

    # 학습 연도 표시
    all_years = sorted(df_daily["연도"].unique())
    hist_years = [y for y in all_years if y < int(target_year)]
    used_years = hist_years[-int(recent_window):]
    st.markdown(f"- **실제 학습에 사용된 연도(해당월 실적 존재)**: {used_years[0]}년 ~ {used_years[-1]}년 (총 {len(used_years)}개)" if used_years else "- 학습 연도 없음")

    # 월 계획량(GJ)
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

    # 월별 계획량(1~12) + 연간
    st.markdown("### 📌 월별 계획량(1~12월) & 연간 총량")
    # 월별 계획표(연간)
    df_year_plan = df_plan[df_plan["연"] == target_year].copy()
    df_year_plan["계획_MJ"] = df_year_plan[plan_col].apply(to_num)

    month_map = {m: df_year_plan[df_year_plan["월"] == m]["계획_MJ"].iloc[0] if ((df_year_plan["월"] == m).any()) else np.nan for m in range(1,13)}
    annual_sum = np.nansum(list(month_map.values()))
    # 표: GJ row + ㎥ row
    header = ["구분"] + [f"{m}월" for m in range(1,13)] + ["연간합계"]
    row_gj = ["사업계획(월별 계획)"] + [mj_to_gj(month_map[m]) if not pd.isna(month_map[m]) else np.nan for m in range(1,13)] + [mj_to_gj(annual_sum)]
    row_m3 = ["(하단) ㎥ 환산"] + [mj_to_m3(month_map[m]) if not pd.isna(month_map[m]) else np.nan for m in range(1,13)] + [mj_to_m3(annual_sum)]
    df_month_table = pd.DataFrame([row_gj, row_m3], columns=header)
    # 표시 포맷
    df_month_show = df_month_table.copy()
    for c in df_month_show.columns[1:]:
        df_month_show[c] = df_month_show[c].apply(lambda x: "" if pd.isna(x) else f"{x:,.0f}")
    show_table_no_index(df_month_show, height=120)

    # 일별 계획 생성
    df_result, df_mat, _ = make_daily_plan_table(
        df_daily=df_daily,
        df_plan=df_plan,
        plan_col=plan_col,
        target_year=int(target_year),
        target_month=int(target_month),
        recent_window=int(recent_window),
    )

    # 표시용 GJ/㎥ 추가
    view = df_result.copy()
    view["최근N년_평균공급량(GJ)"] = view["최근N년_평균공급량(MJ)"].apply(mj_to_gj).round(0)
    view["최근N년_평균공급량(㎥)"] = view["최근N년_평균공급량(MJ)"].apply(mj_to_m3).round(0)
    view["최근N년_총공급량(GJ)"] = view["최근N년_총공급량(MJ)"].apply(mj_to_gj).round(0)
    view["최근N년_총공급량(㎥)"] = view["최근N년_총공급량(MJ)"].apply(mj_to_m3).round(0)
    view["예상공급량(GJ)"] = view["예상공급량(MJ)"].apply(mj_to_gj).round(0)
    view["예상공급량(㎥)"] = view["예상공급량(MJ)"].apply(mj_to_m3).round(0)

    # 합계행(화면)
    total_row = {
        "구분": "합계",
        "일별비율": view["일별비율"].sum(skipna=True),
        "예상공급량(GJ)": view["예상공급량(GJ)"].sum(skipna=True),
        "예상공급량(㎥)": view["예상공급량(㎥)"].sum(skipna=True),
    }

    st.markdown("### 📊 일별 계획(표)")
    show_cols = ["일자","요일","구분","공휴일여부","일별비율","예상공급량(GJ)","예상공급량(㎥)"]
    view_show = view[show_cols].copy()
    view_show["일별비율"] = view_show["일별비율"].apply(lambda x: "" if pd.isna(x) else f"{x:.2%}")
    for c in ["예상공급량(GJ)","예상공급량(㎥)"]:
        view_show[c] = view_show[c].apply(lambda x: "" if pd.isna(x) else f"{x:,.0f}")
    show_table_no_index(view_show, height=420)

    st.markdown("### 🧾 구분별 요약(비율합/계획합)")
    summary = view.groupby("구분", as_index=False).agg(
        일별비율합계=("일별비율", "sum"),
        예상공급량_MJ=("예상공급량(MJ)", "sum"),
    )
    summary["예상공급량(GJ)"] = summary["예상공급량_MJ"].apply(mj_to_gj).round(0)
    summary["예상공급량(㎥)"] = summary["예상공급량_MJ"].apply(mj_to_m3).round(0)
    summary = summary.drop(columns=["예상공급량_MJ"])

    total_row_sum = {
        "구분": "합계",
        "일별비율합계": summary["일별비율합계"].sum(),
        "예상공급량(GJ)": summary["예상공급량(GJ)"].sum(),
        "예상공급량(㎥)": summary["예상공급량(㎥)"].sum(),
    }
    summary = pd.concat([summary, pd.DataFrame([total_row_sum])], ignore_index=True)
    summary_show = summary.copy()
    summary_show["일별비율합계"] = summary_show["일별비율합계"].apply(lambda x: "" if pd.isna(x) else f"{x:.2%}")
    for c in ["예상공급량(GJ)","예상공급량(㎥)"]:
        summary_show[c] = summary_show[c].apply(lambda x: "" if pd.isna(x) else f"{x:,.0f}")
    show_table_no_index(summary_show, height=220)

    # 과거연도 매트릭스(복구)
    st.markdown("### 🧊 (복구) 과거연도 일별 공급량 매트릭스")
    if not df_mat.empty:
        df_mat_show = df_mat.copy()
        # 표시 단위를 GJ로
        df_mat_show = df_mat_show.applymap(lambda x: np.nan if pd.isna(x) else mj_to_gj(x))
        st.dataframe(df_mat_show, use_container_width=True, height=320)
    else:
        st.info("매트릭스 생성용 과거 데이터가 부족해.")

    # 5) 월 다운로드
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

    # 6) 연간 다운로드 + 누적계획현황 시트
    st.markdown("#### 🗂️ 6. 일일계획 다운로드(연간)")
    annual_year = st.selectbox(
        "연간 계획 연도 선택",
        sorted(df_plan["연"].unique()),
        index=sorted(df_plan["연"].unique()).index(int(target_year)) if int(target_year) in sorted(df_plan["연"].unique()) else 0,
        key="annual_year_select",
    )

    buffer_year = BytesIO()
    df_year_daily, df_month_summary = _build_year_daily_plan(
        df_daily=df_daily,
        df_plan=df_plan,
        target_year=int(annual_year),
        recent_window=int(recent_window),
    )

    with pd.ExcelWriter(buffer_year, engine="openpyxl") as writer:
        # 연간 시트 컬럼 순서를 누적계획현황 수식이 기대하는 형태로 맞추기
        if not df_year_daily.empty:
            # 일자(A), 요일(B), 구분(C), 공휴일여부(D), 일별비율(E), 예상공급량(GJ)(F), 예상공급량(㎥)(G)
            tmp = df_year_daily.copy()
            # 요일이 없으면 생성
            if "요일" not in tmp.columns and "weekday_idx" in tmp.columns:
                weekday_names = ["월", "화", "수", "목", "금", "토", "일"]
                tmp["요일"] = tmp["weekday_idx"].map(lambda i: weekday_names[i] if str(i).isdigit() else "")
            cols_order = ["일자", "요일", "구분", "공휴일여부", "일별비율", "예상공급량(GJ)", "예상공급량(㎥)"]
            cols_order = [c for c in cols_order if c in tmp.columns]
            tmp = tmp[cols_order].copy()
        else:
            tmp = df_year_daily

        tmp.to_excel(writer, index=False, sheet_name="연간")
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

        # ★ 누적계획현황 시트 추가
        _add_cumulative_status_sheet(wb, int(annual_year))

    st.download_button(
        label=f"📥 {annual_year}년 연간 일별공급계획 다운로드 (Excel)",
        data=buffer_year.getvalue(),
        file_name=f"{annual_year}_연간_일별공급계획.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key="download_annual_excel",
    )


# ─────────────────────────────────────────────
# 탭2: 3차 다항 회귀 + 비교 + (하단 히트맵 추가)
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

    # 월별 집계
    df_m = df.copy()
    df_m["연"] = df_m["일자"].dt.year
    df_m["월"] = df_m["일자"].dt.month

    df_month = df_m.groupby(["연", "월"], as_index=False).agg(
        평균기온=("평균기온(℃)", "mean"),
        공급량_MJ=("공급량(MJ)", "sum"),
    )
    df_month["공급량_GJ"] = df_month["공급량_MJ"].apply(mj_to_gj)

    # 일 단위(그대로)
    df_window = df_m.dropna(subset=["평균기온(℃)", "공급량(MJ)"]).copy()
    df_window["공급량_GJ"] = df_window["공급량(MJ)"].apply(mj_to_gj)

    # ── ★ 길이 mismatch 방지: 학습에 사용된 index에만 예측값 매핑
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

    # ─────────────────────────────────────────────
    # ★ (요청) 탭 맨 하단: "기온분석 — 일일 평균기온 히트맵" 매트릭스 추가
    #    - 기존 G 화면 로직 “그대로” 살리되, 탭 내부로만 이식
    #    - df_temp_all(앱 데이터) 기본 사용 + 업로드로 대체 가능
    # ─────────────────────────────────────────────
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

    base_cell_px = 34
    approx_width_px = max(600, len(X) * base_cell_px)
    height = max(360, int(approx_width_px * 2 / 3 * 1.30))

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
        height=height,
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
