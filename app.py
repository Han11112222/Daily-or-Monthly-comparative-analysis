# app.py — 도시가스 공급량: 일별 vs 월별 기온 기반 3차 다항식 예측 비교 + 상관도/기온 매트릭스
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
    page_title="도시가스 공급량 – 일별 vs 월별 기온기반 3차 다항식 예측 비교",
    layout="wide",
)

DATA_PATH = pathlib.Path(__file__).parent
DAILY_FILE = DATA_PATH / "공급량(일일실적).xlsx"
CORR_FILE = DATA_PATH / "상관도분석.xlsx"


# ─────────────────────────────────────────────────────────
# 유틸 함수
# ─────────────────────────────────────────────────────────
def center_style(df: pd.DataFrame, fmt_map=None):
    """
    숫자 중앙 정렬용 스타일 반환.
    fmt_map: {"컬럼명": 서식문자열} 형태
    """
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


def thousands(x):
    if pd.isna(x):
        return ""
    if isinstance(x, (int, np.integer)):
        return f"{x:,}"
    if isinstance(x, (float, np.floating)):
        return f"{x:,.0f}"
    return x


# ─────────────────────────────────────────────────────────
# 데이터 로딩
# ─────────────────────────────────────────────────────────
@st.cache_data(ttl=600)
def load_daily() -> pd.DataFrame:
    if not DAILY_FILE.exists():
        st.stop()

    df = pd.read_excel(DAILY_FILE)
    # 예상 컬럼: 일자, 공급량(MJ), 공급량(M3), 평균기온(℃) 등
    df["일자"] = pd.to_datetime(df["일자"])
    df["연도"] = df["일자"].dt.year
    df["월"] = df["일자"].dt.month
    df["일"] = df["일자"].dt.day
    return df


@st.cache_data(ttl=600)
def load_corr_data() -> pd.DataFrame | None:
    if not CORR_FILE.exists():
        return None
    df = pd.read_excel(CORR_FILE)
    return df


# ─────────────────────────────────────────────────────────
# 0. 상관도 분석 섹션
# ─────────────────────────────────────────────────────────
def section_0_correlation():
    st.markdown("### 📊 0. 상관도 분석 (공급량 vs 주요 변수)")

    df_corr_raw = load_corr_data()
    if df_corr_raw is None:
        st.info("`상관도분석.xlsx` 파일이 없어서 상관도 분석을 생략합니다.")
        return

    # 상관분석에 사용할 컬럼 (엑셀에 있는 실제 컬럼명을 그대로 사용)
    # 필요 시 여기 목록만 조정하면 됨.
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
    cols = [c for c in candidate_cols if c in df_corr_raw.columns]
    df_corr = df_corr_raw[cols].corr()

    # ── 레이아웃: 히트맵(왼쪽) + 표(오른쪽, 바로 인접) ──
    col_heat, col_tbl = st.columns([0.7, 0.3], gap="small")

    # ── 히트맵 (정사각형 650×650) ──
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
        # 셀을 정사각형이 되도록 축 고정
        fig.update_yaxes(scaleanchor="x", scaleratio=1)
        st.plotly_chart(fig, use_container_width=False)

    # ── 기준 변수(공급량) vs 다른 변수 상관계수 표 ──
    with col_tbl:
        target_col = "공급량(MJ)"
        if target_col not in df_corr.columns:
            st.info("공급량(MJ) 컬럼을 찾을 수 없어 상관계수 표는 생략합니다.")
            return

        s = df_corr[target_col].drop(target_col, errors="ignore")
        df_target = (
            s.to_frame(name="상관계수")
            .sort_values("상관계수", key=lambda x: x.abs(), ascending=False)
            .reset_index()
            .rename(columns={"index": "변수"})
        )
        # 소수 둘째 자리까지, 숫자 중앙정렬
        df_target["상관계수"] = df_target["상관계수"].round(2)
        st.markdown(
            f"**기준 변수: <span style='color:#008000;'>{target_col}</span> 과(와) 다른 변수들의 상관계수**",
            unsafe_allow_html=True,
        )
        st.dataframe(
            center_style(df_target, fmt_map={"상관계수": "{:.2f}"}),
            use_container_width=True,
            height=400,
        )


# ─────────────────────────────────────────────────────────
# 3. 기온 매트릭스 섹션 (일별 평균기온)
# ─────────────────────────────────────────────────────────
def section_3_temp_matrix():
    st.markdown("### 🌡️ ③ 기온 매트릭스 (일별 평균기온)")

    df = load_daily()

    # 실제 데이터가 있는 연도 범위 (최소 1980년은 보장)
    year_min = int(df["연도"].min())
    year_min = min(year_min, 1980)
    year_max = int(df["연도"].max())

    start_year, end_year = st.slider(
        "연도 범위 (실제 데이터가 있는 연도만 표시됨)",
        min_value=year_min,
        max_value=year_max,
        value=(max(year_min, year_max - 20), year_max),
        step=1,
    )

    # 월 선택(가로폭 좁게) – 좌우 여백을 두고 가운데 좁은 selectbox
    _, col_month, _ = st.columns([0.4, 0.2, 0.4])
    with col_month:
        month_options = sorted(df["월"].unique())
        month = st.selectbox("월 선택", month_options, index=month_options.index(10) if 10 in month_options else 0)

    # 선택 조건에 맞게 필터
    mask = (df["연도"] >= start_year) & (df["연도"] <= end_year) & (df["월"] == month)
    df_sel = df.loc[mask, ["연도", "월", "일", "평균기온(℃)"]].copy()

    if df_sel.empty:
        st.warning("선택한 기간과 월에 해당하는 데이터가 없습니다.")
        return

    # 피벗: index=일(1~31), columns=연도, values=평균기온
    mat = (
        df_sel.pivot_table(
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

    # 정사각형 780×780 (이전보다 약 30% 확대)
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
    # 셀을 정사각형으로
    fig.update_yaxes(scaleanchor="x", scaleratio=1)

    st.plotly_chart(fig, use_container_width=False)


# ─────────────────────────────────────────────────────────
# (참고) 1·2번 섹션: 기온 기반 Poly-3 모델 / R² 비교 / 월별 예측 vs 실적
# 이 부분은 사용자가 기존에 쓰던 로직을 그대로 두고,
# 위의 0번/3번 섹션만 교체해서 사용할 수 있도록 현재 예시는 생략합니다.
# 필요하면 여기에 1, 2 섹션 함수들을 추가해서 전체 앱을 구성하면 됩니다.
# ─────────────────────────────────────────────────────────


def main():
    st.markdown(
        "<h1 style='font-size:32px;'>도시가스 공급량 – 일별 vs 월별 기온기반 3차 다항식 예측력 비교</h1>",
        unsafe_allow_html=True,
    )

    st.write("")

    # 0. 상관도 분석
    section_0_correlation()

    st.write("---")

    # (여기에 ①, ② 섹션: R² 비교 / 월별 예측 vs 실적 그래프 등을 이어서 배치 가능)

    # ③ 기온 매트릭스
    section_3_temp_matrix()


if __name__ == "__main__":
    main()
