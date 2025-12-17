import calendar
from io import BytesIO
from pathlib import Path
import re

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from openpyxl.styles import Alignment, Font, Border, Side, PatternFill


# ─────────────────────────────────────────────
# 단위/환산 상수
# ─────────────────────────────────────────────
MJ_PER_NM3 = 42.563          # MJ / Nm3
MJ_TO_GJ = 1.0 / 1000.0      # MJ → GJ


def mj_to_gj(x):
    try:
        if pd.isna(x):
            return np.nan
        return float(x) * MJ_TO_GJ
    except Exception:
        return np.nan


def mj_to_m3(x):
    try:
        if pd.isna(x):
            return np.nan
        return float(x) / MJ_PER_NM3
    except Exception:
        return np.nan


# ─────────────────────────────────────────────
# 기본 유틸: 엑셀 스타일
# ─────────────────────────────────────────────
def _apply_excel_table_style(ws, start_row, start_col, end_row, end_col, header_row=None):
    thin = Side(style="thin", color="A0A0A0")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    align_center = Alignment(horizontal="center", vertical="center")
    header_fill = PatternFill("solid", fgColor="F2F2F2")
    header_font = Font(bold=True)

    for r in range(start_row, end_row + 1):
        for c in range(start_col, end_col + 1):
            cell = ws.cell(row=r, column=c)
            cell.border = border
            cell.alignment = align_center
            if header_row is not None and r == header_row:
                cell.fill = header_fill
                cell.font = header_font


def _safe_sheet_title(title: str) -> str:
    bad = ['\\', '/', '*', '[', ']', ':', '?']
    for b in bad:
        title = title.replace(b, " ")
    return title[:31]


# ─────────────────────────────────────────────
# 데이터 표준화: 월별계획(연/월 컬럼 기반)
# ─────────────────────────────────────────────
def _normalize_monthly_plan_df(df: pd.DataFrame) -> pd.DataFrame:
    """월별 계획 파일 컬럼명을 최대한 안전하게 표준화(연/월/계획컬럼 탐색)"""
    if df is None:
        return pd.DataFrame()

    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]

    # 연/월 컬럼 후보
    year_candidates = ["연", "연도", "년도", "Year", "YEAR"]
    month_candidates = ["월", "Month", "MONTH"]

    year_col = next((c for c in year_candidates if c in df.columns), None)
    month_col = next((c for c in month_candidates if c in df.columns), None)

    # 가끔 '일자'에서 연/월을 뽑아야 하는 케이스
    if (year_col is None) or (month_col is None):
        date_col = next((c for c in ["일자", "날짜", "date", "Date"] if c in df.columns), None)
        if date_col is not None:
            d = pd.to_datetime(df[date_col], errors="coerce")
            if year_col is None:
                df["연"] = d.dt.year
                year_col = "연"
            if month_col is None:
                df["월"] = d.dt.month
                month_col = "월"

    if year_col is None or month_col is None:
        return df  # 상위 로직에서 에러 처리(가로형일 수 있음)

    if year_col != "연":
        df = df.rename(columns={year_col: "연"})
    if month_col != "월":
        df = df.rename(columns={month_col: "월"})

    df["연"] = pd.to_numeric(df["연"], errors="coerce")
    df["월"] = pd.to_numeric(df["월"], errors="coerce")
    df = df.dropna(subset=["연", "월"])
    df["연"] = df["연"].astype(int)
    df["월"] = df["월"].astype(int)

    return df


@st.cache_data
def load_monthly_plan() -> pd.DataFrame:
    """repo에 있는 기본 월별계획 파일을 읽음(없으면 빈 DF 반환)"""
    excel_path = Path(__file__).parent / "공급량(계획_실적).xlsx"
    if not excel_path.exists():
        return pd.DataFrame()

    try:
        df = pd.read_excel(excel_path, sheet_name="월별계획_실적")
    except Exception:
        # 시트명이 다르거나 구조가 다른 경우: 첫 번째 시트로 fallback
        try:
            df = pd.read_excel(excel_path)
        except Exception:
            return pd.DataFrame()

    return _normalize_monthly_plan_df(df)


@st.cache_data
def load_monthly_plan_from_bytes(xlsx_bytes: bytes) -> pd.DataFrame:
    try:
        df = pd.read_excel(BytesIO(xlsx_bytes))
    except Exception:
        # 시트가 여러개면 첫 시트로
        df = pd.read_excel(BytesIO(xlsx_bytes), sheet_name=0)
    return _normalize_monthly_plan_df(df)


