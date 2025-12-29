import calendar
from io import BytesIO
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px  # 상관도 히트맵용 추가
import streamlit as st
from openpyxl.styles import Alignment, Font, Border, Side, PatternFill

# ─────────────────────────────────────────────
# 1. 단위/환산 상수
# ─────────────────────────────────────────────
MJ_PER_NM3 = 42.563          # MJ / Nm3
MJ_TO_GJ = 1.0 / 1000.0      # MJ → GJ

def mj_to_gj(x):
    try:
        return float(x) * MJ_TO_GJ
    except Exception:
        return np.nan

def mj_to_m3(x):
    try:
        return float(x) / MJ_PER_NM3
    except Exception:
        return np.nan
        
def gj_to_mj(x):
    try:
        return float(x) / MJ_TO_GJ
    except Exception:
        return np.nan

# ─────────────────────────────────────────────
# 2. 기본 설정 및 데이터 로딩 (유연성 강화)
# ─────────────────────────────────────────────
st.set_page_config(
    page_title="도시가스 공급량 예측 및 분석 시스템",
    layout="wide",
)

def standardize_columns(df):
    """
    컬럼명이 조금 달라도(띄어쓰기 등) 표준 컬럼명으로 변환해주는 함수
    """
    col_map = {}
    for c in df.columns:
        cs = str(c).replace(" ", "").strip() # 공백제거 후 비교
        if cs in ["일자", "date", "Date", "날짜"]:
            col_map[c] = "일자"
        elif "공급량" in cs and "MJ" in cs:
            col_map[c] = "공급량(MJ)"
        elif "공급량" in cs and ("GJ" in cs or "Gj" in cs):
            col_map[c] = "공급량(GJ)"
        elif "평균" in cs and ("기온" in cs or "온도" in cs):
            col_map[c] = "평균기온(℃)"
        elif cs in ["연", "연도", "Year"]:
            col_map[c] = "연도"
        elif cs in ["월", "Month"]:
            col_map[c] = "월"
        elif cs in ["일", "Day"]:
            col_map[c] = "일"
    return df.rename(columns=col_map)

@st.cache_data(show_spinner=False)
def load_daily_data(uploaded_file):
    """일일 실적 로딩: 업로드 파일 우선, 없으면 로컬 파일 탐색"""
    if uploaded_file is not None:
        df_raw = pd.read_excel(uploaded_file)
    else:
        # 파일이 없으면 빈 데이터프레임 반환 (에러 방지)
        excel_path = Path(__file__).parent / "공급량(일일실적).xlsx"
        if excel_path.exists():
            df_raw = pd.read_excel(excel_path)
        else:
            return pd.DataFrame(), pd.DataFrame()

    # 컬럼 표준화
    df_raw = standardize_columns(df_raw)
    
    # 필수 컬럼 체크
    if "일자" not in df_raw.columns:
        return pd.DataFrame(), pd.DataFrame()

    # 내부 계산은 MJ 유지 (표기/다운로드는 GJ 및 m³로 변환)
    # GJ만 있고 MJ가 없는 경우 환산
    if "공급량(MJ)" not in df_raw.columns and "공급량(GJ)" in df_raw.columns:
        df_raw["공급량(MJ)"] = df_raw["공급량(GJ)"].apply(gj_to_mj)

    # 필요한 컬럼만 추출 (없으면 생성)
    cols_to_keep = ["일자", "공급량(MJ)", "평균기온(℃)"]
    for c in cols_to_keep:
        if c not in df_raw.columns:
            df_raw[c] = np.nan
            
    df_raw["일자"] = pd.to_datetime(df_raw["일자"], errors='coerce')
    df_raw = df_raw.dropna(subset=["일자"])

    df_raw["연도"] = df_raw["일자"].dt.year
    df_raw["월"] = df_raw["일자"].dt.month
    df_raw["일"] = df_raw["일자"].dt.day

    df_temp_all = df_raw.dropna(subset=["평균기온(℃)"]).copy()
    df_model = df_raw.dropna(subset=["공급량(MJ)"]).copy()
    
    return df_model, df_temp_all

@st.cache_data(show_spinner=False)
def load_monthly_plan(uploaded_file) -> pd.DataFrame:
    """월별 계획 로딩"""
    if uploaded_file is not None:
        df = pd.read_excel(uploaded_file)
    else:
        excel_path = Path(__file__).parent / "공급량(계획_실적).xlsx"
        if excel_path.exists():
            df = pd.read_excel(excel_path)  # 시트명 지정 필요시 수정
        else:
            return pd.DataFrame()
            
    df = standardize_columns(df)
    
    # 연, 월 정수형 변환
    if "연도" in df.columns: df["연도"] = pd.to_numeric(df["연도"], errors='coerce')
    if "월" in df.columns: df["월"] = pd.to_numeric(df["월"], errors='coerce')
    
    return df

