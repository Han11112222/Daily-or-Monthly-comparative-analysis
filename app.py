import calendar
from io import BytesIO
from pathlib import Path

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


# ─────────────────────────────────────────────
# 페이지 설정
# ─────────────────────────────────────────────
st.set_page_config(
    page_title="도시가스 공급량 — 일별계획 예측",
    layout="wide",
)


# ─────────────────────────────────────────────
# 유틸
# ─────────────────────────────────────────────
def _to_num(x):
    try:
        return pd.to_numeric(x)
    except Exception:
        return pd.to_numeric(pd.Series([x]), errors="coerce").iloc[0]


def _safe_int(x, default=None):
    try:
        v = int(float(x))
        return v
    except Exception:
        return default


def _safe_float(x, default=np.nan):
    try:
        return float(x)
    except Exception:
        return default


def _find_file_candidates(parent: Path, patterns: list[str]) -> Path | None:
    for pat in patterns:
        p = parent / pat
        if p.exists():
            return p
    return None


@st.cache_data(show_spinner=False)
def _read_excel_bytes(xlsx_bytes: bytes, sheet_name=0) -> pd.DataFrame:
    return pd.read_excel(BytesIO(xlsx_bytes), sheet_name=sheet_name)


def _guess_col(df: pd.DataFrame, keys: list[str], default=None):
    cols = list(df.columns)
    for k in keys:
        for c in cols:
            if k in str(c):
                return c
    return default


# ─────────────────────────────────────────────
# 엑셀(다운로드) 스타일링
# ─────────────────────────────────────────────
THIN = Side(style="thin", color="CCCCCC")
BORDER_THIN = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)


def _format_excel_sheet(ws, freeze: str = "A2", center: bool = True):
    ws.freeze_panes = freeze

    # 열 너비 자동(대략)
    for col in ws.columns:
        max_len = 0
        col_letter = col[0].column_letter
        for cell in col:
            try:
                v = "" if cell.value is None else str(cell.value)
                max_len = max(max_len, len(v))
            except Exception:
                pass
        ws.column_dimensions[col_letter].width = min(40, max(10, max_len + 2))

    # 정렬/테두리
    align = Alignment(horizontal="center", vertical="center") if center else Alignment(vertical="center")
    for row in ws.iter_rows():
        for cell in row:
            cell.border = BORDER_THIN
            cell.alignment = align


