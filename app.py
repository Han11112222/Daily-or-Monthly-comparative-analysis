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
    """현재 폴더와 상위 폴더를 뒤져서 파일을 찾아냅니다."""
    search_dirs = [Path(__file__).parent, Path.cwd()]
    for folder in search_dirs:
        for name in filename_candidates:
            target = folder / name
            if target.exists():
                return target
    return None

# ─────────────────────────────────────────────
# 2. 단위 변환
# ─────────────────────────────────────────────
MJ_PER_NM3 = 42.563
MJ_TO_GJ = 0.001

def mj_to_gj(mj):
    try: return float(mj) * MJ_TO_GJ
    except: return np.nan

def gj_to_mj(gj):
    try: return float(gj) / MJ_TO_GJ
    except: return np.nan

def mj_to_m3(mj):
    try: return float(mj) / MJ_PER_NM3
    except: return np.nan

def gj_to_m3(gj):
    try: return mj_to_m3(gj_to_mj(gj))
    except: return np.nan

# ─────────────────────────────────────────────
# 3. 데이터 로딩 (형님 파일 구조에 맞춤)
# ─────────────────────────────────────────────
@st.cache_data(show_spinner=False)
def load_monthly_plan(uploaded_file):
    """월별 계획 로딩: '공급량(계획_실적).xlsx' 자동 인식"""
    df = None
    # 1. 업로드 확인
    if uploaded_file:
        try: df = pd.read_excel(uploaded_file)
        except: pass
    
    # 2. 자동 탐색 (형님 파일명 우선)
    if df is None:
        candidates = ["공급량(계획_실적).xlsx", "월별계획.xlsx", "월별 계획.xlsx"]
        file_path = find_repo_file(candidates)
        if file_path:
            try: df = pd.read_excel(file_path)
            except: pass
            
    if df is None: return None

    # 컬럼명 정리 (공백 제거)
    df.columns = [str(c).strip().replace(" ", "") for c in df.columns]
    
    # '연' -> '연도'로 통일 (코드 내 일관성을 위해)
    if "연" in df.columns and "연도" not in df.columns:
        df = df.rename(columns={"연": "연도"})
        
    return df

@st.cache_data(show_spinner=False)
def load_daily_data(uploaded_file_daily):
    """일일 실적 로딩: '공급량(일일실적).xlsx' 자동 인식"""
    df_raw = None
    if uploaded_file_daily:
        try: df_raw = pd.read_excel(uploaded_file_daily)
        except: pass
        
    if df_raw is None:
        candidates = ["공급량(일일실적).xlsx", "일일실적.xlsx"]
        file_path = find_repo_file(candidates)
        if file_path:
            try: df_raw = pd.read_excel(file_path)
            except: pass
            
    if df_raw is None: return None

    # 컬럼 매핑
    col_std = {}
    for c in df_raw.columns:
        cs = str(c).strip().replace(" ", "")
        if cs in ["일자", "date", "Date"]: col_std[c] = "일자"
        if "공급량" in cs and "MJ" in cs: col_std[c] = "공급량(MJ)"
        if "평균" in cs and ("기온" in cs or "온도" in cs): col_std[c] = "평균기온(°C)"
    
    df = df_raw.rename(columns=col_std).copy()

    if "일자" not in df.columns: return None
    
    df["일자"] = pd.to_datetime(df["일자"], errors="coerce")
    df = df.dropna(subset=["일자"])

    # 공급량 숫자 변환
    if "공급량(MJ)" in df.columns:
        df["공급량(MJ)"] = pd.to_numeric(df["공급량(MJ)"], errors="coerce")

    # 파생 변수
    df["연도"] = df["일자"].dt.year # 여기서 '연도' 컬럼 생성
    df["월"] = df["일자"].dt.month
    df["일"] = df["일자"].dt.day
    df["요일"] = df["일자"].dt.day_name()

    return df