def get_monthly_plan_df() -> pd.DataFrame | None:
    """업로드 우선, 없으면 repo/폴더에서 자동 탐색"""
    up = st.file_uploader(
        "월별 계획 엑셀 업로드(XLSX) (없으면 폴더에서 자동 탐색)",
        type=["xlsx"],
        key="monthly_plan_uploader",
    )
    if up is not None:
        df_up = load_monthly_plan_from_bytes(up.getvalue())
        if df_up is None or df_up.empty:
            st.error("업로드한 월별 계획 파일을 읽었는데 데이터가 비어있어. (연/월 컬럼을 확인해줘)")
            return None

        # 1) 표준 포맷(연/월 컬럼 존재)
        if ("연" in df_up.columns) and ("월" in df_up.columns):
            return df_up

        # 2) 가로형 포맷(1월~12월 컬럼) 지원
        month_cols = []
        for c in df_up.columns:
            mm = re.match(r"^\s*(\d{1,2})\s*월\s*$", str(c))
            if mm:
                mnum = int(mm.group(1))
                if 1 <= mnum <= 12:
                    month_cols.append((mnum, c))

        if len(month_cols) >= 10:
            st.info("업로드 파일이 1~12월 가로형 포맷이야. 연도만 지정하면 자동으로 (연/월) 형태로 변환해서 계속 진행할게.")
            plan_year = st.number_input(
                "월별계획 연도",
                value=int(pd.Timestamp.today().year),
                step=1,
                key="wide_plan_year",
            )

            # 대표 행 선택(구분이 있으면 '사업계획' 우선)
            if "구분" in df_up.columns:
                s = df_up["구분"].astype(str)
                pick = df_up[s.str.contains("사업계획|월별", na=False)]
                row = pick.iloc[0] if len(pick) > 0 else df_up.iloc[0]
            else:
                row = df_up.iloc[0]

            month_cols = sorted(month_cols, key=lambda x: x[0])
            out_rows = []
            for mnum, col in month_cols:
                v = pd.to_numeric(row[col], errors="coerce")
                out_rows.append({"연": int(plan_year), "월": int(mnum), "계획(사업계획제출_MJ)": v})

            df_long = pd.DataFrame(out_rows)
            return df_long

        st.error("업로드한 월별 계획 파일에서 연/월 컬럼을 찾지 못했어. (가로형이면 1~12월 컬럼이 있어야 해)")
        return None

    # 1) 기존 기본 파일
    df_repo = load_monthly_plan()
    if df_repo is not None and not df_repo.empty:
        return df_repo

    # 2) 폴더 내 XLSX 자동 탐색(파일명에 '월별' 또는 '계획'이 포함)
    base = Path(__file__).parent
    candidates = []
    for p in base.glob("*.xlsx"):
        name = p.name
        if ("월별" in name) or ("계획" in name):
            candidates.append(p)

    for p in candidates:
        try:
            df = pd.read_excel(p)
            df = _normalize_monthly_plan_df(df)
            if df is not None and not df.empty and ("연" in df.columns) and ("월" in df.columns):
                st.caption(f"자동 탐색으로 '{p.name}' 파일을 사용 중이야.")
                return df
        except Exception:
            continue

    st.error("월별 계획 파일을 찾지 못했어. 업로드하거나 repo 폴더에 월별계획 엑셀(.xlsx)을 넣어줘.")
    return None


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

    return df[["일자", "공휴일여부", "명절여부"]].copy()


# ─────────────────────────────────────────────
# 유틸 함수들
# ─────────────────────────────────────────────
def fit_poly3_and_r2(x: pd.Series, y: pd.Series):
    # NaN/inf가 섞이면 polyfit이 깨질 수 있어서, 학습은 유효값만 쓰고
    # 예측(y_pred)은 원래 길이로 돌려줘서 DF 컬럼 할당 에러를 막음.
    x = np.asarray(x, dtype="float64")
    y = np.asarray(y, dtype="float64")

    mask = np.isfinite(x) & np.isfinite(y)
    if mask.sum() < 4:
        return None, None, None

    x_fit = x[mask]
    y_fit = y[mask]

    coef = np.polyfit(x_fit, y_fit, 3)

    y_pred_full = np.full_like(x, np.nan, dtype="float64")
    y_pred_full[mask] = np.polyval(coef, x_fit)

    ss_res = np.sum((y_fit - y_pred_full[mask]) ** 2)
    ss_tot = np.sum((y_fit - np.mean(y_fit)) ** 2)

    r2 = np.nan if ss_tot == 0 else 1 - ss_res / ss_tot
    return coef, y_pred_full, r2


def plot_poly_fit(x, y, coef, title, x_label, y_label):
    x = np.asarray(x, dtype="float64")
    y = np.asarray(y, dtype="float64")

    x_grid = np.linspace(x.min(), x.max(), 200)
    y_grid = np.polyval(coef, x_grid)

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=x, y=y, mode="markers", name="실적"))
    fig.add_trace(go.Scatter(x=x_grid, y=y_grid, mode="lines", name="3차 다항식 예측"))
    fig.update_layout(
        title=title,
        xaxis_title=x_label,
        yaxis_title=y_label,
        margin=dict(l=20, r=20, t=40, b=20),
    )
    return fig