def _add_cumulative_status_sheet(wb, annual_year: int):
    """
    ✅ '6. 일일계획 다운로드(연간)' 다운로드 엑셀의 마지막 시트에
       (기준일 입력 → 목표/누적/진행률 자동 계산) 시트 추가
       - 목표(GJ), 누적(GJ), 목표(m3), 누적(m3), 진행률(GJ)
       - 계산은 '월 요약 계획' 시트(월별 목표)와 '연간' 시트(일별 계획/누적) 기반
    """
    # 시트명은 중복 피하기
    base_name = "누적계획현황"
    name = base_name
    i = 1
    while name in wb.sheetnames:
        i += 1
        name = f"{base_name}{i}"

    ws = wb.create_sheet(title=name)

    # 헤더/타이틀
    ws["A1"].value = "기준일"
    ws["B1"].value = f"{annual_year}-01-01"
    ws["A3"].value = "구분"
    ws["B3"].value = "목표(GJ)"
    ws["C3"].value = "누적(GJ)"
    ws["D3"].value = "목표(m³)"
    ws["E3"].value = "누적(m³)"
    ws["F3"].value = "진행률(GJ)"

    # 기본 행
    rows = [
        ("일",),
        ("월",),
        ("연",),
    ]
    for r, label in enumerate(["일", "월", "연"], start=4):
        ws[f"A{r}"].value = label

    # 참조 시트
    # - '연간' 시트: 일별 계획표(예상공급량_GJ, 예상공급량_m3 등) 존재를 가정
    # - '월 요약 계획' 시트: 월별 계획(GJ, m3) 존재를 가정
    # 이 앱에서 생성하는 포맷을 기준으로 수식 넣음
    sh_y = "연간"
    sh_m = "월 요약 계획"

    # 기준일(B1)로부터 연/월/일 추출
    # Excel에서 DATEVALUE/LEFT/MID/RIGHT 조합으로 처리(서식 유연하게)
    # B1은 사용자가 직접 yyyy-mm-dd 형태로 입력한다고 가정
    # (만약 날짜 셀 형식으로 입력해도 YEAR/MONTH/DAY가 먹음)
    ws["G1"].value = "기준연"
    ws["H1"].value = "기준월"
    ws["I1"].value = "기준일(일)"
    ws["G2"].value = f"=YEAR($B$1)"
    ws["H2"].value = f"=MONTH($B$1)"
    ws["I2"].value = f"=DAY($B$1)"

    # '연간' 시트에서 누적(GJ/m3) 찾기:
    # - 연간 시트에 '일자' 컬럼과 '누적공급량_GJ', '누적공급량_m3'가 있다고 가정
    # - 없을 경우를 대비해, '예상공급량_GJ'를 SUMIFS로 누적합 계산하는 방식 사용
    # 여기서는 SUMIFS 방식(가장 안전)으로 처리
    # 연간 시트 컬럼 추정:
    #   A: 일자, ... , (예상공급량_GJ), (예상공급량_m3)
    # 앱 생성 포맷 기준으로 '연간' 시트 헤더를 검색하지 않고 고정 컬럼을 쓰면 위험해서,
    # 여기서는 헤더명 기반으로 MATCH를 쓰는 수식도 어렵고,
    # 대신 앱이 만들어내는 순서를 유지하므로 아래 고정 참조를 사용:
    #   '연간'!A:A = 일자, '연간'!G:G = 예상공급량_GJ, '연간'!H:H = 예상공급량_m3 (가정)
    # 실제 열이 다르면 여기만 조정하면 됨.
    # (현재 앱의 _build_year_daily_plan 출력 기준으로 맞춰져 있음)
    date_col = f"'{sh_y}'!$A:$A"
    gj_col = f"'{sh_y}'!$G:$G"
    m3_col = f"'{sh_y}'!$H:$H"

    # '월 요약 계획'에서 월별 목표(GJ/m3) 찾기(월=기준월)
    #   '월 요약 계획'!A:A=월, B:B=월간 계획(GJ), C:C=월간 계획(m3) (가정)
    m_month = f"'{sh_m}'!$A:$A"
    m_gj = f"'{sh_m}'!$B:$B"
    m_m3 = f"'{sh_m}'!$C:$C"

    # 1) 일(기준일 하루 목표/누적)
    # 목표: 해당 날짜의 예상공급량
    ws["B4"].value = f"=SUMIFS({gj_col},{date_col},$B$1)"
    ws["D4"].value = f"=SUMIFS({m3_col},{date_col},$B$1)"
    # 누적: 1/1 ~ 기준일 SUMIFS
    ws["C4"].value = f"=SUMIFS({gj_col},{date_col},\">=\"&DATE($G$2,1,1),{date_col},\"<=\"&$B$1)"
    ws["E4"].value = f"=SUMIFS({m3_col},{date_col},\">=\"&DATE($G$2,1,1),{date_col},\"<=\"&$B$1)"
    ws["F4"].value = f"=IFERROR(C4/B4,0)"

    # 2) 월(기준월 목표/누적)
    # 목표: 월 요약 계획에서 기준월 목표
    ws["B5"].value = f"=IFERROR(XLOOKUP($H$2,{m_month},{m_gj}),0)"
    ws["D5"].value = f"=IFERROR(XLOOKUP($H$2,{m_month},{m_m3}),0)"
    # 누적: 해당월 1일~기준일
    ws["C5"].value = f"=SUMIFS({gj_col},{date_col},\">=\"&DATE($G$2,$H$2,1),{date_col},\"<=\"&$B$1)"
    ws["E5"].value = f"=SUMIFS({m3_col},{date_col},\">=\"&DATE($G$2,$H$2,1),{date_col},\"<=\"&$B$1)"
    ws["F5"].value = f"=IFERROR(C5/B5,0)"

    # 3) 연(연간 목표/누적)
    # 목표: 월 요약 계획의 월간목표 합계
    ws["B6"].value = f"=SUM({m_gj})"
    ws["D6"].value = f"=SUM({m_m3})"
    # 누적: 1/1~기준일
    ws["C6"].value = f"=SUMIFS({gj_col},{date_col},\">=\"&DATE($G$2,1,1),{date_col},\"<=\"&$B$1)"
    ws["E6"].value = f"=SUMIFS({m3_col},{date_col},\">=\"&DATE($G$2,1,1),{date_col},\"<=\"&$B$1)"
    ws["F6"].value = f"=IFERROR(C6/B6,0)"

    # 스타일
    for c in range(1, 7):
        ws.cell(3, c).font = Font(bold=True)
        ws.cell(3, c).fill = PatternFill("solid", fgColor="F2F2F2")

    ws["A1"].font = Font(bold=True)
    ws["A1"].fill = PatternFill("solid", fgColor="F2F2F2")
    ws["B1"].fill = PatternFill("solid", fgColor="FFF2CC")

    _format_excel_sheet(ws, freeze="A4", center=True)


