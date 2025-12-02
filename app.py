# app.py — 도시가스 공급량: 일별 vs 월별 기온기반 3차 다항식 예측력 비교

import pathlib
from typing import Tuple

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st


# ─────────────────────────────────────────────────────────
# 기본 설정
# ─────────────────────────────────────────────────────────
st.set_page_config(
    page_title="도시가스 공급량 – 일별 vs 월별 기온기반 3차 다항식 예측력 비교",
    layout="wide",
)

BASE_PATH = pathlib.Path(__file__).parent
DAILY_FILE = BASE_PATH / "공급량(일일실적).xlsx"
CORR_FILE = BASE_PATH / "상관도분석.xlsx"


# ─────────────────────────────────────────────────────────
# 공통 유틸
# ─────────────────────────────────────────────────────────
def thousands(x):
    if pd.isna(x):
        return ""
    if isinstance(x, (int, np.integer)):
        return f"{x:,}"
    if isinstance(x, (float, np.floating)):
        return f"{x:,.0f}"
    return x


def center_style(df: pd.DataFrame, fmt_map=None):
    """숫자 중앙정렬 + 포맷 적용용 스타일"""
    if fmt_map is None:
        fmt_map = {}

    style = (
        df.style.set_properties(**{"text-align": "center"})
        .set_table_styles(
            [dict(selector="th", props=[("text-align", "center")])]
        )
    )
    if fmt_map:
        style = style.format(fmt_map)
    return style


def r2_score(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    ss_res = np.sum((y_true - y_pred) ** 2)
    ss_tot = np.sum((y_true - np.mean(y_true)) ** 2)
    if ss_tot == 0:
        return np.nan
    return 1 - ss_res / ss_tot


# ─────────────────────────────────────────────────────────
# 데이터 로딩
# ─────────────────────────────────────────────────────────
@st.cache_data(ttl=600)
def load_daily() -> pd.DataFrame:
    df = pd.read_excel(DAILY_FILE)
    df["일자"] = pd.to_datetime(df["일자"])
    df["연도"] = df["일자"].dt.year
    df["월"] = df["일자"].dt.month
    df["일"] = df["일자"].dt.day
    return df


@st.cache_data(ttl=600)
def load_corr_df() -> pd.DataFrame | None:
    if not CORR_FILE.exists():
        return None
    return pd.read_excel(CORR_FILE)


# ─────────────────────────────────────────────────────────
# Poly-3 모델 학습 (월단위 / 일단위)  ★ 에러 방지용 안정화 버전
# ─────────────────────────────────────────────────────────
def _safe_poly3(x: np.ndarray, y: np.ndarray) -> np.poly1d:
    """
    3차 polyfit이 실패하면 1차로 fallback 하고,
    그래도 안 되면 평균값 고정 모델을 반환.
    """
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)

    # 유효 데이터 부족하면 평균값 고정
    if x.size < 4:
        mean_y = float(np.nanmean(y)) if y.size > 0 else 0.0
        return np.poly1d([0.0, 0.0, 0.0, mean_y])

    try:
        coef = np.polyfit(x, y, 3)
        return np.poly1d(coef)
    except np.linalg.LinAlgError:
        # 3차 실패 → 1차 시도
        try:
            a, b = np.polyfit(x, y, 1)
            # 1차를 3차 형태로 변환: 0*x^3 + 0*x^2 + a*x + b
            return np.poly1d([0.0, 0.0, a, b])
        except Exception:
            mean_y = float(np.nanmean(y)) if y.size > 0 else 0.0
            return np.poly1d([0.0, 0.0, 0.0, mean_y])


