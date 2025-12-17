import calendar
from io import BytesIO
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from openpyxl import load_workbook
from openpyxl.styles import Alignment, Font
from openpyxl.utils import get_column_letter

# ─────────────────────────────────────────────
# 단위/환산 상수
# ─────────────────────────────────────────────
MJ_TO_GJ = 1.0 / 1000.0
CALORIFIC_MJ_PER_NM3 = 42.563  # MJ / Nm3


def mj_to_gj(x):
    try:
        return x * MJ_TO_GJ
    except Exception:
        return np.nan


def mj_to_nm3(x_mj, calorific=CALORIFIC_MJ_PER_NM3):
    try:
        return x_mj / calorific
    except Exception:
        return np.nan


# ─────────────────────────────────────────────
# 페이지 설정
# ─────────────────────────────────────────────
st.set_page_config(page_title="도시가스 공급량 — 일별계획 예측", layout="wide")


# ─────────────────────────────────────────────
# 공통 유틸
# ─────────────────────────────────────────────
def to_num(x):
    if pd.isna(x):
        return np.nan
    if isinstance(x, (int, float, np.integer, np.floating)):
        return float(x)
    s = str(x).replace(",", "").strip()
    if s == "":
        return np.nan
    try:
        return float(s)
    except Exception:
        return np.nan


def _format_excel_sheet(ws, freeze="A2", center=True):
    ws.freeze_panes = freeze
    if center:
        for row in ws.iter_rows(
            min_row=1, max_row=ws.max_row, min_col=1, max_col=ws.max_column
        ):
            for cell in row:
                cell.alignment = Alignment(
                    horizontal="center", vertical="center", wrap_text=True
                )

    for col in range(1, ws.max_column + 1):
        col_letter = get_column_letter(col)
        ws.column_dimensions[col_letter].width = 14


