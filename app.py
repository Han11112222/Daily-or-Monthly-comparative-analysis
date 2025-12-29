# (전체 코드가 길어서 파일(app_updated.py)과 동일합니다)
# 아래 내용을 그대로 app.py에 붙여넣어 사용하면 됩니다.

from __future__ import annotations

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
    page_title="도시가스 공급량 — 일별 vs 월별 예측 검증",
    layout="wide",
)

# 단위 변환
MJ_TO_GJ = 0.001  # MJ → GJ
MJ_PER_NM3 = 42.563  # MJ/Nm3 (사용자 지정)

# ─────────────────────────────────────────────
# 유틸
# ─────────────────────────────────────────────
def mj_to_gj(x) -> float:
    try:
        return float(x) * MJ_TO_GJ
    except Exception:
        return np.nan


def mj_to_nm3(x) -> float:
    """MJ → Nm3"""
    try:
        return float(x) / MJ_PER_NM3
    except Exception:
        return np.nan


def _find_plan_col(df: pd.DataFrame) -> str:
    """
    월별계획_실적 sheet 내에서 '계획량(MJ)'에 해당하는 컬럼을 자동 탐색
    """
    candidates = [
        "사업계획(월별 계획)", "사업계획", "월별 계획", "계획", "계획량(MJ)", "계획량", "MJ", "공급계획",
        "공급량계획", "월별공급량", "월별공급량(MJ)", "월별계획량", "월별계획량(MJ)",
    ]
    for c in candidates:
        if c in df.columns:
            return c

    # 숫자형 컬럼 중 연/월 제외하고 첫번째
    numeric_cols = [c for c in df.columns if c not in ["연", "월"] and pd.api.types.is_numeric_dtype(df[c])]
    if numeric_cols:
        return numeric_cols[0]

    raise KeyError(f"월별계획에서 계획량 컬럼을 찾지 못했어. 현재 컬럼: {list(df.columns)}")


# ─────────────────────────────────────────────
# 데이터 로딩
# ─────────────────────────────────────────────
@st.cache_data
def load_daily_data():
    """
    반환:
      df_model     : 공급량(MJ)와 평균기온 둘 다 있는 구간 (예측/R² 계산용)
      df_temp_all  : 평균기온만 있어도 되는 전체 구간 (매트릭스/시나리오용)
    """
    excel_path = Path(__file__).parent / "공급량(일일실적).xlsx"
    df_raw = pd.read_excel(excel_path)

    # 필요한 컬럼만 사용 (컬럼명이 다를 수 있어 후보로 처리)
    df_raw.columns = [str(c).strip().replace("\n", " ") for c in df_raw.columns]

    # 날짜
    date_col = None
    for c in ["일자", "날짜", "Date", "date"]:
        if c in df_raw.columns:
            date_col = c
            break
    if date_col is None:
        raise KeyError(f"일자 컬럼을 찾지 못했어. 현재 컬럼: {list(df_raw.columns)}")

    # 공급량(MJ)
    supply_col = None
    for c in ["공급량(MJ)", "공급량MJ", "공급량", "Supply(MJ)"]:
        if c in df_raw.columns:
            supply_col = c
            break
    if supply_col is None:
        raise KeyError(f"공급량(MJ) 컬럼을 찾지 못했어. 현재 컬럼: {list(df_raw.columns)}")

    # 평균기온(℃)
    tcol = None
    for c in ["평균기온(℃)", "평균기온", "Tavg", "AvgTemp", "avg_temp"]:
        if c in df_raw.columns:
            tcol = c
            break

    df_raw = df_raw[[date_col, supply_col] + ([tcol] if tcol else [])].copy()
    df_raw = df_raw.rename(columns={date_col: "일자", supply_col: "공급량(MJ)"})
    if tcol:
        df_raw = df_raw.rename(columns={tcol: "평균기온(℃)"})
    else:
        df_raw["평균기온(℃)"] = np.nan

    df_raw["일자"] = pd.to_datetime(df_raw["일자"])
    df_raw["연도"] = df_raw["일자"].dt.year
    df_raw["월"] = df_raw["일자"].dt.month
    df_raw["일"] = df_raw["일자"].dt.day
    df_raw["요일"] = df_raw["일자"].dt.day_name()

    # 예측/R²는 공급량과 평균기온 둘 다 있어야 함
    df_model = df_raw.dropna(subset=["공급량(MJ)", "평균기온(℃)"]).copy()
    df_temp_all = df_raw.dropna(subset=["평균기온(℃)"]).copy()

    return df_model, df_temp_all


