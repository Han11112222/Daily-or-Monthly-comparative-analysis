import calendar
from io import BytesIO
from pathlib import Path

import holidays
import numpy as np
import pandas as pd
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
def load_daily_data():
    """
    반환:
      df_model     : 공급량(MJ)와 평균기온 둘 다 있는 구간 (예측/R² 계산용)
      df_temp_all  : 평균기온만 있어도 되는 전체 구간 (1980년 포함, 매트릭스/시나리오용)
    """
    excel_path = Path(__file__).parent / "공급량(일일실적).xlsx"
    df_raw = pd.read_excel(excel_path)

    # 필요한 컬럼만 사용
    df_raw = df_raw[["일자", "공급량(MJ)", "공급량(M3)", "평균기온(℃)"]].copy()
    df_raw["일자"] = pd.to_datetime(df_raw["일자"])

    # 날짜 파생 컬럼
    df_raw["연도"] = df_raw["일자"].dt.year
    df_raw["월"] = df_raw["일자"].dt.month
    df_raw["일"] = df_raw["일자"].dt.day

    # 기온만 있어도 되는 전체 구간
    df_temp_all = df_raw.dropna(subset=["평균기온(℃)"]).copy()

    # 예측·R²용: 공급량과 기온 둘 다 있는 구간
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
    """
    공급량(계획_실적).xlsx 중 '월별계획_실적' 시트 사용
    컬럼 : 일자, 연, 월, 계획(사업계획제출_MJ), ...
    """
    excel_path = Path(__file__).parent / "공급량(계획_실적).xlsx"
    df = pd.read_excel(excel_path, sheet_name="월별계획_실적")
    df["연"] = df["연"].astype(int)
    df["월"] = df["월"].astype(int)
    return df


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
            df[col] = df[col].map(lambda x: f"{x:.4f}")
        elif col in temp_cols:
            df[col] = df[col].map(lambda x: f"{x:.2f}")
        elif pd.api.types.is_numeric_dtype(df[col]):
            df[col] = df[col].map(lambda x: f"{x:,.0f}")
    return df


def center_style(df: pd.DataFrame):
    """모든 표 숫자 및 헤더를 중앙 정렬하는 Styler."""
    styler = (
        df.style.set_table_styles(
            [
                dict(selector="th", props=[("text-align", "center")]),
                dict(selector="td", props=[("text-align", "center")]),
            ]
        ).set_properties(**{"text-align": "center"})
    )
    return styler


