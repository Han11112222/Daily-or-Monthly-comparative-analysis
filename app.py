import calendar
import os
from io import BytesIO
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import streamlit as st
from openpyxl.styles import Alignment, Font, Border, Side, PatternFill

# ─────────────────────────────────────────────
# 1. 기본 설정 및 파일 자동 탐색기
# ─────────────────────────────────────────────
st.set_page_config(page_title="도시가스 공급량 예측 시스템", layout="wide")

def find_repo_file(filename_candidates):
    """
    현재 폴더와 그 상위 폴더를 뒤져서 파일을 찾아내는 함수
    (형님의 파일이 어디에 있든 찾아냅니다)
    """
    search_dirs = [Path(__file__).parent, Path.cwd()]
    for folder in search_dirs:
        for name in filename_candidates:
            target = folder / name
            if target.exists():
                return target
    return None

# ─────────────────────────────────────────────
# 2. 단위 변환 함수 (형님 코드 유지)
# ─────────────────────────────────────────────
MJ_PER_NM3 = 42.563
MJ_TO_GJ = 0.001

def mj_to_gj(mj: float) -> float:
    try: return float(mj) * MJ_TO_GJ
    except: return np.nan

def gj_to_mj(gj: float) -> float:
    try: return float(gj) / MJ_TO_GJ
    except: return np.nan

def mj_to_m3(mj: float) -> float:
    try: return float(mj) / MJ_PER_NM3
    except: return np.nan

def gj_to_m3(gj: float) -> float:
    try: return mj_to_m3(gj_to_mj(gj))
    except: return np.nan

# ─────────────────────────────────────────────
# 3. 데이터 로딩 (형님 코드 로직 + 자동 탐색 기능 추가)
# ─────────────────────────────────────────────
@st.cache_data(show_spinner=False)
def load_monthly_plan(uploaded_file) -> pd.DataFrame:
    # 1. 업로드 확인
    if uploaded_file is not None:
        return pd.read_excel(uploaded_file)
    
    # 2. 자동 탐색 (형님 코드의 Path 로직 강화)
    file_path = find_repo_file(["월별계획.xlsx", "월별 계획.xlsx", "공급량(계획_실적).xlsx"])
    if file_path:
        df = pd.read_excel(file_path)
    else:
        # 파일이 없어도 에러를 내지 않고 None 반환 (메인에서 처리)
        return None

    # 컬럼 표준화
    col_map = {}
    for c in df.columns:
        cs = str(c).strip()
        if cs in ["구분", "항목", "분류"]:
            col_map[c] = "구분"
    df = df.rename(columns=col_map)

    # 월 컬럼, 연간 합계 숫자 변환
    cols_to_numeric = []
    for m in range(1, 13):
        for cand in [f"{m}월", str(m), f"{m:02d}"]:
            if cand in df.columns: cols_to_numeric.append(cand)
    
    for cand in ["연간합계", "연간", "합계"]:
        if cand in df.columns: cols_to_numeric.append(cand)

    for c in cols_to_numeric:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    return df

@st.cache_data(show_spinner=False)
def load_daily_data(uploaded_file_daily) -> pd.DataFrame:
    # 1. 업로드 확인
    if uploaded_file_daily is not None:
        df_raw = pd.read_excel(uploaded_file_daily)
    else:
        # 2. 자동 탐색
        file_path = find_repo_file(["공급량(일일실적).xlsx", "일일실적.xlsx"])
        if file_path:
            df_raw = pd.read_excel(file_path)
        else:
            return None

    # 컬럼 표준화 (형님 코드 로직)
    col_std = {}
    for c in df_raw.columns:
        cs = str(c).strip()
        if cs in ["일자", "date", "Date"]: col_std[c] = "일자"
        if "공급량" in cs and "MJ" in cs: col_std[c] = "공급량(MJ)"
        if "공급량" in cs and ("GJ" in cs or "Gj" in cs): col_std[c] = "공급량(GJ)"
        if "평균" in cs and ("기온" in cs or "온도" in cs): col_std[c] = "평균기온(°C)"
    
    df = df_raw.rename(columns=col_std).copy()

    if "일자" not in df.columns: return None
    
    df["일자"] = pd.to_datetime(df["일자"], errors="coerce")
    if "공급량(MJ)" not in df.columns and "공급량(GJ)" in df.columns:
        df["공급량(MJ)"] = df["공급량(GJ)"].apply(gj_to_mj)
    
    if "공급량(MJ)" in df.columns:
        df["공급량(MJ)"] = pd.to_numeric(df["공급량(MJ)"], errors="coerce")

    df["연"] = df["일자"].dt.year
    df["월"] = df["일자"].dt.month
    df["일"] = df["일자"].dt.day
    df["요일"] = df["일자"].dt.day_name()

    return df