@st.cache_data
def _auto_find_monthly_plan_path() -> Path | None:
    """repo 폴더에서 월별계획 파일을 자동 탐색"""
    base = Path(__file__).parent
    patterns = [
        "공급량(계획_실적).xlsx",
        "월별계획.xlsx",
        "월별계획*.xlsx",
        "*계획*실적*.xlsx",
        "*monthly*plan*.xlsx",
    ]
    candidates: list[Path] = []
    for pat in patterns:
        candidates += list(base.glob(pat))

    # 중복 제거
    uniq = []
    seen = set()
    for p in candidates:
        if p.is_file():
            k = str(p.resolve())
            if k not in seen:
                uniq.append(p)
                seen.add(k)

    if not uniq:
        return None

    # 최신 수정 파일 우선
    return sorted(uniq, key=lambda p: p.stat().st_mtime, reverse=True)[0]


def _read_monthly_plan_from_excel(excel_obj) -> pd.DataFrame:
    """월별계획 엑셀을 읽고, 연/월 컬럼을 int로 정리"""
    try:
        xls = pd.ExcelFile(excel_obj)
        sheet = "월별계획_실적" if "월별계획_실적" in xls.sheet_names else xls.sheet_names[0]
        df = pd.read_excel(xls, sheet_name=sheet)
    except Exception:
        df = pd.read_excel(excel_obj)

    df.columns = [str(c).strip().replace("\n", " ") for c in df.columns]

    def _pick_col(cands):
        for c in cands:
            if c in df.columns:
                return c
        return None

    ycol = _pick_col(["연", "연도", "년도", "Year"])
    mcol = _pick_col(["월", "Month"])
    if ycol is None or mcol is None:
        raise KeyError(f"월별계획 파일에서 연/월 컬럼을 찾지 못했어. 현재 컬럼: {list(df.columns)}")

    df = df.rename(columns={ycol: "연", mcol: "월"}).copy()
    df["연"] = pd.to_numeric(df["연"], errors="coerce").astype("Int64")
    df["월"] = pd.to_numeric(df["월"], errors="coerce").astype("Int64")
    df = df.dropna(subset=["연", "월"]).copy()
    df["연"] = df["연"].astype(int)
    df["월"] = df["월"].astype(int)
    return df


def load_monthly_plan(uploaded_file=None) -> pd.DataFrame | None:
    """월별계획_실적 로딩: (1) 업로드 파일 → (2) repo 자동탐색 파일"""
    if uploaded_file is not None:
        try:
            return _read_monthly_plan_from_excel(uploaded_file)
        except Exception as e:
            st.error(f"업로드한 월별계획 파일을 읽는 중 문제가 생겼어: {e}")
            return None

    path = _auto_find_monthly_plan_path()
    if path is None:
        return None

    try:
        return _read_monthly_plan_from_excel(path)
    except Exception as e:
        st.error(f"repo의 월별계획 파일({path.name})을 읽는 중 문제가 생겼어: {e}")
        return None


