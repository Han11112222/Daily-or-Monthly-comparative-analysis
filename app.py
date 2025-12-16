import calendar
from io import BytesIO
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from openpyxl.styles import Alignment, Font
from openpyxl.utils import get_column_letter


# ────────────────────────────────────────────
# 기본 설정
# ────────────────────────────────────────────
st.set_page_config(
    page_title="도시가스 공급량 — 일별계획/월별검증",
    layout="wide",
)

DATA_FILE = Path(__file__).parent / "공급량(일일실적).xlsx"
MONTH_PLAN_FILE = Path(__file__).parent / "공급량(계획_실적).xlsx"
CORR_FILE = Path(__file__).parent / "상관도분석.xlsx"


# ────────────────────────────────────────────
# 공통 유틸
# ────────────────────────────────────────────
def _to_num(s):
    if isinstance(s, str):
        s = s.replace(",", "")
    return pd.to_numeric(s, errors="coerce")


def _format_excel_sheet(ws, freeze="A2", center=True):
    if freeze:
        ws.freeze_panes = freeze

    # 가로폭 자동(대충)
    for col in range(1, ws.max_column + 1):
        letter = get_column_letter(col)
        ws.column_dimensions[letter].width = max(10, min(26, ws.column_dimensions[letter].width or 12))

    if center:
        for r in range(1, ws.max_row + 1):
            for c in range(1, ws.max_column + 1):
                ws.cell(r, c).alignment = Alignment(horizontal="center", vertical="center")


def _find_plan_col(df_plan: pd.DataFrame):
    # 계획량 컬럼 찾기(유연)
    cand = [c for c in df_plan.columns if "계획" in str(c)]
    if cand:
        # 가장 긴 이름 우선(월별계획/사업계획 등)
        cand = sorted(cand, key=lambda x: len(str(x)), reverse=True)
        return cand[0]
    # fallback
    return df_plan.columns[-1]


@st.cache_data(show_spinner=False)
def load_monthly_plan():
    if not MONTH_PLAN_FILE.exists():
        return None
    try:
        xls = pd.ExcelFile(MONTH_PLAN_FILE)
        # 보통 월별 계획이 들어있는 시트 후보
        for s in xls.sheet_names:
            if "계획" in s or "월" in s:
                df = pd.read_excel(xls, sheet_name=s)
                return df
        return pd.read_excel(MONTH_PLAN_FILE, sheet_name=xls.sheet_names[0])
    except Exception:
        return None


@st.cache_data(show_spinner=False)
def load_corr_data():
    if not CORR_FILE.exists():
        return None
    try:
        return pd.read_excel(CORR_FILE)
    except Exception:
        return None


# ────────────────────────────────────────────
# 데이터 불러오기
# ────────────────────────────────────────────
@st.cache_data(show_spinner=False)
def load_daily_data():
    """
    반환:
      df_model     : 공급량(MJ)와 평균기온 둘 다 있는 구간 (예측/R² 계산용)
      df_temp_all  : 평균기온만 있어도 되는 전체 구간 (1980년 포함, 매트릭스/시나리오용)
    """
    if not DATA_FILE.exists():
        st.error(f"데이터 파일이 없어: {DATA_FILE}")
        st.stop()

    df_raw = pd.read_excel(DATA_FILE)

    # 필요한 컬럼만 사용
    needed = ["일자", "공급량(MJ)", "공급량(M3)", "평균기온(℃)"]
    for c in needed:
        if c not in df_raw.columns:
            st.error(f"필수 컬럼 누락: {c} (엑셀 컬럼 확인해줘)")
            st.stop()

    df_raw = df_raw[needed].copy()
    df_raw["일자"] = pd.to_datetime(df_raw["일자"], errors="coerce")
    df_raw["공급량(MJ)"] = df_raw["공급량(MJ)"].apply(_to_num)
    df_raw["공급량(M3)"] = df_raw["공급량(M3)"].apply(_to_num)
    df_raw["평균기온(℃)"] = df_raw["평균기온(℃)"].apply(_to_num)

    df_raw = df_raw.dropna(subset=["일자"]).sort_values("일자").reset_index(drop=True)

    df_raw["연도"] = df_raw["일자"].dt.year
    df_raw["월"] = df_raw["일자"].dt.month
    df_raw["일"] = df_raw["일자"].dt.day

    # df_temp_all: 평균기온만 있어도 유지
    df_temp_all = df_raw.copy()

    # df_model: 공급량(MJ) 있는 구간
    df_model = df_temp_all.dropna(subset=["공급량(MJ)"]).copy()

    return df_model, df_temp_all