# ─────────────────────────────────────────────
# 4. 분석 로직 (형님 코드 + 기온 분석 기능 추가)
# ─────────────────────────────────────────────
def nth_weekday_of_month(dt: pd.Timestamp) -> int:
    first = dt.replace(day=1)
    n = 1
    cur = first
    while cur < dt:
        cur += pd.Timedelta(days=1)
        if cur.day_name() == dt.day_name():
            n += 1
    return n

def make_daily_plan_table(df_daily, target_year, target_month, monthly_total_gj, n_years=3):
    cand_years = list(range(target_year - 1, target_year - 1 - n_years * 3, -1))
    used_years = []
    df_hist = []

    for y in cand_years:
        sub = df_daily[(df_daily["연"] == y) & (df_daily["월"] == target_month)].copy()
        if sub["공급량(MJ)"].notna().sum() > 0:
            used_years.append(y)
            df_hist.append(sub)
        if len(used_years) >= n_years:
            break

    if not df_hist: return None, []

    df_hist = pd.concat(df_hist, ignore_index=True)

    def weekday_group(dname):
        if dname in ["Saturday", "Sunday"]: return "주말"
        if dname in ["Monday", "Friday"]: return "평일1"
        return "평일2"

    df_hist["요일구분"] = df_hist["요일"].apply(weekday_group)
    df_hist["n번째"] = df_hist["일자"].apply(nth_weekday_of_month)
    df_hist["기준키"] = df_hist.apply(lambda r: f"{'주말' if r['요일구분']=='주말' else r['요일']}-{r['n번째']}", axis=1)

    ratios = []
    for y in used_years:
        sub = df_hist[df_hist["연"] == y].copy()
        s = sub["공급량(MJ)"].sum()
        sub["비율"] = sub["공급량(MJ)"] / s if s != 0 else np.nan
        ratios.append(sub[["기준키", "비율"]].groupby("기준키")["비율"].mean())

    ratio_mean = pd.concat(ratios, axis=1).mean(axis=1)
    ratio_mean = ratio_mean / ratio_mean.sum()

    days_in_month = calendar.monthrange(target_year, target_month)[1]
    dates = pd.date_range(start=f"{target_year}-{target_month:02d}-01", periods=days_in_month, freq="D")
    df_plan = pd.DataFrame({"일자": dates})
    df_plan["연"] = df_plan["일자"].dt.year
    df_plan["월"] = df_plan["일자"].dt.month
    df_plan["일"] = df_plan["일자"].dt.day
    df_plan["요일"] = df_plan["일자"].dt.day_name()
    df_plan["요일구분"] = df_plan["요일"].apply(weekday_group)
    df_plan["n번째"] = df_plan["일자"].apply(nth_weekday_of_month)
    df_plan["기준키"] = df_plan.apply(lambda r: f"{'주말' if r['요일구분']=='주말' else r['요일']}-{r['n번째']}", axis=1)

    df_plan["일별비율"] = df_plan["기준키"].map(ratio_mean)
    
    # 결측치 보정 (형님 코드 로직)
    if df_plan["일별비율"].isna().any():
        weekday_ratio = df_hist.assign(비율=df_hist["공급량(MJ)"]/df_hist.groupby("연")["공급량(MJ)"].transform("sum")).groupby("요일")["비율"].mean()
        df_plan.loc[df_plan["일별비율"].isna(), "일별비율"] = df_plan.loc[df_plan["일별비율"].isna(), "요일"].map(weekday_ratio)

    df_plan["일별비율"] = df_plan["일별비율"] / df_plan["일별비율"].sum()
    df_plan["예상공급량(MJ)"] = df_plan["일별비율"] * gj_to_mj(monthly_total_gj)

    return df_plan, used_years