# ─────────────────────────────────────────────
# 4. 분석 로직 (형님 원본 로직)
# ─────────────────────────────────────────────
def nth_weekday_of_month(dt):
    first = dt.replace(day=1)
    n = 1
    cur = first
    while cur < dt:
        cur += pd.Timedelta(days=1)
        if cur.day_name() == dt.day_name(): n += 1
    return n

def make_daily_plan_table(df_daily, target_year, target_month, monthly_total_gj, n_years=3):
    # 학습 연도 후보 (직전 n개년)
    cand_years = list(range(target_year - 1, target_year - 1 - n_years * 3, -1))
    used_years = []
    df_hist = []

    for y in cand_years:
        # '연도' 컬럼 사용 (위에서 통일함)
        sub = df_daily[(df_daily["연도"] == y) & (df_daily["월"] == target_month)].copy()
        if not sub.empty and sub["공급량(MJ)"].sum() > 0:
            used_years.append(y)
            df_hist.append(sub)
        if len(used_years) >= n_years:
            break

    if not df_hist: return None, []

    df_hist = pd.concat(df_hist, ignore_index=True)

    def weekday_group(dname):
        return "주말" if dname in ["Saturday", "Sunday"] else "평일1" if dname in ["Monday", "Friday"] else "평일2"

    df_hist["요일구분"] = df_hist["요일"].apply(weekday_group)
    df_hist["n번째"] = df_hist["일자"].apply(nth_weekday_of_month)
    df_hist["기준키"] = df_hist.apply(lambda r: f"{'주말' if r['요일구분']=='주말' else r['요일']}-{r['n번째']}", axis=1)

    # 비율 계산
    ratios = []
    for y in used_years:
        sub = df_hist[df_hist["연도"] == y].copy()
        s = sub["공급량(MJ)"].sum()
        sub["비율"] = sub["공급량(MJ)"] / s if s != 0 else np.nan
        ratios.append(sub[["기준키", "비율"]].groupby("기준키")["비율"].mean())

    ratio_mean = pd.concat(ratios, axis=1).mean(axis=1)
    if ratio_mean.sum() > 0: ratio_mean /= ratio_mean.sum()

    # 타겟 월 달력 생성
    days_in_month = calendar.monthrange(target_year, target_month)[1]
    dates = pd.date_range(start=f"{target_year}-{target_month:02d}-01", periods=days_in_month, freq="D")
    df_plan = pd.DataFrame({"일자": dates})
    df_plan["연도"] = df_plan["일자"].dt.year
    df_plan["월"] = df_plan["일자"].dt.month
    df_plan["일"] = df_plan["일자"].dt.day
    df_plan["요일"] = df_plan["일자"].dt.day_name()
    df_plan["요일구분"] = df_plan["요일"].apply(weekday_group)
    df_plan["n번째"] = df_plan["일자"].apply(nth_weekday_of_month)
    df_plan["기준키"] = df_plan.apply(lambda r: f"{'주말' if r['요일구분']=='주말' else r['요일']}-{r['n번째']}", axis=1)

    # 비율 매핑
    df_plan["일별비율"] = df_plan["기준키"].map(ratio_mean)
    
    # 결측치 보정
    if df_plan["일별비율"].isna().any():
        weekday_ratio = df_hist.assign(비율=df_hist["공급량(MJ)"]/df_hist.groupby("연도")["공급량(MJ)"].transform("sum")).groupby("요일")["비율"].mean()
        df_plan.loc[df_plan["일별비율"].isna(), "일별비율"] = df_plan.loc[df_plan["일별비율"].isna(), "요일"].map(weekday_ratio)

    df_plan["일별비율"] = df_plan["일별비율"].fillna(1/len(df_plan))
    if df_plan["일별비율"].sum() > 0:
        df_plan["일별비율"] /= df_plan["일별비율"].sum()

    # 계획량 반영 (GJ -> MJ)
    monthly_total_mj = gj_to_mj(monthly_total_gj)
    df_plan["예상공급량(MJ)"] = df_plan["일별비율"] * monthly_total_mj

    return df_plan, used_years