def fit_poly3_monthly(
    df: pd.DataFrame, year_start: int, year_end: int
) -> Tuple[np.poly1d, float]:
    mask = (df["연도"] >= year_start) & (df["연도"] <= year_end)
    cols = ["연도", "월", "평균기온(℃)", "공급량(MJ)"]
    d = df.loc[mask, cols].copy()

    # NaN/Inf 제거
    d.replace([np.inf, -np.inf], np.nan, inplace=True)
    d.dropna(subset=["평균기온(℃)", "공급량(MJ)"], inplace=True)

    if d.empty:
        mean_y = float(df["공급량(MJ)"].mean()) if "공급량(MJ)" in df else 0.0
        return np.poly1d([0.0, 0.0, 0.0, mean_y]), np.nan

    monthly = (
        d.groupby(["연도", "월"], as_index=False)
        .agg({"평균기온(℃)": "mean", "공급량(MJ)": "sum"})
        .dropna()
    )

    x = monthly["평균기온(℃)"].to_numpy()
    y = monthly["공급량(MJ)"].to_numpy()

    model = _safe_poly3(x, y)
    y_pred = model(x)
    r2 = r2_score(y, y_pred)
    return model, r2


def fit_poly3_daily(
    df: pd.DataFrame, year_start: int, year_end: int
) -> Tuple[np.poly1d, float]:
    mask = (df["연도"] >= year_start) & (df["연도"] <= year_end)
    cols = ["평균기온(℃)", "공급량(MJ)"]
    d = df.loc[mask, cols].copy()

    # NaN/Inf 제거
    d.replace([np.inf, -np.inf], np.nan, inplace=True)
    d.dropna(subset=["평균기온(℃)", "공급량(MJ)"], inplace=True)

    if d.empty:
        mean_y = float(df["공급량(MJ)"].mean()) if "공급량(MJ)" in df else 0.0
        return np.poly1d([0.0, 0.0, 0.0, mean_y]), np.nan

    x = d["평균기온(℃)"].to_numpy()
    y = d["공급량(MJ)"].to_numpy()

    model = _safe_poly3(x, y)
    y_pred = model(x)
    r2 = r2_score(y, y_pred)
    return model, r2


# ─────────────────────────────────────────────────────────
# 0. 상관도 분석 (공급량 vs 주요 변수)
# ─────────────────────────────────────────────────────────
def section_0_correlation():
    st.markdown("### 📊 0. 상관도 분석 (공급량 vs 주요 변수)")

    df_raw = load_corr_df()
    if df_raw is None:
        st.info("`상관도분석.xlsx` 파일이 없어서 상관도 분석을 생략합니다.")
        return

    candidate_cols = [
        "공급량(MJ)",
        "유효월수",
        "평균기온(℃)",
        "최저기온(℃)",
        "최고기온(℃)",
        "체감온도(℃)",
        "총인구수(명)",
        "세대수(세대)",
        "인구순이동(명)",
        "고령인구수(명)",
        "소비자물가지수(%)",
        "청구전",
    ]
    cols = [c for c in candidate_cols if c in df_raw.columns]
    df_corr = df_raw[cols].corr()

    col_heat, col_tbl = st.columns([0.7, 0.3], gap="small")

    # 히트맵 (정사각형)
    with col_heat:
        custom_scale = [
            "#4575b4",
            "#74add1",
            "#abd9e9",
            "#e0f3f8",
            "#f7f7f7",
            "#fee090",
            "#fdae61",
            "#f46d43",
            "#d73027",
        ]
        fig = px.imshow(
            df_corr.values,
            x=df_corr.columns,
            y=df_corr.index,
            color_continuous_scale=custom_scale,
            zmin=-1,
            zmax=1,
            origin="lower",
            text_auto=".2f",
            aspect="auto",
        )
        fig.update_layout(
            width=650,
            height=650,
            margin=dict(l=60, r=0, t=10, b=60),
            coloraxis_colorbar=dict(title="상관계수"),
        )
        fig.update_yaxes(scaleanchor="x", scaleratio=1)
        st.plotly_chart(fig, use_container_width=False)

    # 기준변수: 공급량(MJ) vs 다른 변수
    with col_tbl:
        target = "공급량(MJ)"
        if target not in df_corr.columns:
            st.info("공급량(MJ) 컬럼이 없어 상관계수 표는 생략합니다.")
            return

        s = df_corr[target].drop(target, errors="ignore")
        df_target = (
            s.to_frame(name="상관계수")
            .sort_values("상관계수", key=lambda x: x.abs(), ascending=False)
            .reset_index()
            .rename(columns={"index": "변수"})
        )
        df_target["상관계수"] = df_target["상관계수"].round(2)

        st.markdown(
            f"**기준 변수: <span style='color:#008000;'>{target}</span> 과(와) 다른 변수들의 상관계수**",
            unsafe_allow_html=True,
        )
        st.dataframe(
            center_style(df_target, fmt_map={"상관계수": "{:.2f}"}),
            use_container_width=True,
            height=430,
        )