# ─────────────────────────────────────────────
# 5. 엑셀 다운로드 (지난주 추가한 누적현황 기능 포함)
# ─────────────────────────────────────────────
def _add_cumulative_status_sheet(wb, annual_year: int):
    """엑셀 마지막 시트에 누적계획현황 추가 (자동 수식 포함)"""
    if "누적계획현황" in wb.sheetnames: return
    ws = wb.create_sheet("누적계획현황")
    thin = Side(style="thin", color="999999")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    header_fill = PatternFill("solid", fgColor="F2F2F2")

    ws["A1"] = "기준일"; ws["B1"] = f"{annual_year}-01-01"
    ws["A1"].font = Font(bold=True); ws["B1"].font = Font(bold=True)
    
    headers = ["구분", "목표(GJ)", "누적(GJ)", "목표(m³)", "누적(m³)", "진행률(GJ)"]
    for j, h in enumerate(headers, 1):
        c = ws.cell(3, j, h)
        c.fill = header_fill; c.border = border; c.alignment = Alignment(horizontal="center")
    
    # 엑셀 수식 (연간 시트가 생성될 것이므로 참조 가능)
    d = "$B$1"
    ws["B4"] = f'=IFERROR(XLOOKUP({d},연간!$D:$D,연간!$O:$O),"")'
    ws["C4"] = "=B4" 
    ws["F4"] = '=IFERROR(IF(B4=0,"",C4/B4),"")'
    ws["B5"] = f'=SUMIFS(연간!$O:$O,연간!$A:$A,YEAR({d}),연간!$B:$B,MONTH({d}))'
    ws["C5"] = f'=SUMIFS(연간!$O:$O,연간!$D:$D,">="&EOMONTH({d},-1)+1,연간!$D:$D,"<="&{d})'
    ws["B6"] = f'=SUMIFS(연간!$O:$O,연간!$A:$A,YEAR({d}))'
    ws["C6"] = f'=SUMIFS(연간!$O:$O,연간!$D:$D,">="&DATE(YEAR({d}),1,1),연간!$D:$D,"<="&{d})'
    
    for r in range(4, 7):
        for c in range(1, 7): ws.cell(r, c).border = border

def export_daily_plan_excel(df_plan: pd.DataFrame, sheet_name: str = "일일계획", annual_mode=False, target_year=None) -> bytes:
    out = BytesIO()
    with pd.ExcelWriter(out, engine="openpyxl") as writer:
        df_x = df_plan.copy()
        df_x["예상공급량(GJ)"] = df_x["예상공급량(MJ)"].apply(mj_to_gj)
        df_x["예상공급량(㎥)"] = df_x["예상공급량(MJ)"].apply(mj_to_m3)
        cols = ["일자", "요일", "요일구분", "n번째", "기준키", "일별비율", "예상공급량(GJ)", "예상공급량(㎥)", "연", "월", "일"]
        
        # 연간 모드면 1~12월 탭을 다 만드는 게 아니라 연간 시트 하나로 통합
        if annual_mode:
            df_x[cols].to_excel(writer, sheet_name="연간", index=False)
            if target_year: _add_cumulative_status_sheet(writer.book, target_year)
        else:
            df_x[cols].to_excel(writer, sheet_name=sheet_name, index=False)
            
    return out.getvalue()