@st.cache_data(show_spinner=False)
def load_effective_calendar() -> pd.DataFrame | None:
    excel_path = Path(__file__).parent / "effective_days_calendar.xlsx"
    if not excel_path.exists():
        return None

    df = pd.read_excel(excel_path)
    # 컬럼 표준화 로직 적용 가능
    if "날짜" in df.columns:
        df["일자"] = pd.to_datetime(df["날짜"].astype(str), format="%Y%m%d", errors="coerce")
    elif "일자" in df.columns:
        df["일자"] = pd.to_datetime(df["일자"], errors="coerce")
    else:
        return None

    for col in ["공휴일여부", "명절여부"]:
        if col not in df.columns:
            df[col] = False

    df["공휴일여부"] = df["공휴일여부"].fillna(False).astype(bool)
    df["명절여부"] = df["명절여부"].fillna(False).astype(bool)

    return df[["일자", "공휴일여부", "명절여부"]].copy()


# ─────────────────────────────────────────────
# 3. 유틸 함수들 (수학, 테이블 포맷팅)
# ─────────────────────────────────────────────
def fit_poly3_and_r2(x: pd.Series, y: pd.Series):
    x = np.asarray(x, dtype="float64")
    y = np.asarray(y, dtype="float64")
    # 데이터 부족 시 예외처리
    if len(x) < 4:
        return None, None, None

    try:
        coef = np.polyfit(x, y, 3)
        y_pred = np.polyval(coef, x)

        ss_res = np.sum((y - y_pred) ** 2)
        ss_tot = np.sum((y - np.mean(y)) ** 2)

        r2 = np.nan if ss_tot == 0 else 1 - ss_res / ss_tot
        return coef, y_pred, r2
    except:
        return None, None, None


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


def format_table_generic(df, percent_cols=None, temp_cols=None):
    df = df.copy()
    percent_cols = percent_cols or []
    temp_cols = temp_cols or []

    def _fmt_no_comma(x):
        if pd.isna(x): return ""
        try: return f"{int(x)}"
        except: return str(x)

    for col in df.columns:
        if df[col].dtype == bool:
            df[col] = df[col].map(lambda x: "공휴일" if x else "")
            continue

        if col in percent_cols:
            df[col] = df[col].map(lambda x: f"{x:.4f}" if pd.notna(x) else "")
        elif col in temp_cols:
            df[col] = df[col].map(lambda x: f"{x:.2f}" if pd.notna(x) else "")
        elif pd.api.types.is_numeric_dtype(df[col]):
            if col in ["연", "연도", "월", "일"]:
                df[col] = df[col].map(_fmt_no_comma)
            else:
                df[col] = df[col].map(lambda x: f"{x:,.0f}" if pd.notna(x) else "")
    return df


def show_table_no_index(df: pd.DataFrame, height: int = 260):
    # 인덱스 숨기고 깔끔하게 보여주기
    st.dataframe(df, use_container_width=True, hide_index=True, height=height)


def _format_excel_sheet(ws, freeze="A2", center=True, width_map=None):
    if freeze:
        ws.freeze_panes = freeze

    if center:
        for row in ws.iter_rows(min_row=1, max_row=ws.max_row, min_col=1, max_col=ws.max_column):
            for c in row:
                c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    if width_map:
        for col_letter, w in width_map.items():
            ws.column_dimensions[col_letter].width = w


def _find_plan_col(df_plan: pd.DataFrame) -> str:
    # 계획 컬럼 찾기 (유연하게)
    candidates = [
        "계획(사업계획제출_MJ)", "계획(사업계획제출)", "계획_MJ", "계획", "계획량", "월별계획"
    ]
    for c in candidates:
        if c in df_plan.columns:
            return c
    # 숫자형 컬럼 중 첫번째를 계획으로 간주
    nums = [c for c in df_plan.columns if pd.api.types.is_numeric_dtype(df_plan[c]) and c not in ["연도", "월"]]
    return nums[0] if nums else "계획(사업계획제출_MJ)"