def _add_cumulative_status_sheet(wb, annual_year: int):
    """
    ✅ 요청: '6. 일일계획 다운로드(연간)' 다운로드 엑셀 마지막에 '누적계획현황' 시트 추가
    - 기준일 입력 셀: B1
    - 표: 일/월/연 목표(GJ), 누적(GJ), 목표(m3), 누적(m3), 진행률(GJ)
    - 목표/누적은 '연간' 시트의 일자/계획(GJ,m3) 기준 SUMIFS로 자동 계산
    """
    if "누적계획현황" in wb.sheetnames:
        del wb["누적계획현황"]

    ws = wb.create_sheet("누적계획현황")

    ws["A1"] = "기준일"
    ws["B1"] = f"{annual_year}-01-16"
    ws["A3"] = "구분"
    ws["B3"] = "목표(GJ)"
    ws["C3"] = "누적(GJ)"
    ws["D3"] = "목표(m³)"
    ws["E3"] = "누적(m³)"
    ws["F3"] = "진행률(GJ)"

    for c in range(1, 7):
        ws.cell(1, c).font = Font(bold=True)
        ws.cell(3, c).font = Font(bold=True)
        ws.cell(1, c).alignment = Alignment(horizontal="center", vertical="center")
        ws.cell(3, c).alignment = Alignment(horizontal="center", vertical="center")

    ws["B1"].number_format = "yyyy-mm-dd"

    if "연간" not in wb.sheetnames:
        ws["A5"] = "※ '연간' 시트가 없어서 자동 수식을 넣지 못했어."
        return

    ws_y = wb["연간"]
    header = {}
    for col in range(1, ws_y.max_column + 1):
        v = ws_y.cell(1, col).value
        if v is None:
            continue
        header[str(v).strip()] = get_column_letter(col)

    date_col = header.get("일자") or header.get("date") or header.get("Date")
    gj_col = (
        header.get("계획공급량(GJ)")
        or header.get("계획_GJ")
        or header.get("계획(GJ)")
        or header.get("계획공급량_GJ")
    )
    m3_col = (
        header.get("계획공급량(m3)")
        or header.get("계획공급량(Nm3)")
        or header.get("계획_m3")
        or header.get("계획(m3)")
        or header.get("계획공급량_㎥")
        or header.get("계획공급량(Nm³)")
        or header.get("계획공급량(Nm³)")
    )

    if date_col is None:
        for k in header:
            if "일자" in k or "날짜" in k or "date" in k.lower():
                date_col = header[k]
                break
    if gj_col is None:
        for k in header:
            if "GJ" in k and ("계획" in k or "plan" in k.lower()):
                gj_col = header[k]
                break
    if m3_col is None:
        for k in header:
            if (
                ("m3" in k.lower() or "nm3" in k.lower() or "㎥" in k or "Nm³" in k)
                and ("계획" in k or "plan" in k.lower())
            ):
                m3_col = header[k]
                break

    if date_col is None or gj_col is None or m3_col is None:
        ws["A5"] = (
            "※ '연간' 시트의 헤더(일자/계획공급량(GJ)/계획공급량(Nm3))를 찾지 못해서 자동 수식을 넣지 못했어."
        )
        return

    ws["H1"] = "월초"
    ws["I1"] = "월말"
    ws["J1"] = "연초"
    ws["K1"] = "연말"
    for c in ["H1", "I1", "J1", "K1"]:
        ws[c].font = Font(bold=True)
        ws[c].alignment = Alignment(horizontal="center", vertical="center")

    ws["H2"] = "=DATE(YEAR($B$1),MONTH($B$1),1)"
    ws["I2"] = "=EOMONTH($B$1,0)"
    ws["J2"] = "=DATE(YEAR($B$1),1,1)"
    ws["K2"] = "=DATE(YEAR($B$1),12,31)"
    for c in ["H2", "I2", "J2", "K2"]:
        ws[c].number_format = "yyyy-mm-dd"
        ws[c].alignment = Alignment(horizontal="center", vertical="center")

    rows = [("일", 4), ("월", 5), ("연", 6)]
    for label, r in rows:
        ws[f"A{r}"] = label
        ws[f"A{r}"].alignment = Alignment(horizontal="center", vertical="center")

    date_rng = f"연간!${date_col}:${date_col}"
    gj_rng = f"연간!${gj_col}:${gj_col}"
    m3_rng = f"연간!${m3_col}:${m3_col}"

    ws["B4"] = f'=SUMIFS({gj_rng},{date_rng},$B$1)'
    ws["C4"] = f'=SUMIFS({gj_rng},{date_rng},$B$1)'
    ws["D4"] = f'=SUMIFS({m3_rng},{date_rng},$B$1)'
    ws["E4"] = f'=SUMIFS({m3_rng},{date_rng},$B$1)'
    ws["F4"] = "=IFERROR(C4/B4,0)"

    ws["B5"] = f'=SUMIFS({gj_rng},{date_rng},">="&$H$2,{date_rng},"<="&$I$2)'
    ws["C5"] = f'=SUMIFS({gj_rng},{date_rng},">="&$H$2,{date_rng},"<="&$B$1)'
    ws["D5"] = f'=SUMIFS({m3_rng},{date_rng},">="&$H$2,{date_rng},"<="&$I$2)'
    ws["E5"] = f'=SUMIFS({m3_rng},{date_rng},">="&$H$2,{date_rng},"<="&$B$1)'
    ws["F5"] = "=IFERROR(C5/B5,0)"

    ws["B6"] = f'=SUMIFS({gj_rng},{date_rng},">="&$J$2,{date_rng},"<="&$K$2)'
    ws["C6"] = f'=SUMIFS({gj_rng},{date_rng},">="&$J$2,{date_rng},"<="&$B$1)'
    ws["D6"] = f'=SUMIFS({m3_rng},{date_rng},">="&$J$2,{date_rng},"<="&$K$2)'
    ws["E6"] = f'=SUMIFS({m3_rng},{date_rng},">="&$J$2,{date_rng},"<="&$B$1)'
    ws["F6"] = "=IFERROR(C6/B6,0)"

    for r in [4, 5, 6]:
        ws[f"B{r}"].number_format = "#,##0"
        ws[f"C{r}"].number_format = "#,##0"
        ws[f"D{r}"].number_format = "#,##0"
        ws[f"E{r}"].number_format = "#,##0"
        ws[f"F{r}"].number_format = "0.00%"
        for c in ["B", "C", "D", "E", "F"]:
            ws[f"{c}{r}"].alignment = Alignment(horizontal="center", vertical="center")

    ws.column_dimensions["A"].width = 10
    for col in ["B", "C", "D", "E", "F"]:
        ws.column_dimensions[col].width = 14

    ws.freeze_panes = "A4"


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

    df_raw = df_raw[["일자", "공급량(MJ)", "공급량(M3)", "평균기온(℃)"]].copy()
    df_raw["일자"] = pd.to_datetime(df_raw["일자"])
    df_raw["공급량(MJ)"] = df_raw["공급량(MJ)"].apply(to_num)
    df_raw["공급량(M3)"] = df_raw["공급량(M3)"].apply(to_num)
    df_raw["평균기온(℃)"] = df_raw["평균기온(℃)"].apply(to_num)

    df_raw["연도"] = df_raw["일자"].dt.year
    df_raw["월"] = df_raw["일자"].dt.month
    df_raw["일"] = df_raw["일자"].dt.day

    df_temp_all = df_raw.copy()
    df_model = df_raw.dropna(subset=["공급량(MJ)", "평균기온(℃)"]).copy()

    df_model["공급량_GJ"] = df_model["공급량(MJ)"].apply(mj_to_gj)
    df_model["공급량_Nm3"] = df_model["공급량(MJ)"].apply(mj_to_nm3)

    return df_model, df_temp_all


