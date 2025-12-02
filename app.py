# app.py — Daily vs Monthly Polynomial (3차) R² & 월 비교 (슬라이더 버전)

import numpy as np
import pandas as pd
from pathlib import Path

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
def load_daily_data() -> pd.DataFrame:
    excel_path = Path(__file__).parent / "공급량(일일실적).xlsx"

    df = pd.read_excel(excel_path)

    # 필요한 컬럼만 사용
    df = df[["일자", "공급량(MJ)", "공급량(M3)", "평균기온(℃)"]].copy()

    # 날짜 형식
    df["일자"] = pd.to_datetime(df["일자"])

    # 결측 제거
    df = df.dropna(subset=["공급량(MJ)", "평균기온(℃)"])

    # 연도/월 파생
    df["연도"] = df["일자"].dt.year
    df["월"] = df["일자"].dt.month

    return df


# ─────────────────────────────────────────────
# 3차 다항식 회귀 + R²
# ─────────────────────────────────────────────
def fit_poly3_and_r2(x: pd.Series, y: pd.Series):
    x = np.asarray(x, dtype="float64")
    y = np.asarray(y, dtype="float64")

    # 최소 4개 포인트 필요
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


# ─────────────────────────────────────────────
# 산점도 + 곡선 플롯
# ─────────────────────────────────────────────
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


# ─────────────────────────────────────────────
# 표 숫자 포맷팅 (천단위 콤마)
# ─────────────────────────────────────────────
def format_table_month_summary(df):
    df = df.copy()
    if "월평균 기온(℃)" in df.columns:
        df["월평균 기온(℃)"] = df["월평균 기온(℃)"].map(lambda x: f"{x:.2f}")
    for col in ["실제 공급량(MJ)", "예측 공급량(MJ)", "오차(MJ)"]:
        if col in df.columns:
            df[col] = df[col].map(lambda x: f"{x:,.0f}")
    if "오차율(%)" in df.columns:
        df["오차율(%)"] = df["오차율(%)"].map(lambda x: f"{x:.2f}")
    return df


def format_table_daily(df):
    df = df.copy()
    if "일자" in df.columns and np.issubdtype(df["일자"].dtype, np.datetime64):
        df["일자"] = df["일자"].dt.strftime("%Y-%m-%d")
    if "평균기온(℃)" in df.columns:
        df["평균기온(℃)"] = df["평균기온(℃)"].map(lambda x: f"{x:.1f}")
    for col in ["공급량(MJ)", "예측공급량_MJ", "오차_MJ"]:
        if col in df.columns:
            df[col] = df[col].map(lambda x: f"{x:,.0f}")
    if "오차율_%" in df.columns:
        df["오차율_%"] = df["오차율_%"].map(lambda x: f"{x:.2f}")
        df = df.rename(columns={"오차율_%": "오차율(%)"})
    return df


def format_table_generic(df, percent_cols=None, temp_cols=None):
    df = df.copy()
    if percent_cols is None:
        percent_cols = []
    if temp_cols is None:
        temp_cols = []

    for col in df.columns:
        if col in percent_cols:
            df[col] = df[col].map(lambda x: f"{x:.2f}")
        elif col in temp_cols:
            df[col] = df[col].map(lambda x: f"{x:.2f}")
        elif pd.api.types.is_numeric_dtype(df[col]):
            df[col] = df[col].map(lambda x: f"{x:,.0f}")
    return df