def make_month_plan_horizontal(df_plan: pd.DataFrame, target_year: int, plan_col: str) -> pd.DataFrame:
    if df_plan.empty: return pd.DataFrame()
    
    df_year = df_plan[df_plan["연도"] == target_year][["월", plan_col]].copy()
    base = pd.DataFrame({"월": list(range(1, 13))})
    df_year = base.merge(df_year, on="월", how="left")

    df_year = df_year.rename(columns={plan_col: "월별 계획(MJ)"})
    total_mj = df_year["월별 계획(MJ)"].sum(skipna=True)

    df_year["월별 계획(GJ)"] = (df_year["월별 계획(MJ)"].apply(mj_to_gj)).round(0)
    df_year["월별 계획(㎥)"] = (df_year["월별 계획(MJ)"].apply(mj_to_m3)).round(0)

    total_gj = mj_to_gj(total_mj)
    total_m3 = mj_to_m3(total_mj)

    row_gj = {}
    row_m3 = {}
    for m in range(1, 13):
        try:
            v_gj = df_year.loc[df_year["월"] == m, "월별 계획(GJ)"].iloc[0]
            v_m3 = df_year.loc[df_year["월"] == m, "월별 계획(㎥)"].iloc[0]
            row_gj[f"{m}월"] = v_gj
            row_m3[f"{m}월"] = v_m3
        except:
            row_gj[f"{m}월"] = 0
            row_m3[f"{m}월"] = 0

    row_gj["연간합계"] = round(total_gj, 0) if pd.notna(total_gj) else np.nan
    row_m3["연간합계"] = round(total_m3, 0) if pd.notna(total_m3) else np.nan

    out = pd.DataFrame([row_gj, row_m3])
    out.insert(0, "구분", ["사업계획(월별 계획, GJ)", "사업계획(월별 계획, ㎥)"])
    return out


# ─────────────────────────────────────────────
# 4. 엑셀: 누적계획현황 시트 추가 (고급 기능)
# ─────────────────────────────────────────────
def _add_cumulative_status_sheet(wb, annual_year: int):
    """
    마지막 시트에 '누적계획현황'을 추가.
    B1 기준일 입력 → 일/월/연 목표·누적(GJ, m³) + 진행률 자동 계산 엑셀 수식 삽입
    """
    sheet_name = "누적계획현황"
    if sheet_name in wb.sheetnames:
        return

    ws = wb.create_sheet(sheet_name)

    thin = Side(style="thin", color="999999")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    header_fill = PatternFill("solid", fgColor="F2F2F2")

    ws["A1"] = "기준일"
    ws["A1"].font = Font(bold=True)
    ws["A1"].alignment = Alignment(horizontal="center", vertical="center")

    ws["B1"] = pd.Timestamp(f"{annual_year}-01-01").to_pydatetime()
    ws["B1"].number_format = "yyyy-mm-dd"
    ws["B1"].alignment = Alignment(horizontal="center", vertical="center")
    ws["B1"].font = Font(bold=True)

    headers = ["구분", "목표(GJ)", "누적(GJ)", "목표(m³)", "누적(m³)", "진행률(GJ)"]
    start_row = 3
    for j, h in enumerate(headers, start=1):
        c = ws.cell(row=start_row, column=j, value=h)
        c.font = Font(bold=True)
        c.fill = header_fill
        c.alignment = Alignment(horizontal="center", vertical="center")
        c.border = border

    rows = [("일", 4), ("월", 5), ("연", 6)]
    for label, r in rows:
        ws.cell(row=r, column=1, value=label).alignment = Alignment(horizontal="center", vertical="center")
        ws.cell(row=r, column=1).border = border

    d = "$B$1"
    # 엑셀 수식 주입 (연간 시트 참조)
    ws["B4"] = f'=IFERROR(XLOOKUP({d},연간!$D:$D,연간!$O:$O),"")'
    ws["C4"] = "=B4"
    ws["D4"] = f'=IFERROR(XLOOKUP({d},연간!$D:$D,연간!$P:$P),"")'
    ws["E4"] = "=D4"
    ws["F4"] = '=IFERROR(IF(B4=0,"",C4/B4),"")'

    ws["B5"] = f'=SUMIFS(연간!$O:$O,연간!$A:$A,YEAR({d}),연간!$B:$B,MONTH({d}))'
    ws["C5"] = f'=SUMIFS(연간!$O:$O,연간!$D:$D,">="&EOMONTH({d},-1)+1,연간!$D:$D,"<="&{d})'
    ws["D5"] = f'=SUMIFS(연간!$P:$P,연간!$A:$A,YEAR({d}),연간!$B:$B,MONTH({d}))'
    ws["E5"] = f'=SUMIFS(연간!$P:$P,연간!$D:$D,">="&EOMONTH({d},-1)+1,연간!$D:$D,"<="&{d})'
    ws["F5"] = '=IFERROR(IF(B5=0,"",C5/B5),"")'

    ws["B6"] = f'=SUMIFS(연간!$O:$O,연간!$A:$A,YEAR({d}))'
    ws["C6"] = f'=SUMIFS(연간!$O:$O,연간!$D:$D,">="&DATE(YEAR({d}),1,1),연간!$D:$D,"<="&{d})'
    ws["D6"] = f'=SUMIFS(연간!$P:$P,연간!$A:$A,YEAR({d}))'
    ws["E6"] = f'=SUMIFS(연간!$P:$P,연간!$D:$D,">="&DATE(YEAR({d}),1,1),연간!$D:$D,"<="&{d})'
    ws["F6"] = '=IFERROR(IF(B6=0,"",C6/B6),"")'

    for r in range(4, 7):
        for c in range(2, 6):  # B~E
            cell = ws.cell(row=r, column=c)
            cell.number_format = "#,##0"
            cell.alignment = Alignment(horizontal="center", vertical="center")
            cell.border = border

        pct = ws.cell(row=r, column=6)  # F
        pct.number_format = "0.00%"
        pct.alignment = Alignment(horizontal="center", vertical="center")
        pct.border = border

    for r in range(start_row, 7):
        ws.cell(row=r, column=1).border = border
        ws.cell(row=r, column=1).alignment = Alignment(horizontal="center", vertical="center")

    ws.column_dimensions["A"].width = 10
    ws.column_dimensions["B"].width = 16
    ws.column_dimensions["C"].width = 16
    ws.column_dimensions["D"].width = 16
    ws.column_dimensions["E"].width = 16
    ws.column_dimensions["F"].width = 14
    ws.freeze_panes = "A4"


