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

    df = df[["일자", "공급량(MJ)", "공급량(M3)", "평균기온(℃)"]].copy()
    df["일자"] = pd.to_datetime(df["일자"])
    df = df.dropna(subset=["공급량(MJ)", "평균기온(℃)"])

    df["연도"] = df["일자"].dt.year
    df["월"] = df["일자"].dt.month
    df["일"] = df["일자"].dt.day

    return df


@st.cache_data
def load_corr_data() -> pd.DataFrame | None:
    excel_path = Path(__file__).parent / "상관도분석.xlsx"
    if not excel_path.exists():
        return None
    return pd.read_excel(excel_path)


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

    if ss_tot == 0:
        r2 = np.nan
    else:
        r2 = 1 - ss_res / ss_tot

    return coef, y_pred, r2


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


def center_style(df: pd.DataFrame):
    """모든 표 숫자 및 헤더를 중앙 정렬하는 Styler."""
    styler = (
        df.style
        .set_table_styles(
            [
                dict(selector="th", props=[("text-align", "center")]),
                dict(selector="td", props=[("text-align", "center")]),
            ]
        )
        .set_properties(**{"text-align": "center"})
    )
    return styler


# ─────────────────────────────────────────────
# 메인
# ─────────────────────────────────────────────
def main():
    st.title("도시가스 공급량 — 일별 vs 월별 기온기반 3차 다항식 예측력 비교")

    df = load_daily_data()
    min_year = int(df["연도"].min())
    max_year = int(df["연도"].max())

    # ── 0. 상관도 분석 ────────────────────────────────
    st.subheader("📊 0. 상관도 분석 (공급량 vs 주요 변수)")

    df_corr_raw = load_corr_data()
    if df_corr_raw is None:
        st.caption("상관도분석.xlsx 파일이 없어서 상관도 매트릭스를 표시하지 못했어.")
    else:
        num_df = df_corr_raw.select_dtypes(include=["number"]).copy()
        num_cols = list(num_df.columns)

        if len(num_cols) >= 2:
            corr = num_df.corr()

            nice_colorscale = [
                [0.0, "#313695"],
                [0.2, "#4575b4"],
                [0.4, "#abd9e9"],
                [0.5, "#ffffbf"],
                [0.6, "#fdae61"],
                [0.8, "#d73027"],
                [1.0, "#a50026"],
            ]

            text = corr.round(2).astype(str).values
            n_rows, n_cols = corr.shape

            # 가로를 넓게, 세로는 조금 낮게 (대략 4:3 정도 느낌)
            width = 960
            height = 480

            fig_corr = go.Figure(
                data=go.Heatmap(
                    z=corr.values,
                    x=corr.columns,
                    y=corr.index,
                    colorscale=nice_colorscale,
                    zmin=-0.8,   # 극단값 색을 조금 누그러뜨리기
                    zmax=0.8,
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
                xaxis=dict(
                    side="top",
                    tickangle=45,
                ),
                yaxis=dict(autorange="reversed"),
                width=width,
                height=height,
                margin=dict(l=80, r=20, t=80, b=80),
            )

            # 기준 변수(공급량)와의 상관계수 표 만들기
            target_col = None
            for c in num_cols:
                if "공급량" in str(c):
                    target_col = c
                    break
            if target_col is None:
                target_col = num_cols[0]

            if target_col in corr.columns:
                target_series = corr[target_col].drop(target_col)
                target_series = target_series.reindex(
                    target_series.abs().sort_values(ascending=False).index
                )
                tbl_df = target_series.round(3).to_frame(name="상관계수")

                col_hm, col_tbl = st.columns([3, 1])
                with col_hm:
                    st.plotly_chart(fig_corr, use_container_width=False)
                with col_tbl:
                    st.markdown(
                        f"**기준 변수: `{target_col}` 과(와) 다른 변수들의 상관계수**"
                    )
                    st.table(center_style(tbl_df))
        else:
            st.caption("숫자 컬럼이 2개 미만이라 상관도 분석을 할 수 없어.")

    # ── ① 데이터 학습기간 선택 ──────────────────────
    st.subheader("📚 ① 데이터 학습기간 선택 (3차 다항식 R² 계산용)")

    train_default_start = max(min_year, max_year - 4)

    col_train, _ = st.columns([1, 1])
    with col_train:
        train_start, train_end = st.slider(
            "학습에 사용할 연도 범위",
            min_value=min_year,
            max_value=max_year,
            value=(train_default_start, max_year),
            step=1,
        )

    st.caption(f"현재 학습 구간: **{train_start}년 ~ {train_end}년**")

    df_window = df[df["연도"].between(train_start, train_end)].copy()

    df_month = (
        df_window
        .groupby(["연도", "월"], as_index=False)
        .agg(
            공급량_MJ=("공급량(MJ)", "sum"),
            평균기온=("평균기온(℃)", "mean"),
        )
    )

    coef_m, y_pred_m, r2_m = fit_poly3_and_r2(
        df_month["평균기온"],
        df_month["공급량_MJ"],
    )
    if y_pred_m is not None:
        df_month["예측공급량_MJ"] = y_pred_m
    else:
        df_month["예측공급량_MJ"] = np.nan

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
    st.subheader("📈 기온–공급량 관계 (실적 vs 3차 다항식 곡선)")

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

    # ── ② 기온 시나리오 연도 범위 선택 ───────────
    st.subheader("🧊 ② 기온 시나리오 연도 범위 선택 (월평균 vs 일평균 예측 비교용)")

    scen_default_start = max(min_year, max_year - 4)

    col_scen, _ = st.columns([1, 1])
    with col_scen:
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

    temp_month = (
        df_scen.groupby("월")["평균기온(℃)"]
        .mean()
        .sort_index()
    )

    monthly_pred_from_month_model = None
    if coef_m is not None:
        monthly_pred_vals = np.polyval(coef_m, temp_month.values)
        monthly_pred_from_month_model = pd.Series(
            monthly_pred_vals,
            index=temp_month.index,
            name=f"월단위 Poly-3 예측(MJ) - 기온 {scen_start}~{scen_end}년 평균",
        )

    monthly_pred_from_daily_model = None
    if coef_d is not None:
        df_scen = df_scen.copy()
        df_scen["예측일공급량_MJ_from_daily"] = np.polyval(
            coef_d,
            df_scen["평균기온(℃)"].to_numpy(),
        )

        monthly_daily_by_year = (
            df_scen
            .groupby(["연도", "월"])["예측일공급량_MJ_from_daily"]
            .sum()
            .reset_index()
        )

        monthly_pred_from_daily_model = (
            monthly_daily_by_year
            .groupby("월")["예측일공급량_MJ_from_daily"]
            .mean()
            .sort_index()
        )
        monthly_pred_from_daily_model.name = (
            f"일단위 Poly-3 예측합(MJ) - 기온 {scen_start}~{scen_end}년 평균"
        )

    # 예측/실적 연도 선택
    st.markdown("##### 예측/실적 연도 선택")

    year_options = sorted(df["연도"].unique())
    col_pred_year, _ = st.columns([1, 3])
    with col_pred_year:
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

    # ── 월별 예측 vs 실적 라인그래프 ────────────────
    st.subheader("🔥 월별 예측 vs 실적 — 월단위 Poly-3 vs 일단위 Poly-3(합산)")

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

    colors = {}
    if monthly_actual is not None:
        colors[monthly_actual.name] = "red"
    if monthly_pred_from_month_model is not None:
        colors[monthly_pred_from_month_model.name] = "#1f77b4"
    if monthly_pred_from_daily_model is not None:
        colors[monthly_pred_from_daily_model.name] = "#ff7f0e"

    fig_line = go.Figure()
    for col in df_compare.columns:
        fig_line.add_trace(
            go.Scatter(
                x=list(df_compare.index),
                y=df_compare[col],
                mode="lines+markers",
                name=col,
                line=dict(color=colors.get(col, None)),
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
            ticktext=[f"{m}월" for m in month_index],
        ),
        margin=dict(l=20, r=20, t=40, b=20),
    )

    st.plotly_chart(fig_line, use_container_width=True)

    st.markdown("##### 월별 실적/예측 수치표")
    df_compare_view = df_compare.copy()
    df_compare_view.index = [f"{m}월" for m in df_compare_view.index]
    df_compare_view = format_table_generic(df_compare_view)
    st.table(center_style(df_compare_view))

    # ── 연간 소계 ────────────────────────────────
    if (
        (monthly_actual is not None)
        and (monthly_pred_from_month_model is not None)
        and (monthly_pred_from_daily_model is not None)
    ):
        total_actual = monthly_actual.sum()
        total_month_pred = monthly_pred_from_month_model.sum()
        total_daily_pred = monthly_pred_from_daily_model.sum()

        summary_df = pd.DataFrame(
            {
                "구분": ["실적", "월단위 Poly-3 예측", "일단위 Poly-3 예측합"],
                "연간 공급량(MJ)": [total_actual, total_month_pred, total_daily_pred],
            }
        )
        summary_df["실적대비 차이(MJ)"] = summary_df["연간 공급량(MJ)"] - total_actual
        summary_df["실적대비 오차율(%)"] = (
            summary_df["실적대비 차이(MJ)"] / total_actual * 100
        )

        st.markdown("###### 연간 소계 (실적 vs 예측, 실적대비 차이·오차율)")
        summary_view = format_table_generic(
            summary_df,
            percent_cols=["실적대비 오차율(%)"],
        )
        st.table(center_style(summary_view))

    # ── ③ 기온 매트릭스 (일별 평균기온) ─────────────
    st.subheader("🌡️ ③ 기온 매트릭스 (일별 평균기온)")

    # 실제 데이터가 있는 연도 범위만 선택 가능하도록
    mat_slider_min = min_year
    mat_default_start = mat_slider_min

    col_mat_slider, col_mat_month = st.columns([2, 1])
    with col_mat_slider:
        mat_start, mat_end = st.slider(
            "연도 범위 (실제 데이터가 있는 연도만 표시됨)",
            min_value=mat_slider_min,
            max_value=max_year,
            value=(mat_default_start, max_year),
            step=1,
        )
    with col_mat_month:
        month_sel = st.selectbox(
            "월 선택",
            list(range(1, 12 + 1)),
            index=9,
        )

    df_mat = df[(df["연도"].between(mat_start, mat_end)) & (df["월"] == month_sel)].copy()
    if df_mat.empty:
        st.write("선택한 연도/월 범위에 대한 기온 데이터가 없어.")
        return

    pivot = (
        df_mat.pivot_table(
            index="일",
            columns="연도",
            values="평균기온(℃)",
            aggfunc="mean",
        )
        .sort_index()
        .sort_index(axis=1)
    )

    # 가로를 넓게, 세로는 상대적으로 낮게 (다른 앱 스샷 비율에 맞춤)
    width_hm = 1200  # 기존보다 약 20% 확대
    height_hm = 360  # 세로는 낮게

    fig_hm = go.Figure(
        data=go.Heatmap(
            z=pivot.values,
            x=pivot.columns,
            y=pivot.index,
            colorscale="RdBu_r",
            colorbar_title="℃",
        )
    )
    fig_hm.update_layout(
        title=f"기온 매트릭스 — {month_sel}월 기준 (선택 연도 {mat_start}~{mat_end})",
        xaxis_title="연도",
        yaxis_title="일",
        width=width_hm,
        height=height_hm,
        margin=dict(l=20, r=20, t=40, b=40),
    )

    st.plotly_chart(fig_hm, use_container_width=False)


if __name__ == "__main__":
    main()