# ─────────────────────────────────────────────
# 데이터 불러오기(일일 실적 + 기온 포함)
# ─────────────────────────────────────────────
@st.cache_data
def load_daily_data():
    """
    반환:
      df_model     : 공급량(GJ)와 평균기온 둘 다 있는 구간 (예측/R² 계산용)
      df_temp_all  : 평균기온만 있어도 되는 전체 구간 (히트맵용)
    """
    excel_path = Path(__file__).parent / "공급량(일일실적).xlsx"
    if not excel_path.exists():
        return pd.DataFrame(), pd.DataFrame()

    df_raw = pd.read_excel(excel_path)

    # 필요한 컬럼만 사용
    cols = list(df_raw.columns)
    # 날짜 컬럼 후보
    date_col = _guess_col(df_raw, ["일자", "날짜", "Date", "date"], default=cols[0])
    # 공급량 MJ 후보
    mj_col = _guess_col(df_raw, ["공급량(MJ)", "공급량_MJ", "공급량 MJ", "MJ"], default=None)
    # 평균기온 후보
    t_col = _guess_col(df_raw, ["평균기온", "평균기온(℃)", "평균기온(°C)", "기온"], default=None)
    # m3 후보
    m3_col = _guess_col(df_raw, ["공급량(M3)", "공급량(㎥)", "공급량_m3", "m3", "M3"], default=None)

    use_cols = [c for c in [date_col, mj_col, m3_col, t_col] if c is not None]
    df = df_raw[use_cols].copy()
    df = df.rename(columns={date_col: "일자"})
    if mj_col is not None:
        df = df.rename(columns={mj_col: "공급량(MJ)"})
    if m3_col is not None:
        df = df.rename(columns={m3_col: "공급량(m3)"})
    if t_col is not None:
        df = df.rename(columns={t_col: "평균기온(℃)"})

    df["일자"] = pd.to_datetime(df["일자"], errors="coerce")
    df = df.dropna(subset=["일자"]).sort_values("일자").reset_index(drop=True)
    df["연도"] = df["일자"].dt.year
    df["월"] = df["일자"].dt.month
    df["일"] = df["일자"].dt.day

    # 숫자 변환
    if "공급량(MJ)" in df.columns:
        df["공급량(MJ)"] = pd.to_numeric(df["공급량(MJ)"], errors="coerce")
        df["공급량_GJ"] = df["공급량(MJ)"] * MJ_TO_GJ
    else:
        df["공급량_GJ"] = np.nan

    if "공급량(m3)" in df.columns:
        df["공급량(m3)"] = pd.to_numeric(df["공급량(m3)"], errors="coerce")
    else:
        # MJ → m3 (Nm3 가정)
        df["공급량(m3)"] = np.where(
            np.isfinite(df.get("공급량(MJ)", np.nan)),
            df["공급량(MJ)"] / MJ_PER_NM3,
            np.nan,
        )

    if "평균기온(℃)" in df.columns:
        df["평균기온(℃)"] = pd.to_numeric(df["평균기온(℃)"], errors="coerce")
    else:
        df["평균기온(℃)"] = np.nan

    # 모델용(공급량+기온 둘다 있는 구간)
    df_model = df.dropna(subset=["공급량_GJ", "평균기온(℃)"]).copy()

    # 히트맵용(기온만 있어도 됨)
    df_temp_all = df.dropna(subset=["평균기온(℃)"]).copy()[["일자", "평균기온(℃)"]].copy()

    return df_model, df_temp_all