# ─────────────────────────────────────────────
# 5. 핵심 로직: Daily 공급량 분석 및 계획 생성
# ─────────────────────────────────────────────
def make_daily_plan_table(
    df_daily: pd.DataFrame,
    df_plan: pd.DataFrame,
    target_year: int = 2026,
    target_month: int = 1,
    recent_window: int = 3,
):
    cal_df = load_effective_calendar()
    plan_col = _find_plan_col(df_plan)

    # 과거 실적 연도 찾기
    all_years = sorted(df_daily["연도"].unique())
    start_year = target_year - recent_window
    candidate_years = [y for y in range(start_year, target_year) if y in all_years]
    
    if len(candidate_years) == 0:
        return None, None, [], pd.DataFrame()

    # 해당 월의 실적이 있는 연도만 필터링
    df_pool = df_daily[(df_daily["연도"].isin(candidate_years)) & (df_daily["월"] == target_month)].copy()
    df_pool = df_pool.dropna(subset=["공급량(MJ)"])
    used_years = sorted(df_pool["연도"].unique().tolist())
    
    if len(used_years) == 0:
        return None, None, [], pd.DataFrame()

    df_recent = df_daily[(df_daily["연도"].isin(used_years)) & (df_daily["월"] == target_month)].copy()
    df_recent = df_recent.dropna(subset=["공급량(MJ)"])
    
    # 요일 패턴 계산
    df_recent = df_recent.sort_values(["연도", "일"]).copy()
    df_recent["weekday_idx"] = df_recent["일자"].dt.weekday  # 0=월, 6=일
    
    # 공휴일 처리 로직
    if cal_df is not None:
        df_recent = df_recent.merge(cal_df, on="일자", how="left")
        df_recent["공휴일여부"] = df_recent["공휴일여부"].fillna(False)
        df_recent["명절여부"] = df_recent["명절여부"].fillna(False)
    else:
        df_recent["공휴일여부"] = False
        df_recent["명절여부"] = False

    df_recent["is_holiday"] = df_recent["공휴일여부"] | df_recent["명절여부"]
    df_recent["is_weekend"] = (df_recent["weekday_idx"] >= 5) | df_recent["is_holiday"]
    df_recent["is_weekday1"] = (~df_recent["is_weekend"]) & (df_recent["weekday_idx"].isin([0, 4])) # 월금
    df_recent["is_weekday2"] = (~df_recent["is_weekend"]) & (df_recent["weekday_idx"].isin([1, 2, 3])) # 화수목

    df_recent["month_total"] = df_recent.groupby("연도")["공급량(MJ)"].transform("sum")
    df_recent["ratio"] = df_recent["공급량(MJ)"] / df_recent["month_total"]

    # n번째 요일
    df_recent["nth_dow"] = df_recent.sort_values(["연도", "일"]).groupby(["연도", "weekday_idx"]).cumcount() + 1

    # 그룹별 평균 비율 계산
    weekend_mask = df_recent["is_weekend"]
    w1_mask = df_recent["is_weekday1"]
    w2_mask = df_recent["is_weekday2"]

    # 그룹(주말/평일1/평일2) 및 요일/주차별 dict 생성
    def make_ratio_dict(mask):
        if df_recent[mask].size == 0: return {}
        return df_recent[mask].groupby(["weekday_idx", "nth_dow"])["ratio"].mean().to_dict()
    
    def make_dow_dict(mask):
        if df_recent[mask].size == 0: return {}
        return df_recent[mask].groupby("weekday_idx")["ratio"].mean().to_dict()

    ratio_weekend_group_dict = make_ratio_dict(weekend_mask)
    ratio_weekend_by_dow_dict = make_dow_dict(weekend_mask)
    ratio_w1_group_dict = make_ratio_dict(w1_mask)
    ratio_w1_by_dow_dict = make_dow_dict(w1_mask)
    ratio_w2_group_dict = make_ratio_dict(w2_mask)
    ratio_w2_by_dow_dict = make_dow_dict(w2_mask)

    # ─────────────────────────────────────────
    # Target 생성
    # ─────────────────────────────────────────
    last_day = calendar.monthrange(target_year, target_month)[1]
    date_range = pd.date_range(f"{target_year}-{target_month:02d}-01", periods=last_day, freq="D")

    df_target = pd.DataFrame({"일자": date_range})
    df_target["연"] = target_year
    df_target["월"] = target_month
    df_target["일"] = df_target["일자"].dt.day
    df_target["weekday_idx"] = df_target["일자"].dt.weekday

    if cal_df is not None:
        df_target = df_target.merge(cal_df, on="일자", how="left")
        df_target["공휴일여부"] = df_target["공휴일여부"].fillna(False)
        df_target["명절여부"] = df_target["명절여부"].fillna(False)
    else:
        df_target["공휴일여부"] = False
        df_target["명절여부"] = False

    df_target["is_holiday"] = df_target["공휴일여부"] | df_target["명절여부"]
    df_target["is_weekend"] = (df_target["weekday_idx"] >= 5) | df_target["is_holiday"]
    df_target["is_weekday1"] = (~df_target["is_weekend"]) & (df_target["weekday_idx"].isin([0, 4]))
    df_target["is_weekday2"] = (~df_target["is_weekend"]) & (df_target["weekday_idx"].isin([1, 2, 3]))

    weekday_names = ["월", "화", "수", "목", "금", "토", "일"]
    df_target["요일"] = df_target["weekday_idx"].map(lambda i: weekday_names[i])
    df_target["nth_dow"] = df_target.sort_values("일").groupby("weekday_idx").cumcount() + 1

    def _label(row):
        if row["is_weekend"]: return "주말/공휴일"
        if row["is_weekday1"]: return "평일1(월·금)"
        return "평일2(화·수·목)"

    df_target["구분"] = df_target.apply(_label, axis=1)

    # 비율 매핑
    def _pick_ratio(row):
        dow = int(row["weekday_idx"])
        nth = int(row["nth_dow"])
        key = (dow, nth)

        if bool(row["is_weekend"]):
            v = ratio_weekend_group_dict.get(key, None)
            if v is None: v = ratio_weekend_by_dow_dict.get(dow, None)
            return v
        
        if bool(row["is_weekday1"]):
            v = ratio_w1_group_dict.get(key, None)
            if v is None: v = ratio_w1_by_dow_dict.get(dow, None)
            return v

        v = ratio_w2_group_dict.get(key, None)
        if v is None: v = ratio_w2_by_dow_dict.get(dow, None)
        return v

    df_target["raw"] = df_target.apply(_pick_ratio, axis=1).astype("float64")
    
    # 결측치 보정 (전체 평균)
    overall_mean = df_target["raw"].mean()
    if pd.isna(overall_mean): overall_mean = 1.0 / last_day
    df_target["raw"] = df_target["raw"].fillna(overall_mean)
    
    # 정규화
    raw_sum = df_target["raw"].sum()
    df_target["일별비율"] = (df_target["raw"] / raw_sum) if raw_sum > 0 else (1.0 / last_day)

    # 통계용 컬럼
    month_total_all = df_recent["공급량(MJ)"].sum()
    df_target["최근N년_총공급량(MJ)"] = df_target["일별비율"] * month_total_all
    df_target["최근N년_평균공급량(MJ)"] = df_target["최근N년_총공급량(MJ)"] / len(used_years)

    # 계획량 적용
    row_plan = df_plan[(df_plan["연도"] == target_year) & (df_plan["월"] == target_month)]
    plan_total = 0
    if not row_plan.empty:
        val = row_plan[plan_col].iloc[0]
        # 단위 보정 (값이 100만 이하면 GJ로 간주하여 MJ로 변환)
        plan_total = gj_to_mj(val) if val < 1000000 else val

    df_target["예상공급량(MJ)"] = (df_target["일별비율"] * plan_total).round(0)
    df_target = df_target.sort_values("일").reset_index(drop=True)

    df_result = df_target.copy()
    
    # 실적 매트릭스용
    df_mat = df_recent.pivot_table(index="일", columns="연도", values="공급량(MJ)", aggfunc="sum").sort_index().sort_index(axis=1)

    return df_result, df_mat, used_years, df_target


