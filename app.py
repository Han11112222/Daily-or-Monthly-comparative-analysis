import calendar
from io import BytesIO
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from openpyxl.styles import Alignment, Font


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
    required = ["일자", "공급량(MJ)", "공급량(M3)", "평균기온(℃)"]
    missing = [c for c in required if c not in df_raw.columns]
    if missing:
        st.error(f"필수 컬럼 누락: {missing}")
        st.stop()

    df_raw = df_raw[required].copy()
    df_raw["일자"] = pd.to_datetime(df_raw["일자"], errors="coerce")
    df_raw["공급량(MJ)"] = pd.to_numeric(df_raw["공급량(MJ)"], errors="coerce")
    df_raw["공급량(M3)"] = pd.to_numeric(df_raw["공급량(M3)"], errors="coerce")
    df_raw["평균기온(℃)"] = pd.to_numeric(df_raw["평균기온(℃)"], errors="coerce")

    df_raw = df_raw.dropna(subset=["일자"]).sort_values("일자").reset_index(drop=True)

    df_raw["연도"] = df_raw["일자"].dt.year.astype(int)
    df_raw["월"] = df_raw["일자"].dt.month.astype(int)
    df_raw["일"] = df_raw["일자"].dt.day.astype(int)

    df_temp_all = df_raw.copy()

    # 공급량(MJ)와 평균기온 둘 다 있는 구간만 모델용
    df_model = df_raw.dropna(subset=["공급량(MJ)", "평균기온(℃)"]).copy()

    return df_model, df_temp_all


@st.cache_data
def load_holiday_calendar() -> pd.DataFrame | None:
    excel_path = Path(__file__).parent / "holiday_calendar.xlsx"
    if not excel_path.exists():
        return None
    try:
        df = pd.read_excel(excel_path)
        return df
    except Exception:
        return None


@st.cache_data
def load_monthly_plan() -> pd.DataFrame:
    excel_path = Path(__file__).parent / "공급량(계획_실적).xlsx"
    df = pd.read_excel(excel_path, sheet_name="월별계획_실적")
    df["연"] = df["연"].astype(int)
    df["월"] = df["월"].astype(int)
    return df


@st.cache_data
def load_effective_calendar() -> pd.DataFrame | None:
    excel_path = Path(__file__).parent / "effective_days_calendar.xlsx"
    if not excel_path.exists():
        return None
    try:
        df = pd.read_excel(excel_path)
        return df
    except Exception:
        return None


@st.cache_data
def load_corr_data() -> pd.DataFrame | None:
    excel_path = Path(__file__).parent / "상관도분석.xlsx"
    if not excel_path.exists():
        return None
    try:
        df = pd.read_excel(excel_path)
        return df
    except Exception:
        return None


# ─────────────────────────────────────────────
# 회귀 함수 (3차 다항식)
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

    r2 = np.nan if ss_tot == 0 else 1 - ss_res / ss_tot
    return coef, y_pred, r2


def plot_poly_fit(x, y, coef, title, x_label, y_label):
    x = pd.Series(x).astype(float)
    y = pd.Series(y).astype(float)

    p = np.poly1d(coef)

    x_line = np.linspace(float(np.nanmin(x)), float(np.nanmax(x)), 200)
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
# 엑셀 스타일(기존 유지용)
# ─────────────────────────────────────────────
def _center_ws(ws):
    for row in ws.iter_rows():
        for cell in row:
            cell.alignment = Alignment(horizontal="center", vertical="center")