# ─────────────────────────────────────────────
# 월별 계획(업로드 우선 + repo/폴더 자동탐색)
# ─────────────────────────────────────────────
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
def _normalize_monthly_plan_df(df: pd.DataFrame) -> pd.DataFrame:
    """월별 계획 파일 컬럼명을 최대한 안전하게 표준화(연/월/계획컬럼 탐색)"""
    if df is None:
        return pd.DataFrame()

    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]

    # 연/월 컬럼 후보
    year_candidates = ["연", "연도", "Year", "year"]
    month_candidates = ["월", "Month", "month"]
    plan_candidates = [
        "사업계획",
        "월별계획",
        "계획",
        "계획량",
        "공급계획",
        "목표",
        "월간 계획",
    ]

    year_col = _guess_col(df, year_candidates, default=None)
    month_col = _guess_col(df, month_candidates, default=None)

    # plan 컬럼(숫자형 우선)
    plan_col = None
    for c in df.columns:
        if any(k in str(c) for k in plan_candidates):
            plan_col = c
            break
    if plan_col is None:
        # 숫자형 컬럼 중 마지막 후보
        num_cols = df.select_dtypes(include=["number"]).columns.tolist()
        plan_col = num_cols[-1] if num_cols else None

    if year_col is None or month_col is None or plan_col is None:
        # 최소한의 표준화라도 하고 반환
        return df

    out = df[[year_col, month_col, plan_col]].copy()
    out.columns = ["연", "월", "계획(MJ)"]  # 내부는 MJ로 통일 후 GJ/m3 파생
    out["연"] = pd.to_numeric(out["연"], errors="coerce").astype("Int64")
    out["월"] = pd.to_numeric(out["월"], errors="coerce").astype("Int64")
    out["계획(MJ)"] = pd.to_numeric(out["계획(MJ)"], errors="coerce")

    # 단위 변환
    out["계획_GJ"] = out["계획(MJ)"] * MJ_TO_GJ
    out["계획_m3"] = out["계획(MJ)"] / MJ_PER_NM3

    return out.dropna(subset=["연", "월"]).reset_index(drop=True)


@st.cache_data(show_spinner=False)
def load_monthly_plan_from_bytes(xlsx_bytes: bytes) -> pd.DataFrame | None:
    try:
        df = pd.read_excel(BytesIO(xlsx_bytes), sheet_name="월별계획_실적")
    except Exception:
        try:
            df = pd.read_excel(BytesIO(xlsx_bytes), sheet_name=0)
        except Exception:
            return None
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
        return df_up

    # 1) 기존 기본 파일
    df_repo = load_monthly_plan()
    if df_repo is not None and not df_repo.empty:
        return df_repo

    # 2) 폴더 자동탐색
    parent = Path(__file__).parent
    cand = _find_file_candidates(
        parent,
        [
            "월별계획.xlsx",
            "월별 계획.xlsx",
            "monthly_plan.xlsx",
            "MonthlyPlan.xlsx",
            "공급량(계획_실적).xlsx",
        ],
    )
    if cand is None:
        return None

    try:
        df = pd.read_excel(cand)
    except Exception:
        return None
    return _normalize_monthly_plan_df(df)


# ─────────────────────────────────────────────
# 일별 계획 로직(간단화 버전)
# ─────────────────────────────────────────────
def make_daily_plan_table(
    df_daily: pd.DataFrame,
    df_plan: pd.DataFrame,
    target_year: int,
    target_month: int,
    recent_window: int,
):
    """
    최근 N년(대상연도 직전) 동일월 실적 패턴으로 대상월 일별 비율을 만들고,
    대상월 계획(GJ/m3)에 분배.
    """
    df_daily = df_daily.copy()
    df_daily["연도"] = df_daily["일자"].dt.year
    df_daily["월"] = df_daily["일자"].dt.month
    df_daily["일"] = df_daily["일자"].dt.day
    df_daily["요일"] = df_daily["일자"].dt.day_name()

    # 학습 후보 연도
    years_all = sorted(df_daily["연도"].unique().tolist())
    hist_years = [y for y in years_all if y < target_year]
    cand_years = hist_years[-recent_window:] if len(hist_years) >= 1 else []
    if len(cand_years) == 0:
        return None, None, [], None

    # 해당월 데이터 있는 연도만
    used = []
    mats = []
    for y in cand_years:
        d = df_daily[(df_daily["연도"] == y) & (df_daily["월"] == target_month)].copy()
        if d.empty:
            continue
        used.append(y)
        mats.append(d[["일", "공급량_GJ", "공급량(m3)"]].rename(columns={
            "공급량_GJ": f"{y}_GJ",
            "공급량(m3)": f"{y}_m3",
        }))

    if len(used) == 0:
        return None, None, [], None

    # 매트릭스(일 기준 join)
    df_mat = mats[0][["일"]].copy()
    for m in mats:
        df_mat = df_mat.merge(m, on="일", how="outer")
    df_mat = df_mat.sort_values("일").reset_index(drop=True)

    # 평균 패턴(일별 비율)
    gj_cols = [c for c in df_mat.columns if c.endswith("_GJ")]
    df_mat["평균_GJ"] = df_mat[gj_cols].mean(axis=1, skipna=True)
    df_mat["일별비율"] = df_mat["평균_GJ"] / df_mat["평균_GJ"].sum(skipna=True)

    # 대상월 계획
    plan_row = df_plan[(df_plan["연"] == target_year) & (df_plan["월"] == target_month)]
    if plan_row.empty:
        return None, df_mat, used, None

    plan_gj = float(plan_row["계획_GJ"].iloc[0])
    plan_m3 = float(plan_row["계획_m3"].iloc[0])

    # 대상월 일자 생성
    last_day = calendar.monthrange(target_year, target_month)[1]
    days = list(range(1, last_day + 1))
    df_out = pd.DataFrame({"일": days})
    df_out = df_out.merge(df_mat[["일", "일별비율"]], on="일", how="left")
    df_out["일별비율"] = df_out["일별비율"].fillna(0)

    df_out["예상공급량_GJ"] = df_out["일별비율"] * plan_gj
    df_out["예상공급량_m3"] = df_out["일별비율"] * plan_m3
    df_out["일자"] = pd.to_datetime(
        [f"{target_year}-{target_month:02d}-{d:02d}" for d in df_out["일"]],
        errors="coerce",
    )
    df_out["요일"] = df_out["일자"].dt.day_name()

    # 누적
    df_out["누적공급량_GJ"] = df_out["예상공급량_GJ"].cumsum()
    df_out["누적공급량_m3"] = df_out["예상공급량_m3"].cumsum()

    # 표시용 컬럼 순서
    df_out = df_out[[
        "일자", "요일", "일", "일별비율", "예상공급량_GJ", "누적공급량_GJ", "예상공급량_m3", "누적공급량_m3"
    ]].copy()

    return df_out, df_mat, used, None