def _build_year_daily_plan(df_daily, df_plan, target_year, recent_window):
    """연간 전체 계획 생성용 (1~12월 반복)"""
    cal_df = load_effective_calendar()
    plan_col = _find_plan_col(df_plan)
    
    all_rows = []
    month_summary_rows = []

    for m in range(1, 13):
        df_res, _, _, _ = make_daily_plan_table(df_daily, df_plan, target_year, m, recent_window)
        
        # 데이터가 없어도 빈 틀은 만들어야 함
        if df_res is None:
            last_day = calendar.monthrange(target_year, m)[1]
            dr = pd.date_range(f"{target_year}-{m:02d}-01", periods=last_day, freq="D")
            df_res = pd.DataFrame({"일자": dr, "연": target_year, "월": m, "일": dr.day})
            df_res["예상공급량(MJ)"] = 0
            df_res["일별비율"] = 0
            df_res["weekday_idx"] = dr.weekday
            weekday_names = ["월", "화", "수", "목", "금", "토", "일"]
            df_res["요일"] = df_res["weekday_idx"].map(lambda i: weekday_names[i])
            df_res["구분"] = ""
            df_res["공휴일여부"] = False
            df_res["최근N년_평균공급량(MJ)"] = 0
            df_res["최근N년_총공급량(MJ)"] = 0

        # 결과 저장
        all_rows.append(df_res)
        
        # 월간 요약 저장
        row_plan = df_plan[(df_plan["연도"] == target_year) & (df_plan["월"] == m)]
        plan_val = 0
        if not row_plan.empty:
            val = row_plan[plan_col].iloc[0]
            plan_val = gj_to_mj(val) if val < 1000000 else val
            
        month_summary_rows.append({
            "월": m,
            "월간 계획(GJ)": round(mj_to_gj(plan_val), 0),
            "월간 계획(㎥)": round(mj_to_m3(plan_val), 0),
        })

    df_year = pd.concat(all_rows, ignore_index=True)
    
    # 단위 변환
    for col in ["최근N년_평균공급량", "최근N년_총공급량", "예상공급량"]:
        df_year[f"{col}(GJ)"] = df_year[f"{col}(MJ)"].apply(mj_to_gj).round(0)
        df_year[f"{col}(㎥)"] = df_year[f"{col}(MJ)"].apply(mj_to_m3).round(0)

    # 필요한 컬럼만 정리
    cols = ["연", "월", "일", "일자", "요일", "구분", "공휴일여부", 
            "최근N년_평균공급량(GJ)", "최근N년_총공급량(GJ)", "일별비율", "예상공급량(GJ)", "예상공급량(㎥)"]
    cols = [c for c in cols if c in df_year.columns]
    df_year_out = df_year[cols].copy()
    
    # 합계 행 추가
    total_row = df_year_out.sum(numeric_only=True)
    total_row["요일"] = "합계"
    df_year_with_total = pd.concat([df_year_out, pd.DataFrame([total_row])], ignore_index=True)

    return df_year_with_total, pd.DataFrame(month_summary_rows)