# ─────────────────────────────────────────────
# 6. 메인 앱 실행
# ─────────────────────────────────────────────
def main():
    st.sidebar.title("데이터 설정")
    up_daily = st.sidebar.file_uploader("일일 실적(XLSX)", type=["xlsx"])
    
    # 데이터 로드 (파일이 없으면 경고만 하고 멈춤 -> 에러 박스 안 뜸)
    df_daily = load_daily_data(up_daily)
    if df_daily is None:
        st.warning("⚠️ '공급량(일일실적).xlsx' 파일이 없습니다. 깃허브 폴더를 확인해주세요.")
        return

    tab = st.sidebar.radio("메뉴", ["Daily 공급량 분석", "Daily·Monthly 비교"])

    if tab == "Daily 공급량 분석":
        st.title("🏙️ 도시가스 공급량 - 일별계획 예측")
        uploaded_plan = st.sidebar.file_uploader("월별 계획(XLSX)", type=["xlsx"])
        df_plan_month = load_monthly_plan(uploaded_plan)
        
        if df_plan_month is None:
            st.warning("⚠️ '월별계획.xlsx' 파일이 없습니다. 깃허브 폴더를 확인해주세요.")
            return

        st.markdown("### ⚙️ 예측 설정")
        years = sorted(df_daily["연"].unique())
        c1, c2, c3 = st.columns(3)
        with c1: t_year = st.selectbox("계획 연도", range(max(years)-2, max(years)+3), index=3)
        with c2: t_month = st.selectbox("계획 월", range(1, 13))
        with c3: n_yrs = st.slider("학습 기간(년)", 1, 5, 3)

        # 월 계획량 찾기
        m_col = next((c for c in [f"{t_month}월", str(t_month), f"{t_month:02d}"] if c in df_plan_month.columns), None)
        if m_col is None:
            st.error(f"{t_month}월 계획 데이터를 찾을 수 없습니다.")
            return
        
        m_total_gj = float(df_plan_month.loc[0, m_col])
        st.info(f"**{t_year}년 {t_month}월 계획량**: {m_total_gj:,.0f} GJ")

        # 분석 실행
        df_res, used_yrs = make_daily_plan_table(df_daily, t_year, t_month, m_total_gj, n_yrs)
        
        if df_res is not None:
            st.success(f"✅ 학습 연도: {used_yrs}")
            
            # 차트
            fig = go.Figure()
            # Y축 데이터 준비
            y_gj = df_res["예상공급량(MJ)"].apply(mj_to_gj)
            fig.add_trace(go.Bar(x=df_res["일"], y=y_gj, name="예상(GJ)"))
            fig.add_trace(go.Scatter(x=df_res["일"], y=df_res["일별비율"], name="비율", yaxis="y2", line=dict(color='red')))
            fig.update_layout(yaxis2=dict(overlaying="y", side="right"), title=f"{t_year}년 {t_month}월 예측")
            st.plotly_chart(fig, use_container_width=True)
            
            # 테이블
            st.dataframe(df_res[["일자", "요일", "일별비율", "예상공급량(MJ)"]].style.format({"일별비율": "{:.2%}"}), use_container_width=True)
            
            # 다운로드
            st.markdown("### 💾 다운로드")
            col_d1, col_d2 = st.columns(2)
            
            # 월간
            data_m = export_daily_plan_excel(df_res, sheet_name=f"{t_month}월")
            col_d1.download_button(f"📥 {t_month}월 계획 다운로드", data_m, f"Plan_{t_year}_{t_month}.xlsx")
            
            # 연간 (1~12월 자동 생성)
            if col_d2.button(f"📥 {t_year}년 연간 전체 생성 (누적현황 포함)"):
                # 연간 데이터 생성 로직
                all_dfs = []
                for m in range(1, 13):
                    mc = next((c for c in [f"{m}월", str(m), f"{m:02d}"] if c in df_plan_month.columns), None)
                    if mc:
                        mgj = float(df_plan_month.loc[0, mc])
                        d, _ = make_daily_plan_table(df_daily, t_year, m, mgj, n_yrs)
                        if d is not None: all_dfs.append(d)
                
                if all_dfs:
                    df_year = pd.concat(all_dfs, ignore_index=True)
                    data_y = export_daily_plan_excel(df_year, annual_mode=True, target_year=t_year)
                    st.download_button(f"📥 {t_year}년 연간 파일 저장", data_y, f"AnnualPlan_{t_year}.xlsx")

    else:
        # 탭 2: 비교 및 히트맵
        st.title("📊 기온 분석 및 히트맵")
        if "평균기온(°C)" in df_daily.columns:
            # 상관도
            st.subheader("1. 기온 vs 공급량 상관계수")
            corr = df_daily[["공급량(MJ)", "평균기온(°C)"]].corr()
            st.write(corr)
            
            # 히트맵 (지난주 추가 기능)
            st.subheader("2. 일별 평균기온 히트맵")
            sel_m = st.selectbox("월 선택", range(1, 13))
            df_hm = df_daily[df_daily["월"] == sel_m]
            if not df_hm.empty:
                piv = df_hm.pivot_table(index="일", columns="연", values="평균기온(°C)")
                fig_hm = px.imshow(piv, color_continuous_scale="RdBu_r", title=f"{sel_m}월 연도별 기온")
                st.plotly_chart(fig_hm, use_container_width=True)
        else:
            st.info("기온 데이터가 없습니다.")

if __name__ == "__main__":
    main()
