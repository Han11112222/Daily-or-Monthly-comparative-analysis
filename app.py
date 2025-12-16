import re, textwrap, os, pathlib, json, math, pandas as pd
code = r'''
import calendar
from io import BytesIO
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from openpyxl.styles import Alignment, Font

# ─────────────────────────────────────────────
# 단위 변환 상수 (요청 반영)
# - 1 GJ = 1,000 MJ
# - ㎥(Nm³) 환산: 42.563 MJ/Nm³
# ─────────────────────────────────────────────
MJ_PER_NM3 = 42.563


def mj_to_gj(x):
    return x / 1000.0


def mj_to_m3(x):
    return x / MJ_PER_NM3


def add_gj_m3_columns(
    df: pd.DataFrame,
    mj_cols: list[str],
    drop_mj: bool = True,
    round_digits: int | None = 0,
) -> pd.DataFrame:
    """
    df 안의 MJ 컬럼들을 (GJ), (㎥) 컬럼으로 환산해 추가/치환.
    - ㎥는 'MJ / 42.563' 기준
    - round_digits=None 이면 반올림 안함
    """
    out = df.copy()
    for c in mj_cols:
        if c not in out.columns:
            continue
        base = c.replace("(MJ)", "")
        gj_col = f"{base}(GJ)"
        m3_col = f"{base}(㎥)"

        out[gj_col] = mj_to_gj(out[c].astype("float64"))
        out[m3_col] = mj_to_m3(out[c].astype("float64"))

        if round_digits is not None:
            out[gj_col] = out[gj_col].round(round_digits)
            out[m3_col] = out[m3_col].round(round_digits)

    if drop_mj:
        drop_cols = [c for c in mj_cols if c in out.columns]
        out = out.drop(columns=drop_cols)
    return out


# ─────────────────────────────────────────────
# 컬럼명 유연 매칭(이번 KeyError 원인 해결)
# ─────────────────────────────────────────────
def _norm(s: str) -> str:
    return "".join(ch for ch in str(s) if ch.isalnum()).lower()


def resolve_plan_col(df: pd.DataFrame, preferred: str) -> str:
    """
    엑셀에서 '사업계획(월별 계획)'처럼 띄어쓰기/특수문자 차이로 컬럼명이 바뀌어도 잡아내기.
    """
    cols = list(df.columns)

    # 1) 정확히 일치
    if preferred in cols:
        return preferred

    # 2) 정규화 후 일치
    pref_n = _norm(preferred)
    for c in cols:
        if _norm(c) == pref_n:
            return c

    # 3) 토큰 기반 탐색 (사업계획 + 월별 + 계획)
    tokens = [_norm("사업계획"), _norm("월별"), _norm("계획")]
    candidates = []
    for c in cols:
        cn = _norm(c)
        if all(t in cn for t in tokens):
            candidates.append(c)

    if candidates:
        # 가장 짧은(군더더기 적은) 후보 우선
        candidates = sorted(candidates, key=lambda x: len(str(x)))
        return candidates[0]

    # 4) 못 찾으면, 어떤 컬럼이 있는지 메시지 포함해서 KeyError
    raise KeyError(
        f"월별 계획 컬럼을 찾지 못했어. 기대: '{preferred}' / 실제 컬럼: {cols}"
    )


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

    # 연/월 컬럼명도 혹시 다를 수 있어 최소한의 보정
    if "연" not in df.columns:
        for cand in ["연도", "년도", "YEAR"]:
            if cand in df.columns:
                df = df.rename(columns={cand: "연"})
                break
    if "월" not in df.columns:
        for cand in ["MONTH", "월(숫자)"]:
            if cand in df.columns:
                df = df.rename(columns={cand: "월"})
                break

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

    return df[["일자", "공휴일여부", "명절여부"]].dropna(subset=["일자"]).copy()


# ─────────────────────────────────────────────
# 유틸
# ─────────────────────────────────────────────
def show_table_no_index(df_to_show: pd.DataFrame, height: int = 360):
    st.dataframe(df_to_show, use_container_width=True, height=height, hide_index=True)


def format_table_generic(df: pd.DataFrame, percent_cols=None) -> pd.DataFrame:
    percent_cols = percent_cols or []
    out = df.copy()

    for c in out.columns:
        if c in percent_cols:
            out[c] = out[c].apply(lambda x: "" if pd.isna(x) else f"{x:.4f}")
        else:
            if pd.api.types.is_numeric_dtype(out[c]):
                out[c] = out[c].apply(lambda x: "" if pd.isna(x) else f"{x:,.0f}")
    return out


def make_month_plan_horizontal(df_plan: pd.DataFrame, target_year: int, plan_col: str) -> pd.DataFrame:
    """
    월별 계획 표(가로) + 연간 총량
    - 화면에서 MJ → GJ로 표시
    - 아래 행으로 ㎥(Nm³)도 함께 표시
    """
    df_year = df_plan[df_plan["연"] == target_year][["월", plan_col]].copy()

    base = pd.DataFrame({"월": list(range(1, 13))})
    df_year = base.merge(df_year, on="월", how="left")
    df_year = df_year.rename(columns={plan_col: "월별 계획(MJ)"})

    df_year["월별 계획(GJ)"] = mj_to_gj(df_year["월별 계획(MJ)"].astype("float64")).round(0)
    df_year["월별 계획(㎥)"] = mj_to_m3(df_year["월별 계획(MJ)"].astype("float64")).round(0)

    total_gj = df_year["월별 계획(GJ)"].sum(skipna=True)
    total_m3 = df_year["월별 계획(㎥)"].sum(skipna=True)

    row_gj = {f"{m}월": df_year.loc[df_year["월"] == m, "월별 계획(GJ)"].iloc[0] for m in range(1, 13)}
    row_gj["연간합계"] = total_gj

    row_m3 = {f"{m}월": df_year.loc[df_year["월"] == m, "월별 계획(㎥)"].iloc[0] for m in range(1, 13)}
    row_m3["연간합계"] = total_m3

    out = pd.DataFrame([row_gj, row_m3])
    out.insert(0, "구분", ["사업계획(월별 계획, GJ)", "사업계획(월별 계획, ㎥)"])
    return out


# ─────────────────────────────────────────────
# Daily 공급량: 일별 계획 예측
# ─────────────────────────────────────────────
def _make_target_calendar(target_year: int, target_month: int) -> pd.DataFrame:
    last_day = calendar.monthrange(target_year, target_month)[1]
    dates = pd.date_range(f"{target_year}-{target_month:02d}-01", f"{target_year}-{target_month:02d}-{last_day:02d}", freq="D")
    df = pd.DataFrame({"일자": dates})
    df["연"] = df["일자"].dt.year
    df["월"] = df["일자"].dt.month
    df["일"] = df["일자"].dt.day
    df["요일번호"] = df["일자"].dt.weekday  # 월=0 ... 일=6
    df["요일"] = df["요일번호"].map({0: "월", 1: "화", 2: "수", 3: "목", 4: "금", 5: "토", 6: "일"})

    df["weekday_idx"] = df.groupby("요일번호").cumcount() + 1
    df["nth_dow"] = df["weekday_idx"].astype(str) + "째 " + df["요일"]
    return df


def _classify_day(df_target: pd.DataFrame, df_cal: pd.DataFrame | None) -> pd.DataFrame:
    df = df_target.copy()
    df["공휴일여부"] = False
    df["명절여부"] = False

    if df_cal is not None and not df_cal.empty:
        df = df.merge(df_cal, on="일자", how="left", suffixes=("", "_cal"))
        for col in ["공휴일여부", "명절여부"]:
            if f"{col}_cal" in df.columns:
                df[col] = df[f"{col}_cal"].fillna(False).astype(bool)
                df = df.drop(columns=[f"{col}_cal"])

    df["is_weekend"] = df["요일번호"].isin([5, 6])
    df["is_holiday"] = df["공휴일여부"] | df["명절여부"]
    df["is_weekday1"] = df["요일번호"].isin([0, 4])  # 월/금

    df["구분"] = "평일2(화·수·목)"
    df.loc[df["is_weekday1"], "구분"] = "평일1(월·금)"
    df.loc[df["is_weekend"] | df["is_holiday"], "구분"] = "주말/공휴일"
    return df


def _recent_years(df_daily: pd.DataFrame, target_year: int, recent_window: int) -> list[int]:
    years = sorted(df_daily["연도"].dropna().unique().astype(int).tolist())
    cand = [y for y in years if y < target_year]
    return cand[-recent_window:] if len(cand) > 0 else []


def _prepare_recent_month(df_daily: pd.DataFrame, years: list[int], target_month: int) -> pd.DataFrame:
    df_recent = df_daily[(df_daily["연도"].isin(years)) & (df_daily["월"] == target_month)].copy()
    df_recent["요일번호"] = df_recent["일자"].dt.weekday
    df_recent["weekday_idx"] = df_recent.groupby(["연도", "월", "요일번호"]).cumcount() + 1
    df_recent["nth_dow"] = df_recent["weekday_idx"].astype(str) + "째 " + df_recent["요일번호"].map(
        {0: "월", 1: "화", 2: "수", 3: "목", 4: "금", 5: "토", 6: "일"}
    )
    return df_recent


def _compute_ratios(df_recent: pd.DataFrame, df_target: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, list[int]]:
    used_years = sorted(df_recent["연도"].dropna().unique().astype(int).tolist())

    df_recent = df_recent.copy()
    df_recent["is_weekend"] = df_recent["요일번호"].isin([5, 6])

    df_recent["구분"] = "평일2(화·수·목)"
    df_recent["is_weekday1"] = df_recent["요일번호"].isin([0, 4])
    df_recent.loc[df_recent["is_weekday1"], "구분"] = "평일1(월·금)"
    df_recent.loc[df_recent["is_weekend"], "구분"] = "주말/공휴일"

    grp = df_recent.groupby(["구분", "nth_dow"], as_index=False)["공급량(MJ)"].mean()
    ratio_w1_group = grp[grp["구분"] == "평일1(월·금)"].copy()
    ratio_w2_group = grp[grp["구분"] == "평일2(화·수·목)"].copy()

    grp_dow = df_recent.groupby(["구분", "요일번호"], as_index=False)["공급량(MJ)"].mean()
    ratio_w1_by_dow = grp_dow[grp_dow["구분"] == "평일1(월·금)"].copy()
    ratio_w2_by_dow = grp_dow[grp_dow["구분"] == "평일2(화·수·목)"].copy()
    ratio_weekend_by_dow = grp_dow[grp_dow["구분"] == "주말/공휴일"].copy()

    ratio_w1_group_dict = dict(zip(ratio_w1_group["nth_dow"], ratio_w1_group["공급량(MJ)"]))
    ratio_w2_group_dict = dict(zip(ratio_w2_group["nth_dow"], ratio_w2_group["공급량(MJ)"]))
    ratio_w1_by_dow_dict = dict(zip(ratio_w1_by_dow["요일번호"], ratio_w1_by_dow["공급량(MJ)"]))
    ratio_w2_by_dow_dict = dict(zip(ratio_w2_by_dow["요일번호"], ratio_w2_by_dow["공급량(MJ)"]))
    ratio_weekend_by_dow_dict = dict(zip(ratio_weekend_by_dow["요일번호"], ratio_weekend_by_dow["공급량(MJ)"]))

    df_target = df_target.copy()
    df_target["raw"] = np.nan

    def _pick_ratio(row):
        key = row["nth_dow"]
        dow = row["요일번호"]

        if row["구분"] == "주말/공휴일":
            return ratio_weekend_by_dow_dict.get(dow, None)

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
    df_target["일별비율"] = (df_target["raw"] / raw_sum) if raw_sum > 0 else (1.0 / df_target["일"].max())

    return df_target, df_recent, used_years


def make_daily_plan_table(
    df_daily: pd.DataFrame,
    df_plan: pd.DataFrame,
    target_year: int,
    target_month: int,
    recent_window: int,
    plan_col: str,
    df_cal: pd.DataFrame | None = None,
):
    df_target_base = _make_target_calendar(target_year, target_month)
    df_target = _classify_day(df_target_base, df_cal)

    cand_years = _recent_years(df_daily, target_year, recent_window)
    df_recent = _prepare_recent_month(df_daily, cand_years, target_month)

    df_target, df_recent, used_years = _compute_ratios(df_recent, df_target)

    month_total_all = df_recent["공급량(MJ)"].sum()
    df_target["최근N년_총공급량(MJ)"] = df_target["일별비율"] * month_total_all
    df_target["최근N년_평균공급량(MJ)"] = df_target["최근N년_총공급량(MJ)"] / len(used_years) if len(used_years) > 0 else np.nan

    row_plan = df_plan[(df_plan["연"] == target_year) & (df_plan["월"] == target_month)]
    plan_total = float(row_plan[plan_col].iloc[0]) if not row_plan.empty else np.nan

    df_target["예상공급량(MJ)"] = (df_target["일별비율"] * plan_total).round(0)
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
            "최근N년_평균공급량(MJ)",
            "최근N년_총공급량(MJ)",
            "일별비율",
            "예상공급량(MJ)",
        ]
    ].copy()

    df_mat = (
        df_recent.pivot_table(index="일", columns="연도", values="공급량(MJ)", aggfunc="sum")
        .sort_index()
        .copy()
    )

    df_debug = df_target[
        [
            "일자",
            "요일",
            "요일번호",
            "weekday_idx",
            "nth_dow",
            "구분",
            "is_weekend",
            "is_holiday",
            "is_weekday1",
            "raw",
            "일별비율",
        ]
    ].copy()

    return df_result, df_mat, df_debug, used_years, plan_total


def _build_year_daily_plan(
    df_daily: pd.DataFrame,
    df_plan: pd.DataFrame,
    target_year: int,
    recent_window: int,
    plan_col: str,
):
    df_cal = load_effective_calendar()
    out_all = []
    month_summary_rows = []

    for m in range(1, 13):
        df_result, _, _, _, plan_total = make_daily_plan_table(
            df_daily=df_daily,
            df_plan=df_plan,
            target_year=target_year,
            target_month=m,
            recent_window=recent_window,
            plan_col=plan_col,
            df_cal=df_cal,
        )
        out_all.append(df_result)
        month_summary_rows.append({"연": target_year, "월": m, "월간 계획(MJ)": plan_total})

    df_year = pd.concat(out_all, ignore_index=True)
    df_month_sum = pd.DataFrame(month_summary_rows)

    total_row = {
        "연": "",
        "월": "",
        "일": "",
        "일자": "",
        "요일": "합계",
        "weekday_idx": "",
        "nth_dow": "",
        "구분": "",
        "공휴일여부": False,
        "최근N년_평균공급량(MJ)": df_year["최근N년_평균공급량(MJ)"].sum(),
        "최근N년_총공급량(MJ)": df_year["최근N년_총공급량(MJ)"].sum(),
        "일별비율": df_year["일별비율"].sum(),
        "예상공급량(MJ)": df_year["예상공급량(MJ)"].sum(),
    }
    df_year_with_total = pd.concat([df_year, pd.DataFrame([total_row])], ignore_index=True)

    return df_year_with_total, df_month_sum


def tab_daily_plan(df_daily: pd.DataFrame):
    df_plan = load_monthly_plan()
    df_cal = load_effective_calendar()

    # ✅ plan_col을 실제 파일 컬럼명으로 자동 맞춤 (이번 KeyError 해결)
    plan_col = resolve_plan_col(df_plan, "사업계획(월별계획)")

    st.sidebar.markdown("### ✅ Daily 공급량 계획 설정")
    years = sorted(df_plan["연"].dropna().unique().astype(int).tolist())
    default_year = 2025 if 2025 in years else (years[-1] if years else 2025)

    target_year = st.sidebar.selectbox(
        "계획 연도 선택",
        years if years else [default_year],
        index=(years.index(default_year) if years and default_year in years else 0),
    )

    months = list(range(1, 13))
    target_month = st.sidebar.selectbox("계획 월 선택", months, index=0)

    recent_window = st.sidebar.slider("최근 N년 후보(최대 몇 년 전까지)", min_value=2, max_value=6, value=3, step=1)

    # 0) 월별 계획표(가로) + 연간 총량
    st.markdown("### 📌 월별 계획량(1~12월) & 연간 총량")
    df_plan_h = make_month_plan_horizontal(df_plan=df_plan, target_year=int(target_year), plan_col=plan_col)
    df_plan_h_disp = format_table_generic(df_plan_h)
    show_table_no_index(df_plan_h_disp, height=160)

    # 1) 대상월 계산
    st.markdown("### 📍 1. 대상월 일별 비율, 예상 공급량 테이블")

    df_result, df_mat, df_debug, used_years, plan_total = make_daily_plan_table(
        df_daily=df_daily,
        df_plan=df_plan,
        target_year=int(target_year),
        target_month=int(target_month),
        recent_window=int(recent_window),
        plan_col=plan_col,
        df_cal=df_cal,
    )

    plan_total_gj = mj_to_gj(plan_total)
    plan_total_m3 = mj_to_m3(plan_total)

    st.markdown(
        f"**{target_year}년 {target_month}월 사업계획 제출 공급량 합계:** "
        f"`{plan_total_gj:,.0f} GJ`  /  `{plan_total_m3:,.0f} ㎥`"
    )

    view = df_result.copy()
    total_row = {
        "연": "",
        "월": "",
        "일": "",
        "일자": "",
        "요일": "합계",
        "weekday_idx": "",
        "nth_dow": "",
        "구분": "",
        "공휴일여부": False,
        "최근N년_평균공급량(MJ)": view["최근N년_평균공급량(MJ)"].sum(),
        "최근N년_총공급량(MJ)": view["최근N년_총공급량(MJ)"].sum(),
        "일별비율": view["일별비율"].sum(),
        "예상공급량(MJ)": view["예상공급량(MJ)"].sum(),
    }
    view_with_total = pd.concat([view, pd.DataFrame([total_row])], ignore_index=True)

    view_for_format = view_with_total[
        [
            "연", "월", "일", "요일", "weekday_idx", "nth_dow", "구분", "공휴일여부",
            "최근N년_평균공급량(MJ)", "최근N년_총공급량(MJ)", "일별비율", "예상공급량(MJ)"
        ]
    ].copy()

    # MJ → GJ + ㎥ 변환 (표시용)
    view_for_format = add_gj_m3_columns(
        view_for_format,
        mj_cols=["최근N년_평균공급량(MJ)", "최근N년_총공급량(MJ)", "예상공급량(MJ)"],
        drop_mj=True,
        round_digits=0,
    )

    view_for_format = view_for_format[
        [
            "연", "월", "일", "요일", "weekday_idx", "nth_dow", "구분", "공휴일여부",
            "최근N년_평균공급량(GJ)", "최근N년_평균공급량(㎥)",
            "최근N년_총공급량(GJ)", "최근N년_총공급량(㎥)",
            "일별비율",
            "예상공급량(GJ)", "예상공급량(㎥)",
        ]
    ]

    view_for_format = format_table_generic(view_for_format, percent_cols=["일별비율"])
    show_table_no_index(view_for_format, height=520)

    with st.expander("🔎 (검증) 대상월 '1째 월요일/2째 월요일...' 계산 확인 (weekday_idx/nth_dow/raw/비율)"):
        dbg_disp = format_table_generic(df_debug.copy(), percent_cols=["일별비율"])
        show_table_no_index(dbg_disp, height=420)

    # 2) 그래프
    st.markdown("#### 📊 2. 일별 예상 공급량 & 비율 그래프(평일1/평일2/주말 분리)")

    view_plot = view.copy()
    view_plot["예상공급량(GJ)"] = mj_to_gj(view_plot["예상공급량(MJ)"].astype("float64")).round(0)

    w1_df = view_plot[view_plot["구분"] == "평일1(월·금)"]
    w2_df = view_plot[view_plot["구분"] == "평일2(화·수·목)"]
    wend_df = view_plot[view_plot["구분"] == "주말/공휴일"]

    fig = go.Figure()
    fig.add_bar(x=w1_df["일"], y=w1_df["예상공급량(GJ)"], name="평일1(월·금) 예상공급량(GJ)")
    fig.add_bar(x=w2_df["일"], y=w2_df["예상공급량(GJ)"], name="평일2(화·수·목) 예상공급량(GJ)")
    fig.add_bar(x=wend_df["일"], y=wend_df["예상공급량(GJ)"], name="주말/공휴일 예상공급량(GJ)")
    fig.add_trace(
        go.Scatter(
            x=view["일"],
            y=view["일별비율"],
            mode="lines+markers",
            name=f"일별비율 (최근{len(used_years)}년 실제 사용)",
            yaxis="y2",
        )
    )

    fig.update_layout(
        title=(
            f"{target_year}년 {target_month}월 일별 공급량 계획 "
            f"(최근{recent_window}년 후보 중 실제 사용 {len(used_years)}년, {target_month}월 패턴 기반)"
        ),
        xaxis_title="일",
        yaxis=dict(title="예상 공급량 (GJ)"),
        yaxis2=dict(title="일별비율", overlaying="y", side="right"),
        barmode="group",
        margin=dict(l=20, r=20, t=60, b=40),
    )
    st.plotly_chart(fig, use_container_width=True)

    # 3) 매트릭스(Heatmap)
    st.markdown("#### 🧊 3. (참고) 과거 N년 일별 공급량 매트릭스 (Heatmap)")
    if df_mat.empty:
        st.info("최근 N년 데이터가 부족하여 매트릭스를 표시할 수 없어.")
    else:
        fig_hm = go.Figure(
            data=go.Heatmap(
                z=mj_to_gj(df_mat.values.astype("float64")),
                x=[str(c) for c in df_mat.columns],
                y=[str(i) for i in df_mat.index],
                colorbar_title="공급량(GJ)",
            )
        )
        fig_hm.update_layout(
            title=f"최근 {len(used_years)}년 {target_month}월 일별 실적 공급량(GJ) 매트릭스",
            xaxis_title="연도",
            yaxis_title="일",
            height=420,
        )
        st.plotly_chart(fig_hm, use_container_width=True)

    # 4) 구분별 요약
    st.markdown("#### 🧾 4. 구분별 비중 요약(평일1/평일2/주말)")

    summary = (
        view_plot.groupby("구분", as_index=False)[["일별비율", "예상공급량(GJ)"]]
        .sum()
        .rename(columns={"일별비율": "일별비율합계"})
    )
    summary["예상공급량(㎥)"] = mj_to_m3((summary["예상공급량(GJ)"] * 1000.0).astype("float64")).round(0)

    total_row_sum = {
        "구분": "합계",
        "일별비율합계": summary["일별비율합계"].sum(),
        "예상공급량(GJ)": summary["예상공급량(GJ)"].sum(),
        "예상공급량(㎥)": summary["예상공급량(㎥)"].sum(),
    }
    summary = pd.concat([summary, pd.DataFrame([total_row_sum])], ignore_index=True)
    summary = format_table_generic(summary, percent_cols=["일별비율합계"])
    show_table_no_index(summary, height=220)

    # 5) 월별 다운로드(대상월) — GJ/㎥ 둘 다 포함
    st.markdown("#### ⬇️ 5. 일일계획 다운로드(월별)")
    buffer = BytesIO()
    sheet_name = f"{target_year}-{target_month:02d}"
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df_excel = view_with_total.copy()
        df_excel = add_gj_m3_columns(
            df_excel,
            mj_cols=["최근N년_평균공급량(MJ)", "최근N년_총공급량(MJ)", "예상공급량(MJ)"],
            drop_mj=True,
            round_digits=0,
        )
        df_excel.to_excel(writer, index=False, sheet_name=sheet_name)

        wb = writer.book
        ws = wb[sheet_name]

        header_font = Font(bold=True)
        center = Alignment(horizontal="center", vertical="center", wrap_text=True)

        for cell in ws[1]:
            cell.font = header_font
            cell.alignment = center

        for row in ws.iter_rows(min_row=2, max_row=ws.max_row):
            for cell in row:
                cell.alignment = center

        for col in ws.columns:
            max_len = 0
            col_letter = col[0].column_letter
            for cell in col:
                val = "" if cell.value is None else str(cell.value)
                max_len = max(max_len, len(val))
            ws.column_dimensions[col_letter].width = min(max(10, max_len + 2), 24)

    st.download_button(
        label="📥 엑셀 다운로드(월별)",
        data=buffer.getvalue(),
        file_name=f"일별계획_{target_year}_{target_month:02d}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    # 6) 연간 다운로드 — GJ/㎥ 둘 다 포함
    st.markdown("#### ⬇️ 6. 일일계획 다운로드(연간)")
    annual_year = st.selectbox(
        "연간 다운로드 연도 선택",
        years if years else [default_year],
        index=(years.index(default_year) if years and default_year in years else 0),
        key="annual_year",
    )
    buffer_year = BytesIO()

    df_year_daily, df_month_summary = _build_year_daily_plan(
        df_daily=df_daily,
        df_plan=df_plan,
        target_year=int(annual_year),
        recent_window=int(recent_window),
        plan_col=plan_col,  # ✅ 동일하게 적용
    )

    with pd.ExcelWriter(buffer_year, engine="openpyxl") as writer:
        df_year_excel = add_gj_m3_columns(
            df_year_daily,
            mj_cols=["최근N년_평균공급량(MJ)", "최근N년_총공급량(MJ)", "예상공급량(MJ)"],
            drop_mj=True,
            round_digits=0,
        )
        df_year_excel.to_excel(writer, index=False, sheet_name="연간")

        df_month_excel = df_month_summary.copy()
        if "월간 계획(MJ)" in df_month_excel.columns:
            df_month_excel = add_gj_m3_columns(
                df_month_excel,
                mj_cols=["월간 계획(MJ)"],
                drop_mj=True,
                round_digits=0,
            )
        df_month_excel.to_excel(writer, index=False, sheet_name="월 요약 계획")

        wb = writer.book
        for sheet in ["연간", "월 요약 계획"]:
            ws = wb[sheet]
            header_font = Font(bold=True)
            center = Alignment(horizontal="center", vertical="center", wrap_text=True)

            for cell in ws[1]:
                cell.font = header_font
                cell.alignment = center

            for row in ws.iter_rows(min_row=2, max_row=ws.max_row):
                for cell in row:
                    cell.alignment = center

            for col in ws.columns:
                max_len = 0
                col_letter = col[0].column_letter
                for cell in col:
                    val = "" if cell.value is None else str(cell.value)
                    max_len = max(max_len, len(val))
                ws.column_dimensions[col_letter].width = min(max(10, max_len + 2), 24)

    st.download_button(
        label="📥 엑셀 다운로드(연간)",
        data=buffer_year.getvalue(),
        file_name=f"일별계획_{annual_year}_연간.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


# ─────────────────────────────────────────────
# Daily·Monthly 비교(기온 기반 예측 검증)
# ─────────────────────────────────────────────
def fit_poly3_and_r2(x: pd.Series, y: pd.Series):
    x = x.astype("float64")
    y = y.astype("float64")
    mask = x.notna() & y.notna()
    x = x[mask]
    y = y[mask]
    if len(x) < 6:
        return None, None, None
    coef = np.polyfit(x, y, 3)
    p = np.poly1d(coef)
    y_pred = p(x)
    ss_res = np.sum((y - y_pred) ** 2)
    ss_tot = np.sum((y - y.mean()) ** 2)
    r2 = 1 - ss_res / ss_tot if ss_tot != 0 else np.nan
    return coef, y_pred, r2


def plot_poly_fit(x, y, coef, title, x_label, y_label):
    p = np.poly1d(coef)
    x_line = np.linspace(np.nanmin(x), np.nanmax(x), 200)
    y_line = p(x_line)

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=x, y=y, mode="markers", name="실제"))
    fig.add_trace(go.Scatter(x=x_line, y=y_line, mode="lines", name="3차 회귀"))
    fig.update_layout(title=title, xaxis_title=x_label, yaxis_title=y_label, height=420, margin=dict(l=20, r=20, t=60, b=40))
    return fig


def tab_daily_monthly_compare(df: pd.DataFrame, df_temp_all: pd.DataFrame):
    st.sidebar.markdown("### ✅ 비교 설정")

    min_year = int(df["연도"].min())
    max_year = int(df["연도"].max())
    start_year = st.sidebar.number_input("학습 시작 연도", min_value=min_year, max_value=max_year, value=min_year, step=1)
    end_year = st.sidebar.number_input("학습 종료 연도", min_value=min_year, max_value=max_year, value=max_year, step=1)

    df_window = df[(df["연도"] >= start_year) & (df["연도"] <= end_year)].copy()

    st.markdown("### 📈 기온–공급량 관계(일/월) 3차 회귀 + R² 비교")

    df_month = (
        df_window
        .groupby(["연도", "월"], as_index=False)
        .agg(공급량_MJ=("공급량(MJ)", "sum"), 평균기온=("평균기온(℃)", "mean"))
    )
    df_month["공급량_GJ"] = mj_to_gj(df_month["공급량_MJ"].astype("float64"))

    coef_m, y_pred_m, r2_m = fit_poly3_and_r2(df_month["평균기온"], df_month["공급량_GJ"])
    df_month["예측공급량_GJ"] = y_pred_m if y_pred_m is not None else np.nan

    df_window["공급량_GJ"] = mj_to_gj(df_window["공급량(MJ)"].astype("float64"))
    coef_d, y_pred_d, r2_d = fit_poly3_and_r2(df_window["평균기온(℃)"], df_window["공급량_GJ"])
    df_window["예측공급량_GJ"] = y_pred_d if y_pred_d is not None else np.nan

    st.markdown("##### 월평균 vs 일평균 기온 기반 R² 비교 (학습기간 기준)")
    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**월 단위 모델 (월평균 기온 → 월별 공급량)**")
        if r2_m is not None:
            st.metric("R² (월평균 기온 사용)", f"{r2_m:.3f}")
            st.caption(f"사용 월 수: {len(df_month)}")
        else:
            st.warning("월 단위 모델 계산에 필요한 데이터가 부족해.")

    with col2:
        st.markdown("**일 단위 모델 (일평균 기온 → 일별 공급량)**")
        if r2_d is not None:
            st.metric("R² (일평균 기온 사용)", f"{r2_d:.3f}")
            st.caption(f"사용 일 수: {len(df_window)}")
        else:
            st.warning("일 단위 모델 계산에 필요한 데이터가 부족해.")

    st.markdown("---")
    st.markdown("### 🔍 산점도 + 회귀곡선 (월/일)")

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
    st.markdown("### 📌 상관도 분석(옵션)")

    df_corr = load_corr_data()
    if df_corr is None:
        st.info("상관도분석.xlsx 파일이 없어서 상관도 분석 탭은 생략했어.")
        return

    cols = df_corr.columns.tolist()
    numeric_cols = [c for c in cols if pd.api.types.is_numeric_dtype(df_corr[c])]
    if len(numeric_cols) < 2:
        st.info("상관계수 계산을 위한 수치형 컬럼이 부족해.")
        return

    corr = df_corr[numeric_cols].corr()

    fig_corr = go.Figure(
        data=go.Heatmap(
            z=corr.values,
            x=corr.columns,
            y=corr.index,
            colorbar_title="상관계수",
            zmin=-1,
            zmax=1,
        )
    )
    fig_corr.update_layout(title="수치형 컬럼 상관계수 Heatmap", height=520, margin=dict(l=20, r=20, t=60, b=40))
    st.plotly_chart(fig_corr, use_container_width=True)


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
'''
path = "/mnt/data/app.py"
with open(path, "w", encoding="utf-8") as f:
    f.write(code)
path