# ─────────────────────────────────────────────
# 5. 엑셀 다운로드 (누적현황 기능 포함)
# ─────────────────────────────────────────────
def _add_cumulative_sheet(wb, target_year):
    if "누적계획현황" in wb.sheetnames: return
    ws = wb.create_sheet("누적계획현황")
    thin = Side(style="thin", color="999999")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    fill = PatternFill("solid", fgColor="F2F2F2")
    ws["A1"] = "기준일"; ws["B1"] = f"{target_year}-01-01"
    
    headers = ["구분", "목표(GJ)", "누적(GJ)", "목표(m³)", "누적(m³)", "진행률"]
    for i, h in enumerate(headers, 1):
        c = ws.cell(3, i, h)
        c.fill = fill; c.border = border; c.alignment = Alignment("center")
        
    d = "$B$1"
    ws["B4"] = f'=IFERROR(XLOOKUP({d},연간!$A:$A,연간!$F:$F),"")' 
    ws["C4"] = "=B4"
    ws["F4"] = '=IFERROR(IF(B4=0,"",C4/B4),"")'
    for r in range(4, 7):
        for c in range(1, 7): ws.cell(r, c).border = border

def export_excel(df_plan, sheet_name="일일계획", annual=False, year=None):
    out = BytesIO()
    with pd.ExcelWriter(out, engine="openpyxl") as writer:
        df_x = df_plan.copy()
        df_x["예상공급량(GJ)"] = df_x["예상공급량(MJ)"].apply(mj_to_gj)
        df_x["예상공급량(㎥)"] = df_x["예상공급량(MJ)"].apply(mj_to_m3)
        cols = ["일자", "요일", "요일구분", "n번째", "기준키", "일별비율", "예상공급량(GJ)", "예상공급량(㎥)"]
        
        if annual:
            df_x.to_excel(writer, sheet_name="연간", index=False)
            if year: _add_cumulative_sheet(writer.book, year)
        else:
            df_x[cols].to_excel(writer, sheet_name=sheet_name, index=False)
    return out.getvalue()