# ─────────────────────────────────────────────
# Daily 공급량 분석용 함수
# ─────────────────────────────────────────────
def make_daily_plan_table(
    df_daily: pd.DataFrame,
    df_plan: pd.DataFrame,
    target_year: int = 2026,
    target_month: int = 1,
    recent_window: int = 3,
) -> tuple[pd.DataFrame | None, pd.DataFrame | None, list[int]]:
    """
    최근 recent_window년(예: 2023~2025) 같은 월의 일별 공급 패턴으로
    target_year/target_month 일별 비율과 일별 계획 공급량을 계산.
    반환:
      df_result : 2026년 해당월 일별 계획 테이블
      df_mat    : 최근 n년 일별 실적 매트릭스 (Heatmap용)
      recent_years : 사용된 최근 연도 리스트
    """
    # 사용 가능한 연도 범위
    all_years = sorted(df_daily["연도"].unique())
    start_year = target_year - recent_window
    recent_years = [y for y in range(start_year, target_year) if y in all_years]

    if len(recent_years) == 0:
        return None, None, []

    # 최근 n년 + 대상 월 데이터
    df_recent = df_daily[
        (df_daily["연도"].isin(recent_years)) & (df_daily["월"] == target_month)
    ].copy()
    if df_recent.empty:
        return None, None, recent_years

    # 마지막 일자 (28/29/30/31)
    last_day = calendar.monthrange(target_year, target_month)[1]
    day_range = list(range(1, last_day + 1))

    # 일자별 총공급량 (최근 n년 합계 기준)
    daily_sum = (
        df_recent.groupby("일", as_index=False)["공급량(MJ)"].sum().rename(
            columns={"공급량(MJ)": "최근N년_총공급량(MJ)"}
        )
    )
    daily_sum = daily_sum.set_index("일").reindex(day_range, fill_value=0).reset_index()

    total_month = daily_sum["최근N년_총공급량(MJ)"].sum()
    if total_month <= 0:
        return None, None, recent_years

    # 일별 비율
    daily_sum["일별비율"] = daily_sum["최근N년_총공급량(MJ)"] / total_month

    # 최근 n년 평균 공급량 (설명용)
    daily_avg = (
        df_recent.groupby("일", as_index=False)["공급량(MJ)"].mean().rename(
            columns={"공급량(MJ)": "최근N년_평균공급량(MJ)"}
        )
    )
    daily_sum = daily_sum.merge(daily_avg, on="일", how="left")

    # 2026년 월 계획 총량
    row_plan = df_plan[(df_plan["연"] == target_year) & (df_plan["월"] == target_month)]
    if row_plan.empty:
        plan_total = np.nan
    else:
        plan_total = float(row_plan["계획(사업계획제출_MJ)"].iloc[0])

    # 일별 예상 공급량
    daily_sum["예상공급량(MJ)"] = (daily_sum["일별비율"] * plan_total).round(0)

    # 날짜·요일·주말/공휴일 구분
    dates = pd.to_datetime(
        {
            "year": target_year,
            "month": target_month,
            "day": daily_sum["일"],
        }
    )
    daily_sum["일자"] = dates
    daily_sum["연"] = target_year
    daily_sum["월"] = target_month

    weekday_names = ["월", "화", "수", "목", "금", "토", "일"]
    daily_sum["요일"] = dates.dt.weekday.map(lambda i: weekday_names[i])

    daily_sum["is_weekend"] = dates.dt.weekday >= 5

    kr_holidays = holidays.KR(years=[target_year])
    daily_sum["공휴일여부"] = dates.apply(lambda d: d in kr_holidays)

    def _label(row):
        if row["공휴일여부"]:
            return "공휴일"
        elif row["is_weekend"]:
            return "주말"
        else:
            return "평일"

    daily_sum["구분(평일/주말/공휴일)"] = daily_sum.apply(_label, axis=1)

    # 정렬 및 컬럼 순서
    daily_sum = daily_sum.sort_values("일").reset_index(drop=True)
    daily_sum = daily_sum[
        [
            "연",
            "월",
            "일",
            "일자",
            "요일",
            "구분(평일/주말/공휴일)",
            "공휴일여부",
            "최근N년_평균공급량(MJ)",
            "최근N년_총공급량(MJ)",
            "일별비율",
            "예상공급량(MJ)",
        ]
    ]

    # 최근 n년 일별 실적 매트릭스 (Heatmap)
    df_mat = (
        df_recent.pivot_table(
            index="일", columns="연도", values="공급량(MJ)", aggfunc="sum"
        )
        .reindex(index=day_range)
        .sort_index(axis=1)
    )

    return daily_sum, df_mat, recent_years