# ─────────────────────────────────────────────────────────
# 1. 데이터 학습기간 선택(3차 다항식 R² 비교)
# ─────────────────────────────────────────────────────────
def section_1_train_r2(df: pd.DataFrame):
    st.markdown("### 📐 ① 데이터 학습기간 선택 (3차 다항식 R² 계산용)")

    year_min = int(df["연도"].min())
    year_max = int(df["연도"].max())

    default_start = max(year_min, year_max - 5)
    start_year, end_year = st.slider(
        "학습에 사용할 연도 범위",
        min_value=year_min,
        max_value=year_max,
        value=(default_start, year_max),
        step=1,
    )

    st.write(f"현재 학습 구간: **{start_year}년 ~ {end_year}년**")

    model_m, r2_m = fit_poly3_monthly(df, start_year, end_year)
    model_d, r2_d = fit_poly3_daily(df, start_year, end_year)

    col_m, col_d = st.columns(2)

    with col_m:
        st.markdown("**월 단위 모델 (월평균 기온 → 월별 공급량)**")
        st.metric("R² (월평균 기온 사용)", f"{r2_m:.3f}" if not np.isnan(r2_m) else "N/A")
    with col_d:
        st.markdown("**일 단위 모델 (일평균 기온 → 일별 공급량)**")
        st.metric("R² (일평균 기온 사용)", f"{r2_d:.3f}" if not np.isnan(r2_d) else "N/A")

    return (start_year, end_year, model_m, model_d, r2_m, r2_d)