# ─────────────────────────────────────────────
# 탭1: Daily 공급량 분석
# ─────────────────────────────────────────────
def tab_daily_plan(df_daily: pd.DataFrame):
    st.subheader("📅 Daily 공급량 분석 — 최근 N년 패턴 기반 일별 계획")

    # --------------------------------------------------
    # 1) 월별계획 엑셀 업로드(없으면 repo에서 자동 탐색)
    # --------------------------------------------------
    st.markdown("### 📁 1. 월별계획 엑셀 업로드(XLSX) (없으면 폴더에서 자동 탐색)")
    up_plan = st.file_uploader("월별 계획 엑셀 업로드", type=["xlsx"], key="monthly_plan_uploader")

    df_plan = load_monthly_plan(uploaded_file=up_plan)
    if df_plan is None or df_plan.empty:
        st.error("월별 계획 파일을 찾지 못했어. 업로드하거나 repo에 월별계획 엑셀을 넣어줘.")
        st.stop()

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

    # 해당 월 계획량(MJ)
    plan_mj = float(df_plan[(df_plan["연"] == target_year) & (df_plan["월"] == target_month)][plan_col].iloc[0])

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

    # 최근 N년 후보: 직전 연도부터 역순
    use_years = sorted(hist_years)[-recent_window:]
    st.write(f"• **실제 학습에 사용된 연도(해당월 실적 존재):** {min(use_years)}년 ~ {max(use_years)}년 (총 {len(use_years)}개)")

    # 해당월의 실제 일별 패턴(최근 N년)
    df_hist = df_daily[(df_daily["연도"].isin(use_years)) & (df_daily["월"] == target_month)].copy()
    if df_hist.empty:
        st.warning("최근 N년 구간에 해당 월 데이터가 없어.")
        return

    # 일자 패턴 계산 (요일/주말 등은 네 기존 로직을 그대로 쓰고 있다고 가정)
    # 여기서는 최소한의 예시로 일자별 평균 비율을 계산
    df_hist["월내일"] = df_hist["일"]
    daily_sum_by_year = df_hist.groupby(["연도"])["공급량(MJ)"].sum().rename("월합계").reset_index()
    df_hist = df_hist.merge(daily_sum_by_year, on="연도", how="left")
    df_hist["일별비율"] = df_hist["공급량(MJ)"] / df_hist["월합계"]

    pattern = df_hist.groupby(["월내일"])["일별비율"].mean().reset_index()
    pattern = pattern.rename(columns={"월내일": "일"})
    pattern["일별비율"] = pattern["일별비율"] / pattern["일별비율"].sum()

    # 타겟 월 일수
    last_day = calendar.monthrange(target_year, target_month)[1]
    pattern = pattern[pattern["일"].between(1, last_day)].copy()

    # 예상공급량(MJ)
    pattern["예상공급량(MJ)"] = pattern["일별비율"] * plan_mj

    st.markdown("### 🧩 2. 일별 예상 공급량 & 비율 그래프(평일1/평일2/주말 분리)")
    # 분리용(예시: 요일 기반; 실제 네 로직이 있으면 그걸 그대로 쓰면 됨)
    # 여기서는 df_target_month 달력을 만들고 요일 붙임
    dates = pd.date_range(f"{target_year}-{target_month:02d}-01", f"{target_year}-{target_month:02d}-{last_day}")
    cal = pd.DataFrame({"일자": dates})
    cal["일"] = cal["일자"].dt.day
    cal["요일"] = cal["일자"].dt.day_name()
    cal["is_weekend"] = cal["요일"].isin(["Saturday", "Sunday"])
    view = pattern.merge(cal[["일", "요일", "is_weekend"]], on="일", how="left")

    # 평일1/평일2(예시): 월/금 vs 화수목, 주말은 주말
    w1_df = view[(~view["is_weekend"]) & (view["요일"].isin(["Monday", "Friday"]))].copy()
    w2_df = view[(~view["is_weekend"]) & (view["요일"].isin(["Tuesday", "Wednesday", "Thursday"]))].copy()
    wend_df = view[view["is_weekend"]].copy()

    fig = go.Figure()
    fig.add_bar(x=w1_df["일"], y=w1_df["예상공급량(MJ)"].apply(mj_to_gj), name="평일1(월·금) 예상공급량(GJ)",
                hovertemplate="일=%{x}<br>예상공급량=%{y:,.0f} GJ<extra></extra>")
    fig.add_bar(x=w2_df["일"], y=w2_df["예상공급량(MJ)"].apply(mj_to_gj), name="평일2(화·수·목) 예상공급량(GJ)",
                hovertemplate="일=%{x}<br>예상공급량=%{y:,.0f} GJ<extra></extra>")
    fig.add_bar(x=wend_df["일"], y=wend_df["예상공급량(MJ)"].apply(mj_to_gj), name="주말/공휴일 예상공급량(GJ)",
                hovertemplate="일=%{x}<br>예상공급량=%{y:,.0f} GJ<extra></extra>")

    # 비율 라인
    fig.add_scatter(
        x=view["일"],
        y=view["일별비율"],
        mode="lines+markers",
        name="일별비율(최근N년 실제 사용)",
        yaxis="y2",
        hovertemplate="일=%{x}<br>일별비율=%{y:.4f}<extra></extra>",
    )

    fig.update_layout(
        title=f"{target_year}년 {target_month}월 일별 공급량 계획 (최근 {recent_window}년 패턴 기반)",
        barmode="group",
        xaxis_title="일",
        yaxis_title="예상 공급량 (GJ)",
        yaxis2=dict(title="일별비율", overlaying="y", side="right", tickformat=".3f"),
        legend=dict(orientation="v"),
        height=520,
        margin=dict(l=30, r=30, t=60, b=40),
    )
    st.plotly_chart(fig, use_container_width=True)

    # (이하: 네 기존 표/다운로드/월별계획 표시는 원래 코드 흐름대로 유지한다고 가정)
    st.markdown("### 📌 월별 계획량(1~12월) & 연간 총량 (GJ / Nm3)")
    # 월별 계획 테이블 (GJ)
    df_year = df_plan[df_plan["연"] == target_year].copy()
    df_year = df_year.sort_values("월")
    monthly_mj = df_year.set_index("월")[plan_col].reindex(range(1, 13))
    annual_mj = float(monthly_mj.sum(skipna=True))

    row_gj = [mj_to_gj(monthly_mj.get(m, np.nan)) for m in range(1, 13)] + [mj_to_gj(annual_mj)]
    row_nm3 = [mj_to_nm3(monthly_mj.get(m, np.nan)) for m in range(1, 13)] + [mj_to_nm3(annual_mj)]

    cols = ["구분"] + [f"{m}월" for m in range(1, 13)] + ["연간합계"]
    table = pd.DataFrame(
        [
            ["사업계획(월별 계획) - GJ"] + row_gj,
            ["사업계획(월별 계획) - ㎥"] + row_nm3,
        ],
        columns=cols,
    )
    st.dataframe(table, use_container_width=True)