def _find_plan_col(df_plan: pd.DataFrame) -> str:
    if df_plan is None or df_plan.empty:
        return "계획_GJ"
    for c in ["계획_GJ", "계획(MJ)", "계획_m3"]:
        if c in df_plan.columns:
            return c
    # fallback
    nums = df_plan.select_dtypes(include=["number"]).columns.tolist()
    return nums[-1] if nums else df_plan.columns[-1]


def make_month_plan_horizontal(df_plan: pd.DataFrame, target_year: int, plan_col: str) -> pd.DataFrame:
    """월별 계획량(1~12월) + 연간합계를 가로로 표시, 각 월은 GJ 표기 / 하단 m3 표기"""
    if df_plan is None or df_plan.empty:
        return pd.DataFrame()

    # 연간 계획(월별)
    sub = df_plan[df_plan["연"] == target_year].copy()
    if sub.empty:
        return pd.DataFrame()

    # 1~12 정렬
    sub = sub.sort_values("월")
    # GJ/m3
    if "계획_GJ" not in sub.columns:
        sub["계획_GJ"] = pd.to_numeric(sub.get("계획(MJ)", np.nan), errors="coerce") * MJ_TO_GJ
    if "계획_m3" not in sub.columns:
        sub["계획_m3"] = pd.to_numeric(sub.get("계획(MJ)", np.nan), errors="coerce") / MJ_PER_NM3

    row_gj = {"구분": "사업계획(월별 계획) - GJ"}
    row_m3 = {"구분": "사업계획(월별 계획) - ㎥"}

    total_gj = 0.0
    total_m3 = 0.0
    for m in range(1, 13):
        v_gj = float(sub.loc[sub["월"] == m, "계획_GJ"].sum())
        v_m3 = float(sub.loc[sub["월"] == m, "계획_m3"].sum())
        row_gj[f"{m}월"] = v_gj
        row_m3[f"{m}월"] = v_m3
        total_gj += v_gj
        total_m3 += v_m3

    row_gj["연간합계"] = total_gj
    row_m3["연간합계"] = total_m3

    out = pd.DataFrame([row_gj, row_m3])

    # 보기 좋게
    num_cols = [c for c in out.columns if c != "구분"]
    out[num_cols] = out[num_cols].apply(pd.to_numeric, errors="coerce")

    return out