# ─────────────────────────────────────────────
# 월별 요약/회귀 유틸
# ─────────────────────────────────────────────
def monthly_agg(df_model: pd.DataFrame):
    g = (
        df_model.groupby(["연도", "월"], as_index=False)
        .agg(
            평균기온=("평균기온(℃)", "mean"),
            공급량_MJ=("공급량(MJ)", "sum"),
        )
        .sort_values(["연도", "월"])
        .reset_index(drop=True)
    )
    return g


def fit_poly3_and_r2(x, y):
    x = pd.Series(x).astype(float)
    y = pd.Series(y).astype(float)
    m = (~x.isna()) & (~y.isna())
    x = x[m]
    y = y[m]
    if len(x) < 10:
        return None, None, None
    coef = np.polyfit(x, y, deg=3)
    p = np.poly1d(coef)
    y_pred = p(x)
    ss_res = np.sum((y - y_pred) ** 2)
    ss_tot = np.sum((y - np.mean(y)) ** 2)
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else None
    return coef, p, r2


def plot_poly_fit(x, y, coef, title, x_label, y_label):
    x = pd.Series(x).astype(float)
    y = pd.Series(y).astype(float)
    m = (~x.isna()) & (~y.isna())
    x = x[m]
    y = y[m]
    coef = np.array(coef)
    p = np.poly1d(coef)

    x_line = np.linspace(float(x.min()), float(x.max()), 200)
    y_line = p(x_line)

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=x, y=y, mode="markers", name="실적", opacity=0.65))
    fig.add_trace(go.Scatter(x=x_line, y=y_line, mode="lines", name="3차 다항식", line=dict(width=3)))

    fig.update_layout(
        template="simple_white",
        title=title,
        xaxis_title=x_label,
        yaxis_title=y_label,
        margin=dict(l=40, r=20, t=50, b=40),
    )
    return fig