# ─────────────────────────────────────────────────────────
# 2. 기온 시나리오 연도 범위 선택 (월예측 vs 일예측합)
# ─────────────────────────────────────────────────────────
def section_2_scenario(
    df: pd.DataFrame,
    train_range: Tuple[int, int],
    model_m: np.poly1d,
    model_d: np.poly1d,
    r2_m: float,
    r2_d: float,
):
    st.markdown("### 📈 ② 기온 시나리오 연도 범위 선택 (월평균 vs 일평균 예측 비교용)")

    year_min = int(df["연도"].min())
    year_max = int(df["연도"].max())

    scen_start, scen_end = st.slider(
        "기온 시나리오에 사용할 연도 범위",
        min_value=year_min,
        max_value=year_max - 1,
        value=(year_max - 4, year_max - 1),
        step=1,
    )

    st.write(
        f"선택한 기온 시나리오 연도: **{scen_start}년 ~ {scen_end}년** "
        "(각 월별로 이 기간의 평균기온 사용)"
    )

    pred_year = st.selectbox(
        "예측/실적 연도 선택 (실제 월별 공급량을 확인할 연도)",
        sorted(df["연도"].unique())[::-1],
    )

    df_scen = df[(df["연도"] >= scen_start) & (df["연도"] <= scen_end)].copy()

    scen_month_temp = (
        df_scen.groupby("월", as_index=False)["평균기온(℃)"].mean().rename(
            columns={"평균기온(℃)": "시나리오_월평균기온"}
        )
    )

    scen_daily_temp = (
        df_scen.groupby(["월", "일"], as_index=False)["평균기온(℃)"]
        .mean()
        .rename(columns={"평균기온(℃)": "시나리오_일평균기온"})
    )

    df_pred_year = df[df["연도"] == pred_year].copy()
    actual_month = (
        df_pred_year.groupby("월", as_index=False)["공급량(MJ)"].sum().rename(
            columns={"공급량(MJ)": "실적(MJ)"}
        )
    )

    scen_m = scen_month_temp.copy()
    scen_m["월단위_Poly3_예측(MJ)"] = model_m(scen_m["시나리오_월평균기온"])

    scen_d = scen_daily_temp.copy()
    scen_d["일별_예측(MJ)"] = model_d(scen_d["시나리오_일평균기온"])
    scen_d_month_sum = (
        scen_d.groupby("월", as_index=False)["일별_예측(MJ)"]
        .sum()
        .rename(columns={"일별_예측(MJ)": "일단위_Poly3_예측합(MJ)"})
    )

    monthly_all = (
        actual_month.merge(scen_m[["월", "월단위_Poly3_예측(MJ)"]], on="월", how="left")
        .merge(scen_d_month_sum, on="월", how="left")
        .sort_values("월")
    )

    total_actual = monthly_all["실적(MJ)"].sum()
    total_m = monthly_all["월단위_Poly3_예측(MJ)"].sum()
    total_d = monthly_all["일단위_Poly3_예측합(MJ)"].sum()

    fig = go.Figure()
    months = monthly_all["월"]

    fig.add_trace(
        go.Scatter(
            x=months,
            y=monthly_all["실적(MJ)"],
            mode="lines+markers",
            name=f"{pred_year}년 실적(MJ)",
            line=dict(color="red", width=3),
            marker=dict(size=7),
        )
    )
    fig.add_trace(
        go.Scatter(
            x=months,
            y=monthly_all["월단위_Poly3_예측(MJ)"],
            mode="lines+markers",
            name=f"월단위 Poly-3 예측(MJ) - 기온 {scen_start}~{scen_end}년 평균",
            line=dict(color="#4C78A8", dash="solid"),
            marker=dict(size=6),
        )
    )
    fig.add_trace(
        go.Scatter(
            x=months,
            y=monthly_all["일단위_Poly3_예측합(MJ)"],
            mode="lines+markers",
            name=f"일단위 Poly-3 예측합(MJ) - 기온 {scen_start}~{scen_end}년 평균",
            line=dict(color="#F58518", dash="dot"),
            marker=dict(size=6),
        )
    )

    fig.update_layout(
        title=(
            f"{pred_year}년 월별 공급량: 실적 vs 예측 "
            f"(기온 시나리오 {scen_start}~{scen_end}년 평균, Poly-3)<br>"
            f"<span style='font-size:12px;'>월평균 기온 기반 R²="
            f"{r2_m:.3f if not np.isnan(r2_m) else 'N/A'}, "
            f"일평균 기온 기반 R²="
            f"{r2_d:.3f if not np.isnan(r2_d) else 'N/A'}</span>"
        ),
        xaxis_title="월",
        yaxis_title="공급량(MJ)",
        margin=dict(l=60, r=40, t=80, b=40),
    )
    st.plotly_chart(fig, use_container_width=True)

    df_table = monthly_all.copy()
    df_table["실적(MJ)"] = df_table["실적(MJ)"].round(0).astype("Int64")
    df_table["월단위_Poly3_예측(MJ)"] = (
        df_table["월단위_Poly3_예측(MJ)"].round(0).astype("Int64")
    )
    df_table["일단위_Poly3_예측합(MJ)"] = (
        df_table["일단위_Poly3_예측합(MJ)"].round(0).astype("Int64")
    )

    total_row = pd.DataFrame(
        {
            "월": ["합계"],
            "실적(MJ)": [total_actual],
            "월단위_Poly3_예측(MJ)": [total_m],
            "일단위_Poly3_예측합(MJ)": [total_d],
        }
    )
    df_table_total = pd.concat([df_table, total_row], ignore_index=True)

    st.markdown("**월별 실적/예측 수치표 (하단 합계 포함)**")
    df_tbl_fmt = df_table_total.copy()
    for col in ["실적(MJ)", "월단위_Poly3_예측(MJ)", "일단위_Poly3_예측합(MJ)"]:
        df_tbl_fmt[col] = df_tbl_fmt[col].apply(thousands)

    st.dataframe(
        center_style(df_tbl_fmt),
        use_container_width=True,
        height=430,
    )

    st.markdown("**연간 누적 공급량 비교 — 실적 vs 월단위 Poly-3 vs 일단위 Poly-3**")

    df_tot = pd.DataFrame(
        {
            "구분": ["실적", "월단위 Poly-3 예측", "일단위 Poly-3 예측합"],
            "연간 공급량(MJ)": [total_actual, total_m, total_d],
        }
    )

    fig_tot = px.bar(
        df_tot,
        x="구분",
        y="연간 공급량(MJ)",
        text="연간 공급량(MJ)",
    )
    fig_tot.update_traces(texttemplate="%{text:,.0f}", textposition="outside")
    fig_tot.update_layout(
        yaxis_title="연간 공급량(MJ)",
        margin=dict(l=60, r=40, t=40, b=40),
    )
    st.plotly_chart(fig_tot, use_container_width=True)

    df_tot_tbl = df_tot.copy()
    df_tot_tbl["실적대비 차이(MJ)"] = df_tot_tbl["연간 공급량(MJ)"] - total_actual
    df_tot_tbl["실적대비 오차율(%)"] = (
        df_tot_tbl["실적대비 차이(MJ)"] / total_actual * 100
    )

    for col in ["연간 공급량(MJ)", "실적대비 차이(MJ)"]:
        df_tot_tbl[col] = df_tot_tbl[col].apply(thousands)
    df_tot_tbl["실적대비 오차율(%)"] = df_tot_tbl["실적대비 오차율(%)"].round(2)

    st.markdown("**연간 누적 공급량 수치표**")
    st.dataframe(
        center_style(df_tot_tbl, fmt_map={"실적대비 오차율(%)": "{:.2f}"}),
        use_container_width=True,
    )