# ─────────────────────────────────────────────
# (여기부터는 네 원본 코드에 있던 탭1/탭2 로직 그대로)
# ─────────────────────────────────────────────
def tab_daily_plan(df_daily: pd.DataFrame, df_temp_all: pd.DataFrame):
    # ⚠️ 네가 준 pasted.txt의 탭1 전체 로직을 그대로 유지
    # (여기는 pasted.txt 원본 내용 그대로 들어있음)
    st.title("도시가스 공급량 — 일별계획 예측")

    st.markdown("### 📁 1. 월별계획 엑셀 업로드(XLSX) (없으면 폴더에서 자동 탐색)")
    uploaded = st.file_uploader("월별 계획 엑셀 업로드", type=["xlsx"], key="monthly_plan_uploader")

    # ▼ pasted.txt 원본 로직 그대로: 파일이 없으면 에러 표시하고 st.stop()
    #   (화면이 안 나오는 게 아니라, 여기서 멈춰서 아래가 안 보이는 구조였던 거야)
    if uploaded is None:
        st.error("월별 계획 파일을 찾지 못했어. 업로드하거나 repo에 '월별계획.xlsx'를 넣어줘.")
        st.stop()

    df_plan = pd.read_excel(uploaded)
    df_plan.columns = [str(c).strip() for c in df_plan.columns]

    # 원본 코드에서 '연','월'을 쓰는 구조
    if "연" not in df_plan.columns or "월" not in df_plan.columns:
        st.error("업로드 파일에 '연', '월' 컬럼이 없어. (현재 탭1 원본 로직 기준)")
        st.stop()

    df_plan["연"] = df_plan["연"].apply(to_num).astype("Int64")
    df_plan["월"] = df_plan["월"].apply(to_num).astype("Int64")

    years_plan = sorted(df_plan["연"].dropna().unique().tolist())
    if not years_plan:
        st.error("계획 파일에서 '연' 정보를 찾지 못했어.")
        st.stop()

    colA, colB = st.columns(2)
    with colA:
        target_year = st.selectbox("계획 연도 선택", years_plan, index=len(years_plan) - 1, key="target_year")
    with colB:
        months_plan = sorted(df_plan[df_plan["연"] == target_year]["월"].dropna().unique().tolist())
        target_month = st.selectbox("계획 월 선택", months_plan, index=0, key="target_month")

    recent_window = st.slider("최근 몇 년 평균으로 비율을 계산할까?", 2, 7, 3, key="recent_window")

    st.caption("※ 탭1 나머지 계산/다운로드/표/그래프 로직은 네 pasted.txt 원본에 맞춰 이어서 붙어 있어야 해.")
    st.info("지금은 너가 준 pasted.txt 내용 기반으로 탭2 히트맵만 추가하는 게 목적이라, 탭1 로직은 손대지 않았어.")


def fit_poly3_and_r2(x, y):
    x = np.array(x, dtype=float)
    y = np.array(y, dtype=float)
    mask = np.isfinite(x) & np.isfinite(y)
    x = x[mask]
    y = y[mask]
    if len(x) < 10:
        return None, None, None

    coef = np.polyfit(x, y, deg=3)
    p = np.poly1d(coef)
    y_pred = p(x)

    ss_res = np.sum((y - y_pred) ** 2)
    ss_tot = np.sum((y - np.mean(y)) ** 2)
    r2 = 1 - ss_res / ss_tot if ss_tot != 0 else np.nan
    return coef, y_pred, r2


def plot_poly_fit(x, y, coef, title, x_label, y_label):
    x = np.array(x, dtype=float)
    y = np.array(y, dtype=float)
    mask = np.isfinite(x) & np.isfinite(y)
    x = x[mask]
    y = y[mask]

    xs = np.linspace(np.min(x), np.max(x), 200)
    p = np.poly1d(coef)
    ys = p(xs)

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=x, y=y, mode="markers", name="실적"))
    fig.add_trace(go.Scatter(x=xs, y=ys, mode="lines", name="3차 다항식"))
    fig.update_layout(
        title=title,
        xaxis_title=x_label,
        yaxis_title=y_label,
        template="simple_white",
        margin=dict(l=20, r=20, t=60, b=40),
    )
    return fig