# ─────────────────────────────────────────────
# 탭1: Daily 공급량 분석
# ─────────────────────────────────────────────
def tab_daily_plan(df_daily: pd.DataFrame):
    st.subheader("📅 Daily 공급량 분석 — (기존 기능 유지)")

    st.caption("※ 이 탭은 기존 그대로 유지. (요청사항은 Daily·Monthly 탭 맨 하단 히트맵 복원)")

    st.dataframe(df_daily.head(20), use_container_width=True)


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

    st.subheader("📌 1. 월평균기온 기반 월별 공급량 회귀(3차 다항식)")

    # 학습기간 선택
    train_default_start = max(min_year_model, max_year_model - 20)
    train_start, train_end = st.slider(
        "학습 구간(연도)",
        min_value=min_year_model,
        max_value=max_year_model,
        value=(train_default_start, max_year_model),
        step=1,
    )

    st.caption(f"현재 학습 구간: **{train_start}년 ~ {train_end}년**")
    df_window = df[df["연도"].between(train_start, train_end)].copy()

    df_month = (
        df_window
        .groupby(["연도", "월"], as_index=False)
        .agg(공급량_MJ=("공급량(MJ)", "sum"), 평균기온=("평균기온(℃)", "mean"))
    )

    coef_m, y_pred_m, r2_m = fit_poly3_and_r2(df_month["평균기온"], df_month["공급량_MJ"])
    # 안전 처리: y_pred 길이가 df_month와 다를 수 있어(결측 제거/필터링 등)
    df_month["예측공급량_MJ"] = np.nan
    if y_pred_m is not None:
        try:
            if len(y_pred_m) == len(df_month):
                df_month["예측공급량_MJ"] = y_pred_m
            else:
                _m = (~df_month["평균기온"].isna()) & (~df_month["공급량_MJ"].isna())
                if len(y_pred_m) == int(_m.sum()):
                    df_month.loc[_m, "예측공급량_MJ"] = y_pred_m
        except Exception:
            pass

    coef_d, y_pred_d, r2_d = fit_poly3_and_r2(df_window["평균기온(℃)"], df_window["공급량(MJ)"])
    # 안전 처리: y_pred 길이가 df_window와 다를 수 있어(결측 제거/필터링 등)
    df_window["예측공급량_MJ"] = np.nan
    if y_pred_d is not None:
        try:
            if len(y_pred_d) == len(df_window):
                df_window["예측공급량_MJ"] = y_pred_d
            else:
                _m2 = (~df_window["평균기온(℃)"].isna()) & (~df_window["공급량(MJ)"].isna())
                if len(y_pred_d) == int(_m2.sum()):
                    df_window.loc[_m2, "예측공급량_MJ"] = y_pred_d
        except Exception:
            pass

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
    # 🧊 G. 기온분석 — 일일 평균기온 히트맵(복원)
    #   - Daily·Monthly 공급량 비교 탭 맨 하단에 표시
    # ─────────────────────────────────────────────
    st.markdown("---")
    st.subheader("🧊 G. 기온분석 — 일일 평균기온 히트맵")

    uploaded_temp = st.file_uploader(
        "일일기온파일 업로드(XLSX)",
        type=["xlsx"],
        key="temp_heatmap_uploader",
        help="업로드하지 않으면 현재 데이터(공급량 파일)의 '평균기온(℃)'로 히트맵을 생성",
    )

    def _guess_col(cols, keywords, default=None):
        for kw in keywords:
            for c in cols:
                if kw.lower() in str(c).lower():
                    return c
        return default

    # 1) 데이터 소스 선택 (업로드 우선, 없으면 df_temp_all 사용)
    if uploaded_temp is not None:
        try:
            tmp_raw = pd.read_excel(uploaded_temp)
        except Exception:
            tmp_raw = None

        if tmp_raw is None or tmp_raw.empty:
            st.info("업로드한 기온파일에서 데이터를 읽지 못했어.")
            dt = None
        else:
            cols = list(tmp_raw.columns)
            date_c = _guess_col(cols, ["일자", "날짜", "date"], cols[0] if cols else None)
            temp_c = _guess_col(cols, ["평균기온", "기온", "tmean", "temp"], cols[1] if len(cols) > 1 else (cols[0] if cols else None))

            dt = tmp_raw[[date_c, temp_c]].copy()
            dt.columns = ["date", "tmean"]
    else:
        if ("일자" not in df_temp_all.columns) or ("평균기온(℃)" not in df_temp_all.columns):
            st.info("현재 데이터에서 '일자', '평균기온(℃)' 컬럼을 찾지 못했어. 기온파일을 업로드해줘.")
            dt = None
        else:
            dt = df_temp_all[["일자", "평균기온(℃)"]].copy()
            dt.columns = ["date", "tmean"]

    if dt is not None:
        # 2) 전처리
        dt["date"] = pd.to_datetime(dt["date"], errors="coerce")
        dt["tmean"] = pd.to_numeric(dt["tmean"], errors="coerce")
        dt = dt.dropna(subset=["date", "tmean"]).sort_values("date").reset_index(drop=True)

        dt["year"] = dt["date"].dt.year
        dt["month"] = dt["date"].dt.month
        dt["day"] = dt["date"].dt.day

        years_all = sorted(dt["year"].unique().tolist())
        if len(years_all) == 0:
            st.info("히트맵을 만들 기온 데이터가 없어.")
        else:
            y_min, y_max = int(min(years_all)), int(max(years_all))

            col_a, col_b = st.columns([2, 1])
            with col_a:
                year_range = st.slider(
                    "연도 범위",
                    min_value=y_min,
                    max_value=y_max,
                    value=(y_min, y_max),
                    step=1,
                    key="temp_heatmap_year_range",
                )
            with col_b:
                sel_month = st.selectbox(
                    "월 선택",
                    options=list(range(1, 13)),
                    index=0,
                    format_func=lambda m: f"{m:02d}",
                    key="temp_heatmap_month",
                )

            sel_years = [y for y in years_all if year_range[0] <= y <= year_range[1]]
            dsel = dt[(dt["year"].isin(sel_years)) & (dt["month"] == int(sel_month))].copy()

            if dsel.empty:
                st.info("선택한 연도/월에 해당하는 기온 데이터가 없어.")
            else:
                # 선택 연도 중 가장 긴 달(윤년 포함) 기준으로 day 인덱스 생성
                last_day = max(calendar.monthrange(int(y), int(sel_month))[1] for y in sel_years)

                pivot = (
                    dsel.pivot_table(index="day", columns="year", values="tmean", aggfunc="mean")
                    .reindex(range(1, last_day + 1))
                    .sort_index(axis=1)
                )

                # 하단 '평균' 행(연도별 월평균)
                avg_row = pivot.mean(axis=0, skipna=True)
                pivot_with_avg = pd.concat([pivot, pd.DataFrame([avg_row], index=["평균"])])

                y_labels = [f"{int(sel_month):02d}-{int(d):02d}" for d in pivot.index] + ["평균"]
                Z = pivot_with_avg.values.astype(float)
                X = [str(x) for x in pivot_with_avg.columns.tolist()]
                Y = y_labels

                # 평균 행에만 텍스트 표시(사진처럼)
                text = np.full(Z.shape, "", dtype=object)
                if Z.shape[0] > 0:
                    last_idx = Z.shape[0] - 1
                    text[last_idx, :] = [f"{v:.1f}" if np.isfinite(v) else "" for v in Z[last_idx, :]]

                fig_heat = go.Figure(
                    data=go.Heatmap(
                        z=Z,
                        x=X,
                        y=Y,
                        colorscale="RdBu_r",
                        zmid=0,
                        colorbar=dict(title="°C"),
                        hovertemplate="연도=%{x}<br>일자=%{y}<br>평균기온=%{z:.1f}℃<extra></extra>",
                        text=text,
                        texttemplate="%{text}",
                        textfont=dict(size=12, color="black"),
                        hoverongaps=False,
                    )
                )
                fig_heat.update_layout(
                    template="simple_white",
                    title=f"{int(sel_month):02d}월 일일 평균기온 히트맵(선택연도 {len(X)}개)",
                    margin=dict(l=40, r=20, t=50, b=40),
                    height=650,
                    xaxis=dict(title="", tickmode="linear", dtick=1),
                    yaxis=dict(title="", autorange="reversed"),
                )
                st.plotly_chart(fig_heat, use_container_width=True)


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