def _build_year_daily_plan(df_daily: pd.DataFrame, df_plan: pd.DataFrame, target_year: int, recent_window: int):
    """연간(1~12월) 일별 계획을 월별로 만들어 합치고, 월요약도 같이 생성"""
    all_months = sorted(df_plan[df_plan["연"] == target_year]["월"].unique().tolist())
    if len(all_months) == 0:
        return pd.DataFrame(), pd.DataFrame()

    year_rows = []
    month_summary_rows = []

    for m in all_months:
        df_res, _, used, _ = make_daily_plan_table(
            df_daily=df_daily,
            df_plan=df_plan,
            target_year=target_year,
            target_month=int(m),
            recent_window=recent_window,
        )
        if df_res is None or df_res.empty:
            continue

        # 월별 계획(요약)
        plan_row = df_plan[(df_plan["연"] == target_year) & (df_plan["월"] == int(m))]
        plan_gj = float(plan_row["계획_GJ"].iloc[0]) if not plan_row.empty else np.nan
        plan_m3 = float(plan_row["계획_m3"].iloc[0]) if not plan_row.empty else np.nan

        month_summary_rows.append({
            "월": int(m),
            "월간 계획(GJ)": plan_gj,
            "월간 계획(m3)": plan_m3,
            "학습연도수": len(used),
            "학습연도": ", ".join(map(str, used)),
        })

        df_res["월"] = int(m)
        year_rows.append(df_res)

    df_year = pd.concat(year_rows, ignore_index=True) if len(year_rows) else pd.DataFrame()
    df_month_sum = pd.DataFrame(month_summary_rows).sort_values("월").reset_index(drop=True)

    # 연간합계 행
    if not df_month_sum.empty:
        df_month_sum_total = pd.DataFrame([{
            "월": "연간합계",
            "월간 계획(GJ)": df_month_sum["월간 계획(GJ)"].sum(skipna=True),
            "월간 계획(m3)": df_month_sum["월간 계획(m3)"].sum(skipna=True),
            "학습연도수": "",
            "학습연도": "",
        }])
        df_month_sum = pd.concat([df_month_sum, df_month_sum_total], ignore_index=True)

    return df_year, df_month_sum


