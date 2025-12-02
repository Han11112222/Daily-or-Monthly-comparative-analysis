# app.py — Daily vs Monthly Polynomial (3차) R² 비교

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
    layout="wide"
)

# ─────────────────────────────────────────────
# 데이터 불러오기 (깃허브 레포의 엑셀 파일)
# ─────────────────────────────────────────────
@st.cache_data
def load_daily_data() -> pd.DataFrame:
    excel_path = Path(__file__).parent / "공급량(일일실적).xlsx"

    df = pd.read_excel(excel_path)

    # 필수 컬럼만 사용
    df = df[["일자", "공급량(MJ)", "공급량(M3)", "평균기온(℃)"]].copy()

    # 날짜 형식 변환
    df["일자"] = pd.to_datetime(df["일자"])

    # 공급량 또는 기온이 없는 날 제거
    df = df.dropna(subset=["공급량(MJ)", "평균기온(℃)"])

    # 연도, 월 파생
    df["연도"] = df["일자"].dt.year
    df["월"] = df["일자"].dt.month

    return df


# ─────────────────────────────────────────────
# 3차 다항식 회귀 + R² 계산 함수
# ─────────────────────────────────────────────
def fit_poly3_and_r2(x: pd.Series, y: pd.Series):
    """
    x : 독립변수 (기온)
    y : 종속변수 (공급량)
    return: (coef, y_pred, r2)
    """
    x = np.asarray(x, dtype="float64")
    y = np.asarray(y, dtype="float64")

    # 3차 다항식은 최소 4개 이상의 점 필요
    if len(x) < 4:
        return None, None, None

    coef = np.polyfit(x, y, 3)          # 계수 (a3, a2, a1, a0)
    y_pred = np.polyval(coef, x)        # 예측값

    ss_res = np.sum((y - y_pred) ** 2)  # 잔차 제곱합
    ss_tot = np.sum((y - np.mean(y)) ** 2)  # 전체 제곱합

    if ss_tot == 0:
        r2 = np.nan
    else:
        r2 = 1 - ss_res / ss_tot

    return coef, y_pred, r2


# ─────────────────────────────────────────────
# 플롯 함수 (산점도 + 3차 곡선)
# ─────────────────────────────────────────────
def plot_poly_fit(x, y, coef, title, x_label, y_label):
    x = np.asarray(x, dtype="float64")
    y = np.asarray(y, dtype="float64")

    # 곡선용 x-grid
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
# 메인 화면
# ─────────────────────────────────────────────
def main():
    st.title("도시가스 공급량 — 일별 vs 월별 기온기반 3차 다항식 예측력 비교")

    df = load_daily_data()

    min_year = int(df["연도"].min())
    max_year = int(df["연도"].max())
    max_window = min(10, max_year - min_year + 1)

    st.sidebar.header("분석 옵션")
    year_window = st.sidebar.slider(
        "최근 N년 사용 (1~10년)",
        min_value=1,
        max_value=max_window,
        value=min(5, max_window),
        step=1,
    )

    start_year = max_year - year_window + 1
    st.sidebar.write(f"사용 구간: **{start_year}년 ~ {max_year}년**")

    # 선택 기간 필터링
    df_window = df[df["연도"].between(start_year, max_year)].copy()

    # ── 월별 집계 데이터 생성 ──────────────────
    df_month = (
        df_window
        .groupby(["연도", "월"], as_index=False)
        .agg(
            공급량_MJ=("공급량(MJ)", "sum"),
            평균기온=("평균기온(℃)", "mean"),
        )
    )

    # 월별 회귀
    coef_m, y_pred_m, r2_m = fit_poly3_and_r2(
        df_month["평균기온"],
        df_month["공급량_MJ"],
    )
    df_month["예측공급량_MJ"] = y_pred_m

    # 일별 회귀
    coef_d, y_pred_d, r2_d = fit_poly3_and_r2(
        df_window["평균기온(℃)"],
        df_window["공급량(MJ)"],
    )
    df_window["예측공급량_MJ"] = y_pred_d

    # ── 상단: R² 비교 ────────────────────────
    st.subheader("최근 N년 기준 R² 비교 (3차 다항식)")

    col1, col2 = st.columns(2)
    with col1:
        st.markdown("#### 월 단위 모델 (월별 합계 공급량 vs 월평균 기온)")
        if r2_m is not None:
            st.metric("R² (월)", f"{r2_m:.3f}", help="1에 가까울수록 월 단위 예측력이 높음")
            st.caption(f"사용 월 수: {len(df_month)}")
        else:
            st.write("월 단위 회귀에 필요한 데이터가 부족해.")

    with col2:
        st.markdown("#### 일 단위 모델 (일별 공급량 vs 일평균 기온)")
        if r2_d is not None:
            st.metric("R² (일)", f"{r2_d:.3f}", help="1에 가까울수록 일 단위 예측력이 높음")
            st.caption(f"사용 일 수: {len(df_window)}")
        else:
            st.write("일 단위 회귀에 필요한 데이터가 부족해.")

    # ── 중단: 산점도 + 곡선 ───────────────────
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

    # ── 하단: 특정 연/월 예측 vs 실적 상세 ─────
    st.subheader("선택 연·월 기준 예측 vs 실적 상세 비교")

    # 선택 가능한 연/월
    year_list = sorted(df_window["연도"].unique())
    sel_year = st.selectbox("연도 선택", year_list, index=len(year_list) - 1)

    month_list = sorted(df_window.loc[df_window["연도"] == sel_year, "월"].unique())
    sel_month = st.selectbox("월 선택", month_list)

    st.markdown(f"**선택 월: {sel_year}년 {sel_month}월**")

    # (1) 월별 비교 한 줄 요약
    month_row = df_month[
        (df_month["연도"] == sel_year) & (df_month["월"] == sel_month)
    ]

    if not month_row.empty:
        r = month_row.iloc[0].copy()
        r["오차_MJ"] = r["공급량_MJ"] - r["예측공급량_MJ"]
        r["오차율_%"] = r["오차_MJ"] / r["공급량_MJ"] * 100

        st.markdown("##### 월 단위 합계 비교")
        st.table(
            pd.DataFrame(
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
        )

    # (2) 일별 상세 비교
    st.markdown("##### 일 단위 상세 비교 (선택 연·월)")

    df_month_days = df_window[
        (df_window["연도"] == sel_year) & (df_window["월"] == sel_month)
    ].copy()

    if not df_month_days.empty:
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

        st.dataframe(
            df_month_days[show_cols]
            .sort_values("일자")
            .reset_index(drop=True)
        )
    else:
        st.write("선택한 연·월에 해당하는 일별 데이터가 없어.")


if __name__ == "__main__":
    main()