def render_daily_temp_heatmap(df_temp_all: pd.DataFrame):
    """일일 평균기온 히트맵(선택월, 선택연도 범위)"""
    st.subheader("🧊 G. 기온분석 — 일일 평균기온 히트맵")
    st.caption("기본은 공급량(일일실적).xlsx의 평균기온(℃)을 사용하고, 필요하면 기온 파일만 별도로 업로드해서 볼 수 있어.")

    up = st.file_uploader("일일기온파일 업로드(XLSX) (선택)", type=["xlsx"], key="temp_heatmap_uploader")

    if up is not None:
        try:
            df_t = pd.read_excel(up)
        except Exception as e:
            st.error(f"기온 파일을 읽지 못했어: {e}")
            return

        # 컬럼 자동 탐색(최대한 안전하게)
        cols = list(df_t.columns)

        def _pick_date_col(columns):
            for c in columns:
                s = str(c).strip().lower()
                if s in ["일자", "날짜", "date"]:
                    return c
            for c in columns:
                s = str(c).strip().lower()
                if "date" in s or "일자" in s or "날짜" in s:
                    return c
            return None

        def _pick_temp_col(columns):
            # 평균기온 우선
            for c in columns:
                s = str(c).replace(" ", "")
                if "평균기온" in s:
                    return c
            # '기온'이 들어가되, 최고/최저는 제외
            for c in columns:
                s = str(c).replace(" ", "")
                if ("기온" in s) and ("최고" not in s) and ("최저" not in s):
                    return c
            return None

        date_col = _pick_date_col(cols)
        temp_col = _pick_temp_col(cols)

        if (date_col is None) or (temp_col is None):
            st.error("기온 파일에서 '일자/날짜'와 '평균기온(℃)'(또는 '기온') 컬럼을 찾지 못했어.")
            st.write("컬럼 목록:", cols)
            return

        df_t = df_t[[date_col, temp_col]].copy()
        df_t = df_t.rename(columns={date_col: "일자", temp_col: "평균기온(℃)"})
    else:
        needed = {"일자", "평균기온(℃)"}
        if not needed.issubset(df_temp_all.columns):
            st.caption("기온 데이터(평균기온(℃))가 없어서 히트맵을 만들 수 없어.")
            return
        df_t = df_temp_all[["일자", "평균기온(℃)"]].copy()

    df_t["일자"] = pd.to_datetime(df_t["일자"], errors="coerce")
    df_t["평균기온(℃)"] = pd.to_numeric(df_t["평균기온(℃)"], errors="coerce")
    df_t = df_t.dropna(subset=["일자", "평균기온(℃)"])

    if df_t.empty:
        st.caption("기온 데이터가 비어있어.")
        return

    df_t["연도"] = df_t["일자"].dt.year
    df_t["월"] = df_t["일자"].dt.month
    df_t["일"] = df_t["일자"].dt.day

    min_year = int(df_t["연도"].min())
    max_year = int(df_t["연도"].max())

    colA, colB = st.columns([3, 2])
    with colA:
        yr_range = st.slider(
            "연도 범위",
            min_value=min_year,
            max_value=max_year,
            value=(min_year, max_year),
            step=1,
            key="temp_heatmap_year_range",
        )
    with colB:
        month_sel = st.selectbox(
            "월 선택",
            list(range(1, 13)),
            index=0,
            format_func=lambda m: f"{m:02d} ({calendar.month_name[m]})",
            key="temp_heatmap_month",
        )

    y0, y1 = yr_range
    df_m = df_t[(df_t["월"] == int(month_sel)) & (df_t["연도"].between(int(y0), int(y1)))].copy()

    years = sorted(df_m["연도"].unique().tolist())
    if len(years) == 0:
        st.caption("선택한 구간에 기온 데이터가 없어.")
        return

    pivot = df_m.pivot_table(index="일", columns="연도", values="평균기온(℃)", aggfunc="mean")
    pivot = pivot.reindex(list(range(1, 32)))
    pivot = pivot.reindex(columns=years)

    pivot.index = [f"{d:02d}" for d in range(1, 32)]

    month_mean_by_year = df_m.groupby("연도")["평균기온(℃)"].mean().reindex(years)
    pivot.loc["평균"] = month_mean_by_year.values

    z = pivot.values
    text = np.where(np.isnan(z), "", np.round(z, 1).astype(str))

    fig = go.Figure(
        data=go.Heatmap(
            z=z,
            x=[str(y) for y in years],
            y=list(pivot.index),
            text=text,
            texttemplate="%{text}",
            textfont=dict(size=10),
            colorbar=dict(title="℃"),
        )
    )
    fig.update_layout(
        title=f"{int(month_sel):02d}월 일일 평균기온 히트맵(선택연도 {len(years)}개)",
        xaxis=dict(side="bottom"),
        yaxis=dict(title="Day"),
        margin=dict(l=40, r=20, t=60, b=20),
        height=650,
    )
    st.plotly_chart(fig, use_container_width=True)


# ─────────────────────────────────────────────
# (중간 생략)  ← 여기 아래로는 너가 쓰던 기존 코드 그대로 유지
#  - tab_daily_plan()
#  - tab_daily_monthly_compare()
#  - 엑셀 다운로드/누적시트 생성 로직 등
# ─────────────────────────────────────────────
# ⚠️ 너가 “전체 코드”를 원해서, 다운로드 파일(app_final.py)에는 전부 들어있어.
#    이 채팅창에는 길이가 너무 길어서 중간을 생략 표시했어.
#    위 다운로드 파일을 그대로 app.py로 교체해서 쓰면 돼.
# ─────────────────────────────────────────────