# ─────────────────────────────────────────────
# 일별 계획 만들기(연간용)
# ─────────────────────────────────────────────
def _build_year_daily_plan(df_daily: pd.DataFrame, df_plan: pd.DataFrame, target_year: int, recent_window: int = 3):
    """
    - 월별 계획(df_plan)에서 target_year의 월별 계획량을 가져오고
    - df_daily(과거 일별 실적)에서 최근 N년 동일 월/요일/주차패턴 기반으로 비율을 만들어
    - 연간 일별 계획을 생성
    """
    plan_col = _find_plan_col(df_plan)
    df_plan_y = df_plan[df_plan["연"] == target_year].copy()
    df_plan_y["월"] = df_plan_y["월"].astype(int)
    df_plan_y[plan_col] = df_plan_y[plan_col].apply(_to_num)

    # 과거 기준 연도 선택
    all_years = sorted(df_daily["연도"].unique())
    hist_years = [y for y in all_years if y < target_year]
    hist_years = hist_years[-recent_window:] if len(hist_years) >= recent_window else hist_years

    def _weekday_idx(d):
        # 0=월 ... 6=일
        return int(pd.Timestamp(d).dayofweek)

    def _nth_dow_in_month(ts: pd.Timestamp):
        # 같은 월 안에서 몇 번째 해당 요일인지(1부터)
        first = ts.replace(day=1)
        dow = ts.dayofweek
        cnt = 0
        cur = first
        while cur <= ts:
            if cur.dayofweek == dow:
                cnt += 1
            cur += pd.Timedelta(days=1)
        return cnt

    all_rows = []
    month_summary_rows = []

    for m in range(1, 13):
        plan_total = df_plan_y.loc[df_plan_y["월"] == m, plan_col].sum()
        if pd.isna(plan_total):
            plan_total = np.nan

        # target year의 해당 월 날짜 생성
        last_day = calendar.monthrange(target_year, m)[1]
        dates = pd.date_range(f"{target_year}-{m:02d}-01", f"{target_year}-{m:02d}-{last_day:02d}", freq="D")

        tmp = pd.DataFrame({"일자": dates})
        tmp["연"] = target_year
        tmp["월"] = m
        tmp["일"] = tmp["일자"].dt.day
        tmp["요일"] = tmp["일자"].dt.day_name()
        tmp["weekday_idx"] = tmp["일자"].apply(_weekday_idx)
        tmp["nth_dow"] = tmp["일자"].apply(_nth_dow_in_month)

        # 과거 동일 (월, weekday_idx, nth_dow) 평균비율
        hist = df_daily[df_daily["연도"].isin(hist_years) & (df_daily["월"] == m)].copy()
        if hist.empty:
            tmp["최근N년_총공급량(MJ)"] = np.nan
            tmp["최근N년_평균공급량(MJ)"] = np.nan
            tmp["일별비율"] = np.nan
        else:
            hist["weekday_idx"] = hist["일자"].dt.dayofweek
            hist["nth_dow"] = hist["일자"].apply(_nth_dow_in_month)
            hist_g = (
                hist.groupby(["weekday_idx", "nth_dow"], as_index=False)
                .agg(
                    최근N년_총공급량_MJ=("공급량(MJ)", "sum"),
                    최근N년_평균공급량_MJ=("공급량(MJ)", "mean"),
                )
            )
            tmp = tmp.merge(hist_g, on=["weekday_idx", "nth_dow"], how="left")
            tmp["최근N년_총공급량(MJ)"] = tmp["최근N년_총공급량_MJ"]
            tmp["최근N년_평균공급량(MJ)"] = tmp["최근N년_평균공급량_MJ"]
            tmp = tmp.drop(columns=["최근N년_총공급량_MJ", "최근N년_평균공급량_MJ"])

            # 월 내 비율(총합 기준)
            s = tmp["최근N년_총공급량(MJ)"].sum(skipna=True)
            if s and s > 0:
                tmp["일별비율"] = tmp["최근N년_총공급량(MJ)"] / s
            else:
                tmp["일별비율"] = np.nan

        # 예상공급량(MJ)
        tmp["예상공급량(MJ)"] = (tmp["일별비율"] * plan_total).round(0) if pd.notna(plan_total) else np.nan

        df_res = tmp[
            [
                "연",
                "월",
                "일",
                "일자",
                "요일",
                "weekday_idx",
                "nth_dow",
                "최근N년_평균공급량(MJ)",
                "최근N년_총공급량(MJ)",
                "일별비율",
                "예상공급량(MJ)",
            ]
        ].copy()

        all_rows.append(df_res)
        month_summary_rows.append({"월": m, "월간 계획(MJ)": plan_total})

    df_year = pd.concat(all_rows, ignore_index=True)
    df_year = df_year.sort_values(["월", "일"]).reset_index(drop=True)

    total_row = {
        "연": "",
        "월": "",
        "일": "",
        "일자": "",
        "요일": "합계",
        "weekday_idx": "",
        "nth_dow": "",
        "최근N년_평균공급량(MJ)": df_year["최근N년_평균공급량(MJ)"].sum(skipna=True),
        "최근N년_총공급량(MJ)": df_year["최근N년_총공급량(MJ)"].sum(skipna=True),
        "일별비율": df_year["일별비율"].sum(skipna=True),
        "예상공급량(MJ)": df_year["예상공급량(MJ)"].sum(skipna=True),
    }
    df_year_with_total = pd.concat([df_year, pd.DataFrame([total_row])], ignore_index=True)

    df_month_sum = pd.DataFrame(month_summary_rows).sort_values("월").reset_index(drop=True)
    df_month_sum_total = pd.DataFrame(
        [{"월": "연간합계", "월간 계획(MJ)": df_month_sum["월간 계획(MJ)"].sum(skipna=True)}]
    )
    df_month_sum = pd.concat([df_month_sum, df_month_sum_total], ignore_index=True)

    return df_year_with_total, df_month_sum