# ─────────────────────────────────────────────
# 탭1: Daily 공급량 분석
# ─────────────────────────────────────────────
def tab_daily_plan(df_daily: pd.DataFrame):
    st.subheader("📅 Daily 공급량 분석 — 최근 3년 패턴 기반 일별 계획")

    df_plan = load_monthly_plan()

    # 기본값: 2026년 1월
    years_plan = sorted(df_plan["연"].unique())
    default_year_idx = years_plan.index(2026) if 2026 in years_plan else len(years_plan) - 1

    col_y, col_m = st.columns(2)
    with col_y:
        target_year = st.selectbox("계획 연도 선택", years_plan, index=default_year_idx)
    with col_m:
        months_plan = sorted(df_plan[df_plan["연"] == target_year]["월"].unique())
        default_month_idx = months_plan.index(1) if 1 in months_plan else 0
        target_month = st.selectbox(
            "계획 월 선택", months_plan, index=default_month_idx, format_func=lambda m: f"{m}월"
        )

    st.caption(
        f"최근 **{target_year-3}년 ~ {target_year-1}년**까지의 "
        f"{target_month}월 일별 공급 패턴으로 **{target_year}년 {target_month}월** 일별 계획을 계산."
    )

    df_result, df_mat, recent_years = make_daily_plan_table(
        df_daily=df_daily,
        df_plan=df_plan,
        target_year=target_year,
        target_month=target_month,
        recent_window=3,
    )

    if df_result is None or len(recent_years) == 0:
        st.warning("해당 연도/월에 대해 최근 3년 기준으로 계산할 수 있는 데이터가 없어.")
        return

    plan_total = df_result["예상공급량(MJ)"].sum()
    st.markdown(
        f"**{target_year}년 {target_month}월 사업계획 제출 공급량 합계:** "
        f"`{plan_total:,.0f} MJ`"
    )

    # 1. 일별 테이블
    st.markdown("#### 1. 일별 비율·예상 공급량 테이블")

    view = df_result.copy()
    view_for_format = view[
        [
            "연",
            "월",
            "일",
            "요일",
            "구분(평일/주말/공휴일)",
            "공휴일여부",
            "최근N년_평균공급량(MJ)",
            "최근N년_총공급량(MJ)",
            "일별비율",
            "예상공급량(MJ)",
        ]
    ]
    view_for_format = format_table_generic(
        view_for_format,
        percent_cols=["일별비율"],
    )
    st.table(center_style(view_for_format))

    # 2. 그래프 (Bar: 예상공급량, Line: 일별비율)
    st.markdown("#### 2. 일별 예상 공급량 & 비율 그래프")

    weekday_df = view[view["구분(평일/주말/공휴일)"] == "평일"]
    weekend_df = view[view["구분(평일/주말/공휴일)"] != "평일"]

    fig = go.Figure()
    # 평일/주말·공휴일을 색으로 분리
    fig.add_bar(
        x=weekday_df["일"],
        y=weekday_df["예상공급량(MJ)"],
        name="평일 예상공급량(MJ)",
    )
    fig.add_bar(
        x=weekend_df["일"],
        y=weekend_df["예상공급량(MJ)"],
        name="주말·공휴일 예상공급량(MJ)",
    )
    fig.add_trace(
        go.Scatter(
            x=view["일"],
            y=view["일별비율"],
            mode="lines+markers",
            name="일별비율 (최근3년)",
            yaxis="y2",
        )
    )

    fig.update_layout(
        title=f"{target_year}년 {target_month}월 일별 공급량 계획 (최근3년 {target_month}월 비율 기반)",
        xaxis_title="일",
        yaxis=dict(title="예상 공급량 (MJ)"),
        yaxis2=dict(
            title="일별비율",
            overlaying="y",
            side="right",
        ),
        barmode="group",
        margin=dict(l=20, r=20, t=60, b=40),
    )
    st.plotly_chart(fig, use_container_width=True)

    # 3. 매트릭스(Heatmap) — 최근 3년 일별 실적
    st.markdown("#### 3. 최근 3년 일별 실적 매트릭스")

    if df_mat is not None:
        fig_hm = go.Figure(
            data=go.Heatmap(
                z=df_mat.values,
                x=df_mat.columns.astype(str),
                y=df_mat.index,
                colorbar_title="공급량(MJ)",
                colorscale="RdBu_r",
            )
        )
        fig_hm.update_layout(
            title=f"최근 {len(recent_years)}년 {target_month}월 일별 실적 공급량(MJ) 매트릭스",
            xaxis_title="연도",
            yaxis_title="일",
            margin=dict(l=40, r=40, t=60, b=40),
        )
        st.plotly_chart(fig_hm, use_container_width=False)

    # 4. 요약 (평일/주말/공휴일 비중)
    st.markdown("#### 4. 평일·주말·공휴일 비중 요약")

    summary = (
        view.groupby("구분(평일/주말/공휴일)", as_index=False)[["일별비율", "예상공급량(MJ)"]]
        .sum()
        .rename(columns={"일별비율": "일별비율합계"})
    )
    summary = format_table_generic(summary, percent_cols=["일별비율합계"])
    st.table(center_style(summary))

    # 5. 엑셀 다운로드
    st.markdown("#### 5. 일별 계획 엑셀 다운로드")

    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        view.to_excel(
            writer,
            index=False,
            sheet_name=f"{target_year}_{target_month:02d}_일별계획",
        )

    st.download_button(
        label=f"📥 {target_year}년 {target_month}월 일별공급계획 다운로드 (Excel)",
        data=buffer.getvalue(),
        file_name=f"{target_year}_{target_month:02d}_일별공급계획.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


# ─────────────────────────────────────────────
# 탭2: Daily·Monthly 공급량 비교 (기존 내용)
# ─────────────────────────────────────────────
def tab_daily_monthly_compare(df: pd.DataFrame, df_temp_all: pd.DataFrame):
    # 공급량이 있는 구간(예측/R²용) 연도 범위
    min_year_model = int(df["연도"].min())
    max_year_model = int(df["연도"].max())

    # 기온 전체 구간 연도 범위 (매트릭스/시나리오용)
    min_year_temp = int(df_temp_all["연도"].min())
    max_year_temp = int(df_temp_all["연도"].max())

    # ── 0. 상관도 분석 ───────────────────────────
    st.subheader("📊 0. 상관도 분석 (공급량 vs 주요 변수)")

    df_corr_raw = load_corr_data()
    if df_corr_raw is None:
        st.caption("상관도분석.xlsx 파일이 없어서 상관도 매트릭스를 표시하지 못했어.")
    else:
        num_df = df_corr_raw.select_dtypes(include=["number"]).copy()
        num_cols = list(num_df.columns)

        if len(num_cols) >= 2:
            corr = num_df.corr()

            # 색을 너무 진하게 쓰지 않도록, 표시용 값은 ±0.7으로 클리핑
            z = corr.values
            z_display = np.clip(z, -0.7, 0.7)
            text = corr.round(2).astype(str).values

            n_rows, n_cols = corr.shape

            # 정사각형 도화지
            side = 700

            nice_colorscale = [
                [0.0, "#313695"],
                [0.2, "#4575b4"],
                [0.4, "#abd9e9"],
                [0.5, "#ffffbf"],
                [0.6, "#fdae61"],
                [0.8, "#d73027"],
                [1.0, "#a50026"],
            ]

            fig_corr = go.Figure(
                data=go.Heatmap(
                    z=z_display,
                    x=corr.columns,
                    y=corr.index,
                    colorscale=nice_colorscale,
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
                xaxis=dict(
                    side="top",
                    tickangle=45,
                ),
                yaxis=dict(autorange="reversed"),
                width=side,
                height=side,  # 정사각형
                margin=dict(l=80, r=20, t=80, b=80),
            )

            # 기준 변수(공급량)와의 상관계수 표
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

    # ── ① 데이터 학습기간 선택 ───────────────────
    st.subheader("📚 ① 데이터 학습기간 선택 (3차 다항식 R² 계산용)")

    train_default_start = max(min_year_model, max_year_model - 4)

    col_train, _ = st.columns([1, 1])
    with col_train:
        train_start, train_end = st.slider(
            "학습에 사용할 연도 범위",
            min_value=min_year_model,
            max_value=max_year_model,
            value=(train_default_start, max_year_model),
            step=1,
        )

    st.caption(f"현재 학습 구간: **{train_start}년 ~ {train_end}년**")

    df_window = df[df["연도"].between(train_start, train_end)].copy()

    df_month = (
        df_window.groupby(["연도", "월"], as_index=False).agg(
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

    # ── R² 비교 ───────────────────────────────
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

    # ── 산점도 + 곡선 ──────────────────────────
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

    # ── ② 기온 시나리오 연도 범위 선택 ──────────
    st.subheader("🧊 ② 기온 시나리오 연도 범위 선택 (월평균 vs 일평균 예측 비교용)")

    scen_default_start = max(min_year_temp, max_year_temp - 4)

    col_scen, _ = st.columns([1, 1])
    with col_scen:
        scen_start, scen_end = st.slider(
            "기온 시나리오에 사용할 연도 범위",
            min_value=min_year_temp,  # 기온 전체 구간 기준 (1980 포함)
            max_value=max_year_temp,
            value=(scen_default_start, max_year_temp),
            step=1,
        )

    st.caption(
        f"선택한 기온 시나리오 연도: **{scen_start}년 ~ {scen_end}년** "
        "(각 월별로 이 기간의 평균기온을 사용)"
    )

    df_scen = df_temp_all[df_temp_all["연도"].between(scen_start, scen_end)].copy()
    if df_scen.empty:
        st.write("선택한 기온 시나리오 구간에 데이터가 없어.")
        return

    temp_month = df_scen.groupby("월")["평균기온(℃)"].mean().sort_index()

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
            df_scen.groupby(["연도", "월"])["예측일공급량_MJ_from_daily"]
            .sum()
            .reset_index()
        )

        monthly_pred_from_daily_model = (
            monthly_daily_by_year.groupby("월")["예측일공급량_MJ_from_daily"]
            .mean()
            .sort_index()
        )
        monthly_pred_from_daily_model.name = (
            f"일단위 Poly-3 예측합(MJ) - 기온 {scen_start}~{scen_end}년 평균"
        )

    # 예측/실적 연도 선택 (공급량이 있는 연도만)
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
            df_actual_year.groupby("월")["공급량(MJ)"].sum().sort_index()
        )
        monthly_actual.name = f"{pred_year}년 실적(MJ)"

    # ── 월별 예측 vs 실적 라인그래프 ───────────────
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
        colors[monthly_actual.name] = "red"  # 실적 = 붉은색
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

    # ── 연간 소계 ───────────────────────────────
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
        summary_df["실적대비 차이(MJ)"] = (
            summary_df["연간 공급량(MJ)"] - total_actual
        )
        summary_df["실적대비 오차율(%)"] = (
            summary_df["실적대비 차이(MJ)"] / total_actual * 100
        )

        st.markdown("###### 연간 소계 (실적 vs 예측, 실적대비 차이·오차율)")
        summary_view = format_table_generic(
            summary_df,
            percent_cols=["실적대비 오차율(%)"],
        )
        st.table(center_style(summary_view))

    # ── ③ 기온 매트릭스 (일별 평균기온) ───────────
    st.subheader("🌡️ ③ 기온 매트릭스 (일별 평균기온)")

    # 기온 전체 구간(평균기온만 있는 데이터) 기준
    mat_slider_min = min_year_temp  # 1980까지 가능
    mat_slider_max = max_year_temp
    mat_default_start = mat_slider_min

    col_mat_slider, col_mat_month = st.columns([2, 1])
    with col_mat_slider:
        mat_start, mat_end = st.slider(
            "연도 범위 (실제 데이터가 있는 연도만 표시됨)",
            min_value=mat_slider_min,
            max_value=mat_slider_max,
            value=(mat_default_start, mat_slider_max),
            step=1,
        )
    with col_mat_month:
        month_sel = st.selectbox(
            "월 선택",
            list(range(1, 12 + 1)),
            index=9,  # 10월
        )

    df_mat = df_temp_all[
        (df_temp_all["연도"].between(mat_start, mat_end))
        & (df_temp_all["월"] == month_sel)
    ].copy()
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

    # 정사각형 도화지
    side_hm = 700

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
        width=side_hm,
        height=side_hm,  # 정사각형
        margin=dict(l=20, r=20, t=40, b=40),
    )

    st.plotly_chart(fig_hm, use_container_width=False)


# ─────────────────────────────────────────────
# 메인
# ─────────────────────────────────────────────
def main():
    st.title("도시가스 공급량 — 일별 vs 월별 기온기반 3차 다항식 예측력 비교")

    df, df_temp_all = load_daily_data()

    mode = st.sidebar.radio(
        "좌측 탭 선택",
        ("📅 Daily 공급량 분석", "📊 Daily·Monthly 공급량 비교"),
        index=0,
    )

    if mode == "📅 Daily 공급량 분석":
        tab_daily_plan(df_daily=df)
    else:
        tab_daily_monthly_compare(df=df, df_temp_all=df_temp_all)


if __name__ == "__main__":
    main()