# ─────────────────────────────────────────────────────────
# 3. 기온 매트릭스 (일별 평균기온)
# ─────────────────────────────────────────────────────────
def section_3_temp_matrix(df: pd.DataFrame):
    st.markdown("### 🌡️ ③ 기온 매트릭스 (일별 평균기온)")

    year_min = int(df["연도"].min())
    year_max = int(df["연도"].max())

    start_year, end_year = st.slider(
        "연도 범위 (실제 데이터가 있는 연도만 표시됨)",
        min_value=year_min,
        max_value=year_max,
        value=(max(year_min, year_max - 20), year_max),
        step=1,
    )

    _, col_month, _ = st.columns([0.4, 0.2, 0.4])
    with col_month:
        month_options = sorted(df["월"].unique())
        month = st.selectbox(
            "월 선택",
            month_options,
            index=month_options.index(10) if 10 in month_options else 0,
        )

    mask = (df["연도"] >= start_year) & (df["연도"] <= end_year) & (df["월"] == month)
    d = df.loc[mask, ["연도", "월", "일", "평균기온(℃)"]].copy()
    if d.empty:
        st.warning("선택한 기간과 월에 해당하는 기온 데이터가 없습니다.")
        return

    mat = (
        d.pivot_table(
            index="일",
            columns="연도",
            values="평균기온(℃)",
            aggfunc="mean",
        )
        .sort_index(axis=1)
        .sort_index(axis=0)
    )

    st.markdown(
        f"**기온 매트릭스 – {month}월 기준 (선택 연도 {start_year}~{end_year})**"
    )

    fig = px.imshow(
        mat.values,
        x=mat.columns,
        y=mat.index,
        color_continuous_scale="RdBu_r",
        origin="lower",
        labels=dict(x="연도", y="일", color="°C"),
        aspect="auto",
    )
    fig.update_layout(
        width=780,
        height=780,
        margin=dict(l=80, r=30, t=20, b=60),
        coloraxis_colorbar=dict(title="°C"),
    )
    fig.update_yaxes(scaleanchor="x", scaleratio=1)

    st.plotly_chart(fig, use_container_width=False)


# ─────────────────────────────────────────────────────────
# 메인
# ─────────────────────────────────────────────────────────
def main():
    st.markdown(
        "<h1 style='font-size:32px;'>도시가스 공급량 – 일별 vs 월별 기온 기반 3차 다항식 예측력 비교</h1>",
        unsafe_allow_html=True,
    )
    st.write("")

    df_daily = load_daily()

    section_0_correlation()
    st.write("---")

    train_start, train_end, model_m, model_d, r2_m, r2_d = section_1_train_r2(df_daily)
    st.write("---")

    section_2_scenario(
        df_daily,
        (train_start, train_end),
        model_m,
        model_d,
        r2_m,
        r2_d,
    )
    st.write("---")

    section_3_temp_matrix(df_daily)


if __name__ == "__main__":
    main()