# ─────────────────────────────────────────────
# 탭1: Daily 공급량 분석
# ─────────────────────────────────────────────
def tab_daily_plan(df_daily: pd.DataFrame):
    st.subheader("📅 Daily 공급량 분석 — 최근 N년 패턴 기반 일별 계획")

    df_plan = load_monthly_plan()
    if df_plan is None:
        st.warning("월별 계획 파일(공급량(계획_실적).xlsx)을 찾지 못했어.")
        return

    plan_col = _find_plan_col(df_plan)
    if "연" not in df_plan.columns or "월" not in df_plan.columns or plan_col not in df_plan.columns:
        st.warning("월별 계획 파일 컬럼 구성이 예상과 달라. (연/월/계획량 필요)")
        return

    years_plan = sorted(df_plan["연"].unique())
    default_year_idx = years_plan.index(2026) if 2026 in years_plan else len(years_plan) - 1

    col_y, _, _ = st.columns([1, 1, 2])
    with col_y:
        target_year = st.selectbox("계획 연도 선택", years_plan, index=default_year_idx)

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
            key="recent_years_window",
        )

    st.caption(f"학습(참조) 연도: {hist_years[-recent_window:]}")

    # 다운로드(연간)
    annual_year = st.selectbox(
        "연간 계획 다운로드 대상 연도",
        years_plan,
        index=years_plan.index(target_year) if target_year in years_plan else 0,
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
        df_year_daily.to_excel(writer, index=False, sheet_name="연간")
        df_month_summary.to_excel(writer, index=False, sheet_name="월 요약 계획")

        wb = writer.book
        ws_y = wb["연간"]
        ws_m = wb["월 요약 계획"]

        _format_excel_sheet(ws_y, freeze="A2", center=True)
        _format_excel_sheet(ws_m, freeze="A2", center=True)

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
    min_year_model = int(df["연도"].min())
    max_year_model = int(df["연도"].max())

    st.subheader("📊 0. 상관도 분석 (공급량 vs 주요 변수)")

    df_corr_raw = load_corr_data()
    if df_corr_raw is None:
        st.caption("상관도분석.xlsx 파일이 없어서 상관도 매트릭스를 표시하지 못했어.")
    else:
        num_df = df_corr_raw.select_dtypes(include=["number"]).copy()
        num_cols = list(num_df.columns)

        if len(num_cols) >= 2:
            corr = num_df.corr()
            z = np.clip(corr.values, -0.7, 0.7)
            text = corr.round(2).astype(str).values

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
                xaxis=dict(side="top", tickangle=0),
                template="simple_white",
                margin=dict(l=40, r=20, t=60, b=40),
                height=520,
            )
            st.plotly_chart(fig_corr, use_container_width=True, config={"displaylogo": False})
        else:
            st.caption("상관도 분석에 사용할 숫자형 컬럼이 부족해.")


    st.subheader("📌 1. 월평균 기온 기반 월별 공급량 회귀(3차 다항식)")

    df_month = monthly_agg(df_model=df)

    coef_m, y_pred_m, r2_m = fit_poly3_and_r2(df_month["평균기온"], df_month["공급량_MJ"])
    df_month["예측공급량_MJ"] = y_pred_m if y_pred_m is not None else np.nan

    colA, colB = st.columns([1, 2])
    with colA:
        if r2_m is not None:
            st.metric("R² (월평균 기온 → 월별 공급량)", f"{r2_m:.3f}")
            st.caption(f"기간: {min_year_model}~{max_year_model} / 월 수: {len(df_month)}")
        else:
            st.write("월 단위 회귀에 필요한 데이터가 부족해.")

    with colB:
        if coef_m is not None:
            fig_m1 = plot_poly_fit(
                df_month["평균기온"],
                df_month["공급량_MJ"],
                coef_m,
                title="월단위: 월평균 기온 vs 월별 공급량(MJ)",
                x_label="월평균 기온 (℃)",
                y_label="월별 공급량 합계 (MJ)",
            )
            st.plotly_chart(fig_m1, use_container_width=True, config={"displaylogo": False})

    st.subheader("📌 2. 일평균 기온 기반 일별 공급량 회귀(3차 다항식)")

    # 학습기간 선택
    c1, c2, c3 = st.columns([1, 1, 2])
    with c1:
        win_start = st.number_input("학습 시작연도", min_value=min_year_model, max_value=max_year_model, value=min_year_model)
    with c2:
        win_end = st.number_input("학습 종료연도", min_value=min_year_model, max_value=max_year_model, value=max_year_model)
    with c3:
        st.caption("선택 기간의 '일평균 기온 vs 일별 공급량'으로 3차 다항 회귀(R² 비교용)")

    df_window = df[(df["연도"] >= int(win_start)) & (df["연도"] <= int(win_end))].dropna(subset=["공급량(MJ)", "평균기온(℃)"]).copy()

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
            fig_m = plot_poly_fit(
                df_month["평균기온"], df_month["공급량_MJ"], coef_m,
                title="월단위: 월평균 기온 vs 월별 공급량(MJ)",
                x_label="월평균 기온 (℃)", y_label="월별 공급량 합계 (MJ)"
            )
            st.plotly_chart(fig_m, use_container_width=True)

    with col4:
        if coef_d is not None:
            fig_d = plot_poly_fit(
                df_window["평균기온(℃)"], df_window["공급량(MJ)"], coef_d,
                title="일단위: 일평균 기온 vs 일별 공급량(MJ)",
                x_label="일평균 기온 (℃)", y_label="일별 공급량 (MJ)"
            )
            st.plotly_chart(fig_d, use_container_width=True)

    # ─────────────────────────────────────────────
    # 🧊 G. 기온분석 — 일일 평균기온 히트맵 (일자 × 연도 매트릭스)
    #   - 기존 Daily-Monthly 비교 탭 맨 하단에 복원
    # ─────────────────────────────────────────────
    st.subheader("🧊 G. 기온분석 — 일일 평균기온 히트맵")

    st.caption("선택 월의 일별 평균기온을 '일자 × 연도' 매트릭스로 표시 (하단 '평균' 행 포함).")

    uploaded_temp = st.file_uploader(
        "일일기온파일 업로드(XLSX)",
        type=["xlsx"],
        key="temp_heatmap_uploader",
        help="업로드하지 않으면, 현재 공급량 파일에 포함된 '평균기온(℃)'로 자동 생성",
    )

    def _guess_col(cols, keys, default=None):
        for k in keys:
            for c in cols:
                if k in str(c):
                    return c
        return default

    # 1) 데이터 소스 결정: 업로드 파일 우선, 없으면 df_temp_all(공급량 파일의 평균기온) 사용
    if uploaded_temp is not None:
        tmp_raw = pd.read_excel(uploaded_temp)
        tmp_cols = tmp_raw.columns.tolist()

        date_c = _guess_col(tmp_cols, ["일자", "날짜", "date", "Date"], tmp_cols[0] if tmp_cols else None)
        tmean_c = _guess_col(
            tmp_cols,
            ["평균기온", "기온", "tmean", "temp", "avg"],
            tmp_cols[1] if len(tmp_cols) > 1 else (tmp_cols[0] if tmp_cols else None),
        )

        dt = tmp_raw[[date_c, tmean_c]].copy()
        dt.columns = ["date", "tmean"]
    else:
        # df_temp_all 은 load_daily_data()에서 만든 평균기온 포함 데이터
        # 컬럼명이 다를 수 있으니 안전하게 처리
        if ("일자" in df_temp_all.columns) and ("평균기온(℃)" in df_temp_all.columns):
            dt = df_temp_all[["일자", "평균기온(℃)"]].copy()
            dt.columns = ["date", "tmean"]
        else:
            # 최후 fallback
            col_date = "일자" if "일자" in df_temp_all.columns else None
            col_temp = "평균기온(℃)" if "평균기온(℃)" in df_temp_all.columns else None
            if col_date is None or col_temp is None:
                st.info("히트맵을 만들 평균기온 데이터 컬럼을 찾지 못했어. (일자/평균기온(℃) 필요)")
                return
            dt = df_temp_all[[col_date, col_temp]].copy()
            dt.columns = ["date", "tmean"]

    # 2) 전처리
    dt["date"] = pd.to_datetime(dt["date"], errors="coerce")
    dt["tmean"] = pd.to_numeric(dt["tmean"], errors="coerce")
    dt = dt.dropna(subset=["date", "tmean"]).sort_values("date").reset_index(drop=True)

    dt["year"] = dt["date"].dt.year
    dt["month"] = dt["date"].dt.month
    dt["day"] = dt["date"].dt.day

    years_all = sorted(dt["year"].unique().tolist())
    if len(years_all) == 0:
        st.info("히트맵을 만들 데이터가 없어.")
        return

    y_min, y_max = int(min(years_all)), int(max(years_all))
    months_all = list(range(1, 13))
    month_names = {
        1: "January", 2: "February", 3: "March", 4: "April",
        5: "May", 6: "June", 7: "July", 8: "August",
        9: "September", 10: "October", 11: "November", 12: "December"
    }

    c1, c2 = st.columns([2, 1])
    with c1:
        year_range = st.slider(
            "연도 범위",
            min_value=y_min,
            max_value=y_max,
            value=(y_min, y_max),
            step=1,
            key="temp_heatmap_year_range",
        )
    with c2:
        sel_month = st.selectbox(
            "월 선택",
            options=months_all,
            index=0,  # 기본 01월
            format_func=lambda m: f"{m:02d} ({month_names[m]})",
            key="temp_heatmap_month",
        )

    sel_years = [y for y in years_all if year_range[0] <= y <= year_range[1]]
    if len(sel_years) == 0:
        st.info("선택한 연도 범위에 해당하는 데이터가 없어.")
        return

    dsel = dt[(dt["year"].isin(sel_years)) & (dt["month"] == int(sel_month))].copy()
    if dsel.empty:
        st.info("선택한 연도/월에 데이터가 없어.")
        return

    # 3) 월의 최대 일수(2월은 윤년 포함 가능)
    try:
        last_day = max(calendar.monthrange(int(y), int(sel_month))[1] for y in sel_years)
    except Exception:
        last_day = int(dsel["day"].max())

    pivot = (
        dsel.pivot_table(index="day", columns="year", values="tmean", aggfunc="mean")
        .reindex(range(1, last_day + 1))
        .sort_index(axis=1)
    )

    avg_row = pivot.mean(axis=0, skipna=True)
    pivot_with_avg = pd.concat([pivot, pd.DataFrame([avg_row], index=["평균"])])

    y_labels = [f"{int(sel_month):02d}-{int(d):02d}" for d in pivot.index] + ["평균"]

    Z = pivot_with_avg.values.astype(float)
    X = pivot_with_avg.columns.tolist()
    Y = y_labels

    zmid = float(np.nanmean(pivot.values)) if np.isfinite(np.nanmean(pivot.values)) else 0.0

    text = np.full_like(Z, "", dtype=object)
    if Z.shape[0] > 0:
        last_idx = Z.shape[0] - 1
        text[last_idx, :] = [f"{v:.1f}" if np.isfinite(v) else "" for v in Z[last_idx, :]]

    # 4) 화면 크기 자동 산정(연도 개수 따라 높이 보정)
    base_cell_px = 34
    approx_width_px = max(650, len(X) * base_cell_px)
    height = max(420, int(approx_width_px * 2 / 3 * 1.15))

    fig_heat = go.Figure(
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
    fig_heat.update_layout(
        template="simple_white",
        margin=dict(l=40, r=20, t=50, b=40),
        xaxis=dict(title="Year", tickmode="linear", dtick=1, showgrid=False),
        yaxis=dict(title="Day", autorange="reversed", showgrid=False, type="category"),
        title=f"{int(sel_month):02d}월 일일 평균기온 히트맵 (선택연도 {len(X)}개)",
        height=height,
    )
    st.plotly_chart(fig_heat, use_container_width=True, config={"displaylogo": False})


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