def _make_display_table_gj_m3(df_mj: pd.DataFrame) -> pd.DataFrame:
    df = df_mj.copy()
    for base_col in ["최근N년_평균공급량(MJ)", "최근N년_총공급량(MJ)", "예상공급량(MJ)"]:
        if base_col not in df.columns: continue
        gj_col = base_col.replace("(MJ)", "(GJ)")
        m3_col = base_col.replace("(MJ)", "(㎥)")
        df[gj_col] = df[base_col].apply(mj_to_gj).round(0)
        df[m3_col] = df[base_col].apply(mj_to_m3).round(0)
    
    keep_cols = ["연", "월", "일", "요일", "구분", "공휴일여부", 
                 "최근N년_평균공급량(GJ)", "최근N년_총공급량(GJ)", "일별비율", "예상공급량(GJ)", "예상공급량(㎥)"]
    return df[[c for c in keep_cols if c in df.columns]]


# ─────────────────────────────────────────────
# 6. 메인 화면 구성
# ─────────────────────────────────────────────
def tab_daily_plan(df_daily):
    st.subheader("📅 Daily 공급량 분석 — 최근 N년 패턴 기반 일별 계획")
    
    # 파일 업로더 (월별계획)
    uploaded_plan = st.file_uploader("월별 계획 엑셀 업로드", type=["xlsx"], key="plan_upload")
    df_plan = load_monthly_plan(uploaded_plan)

    if df_plan.empty:
        st.warning("월별 계획 파일이 필요합니다. 업로드하거나 프로젝트 폴더에 넣어주세요.")
        return

    # 설정
    plan_years = sorted(df_plan["연도"].dropna().unique())
    default_year = plan_years[-1] if plan_years else 2026
    
    c1, c2, c3 = st.columns([1,1,2])
    with c1: target_year = st.selectbox("계획 연도", [y for y in range(default_year-2, default_year+3)], index=2)
    with c2: target_month = st.selectbox("계획 월", list(range(1, 13)))
    with c3: recent_window = st.slider("학습 기간 (최근 N년)", 1, 5, 3)

    # 분석 실행
    df_result, df_mat, used_years, df_debug = make_daily_plan_table(
        df_daily, df_plan, target_year, target_month, recent_window
    )

    if df_result is None:
        st.error("과거 실적 데이터가 부족하여 분석할 수 없습니다.")
        return

    st.success(f"학습 연도: {used_years} (총 {len(used_years)}개 년도 사용)")

    # 1. 월별 계획량 표시
    plan_col = _find_plan_col(df_plan)
    df_plan_h = make_month_plan_horizontal(df_plan, int(target_year), plan_col)
    show_table_no_index(format_table_generic(df_plan_h), height=140)

    # 2. 결과 테이블
    view = df_result.copy()
    # 합계 행
    total_vals = view.sum(numeric_only=True)
    total_row = pd.DataFrame([total_vals])
    total_row["요일"] = "합계"
    view_with_total = pd.concat([view, total_row], ignore_index=True)
    
    view_show = _make_display_table_gj_m3(view_with_total)
    view_show = format_table_generic(view_show, percent_cols=["일별비율"])
    
    st.markdown("#### 📋 일별 계획 결과")
    show_table_no_index(view_show, height=500)

    # 3. 그래프
    st.markdown("#### 📊 일별 예상 공급량 그래프")
    fig = go.Figure()
    fig.add_trace(go.Bar(x=df_result["일"], y=df_result["예상공급량(MJ)"].apply(mj_to_gj), name="공급량(GJ)"))
    fig.add_trace(go.Scatter(x=df_result["일"], y=df_result["일별비율"], name="비율", yaxis="y2", line=dict(color='red')))
    fig.update_layout(yaxis2=dict(overlaying="y", side="right"), title=f"{target_year}년 {target_month}월 예측")
    st.plotly_chart(fig, use_container_width=True)

    # 4. 다운로드 (연간 엑셀 생성 기능 포함)
    st.markdown("#### 💾 엑셀 다운로드")
    
    # 4-1. 월간 다운로드
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        view_show.to_excel(writer, index=False, sheet_name=f"{target_month}월")
        _format_excel_sheet(writer.book[f"{target_month}월"])
    st.download_button(f"📥 {target_month}월 일별계획 다운로드", buffer.getvalue(), f"DailyPlan_{target_year}_{target_month}.xlsx")

    # 4-2. 연간 다운로드 (누적현황 포함)
    st.divider()
    if st.button(f"📥 {target_year}년 연간 전체 계획 다운로드 (누적현황 포함)"):
        buf_year = BytesIO()
        df_y, df_m_sum = _build_year_daily_plan(df_daily, df_plan, target_year, recent_window)
        with pd.ExcelWriter(buf_year, engine="openpyxl") as writer:
            df_y.to_excel(writer, index=False, sheet_name="연간")
            df_m_sum.to_excel(writer, index=False, sheet_name="월 요약 계획")
            wb = writer.book
            _format_excel_sheet(wb["연간"])
            _format_excel_sheet(wb["월 요약 계획"])
            _add_cumulative_status_sheet(wb, target_year)
            
        st.download_button(
            f"📥 {target_year}년 연간 파일 받기", 
            buf_year.getvalue(), 
            f"AnnualPlan_{target_year}.xlsx",
            key="annual_down"
        )