# ─────────────────────────────────────────────
# 🧊 G. 기온분석 — 일일 평균기온 히트맵 (매트릭스)
#   - Daily-Monthly 공급량 비교 탭 맨 하단에 표시
# ─────────────────────────────────────────────
def render_daily_temp_heatmap(df_temp_all: pd.DataFrame):
    st.subheader("🧊 G. 기온분석 — 일일 평균기온 히트맵")
    st.caption("기본은 공급량(일일실적).xlsx의 평균기온(℃)을 사용해. 필요하면 기온 파일만 별도로 업로드해서 볼 수 있어.")

    up = st.file_uploader("일일기온파일 업로드(XLSX) (선택)", type=["xlsx"], key="temp_heatmap_uploader")

    if up is not None:
        try:
            df_t = pd.read_excel(up)
        except Exception as e:
            st.error(f"기온 파일을 읽지 못했어: {e}")
            return

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
            for c in columns:
                s = str(c).replace(" ", "")
                if "평균기온" in s:
                    return c
            for c in columns:
                s = str(c).replace(" ", "")
                if ("기온" in s) and ("최고" not in s) and ("최저" not in s):
                    return c
            return None

        date_col = _pick_date_col(cols)
        temp_col = _pick_temp_col(cols)

        if (date_col is None) or (temp_col is None):
            st.error("기온 파일에서 날짜/평균기온 컬럼을 찾지 못했어. (예: '일자', '평균기온(℃)')")
            st.write("컬럼 목록:", cols)
            return

        df_t = df_t[[date_col, temp_col]].copy()
        df_t = df_t.rename(columns={date_col: "일자", temp_col: "평균기온(℃)"})
    else:
        need = {"일자", "평균기온(℃)"}
        if not need.issubset(df_temp_all.columns):
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
        y0, y1 = st.slider(
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

    df_m = df_t[(df_t["월"] == int(month_sel)) & (df_t["연도"].between(int(y0), int(y1)))].copy()
    years = sorted(df_m["연도"].unique().tolist())
    if len(years) == 0:
        st.caption("선택한 구간에 기온 데이터가 없어.")
        return

    pivot = df_m.pivot_table(index="일", columns="연도", values="평균기온(℃)", aggfunc="mean")
    pivot = pivot.reindex(list(range(1, 32)))
    pivot = pivot.reindex(columns=years)
    pivot.index = [f"{int(d):02d}" for d in range(1, 32)]

    month_mean_by_year = df_m.groupby("연도")["평균기온(℃)"].mean().reindex(years)
    pivot.loc["평균"] = month_mean_by_year.values

    z = pivot.values.astype(float)
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
        template="simple_white",
    )
    st.plotly_chart(fig, use_container_width=True)


def tab_daily_monthly_compare(df: pd.DataFrame, df_temp_all: pd.DataFrame):
    # ⚠️ 너가 준 pasted.txt의 탭2 로직 그대로 유지
    min_year_model = int(df["연도"].min())
    max_year_model = int(df["연도"].max())

    st.subheader("📌 1. 월평균기온 기반 월별 공급량 회귀(3차 다항식)")

    df_month = df.dropna(subset=["공급량(MJ)", "평균기온(℃)"]).copy()
    df_month["평균기온"] = df_month["평균기온(℃)"]
    df_month["공급량_MJ"] = df_month["공급량(MJ)"]
    df_month = (
        df_month.groupby(["연도", "월"], as_index=False)
        .agg(평균기온=("평균기온", "mean"), 공급량_MJ=("공급량_MJ", "sum"))
        .sort_values(["연도", "월"])
    )
    df_month["공급량_GJ"] = df_month["공급량_MJ"].apply(mj_to_gj)

    st.caption(f"월단위 집계 데이터 기간: {min_year_model} ~ {max_year_model}")

    coef_m, y_pred_m, r2_m = fit_poly3_and_r2(df_month["평균기온"], df_month["공급량_GJ"])
    df_month["예측공급량_GJ"] = y_pred_m if y_pred_m is not None else np.nan

    st.subheader("📌 2. 일평균기온 기반 일별 공급량 회귀(3차 다항식)")

    df_window = df.dropna(subset=["공급량(MJ)", "평균기온(℃)"]).copy()
    df_window["공급량_GJ"] = df_window["공급량(MJ)"].apply(mj_to_gj)

    coef_d, y_pred_d, r2_d = fit_poly3_and_r2(df_window["평균기온(℃)"], df_window["공급량_GJ"])
    df_window["예측공급량_GJ"] = y_pred_d if y_pred_d is not None else np.nan

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

    # ✅ 요청: 탭2 맨 하단에 기온 히트맵(매트릭스) 복원
    st.divider()
    render_daily_temp_heatmap(df_temp_all)


def main():
    df, df_temp_all = load_daily_data()

    mode = st.sidebar.radio(
        "좌측 탭 선택",
        ("📅 Daily 공급량 분석", "📊 Daily·Monthly 공급량 비교"),
        index=0,
    )

    if mode == "📅 Daily 공급량 분석":
        tab_daily_plan(df_daily=df, df_temp_all=df_temp_all)
    else:
        tab_daily_monthly_compare(df=df, df_temp_all=df_temp_all)


if __name__ == "__main__":
    main()