# ─────────────────────────────────────────────
# 6. 메인 앱 (UI)
# ─────────────────────────────────────────────
def main():
    st.sidebar.title("데이터 로드")
    up_daily = st.sidebar.file_uploader("일일 실적(선택)", type=["xlsx"], key="daily")
    
    # 1. 일일 데이터 로드 (자동 탐색)
    df_daily = load_daily_data(up_daily)
    
    if df_daily is None:
        st.warning("⚠️ '공급량(일일실적).xlsx' 파일을 찾을 수 없습니다. 깃허브에 파일이 있는지 확인해주세요.")
        return

    tab = st.sidebar.radio("메뉴", ["Daily 공급량 분석", "Daily·Monthly 비교"])

    # --- 탭 1 ---
    if tab == "Daily 공급량 분석":
        st.title("🏙️ 도시가스 공급량 - 일별계획 예측")
        up_plan = st.sidebar.file_uploader("월별 계획(선택)", type=["xlsx"], key="plan")
        df_plan = load_monthly_plan(up_plan)
        
        if df_plan is None:
            st.warning("⚠️ '공급량(계획_실적).xlsx' 파일을 찾을 수 없습니다.")
            return

        # 2. 연도 선택 (★일일 실적 파일 기준★ - KeyError 해결)
        years = sorted(df_daily["연도"].unique())
        default_year = max(years) + 1 if years else 2026
        
        c1, c2, c3 = st.columns(3)
        with c1: t_year = st.selectbox("계획 연도", range(default_year-5, default_year+3), index=5)
        with c2: t_month = st.selectbox("계획 월", range(1, 13))
        with c3: n_yrs = st.slider("학습 기간", 1, 5, 3)

        # 3. 월 계획량 추출 (★형님 파일 구조: 연도, 월, 계획값★)
        # 파일에 '연도'와 '월' 컬럼이 있으면 해당 행을 찾음
        target_row = pd.DataFrame()
        if "연도" in df_plan.columns and "월" in df_plan.columns:
            target_row = df_plan[(df_plan["연도"] == t_year) & (df_plan["월"] == t_month)]
        
        if target_row.empty:
            st.error(f"{t_year}년 {t_month}월 계획 데이터를 찾을 수 없습니다.")
            return
        
        # '계획'이라는 글자가 들어간 컬럼 찾기 (사업계획제출_MJ 등)
        val_col = next((c for c in df_plan.columns if "계획" in c), None)
        if not val_col:
            # 계획 컬럼이 없으면 3번째 컬럼(숫자일 확률 높음)을 사용
            val_col = df_plan.columns[2] if len(df_plan.columns) > 2 else None

        if val_col:
            m_total_gj = float(target_row.iloc[0][val_col])
            # MJ 단위면 GJ로 변환 (숫자가 크면 MJ로 간주)
            if m_total_gj > 1000000: m_total_gj = mj_to_gj(m_total_gj)
        else:
            st.error("계획량 컬럼을 찾을 수 없습니다.")
            return

        st.info(f"**{t_year}년 {t_month}월 목표**: {m_total_gj:,.0f} GJ")

        # 4. 분석 실행
        df_res, used_yrs = make_daily_plan_table(df_daily, t_year, t_month, m_total_gj, n_yrs)
        
        if df_res is not None:
            st.success(f"✅ 학습 연도: {used_yrs}")
            
            # 차트
            fig = go.Figure()
            y_gj = df_res["예상공급량(MJ)"].apply(mj_to_gj)
            fig.add_trace(go.Bar(x=df_res["일"], y=y_gj, name="예상(GJ)"))
            fig.add_trace(go.Scatter(x=df_res["일"], y=df_res["일별비율"], name="비율", yaxis="y2", line=dict(color='red')))
            fig.update_layout(yaxis2=dict(overlaying="y", side="right"), title=f"{t_year}년 {t_month}월 예측")
            st.plotly_chart(fig, use_container_width=True)
            
            # 테이블
            st.dataframe(df_res[["일자", "요일", "일별비율", "예상공급량(MJ)"]].style.format({"일별비율": "{:.2%}"}), use_container_width=True)
            
            # 다운로드
            c_d1, c_d2 = st.columns(2)
            c_d1.download_button("📥 월간 다운로드", export_excel(df_res, f"{t_month}월"), f"Plan_{t_year}_{t_month}.xlsx")
            
            if c_d2.button("📥 연간 전체 생성"):
                all_dfs = []
                for m in range(1, 13):
                    t_row = df_plan[(df_plan["연도"] == t_year) & (df_plan["월"] == m)]
                    if not t_row.empty:
                        mgj = float(t_row.iloc[0][val_col])
                        if mgj > 1000000: mgj = mj_to_gj(mgj)
                        d, _ = make_daily_plan_table(df_daily, t_year, m, mgj, n_yrs)
                        if d is not None: all_dfs.append(d)
                
                if all_dfs:
                    full_df = pd.concat(all_dfs, ignore_index=True)
                    st.download_button("📥 파일 저장", export_excel(full_df, annual=True, year=t_year), f"Annual_{t_year}.xlsx")

        else:
            st.warning("분석할 과거 데이터가 부족합니다.")

    # --- 탭 2 ---
    else:
        st.title("📊 기온 분석 및 히트맵")
        if "평균기온(°C)" in df_daily.columns:
            st.subheader("1. 기온 vs 공급량 상관계수")
            corr = df_daily[["공급량(MJ)", "평균기온(°C)"]].corr()
            st.write(corr)
            
            st.subheader("2. 일별 평균기온 히트맵")
            sel_m = st.selectbox("월 선택", range(1, 13))
            df_hm = df_daily[df_daily["월"] == sel_m]
            if not df_hm.empty:
                piv = df_hm.pivot_table(index="일", columns="연도", values="평균기온(°C)")
                fig_hm = px.imshow(piv, color_continuous_scale="RdBu_r", title=f"{sel_m}월 연도별 기온")
                st.plotly_chart(fig_hm, use_container_width=True)
        else:
            st.info("기온 데이터가 없습니다.")

if __name__ == "__main__":
    main()