def tab_daily_monthly_compare(df, df_temp_all):
    st.subheader("📊 Daily·Monthly 공급량 비교 및 검증")
    
    # 상관도 분석
    st.markdown("##### 1. 변수간 상관계수")
    if "공급량(MJ)" in df.columns and "평균기온(℃)" in df.columns:
        corr = df[["공급량(MJ)", "평균기온(℃)", "연도", "월"]].corr()
        fig_corr = px.imshow(corr, text_auto=".2f", color_continuous_scale="RdBu_r", zmin=-1, zmax=1)
        st.plotly_chart(fig_corr, use_container_width=False)
    else:
        st.info("상관도를 분석할 데이터가 부족합니다.")

    # R2 검증
    st.markdown("##### 2. 기온 기반 예측력 검증 (R²)")
    min_y, max_y = int(df["연도"].min()), int(df["연도"].max())
    y_range = st.slider("학습 연도 범위", min_y, max_y, (max(min_y, max_y-4), max_y))
    
    df_win = df[df["연도"].between(y_range[0], y_range[1])].copy()
    if not df_win.empty:
        # 일별 모델
        _, _, r2_d = fit_poly3_and_r2(df_win["평균기온(℃)"], df_win["공급량(MJ)"].apply(mj_to_gj))
        
        # 월별 모델
        df_m = df_win.groupby(["연도", "월"]).agg({"공급량(MJ)": "sum", "평균기온(℃)": "mean"}).reset_index()
        _, _, r2_m = fit_poly3_and_r2(df_m["평균기온(℃)"], df_m["공급량(MJ)"].apply(mj_to_gj))
        
        c1, c2 = st.columns(2)
        c1.metric("월 단위 R² (월평균기온)", f"{r2_m:.3f}" if r2_m else "N/A")
        c2.metric("일 단위 R² (일평균기온)", f"{r2_d:.3f}" if r2_d else "N/A")
        
        # 회귀 곡선 그래프
        if r2_d is not None:
             # 기온 vs 공급량 산점도 및 추세선
            fig_poly = px.scatter(df_win, x="평균기온(℃)", y="공급량(MJ)", trendline="lowess", title="기온별 공급량 분포")
            st.plotly_chart(fig_poly, use_container_width=True)

    # 기온 히트맵 (요청하신 기능 복원)
    st.markdown("##### 3. 일일 평균기온 히트맵")
    if not df_temp_all.empty:
        m_sel = st.selectbox("월 선택", range(1, 13))
        df_hm = df_temp_all[df_temp_all["월"] == m_sel]
        pivot = df_hm.pivot_table(index="일", columns="연도", values="평균기온(℃)")
        fig_hm = px.imshow(pivot, labels=dict(color="기온(℃)"), color_continuous_scale="RdBu_r")
        fig_hm.update_layout(height=600, title=f"{m_sel}월 연도별 기온 패턴")
        st.plotly_chart(fig_hm, use_container_width=True)

# ─────────────────────────────────────────────
# Main Execution
# ─────────────────────────────────────────────
def main():
    st.sidebar.title("데이터 로드")
    up_daily = st.sidebar.file_uploader("일일 실적(공급량) 엑셀", type=["xlsx"])
    
    df_daily, df_temp_all = load_daily_data(up_daily)
    
    if df_daily.empty:
        st.info("👈 좌측 사이드바에서 '일일 실적' 엑셀 파일을 업로드해주세요.")
        return

    mode = st.radio("분석 모드", ["📅 Daily 공급량 분석", "📊 Daily·Monthly 비교"], horizontal=True)

    if mode == "📅 Daily 공급량 분석":
        tab_daily_plan(df_daily)
    else:
        tab_daily_monthly_compare(df_daily, df_temp_all)

if __name__ == "__main__":
    main()