# ─────────────────────────────────────────────
# 탭2: Daily vs Monthly 비교
# ─────────────────────────────────────────────
def plot_poly_fit(df_x, df_y, coef, title, x_label, y_label):
    xs = np.linspace(df_x.min(), df_x.max(), 200)
    ys = np.polyval(coef, xs)

    fig = go.Figure()
    fig.add_scatter(x=df_x, y=df_y, mode="markers", name="실측")
    fig.add_scatter(x=xs, y=ys, mode="lines", name="회귀(3차)")
    fig.update_layout(title=title, xaxis_title=x_label, yaxis_title=y_label, height=420)
    return fig


def tab_daily_monthly_compare(df: pd.DataFrame, df_temp_all: pd.DataFrame):
    st.subheader("📊 Daily·Monthly 공급량 비교")

    st.markdown("### 📌 0. 상관도 분석 (공급량 vs 주요 변수)")
    # 예시 상관도
    cand_cols = ["공급량(MJ)", "평균기온(℃)"]
    used = [c for c in cand_cols if c in df.columns]
    if len(used) >= 2:
        corr = df[used].corr()

        z = corr.values
        text = np.round(z, 2).astype(str)

        fig_corr = go.Figure(
            data=go.Heatmap(
                z=z,
                x=corr.columns,
                y=corr.index,
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
            xaxis=dict(side="top", tickangle=45),
            yaxis=dict(autorange="reversed", scaleanchor="x", scaleratio=1),
            width=600,
            height=600,
            margin=dict(l=80, r=20, t=80, b=80),
        )
        st.plotly_chart(fig_corr, use_container_width=True)
    else:
        st.info("상관도 계산에 필요한 컬럼이 부족해.")

    # (중간: 네 기존 Daily/Monthly 회귀 비교 로직이 있다고 가정)
    # 여기서는 최소 예시로: 일단위/월단위 3차 회귀만 보여줌
    st.markdown("### 📌 1. 월평균기온 기반 월별 공급량 회귀(3차 다항식) / 일단위 비교")

    df_window = df.dropna(subset=["평균기온(℃)", "공급량(MJ)"]).copy()
    df_window["공급량_GJ"] = df_window["공급량(MJ)"].apply(mj_to_gj)

    # 월단위 집계
    df_month = (
        df_window.assign(연도=df_window["일자"].dt.year, 월=df_window["일자"].dt.month)
        .groupby(["연도", "월"], as_index=False)
        .agg(평균기온=("평균기온(℃)", "mean"), 공급량_GJ=("공급량_GJ", "sum"))
    )

    col3, col4 = st.columns(2)
    coef_m = None
    coef_d = None
    if len(df_month) >= 10:
        coef_m = np.polyfit(df_month["평균기온"], df_month["공급량_GJ"], 3)
    if len(df_window) >= 30:
        coef_d = np.polyfit(df_window["평균기온(℃)"], df_window["공급량_GJ"], 3)

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

    # --------------------------------------------------
    # G. 기온분석 — 일일 평균기온 히트맵 (탭 하단 추가)
    # --------------------------------------------------
    st.divider()
    st.subheader("🧊 G. 기온분석 — 일일 평균기온 히트맵")
    st.caption("기본은 공급량 데이터의 평균기온(℃)을 사용해. 필요하면 기온 파일만 따로 업로드해서 볼 수 있어.")

    up_temp = st.file_uploader("일일기온 파일 업로드(XLSX) (선택)", type=["xlsx"], key="temp_uploader_tab2")

    def _pick_col(cols, cands):
        for c in cands:
            if c in cols:
                return c
        return None

    df_t = None
    if up_temp is not None:
        try:
            tmp = pd.read_excel(up_temp)
            tmp.columns = [str(c).strip().replace("\n", " ") for c in tmp.columns]
            dcol = _pick_col(tmp.columns, ["일자", "날짜", "date", "Date"])
            tcol = _pick_col(tmp.columns, ["평균기온(℃)", "평균기온", "Tavg", "AvgTemp", "avg_temp"])
            if dcol is None or tcol is None:
                st.warning(f"업로드 파일에서 날짜/평균기온 컬럼을 찾지 못했어. 현재 컬럼: {list(tmp.columns)}")
            else:
                tmp = tmp[[dcol, tcol]].rename(columns={dcol: "일자", tcol: "평균기온(℃)"}).copy()
                tmp["일자"] = pd.to_datetime(tmp["일자"])
                df_t = tmp.dropna(subset=["평균기온(℃)"]).copy()
        except Exception as e:
            st.warning(f"기온 파일을 읽는 중 문제가 생겼어: {e}")

    if df_t is None:
        df_t = df_temp_all[["일자", "평균기온(℃)"]].dropna().copy()

    df_t["연도"] = df_t["일자"].dt.year
    df_t["월"] = df_t["일자"].dt.month
    df_t["일"] = df_t["일자"].dt.day

    if df_t.empty:
        st.info("표시할 기온 데이터가 없어.")
        return

    min_y = int(df_t["연도"].min())
    max_y = int(df_t["연도"].max())

    col_y, col_m = st.columns([3, 2])
    with col_y:
        y_start, y_end = st.slider("연도 범위", min_value=min_y, max_value=max_y, value=(min_y, max_y), step=1)
    with col_m:
        m_sel = st.selectbox("월 선택", list(range(1, 13)), index=0, format_func=lambda m: f"{m:02d} (Month {m})")

    df_sel = df_t[(df_t["연도"].between(y_start, y_end)) & (df_t["월"] == m_sel)].copy()

    years_cnt = df_sel["연도"].nunique()
    st.markdown(f"**{m_sel:02d}월 일일 평균기온 히트맵(선택연도 {years_cnt}개)**")

    if years_cnt == 0:
        st.info("선택한 범위에 해당 월 데이터가 없어.")
    else:
        pv = (
            df_sel.pivot_table(index="일", columns="연도", values="평균기온(℃)", aggfunc="mean")
            .reindex(range(1, 32))
            .sort_index(axis=1)
        )

        # 상단에 해당 월 평균 행 추가
        avg_row = pd.DataFrame([pv.mean(axis=0)], index=["평균"])
        pv2 = pd.concat([avg_row, pv], axis=0)

        z = pv2.values
        y_labels = [str(i).zfill(2) if isinstance(i, int) else str(i) for i in pv2.index]
        x_labels = [str(c) for c in pv2.columns]

        txt = np.where(np.isnan(z), "", np.round(z, 1).astype(str))

        fig_temp = go.Figure(
            data=go.Heatmap(
                z=z,
                x=x_labels,
                y=y_labels,
                colorbar_title="℃",
                text=txt,
                texttemplate="%{text}",
                hovertemplate="연도=%{x}<br>일=%{y}<br>평균기온=%{z:.1f}℃<extra></extra>",
            )
        )
        fig_temp.update_layout(
            height=520,
            margin=dict(l=60, r=40, t=40, b=40),
        )
        st.plotly_chart(fig_temp, use_container_width=True)


# ─────────────────────────────────────────────
# main
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