# ─────────────────────────────────────────────
# 탭1: Daily 공급량 분석
# ─────────────────────────────────────────────
def tab_daily_plan(df_daily: pd.DataFrame):
    st.subheader("📅 Daily 공급량 분석 — 최근 N년 패턴 기반 일별 계획")

    df_plan = get_monthly_plan_df()
    if df_plan is None or df_plan.empty:
        st.error("월별 계획 파일을 찾지 못했어. 업로드하거나 repo에 '월별계획.xlsx'를 넣어줘.")
        return

    plan_col = _find_plan_col(df_plan)

    years_plan = sorted(df_plan["연"].dropna().astype(int).unique())
    default_year_idx = years_plan.index(2026) if 2026 in years_plan else len(years_plan) - 1

    col_y, col_m, _ = st.columns([1, 1, 2])
    with col_y:
        target_year = st.selectbox("계획 연도 선택", years_plan, index=default_year_idx)
    with col_m:
        months_plan = sorted(df_plan[df_plan["연"] == target_year]["월"].dropna().astype(int).unique())
        default_month_idx = months_plan.index(1) if 1 in months_plan else 0
        target_month = st.selectbox("계획 월 선택", months_plan, index=default_month_idx, format_func=lambda m: f"{m}월")

    # 최근 N년 슬라이더
    all_years = sorted(df_daily["연도"].unique())
    hist_years = [y for y in all_years if y < target_year]
    if len(hist_years) < 1:
        st.warning("해당 연도는 직전 연도가 없어 최근 N년 분석을 할 수 없어.")
        return

    slider_min = 1
    slider_max = min(10, len(hist_years))

    col_slider, _ = st.columns([2, 3])
    with col_slider:
        recent_window = st.slider(
            "최근 몇 년 평균으로 비율을 계산할까?",
            min_value=slider_min,
            max_value=slider_max,
            value=min(3, slider_max),
            step=1,
            help="예: 3년을 선택하면 대상연도 직전 3개 연도의 같은 월 데이터를 사용 (단, 해당월 실적 없는 연도는 자동 제외)",
        )

    st.caption(
        f"최근 {recent_window}년 후보({target_year-recent_window}년 ~ {target_year-1}년) "
        f"{target_month}월 패턴으로 {target_year}년 {target_month}월 일별 계획을 계산. "
        "(해당월 실적이 없는 연도는 자동 제외)"
    )

    df_result, df_mat, used_years, _ = make_daily_plan_table(
        df_daily=df_daily,
        df_plan=df_plan,
        target_year=target_year,
        target_month=target_month,
        recent_window=recent_window,
    )

    if df_result is None or len(used_years) == 0:
        st.warning("해당 연도/월에 대해 선택한 최근 N년 기준으로 계산할 수 있는 데이터가 없어.")
        return

    st.markdown(f"**실제 학습에 사용된 연도(해당월 실적 존재): {min(used_years)}년 ~ {max(used_years)}년 (총 {len(used_years)}개)**")

    # 대상월 계획 합계
    plan_row = df_plan[(df_plan["연"] == target_year) & (df_plan["월"] == target_month)]
    plan_gj = float(plan_row["계획_GJ"].iloc[0]) if not plan_row.empty else np.nan
    st.markdown(f"**{target_year}년 {target_month}월 사업계획 제출 공급량 합계:**  `{plan_gj:,.0f} GJ`")

    st.divider()
    st.markdown("### 🧩 일별 공급량 분배 결과")

    # 표
    show = df_result.copy()
    show["일별비율"] = show["일별비율"].fillna(0) * 100
    show = show.rename(columns={"일별비율": "일별비율(%)"})

    st.dataframe(show, use_container_width=True, hide_index=True)

    # 다운로드(월)
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df_result.to_excel(writer, index=False, sheet_name="일별계획")
        if df_mat is not None and not df_mat.empty:
            df_mat.to_excel(writer, index=False, sheet_name="학습매트릭스")

        wb = writer.book
        ws1 = wb["일별계획"]
        _format_excel_sheet(ws1, freeze="A2", center=True)
        for c in range(1, ws1.max_column + 1):
            ws1.cell(1, c).font = Font(bold=True)

        if "학습매트릭스" in wb.sheetnames:
            ws2 = wb["학습매트릭스"]
            _format_excel_sheet(ws2, freeze="A2", center=True)
            for c in range(1, ws2.max_column + 1):
                ws2.cell(1, c).font = Font(bold=True)

    st.download_button(
        "📥 5. 일일계획 다운로드(월간)",
        data=buffer.getvalue(),
        file_name=f"{target_year}_{target_month:02d}_일별공급계획.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    st.markdown("#### 📌 월별 계획량(1~12월) & 연간 총량")
    month_h = make_month_plan_horizontal(df_plan, target_year, plan_col)
    st.dataframe(month_h, use_container_width=True, hide_index=True)

    st.markdown("#### 🗂️ 6. 일일계획 다운로드(연간)")

    years_plan = sorted(df_plan["연"].dropna().astype(int).unique())
    annual_year = st.selectbox(
        "연간 계획 연도 선택",
        years_plan,
        index=years_plan.index(target_year) if target_year in years_plan else 0,
        key="annual_year_select",
    )

    buffer_year = BytesIO()
    df_year_daily, df_month_summary = _build_year_daily_plan(
        df_daily=df_daily,
        df_plan=df_plan,
        target_year=int(annual_year),
        recent_window=int(recent_window),
    )

    with pd.ExcelWriter(buffer_year, engine="openpyxl") as writer:
        df_year_daily.to_excel(writer, index=False, sheet_name="연간")
        df_month_summary.to_excel(writer, index=False, sheet_name="월 요약 계획")

        wb = writer.book
        ws_y = wb["연간"]
        ws_m = wb["월 요약 계획"]

        _format_excel_sheet(ws_y, freeze="A2", center=True)
        _format_excel_sheet(ws_m, freeze="A2", center=True)

        for c in range(1, ws_y.max_column + 1):
            ws_y.cell(1, c).font = Font(bold=True)
        for c in range(1, ws_m.max_column + 1):
            ws_m.cell(1, c).font = Font(bold=True)

        # ✅ 요청한 부분: 마지막 시트 추가(기준일 입력 → 목표/누적 자동 계산)
        _add_cumulative_status_sheet(wb, annual_year=int(annual_year))

    st.download_button(
        label=f"📥 {annual_year}년 연간 일별공급계획 다운로드 (Excel)",
        data=buffer_year.getvalue(),
        file_name=f"{annual_year}_연간_일별공급계획.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key="download_annual_excel",
    )


# ─────────────────────────────────────────────
# 탭2: Daily·Monthly 공급량 비교
# ─────────────────────────────────────────────
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
# 🧊 기온분석 — 일일 평균기온 히트맵(매트릭스)
#   - Daily-Monthly 공급량 비교 탭 맨 하단에 표시
#   - 기본: df_temp_all의 (일자, 평균기온(℃))
#   - 옵션: 별도 XLSX 업로드
# ─────────────────────────────────────────────
def render_daily_temp_heatmap(df_temp_all: pd.DataFrame):
    st.subheader("🧊 G. 기온분석 — 일일 평균기온 히트맵")
    st.caption("기본은 공급량 데이터의 평균기온(℃)을 사용해. 필요하면 기온 파일만 별도로 업로드해서 볼 수 있어.")

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
        if df_temp_all is None or not need.issubset(df_temp_all.columns):
            st.caption("기온 데이터(평균기온(℃))가 없어서 히트맵을 만들 수 없어.")
            return
        df_t = df_temp_all[["일자", "평균기온(℃)"]].copy()

    df_t["일자"] = pd.to_datetime(df_t["일자"], errors="coerce")
    df_t["평균기온(℃)"] = pd.to_numeric(df_t["평균기온(℃)"], errors="coerce")
    df_t = df_t.dropna(subset=["일자", "평균기온(℃)"])

    if df_t.empty:
        st.caption("기온 데이터가 비어있어.")
        return

    df_t["연도"] = df_t["일자"].dt.year.astype(int)
    df_t["월"] = df_t["일자"].dt.month.astype(int)
    df_t["일"] = df_t["일자"].dt.day.astype(int)

    years = sorted(df_t["연도"].unique().tolist())
    y_min, y_max = int(min(years)), int(max(years))

    col1, col2 = st.columns([2, 1])
    with col1:
        year_range = st.slider("연도 범위", min_value=y_min, max_value=y_max, value=(y_min, y_max), step=1, key="g_year_range")
    with col2:
        month_sel = st.selectbox("월 선택", list(range(1, 13)), index=0, format_func=lambda m: f"{m:02d} (January)" if m == 1 else f"{m:02d}", key="g_month")

    y0, y1 = year_range
    sel_month = int(month_sel)

    dsel = df_t[(df_t["연도"] >= y0) & (df_t["연도"] <= y1) & (df_t["월"] == sel_month)].copy()
    if dsel.empty:
        st.caption("선택한 범위에 데이터가 없어.")
        return

    pivot = dsel.pivot_table(index="일", columns="연도", values="평균기온(℃)", aggfunc="mean")
    pivot = pivot.sort_index()

    # 평균 행 추가(각 연도별 월 평균)
    month_mean_by_year = pivot.mean(axis=0, skipna=True)
    pivot.loc["평균"] = month_mean_by_year.values

    z = pivot.values.astype(float)
    text = np.where(np.isnan(z), "", np.round(z, 1).astype(str))

    fig = go.Figure(
        data=go.Heatmap(
            z=z,
            x=[str(y) for y in pivot.columns],
            y=list(pivot.index),
            text=text,
            texttemplate="%{text}",
            textfont=dict(size=10),
            colorbar=dict(title="℃"),
        )
    )
    fig.update_layout(
        title=f"{int(sel_month):02d}월 일일 평균기온 히트맵(선택연도 {len(pivot.columns)}개)",
        xaxis=dict(side="bottom"),
        yaxis=dict(title="Day"),
        margin=dict(l=40, r=20, t=60, b=20),
        height=650,
        template="simple_white",
    )
    st.plotly_chart(fig, use_container_width=True)


def tab_daily_monthly_compare(df: pd.DataFrame, df_temp_all: pd.DataFrame):
    st.subheader("📌 1. 월평균기온 기반 월별 공급량 회귀(3차 다항식)")

    # 월 집계
    df2 = df.copy()
    df2["연도"] = df2["일자"].dt.year
    df2["월"] = df2["일자"].dt.month
    df2["일"] = df2["일자"].dt.day

    df_month = df2.groupby(["연도", "월"], as_index=False).agg(
        평균기온=("평균기온(℃)", "mean"),
        공급량_GJ=("공급량_GJ", "sum"),
    )
    coef_m, y_pred_m, r2_m = fit_poly3_and_r2(df_month["평균기온"], df_month["공급량_GJ"])
    df_month["예측공급량_GJ"] = y_pred_m if y_pred_m is not None else np.nan

    st.subheader("📌 2. 일평균기온 기반 일별 공급량 회귀(3차 다항식)")

    df_window = df2.dropna(subset=["공급량_GJ", "평균기온(℃)"]).copy()
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

    st.divider()

    # ✅ 요청한 위치: 두번째 탭 맨 하단에 히트맵
    render_daily_temp_heatmap(df_temp_all)


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