# ─────────────────────────────────────────────
# 메인
# ─────────────────────────────────────────────
def main():
    st.title("도시가스 공급량 — 일별 vs 월별 기온기반 3차 다항식 예측력 비교")

    df = load_daily_data()

    min_year = int(df["연도"].min())
    max_year = int(df["연도"].max())

    # ── 1. 데이터 학습기간 bar (연도 범위 슬라이더) ──────
    st.subheader("① 데이터 학습기간 선택 (3차 다항식 R² 계산용)")

    default_start = max(min_year, max_year - 4)
    train_start, train_end = st.slider(
        "학습에 사용할 연도 범위",
        min_value=min_year,
        max_value=max_year,
        value=(default_start, max_year),
        step=1,
    )

    st.caption(f"현재 학습 구간: **{train_start}년 ~ {train_end}년**")

    # 학습용 데이터 (train_start ~ train_end)
    df_window = df[df["연도"].between(train_start, train_end)].copy()

    # 월별 집계 (학습용)
    df_month = (
        df_window
        .groupby(["연도", "월"], as_index=False)
        .agg(
            공급량_MJ=("공급량(MJ)", "sum"),
            평균기온=("평균기온(℃)", "mean"),
        )
    )

    # 월단위 모델
    coef_m, y_pred_m, r2_m = fit_poly3_and_r2(
        df_month["평균기온"],
        df_month["공급량_MJ"],
    )
    if y_pred_m is not None:
        df_month["예측공급량_MJ"] = y_pred_m
    else:
        df_month["예측공급량_MJ"] = np.nan

    # 일단위 모델
    coef_d, y_pred_d, r2_d = fit_poly3_and_r2(
        df_window["평균기온(℃)"],
        df_window["공급량(MJ)"],
    )
    if y_pred_d is not None:
        df_window["예측공급량_MJ"] = y_pred_d
    else:
        df_window["예측공급량_MJ"] = np.nan

    # ── R² 비교 ────────────────────────────────
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

    # ── 산점도 + 곡선 ───────────────────────────
    st.subheader("기온–공급량 관계 (실적 vs 3차 다항식 곡선)")

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

    # ── 선택 연·월 상세 비교 (학습기간 내부) ──────────
    st.subheader("선택 연·월 기준 예측 vs 실적 상세 비교 (학습기간 내)")

    year_list = sorted(df_window["연도"].unique())
    sel_year = st.selectbox("연도 선택", year_list, index=len(year_list) - 1)

    month_list = sorted(df_window.loc[df_window["연도"] == sel_year, "월"].unique())
    sel_month = st.selectbox("월 선택", month_list)

    st.markdown(f"**선택 월: {sel_year}년 {sel_month}월**")

    # 월 단위 한 줄 요약
    month_row = df_month[
        (df_month["연도"] == sel_year) & (df_month["월"] == sel_month)
    ]

    if not month_row.empty:
        r = month_row.iloc[0].copy()
        r["오차_MJ"] = r["공급량_MJ"] - r["예측공급량_MJ"]
        r["오차율_%"] = r["오차_MJ"] / r["공급량_MJ"] * 100

        st.markdown("##### 월 단위 합계 비교")
        summary_df = pd.DataFrame(
            {
                "연도": [r["연도"]],
                "월": [r["월"]],
                "월평균 기온(℃)": [round(r["평균기온"], 2)],
                "실제 공급량(MJ)": [round(r["공급량_MJ"], 0)],
                "예측 공급량(MJ)": [round(r["예측공급량_MJ"], 0)],
                "오차(MJ)": [round(r["오차_MJ"], 0)],
                "오차율(%)": [round(r["오차율_%"], 2)],
            }
        )
        summary_df = format_table_month_summary(summary_df)
        st.table(summary_df)

    # 일 단위 상세
    st.markdown("##### 일 단위 상세 비교 (선택 연·월, 학습기간 내)")

    df_month_days = df_window[
        (df_window["연도"] == sel_year) & (df_window["월"] == sel_month)
    ].copy()

    if not df_month_days.empty and "예측공급량_MJ" in df_month_days.columns:
        df_month_days["오차_MJ"] = (
            df_month_days["공급량(MJ)"] - df_month_days["예측공급량_MJ"]
        )
        df_month_days["오차율_%"] = (
            df_month_days["오차_MJ"] / df_month_days["공급량(MJ)"] * 100
        )

        show_cols = [
            "일자",
            "평균기온(℃)",
            "공급량(MJ)",
            "예측공급량_MJ",
            "오차_MJ",
            "오차율_%"
        ]

        view_daily = (
            df_month_days[show_cols]
            .sort_values("일자")
            .reset_index(drop=True)
        )
        view_daily = format_table_daily(view_daily)
        st.dataframe(view_daily)
    else:
        st.write("선택한 연·월에 대한 일별 예측 데이터가 없어.")

    # ── 2. 기온선택 bar (시나리오용) ─────────────────
    st.subheader("② 기온 시나리오 연도 범위 선택 (월평균 vs 일평균 예측 비교용)")

    scen_default_start = max(min_year, max_year - 4)
    scen_start, scen_end = st.slider(
        "기온 시나리오에 사용할 연도 범위",
        min_value=min_year,
        max_value=max_year,
        value=(scen_default_start, max_year),
        step=1,
    )
    st.caption(
        f"선택한 기온 시나리오 연도: **{scen_start}년 ~ {scen_end}년** "
        "(각 월별로 이 기간의 평균기온을 사용)"
    )

    df_scen = df[df["연도"].between(scen_start, scen_end)].copy()
    if df_scen.empty:
        st.write("선택한 기온 시나리오 구간에 데이터가 없어.")
        return

    # 월평균 기온 (시나리오: 여러 년도의 월 평균을 다시 평균)
    temp_month = (
        df_scen.groupby("월")["평균기온(℃)"]
        .mean()
        .sort_index()
    )

    # 월단위 모델로 예측한 월별 공급량 (시나리오 기온)
    monthly_pred_from_month_model = None
    if coef_m is not None:
        monthly_pred_vals = np.polyval(coef_m, temp_month.values)
        monthly_pred_from_month_model = pd.Series(
            monthly_pred_vals,
            index=temp_month.index,
            name=f"월단위 Poly-3 예측(MJ) - 기온 {scen_start}~{scen_end}년 평균",
        )

    # 일단위 모델로 예측한 일별 공급량 → 월별 합산 (시나리오 기온)
    monthly_pred_from_daily_model = None
    if coef_d is not None:
        df_scen = df_scen.copy()
        df_scen["예측일공급량_MJ_from_daily"] = np.polyval(
            coef_d,
            df_scen["평균기온(℃)"].to_numpy(),
        )
        monthly_pred_from_daily_model = (
            df_scen
            .groupby("월")["예측일공급량_MJ_from_daily"]
            .sum()
            .sort_index()
        )
        monthly_pred_from_daily_model.name = (
            f"일단위 Poly-3 예측합(MJ) - 기온 {scen_start}~{scen_end}년 평균"
        )

    # 예측/실적 연도 선택 (실제 월별 공급량 비교용)
    st.markdown("##### 예측/실적 연도 선택")

    year_options = sorted(df["연도"].unique())
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

    # ── 월별 예측 vs 실적 라인그래프 ───────────────────
    st.subheader("월별 예측 vs 실적 — 월단위 Poly-3 vs 일단위 Poly-3(합산)")

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
        fig_line.add_trace(
            go.Scatter(
                x=list(df_compare.index),
                y=df_compare[col],
                mode="lines+markers",
                name=col,
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
            ticktext=[f'{m}월' for m in month_index],
        ),
        margin=dict(l=20, r=20, t=40, b=20),
    )

    st.plotly_chart(fig_line, use_container_width=True)

    st.markdown("##### 월별 실적/예측 수치표")
    df_compare_view = df_compare.copy()
    df_compare_view.index = [f"{m}월" for m in df_compare_view.index]
    df_compare_view = format_table_generic(df_compare_view)
    st.dataframe(df_compare_view)


if __name__ == "__main__":
    main()
