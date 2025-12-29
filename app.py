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

# =========================================================
# 1. 파일 자동 탐색 함수 (이 부분만 추가되었습니다)
# =========================================================
def find_repo_file(filename_candidates):
    """
    현재 폴더나 상위 폴더에서 파일명 후보들을 검색하여
    가장 먼저 발견된 파일의 경로를 반환합니다.
    """
    # 검색할 경로들: 현재 파일 위치, 현재 작업 디렉토리
    search_dirs = [Path(__file__).parent, Path.cwd()]
    
    for folder in search_dirs:
        for name in filename_candidates:
            target = folder / name
            if target.exists():
                return target
    return None

# =========================================================
# 2. 단위/환산
# =========================================================
MJ_PER_NM3 = 42.563  # MJ/Nm3
MJ_TO_GJ = 0.001     # 1 MJ = 0.001 GJ

def mj_to_gj(mj: float) -> float:
    try: return float(mj) * MJ_TO_GJ
    except Exception: return np.nan

def gj_to_mj(gj: float) -> float:
    try: return float(gj) / MJ_TO_GJ
    except Exception: return np.nan

def mj_to_m3(mj: float) -> float:
    try: return float(mj) / MJ_PER_NM3
    except Exception: return np.nan

def gj_to_m3(gj: float) -> float:
    try: return mj_to_m3(gj_to_mj(gj))
    except Exception: return np.nan

# =========================================================
# 3. 데이터 로딩 (사용자님 코드 로직 복원 + 자동탐색)
# =========================================================
@st.cache_data(show_spinner=False)
def load_monthly_plan(uploaded_file) -> pd.DataFrame:
    """
    월별 계획을 읽어오는 함수
    1. 업로드된 파일이 있으면 그걸 씀
    2. 없으면 repo 내 파일을 자동 탐색함
    """
    df = None
    
    # 1. 업로드 파일 확인
    if uploaded_file is not None:
        try: df = pd.read_excel(uploaded_file)
        except: pass
    
    # 2. 자동 탐색 (업로드가 없을 경우)
    if df is None:
        # 찾을 파일명 후보들
        candidates = ["월별계획.xlsx", "월별 계획.xlsx", "공급량(계획_실적).xlsx", "plan.xlsx"]
        file_path = find_repo_file(candidates)
        if file_path:
            try: df = pd.read_excel(file_path)
            except: pass
    
    # 3. 그래도 없으면 None
    if df is None:
        return None

    # --- 사용자님의 기존 로직 그대로 적용 ---
    col_map = {}
    for c in df.columns:
        cs = str(c).strip()
        if cs in ["구분", "항목", "분류"]:
            col_map[c] = "구분"
    df = df.rename(columns=col_map)

    # 월 컬럼 정리
    month_cols = []
    for m in range(1, 13):
        for cand in [f"{m}월", str(m), f"{m:02d}"]:
            if cand in df.columns:
                month_cols.append(cand)
                break

    # 연간합계 컬럼
    annual_col = None
    for cand in ["연간합계", "연간", "합계", "Total", "TOTAL"]:
        if cand in df.columns:
            annual_col = cand
            break

    # 수치 변환
    for c in month_cols + ([annual_col] if annual_col else []):
        if c is None: continue
        df[c] = pd.to_numeric(df[c], errors="coerce")

    return df


@st.cache_data(show_spinner=False)
def load_daily_data(uploaded_file_daily) -> pd.DataFrame:
    """
    일일 실적 로딩
    1. 업로드된 파일 우선
    2. 없으면 repo 내 파일 자동 탐색
    """
    df_raw = None
    
    # 1. 업로드 파일 확인
    if uploaded_file_daily is not None:
        try: df_raw = pd.read_excel(uploaded_file_daily)
        except: pass
        
    # 2. 자동 탐색
    if df_raw is None:
        candidates = ["공급량(일일실적).xlsx", "일일실적.xlsx", "daily_data.xlsx", "공급량.xlsx"]
        file_path = find_repo_file(candidates)
        if file_path:
            try: df_raw = pd.read_excel(file_path)
            except: pass
            
    if df_raw is None:
        return None

    # --- 사용자님의 기존 로직 그대로 적용 ---
    col_std = {}
    for c in df_raw.columns:
        cs = str(c).strip()
        if cs in ["일자", "date", "Date"]: col_std[c] = "일자"
        if "공급량" in cs and "MJ" in cs: col_std[c] = "공급량(MJ)"
        if "공급량" in cs and ("GJ" in cs or "Gj" in cs): col_std[c] = "공급량(GJ)"
        if "평균" in cs and ("기온" in cs or "온도" in cs): col_std[c] = "평균기온(°C)"
    
    df = df_raw.rename(columns=col_std).copy()

    if "일자" not in df.columns:
        return None

    df["일자"] = pd.to_datetime(df["일자"], errors="coerce")
    
    # 공급량 정리
    if "공급량(MJ)" not in df.columns and "공급량(GJ)" in df.columns:
        df["공급량(MJ)"].apply(gj_to_mj)
    
    if "공급량(MJ)" in df.columns:
        df["공급량(MJ)"] = pd.to_numeric(df["공급량(MJ)"], errors="coerce")

    # 파생 변수 (기존 로직)
    df["연"] = df["일자"].dt.year
    df["월"] = df["일자"].dt.month
    df["일"] = df["일자"].dt.day
    df["요일"] = df["일자"].dt.day_name()

    return df

# =========================================================
# 4. 일별 계획 산출 로직 (사용자님 코드 로직 유지)
# =========================================================
def nth_weekday_of_month(dt: pd.Timestamp) -> int:
    first = dt.replace(day=1)
    n = 1
    cur = first
    while cur < dt:
        cur += pd.Timedelta(days=1)
        if cur.day_name() == dt.day_name():
            n += 1
    return n

def make_daily_plan_table(
    df_daily: pd.DataFrame,
    target_year: int,
    target_month: int,
    monthly_total_gj: float,
    n_years: int = 3,
) -> tuple[pd.DataFrame, list[int]]:
    
    # 학습연도 후보
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

    if len(df_hist) == 0:
        return None, [] # 에러 대신 None 반환

    df_hist = pd.concat(df_hist, ignore_index=True)

    def weekday_group(dname: str) -> str:
        if dname in ["Saturday", "Sunday"]: return "주말"
        if dname in ["Monday", "Friday"]: return "평일1"
        return "평일2"

    df_hist["요일구분"] = df_hist["요일"].apply(weekday_group)
    df_hist["n번째"] = df_hist["일자"].apply(nth_weekday_of_month)

    def make_key(row) -> str:
        if row["요일구분"] == "주말": return f"주말-{row['n번째']}"
        return f"{row['요일']}-{row['n번째']}"

    df_hist["기준키"] = df_hist.apply(make_key, axis=1)

    ratios = []
    for y in used_years:
        sub = df_hist[df_hist["연"] == y].copy()
        s = sub["공급량(MJ)"].sum()
        sub["비율"] = sub["공급량(MJ)"] / s if s != 0 else np.nan
        ratios.append(sub[["기준키", "비율"]].groupby("기준키")["비율"].mean())

    ratio_mean = pd.concat(ratios, axis=1).mean(axis=1)
    if ratio_mean.sum() > 0:
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
    df_plan["기준키"] = df_plan.apply(make_key, axis=1)

    df_plan["일별비율"] = df_plan["기준키"].map(ratio_mean)

    if df_plan["일별비율"].isna().any():
        weekday_ratio = (
            df_hist.assign(비율=df_hist["공급량(MJ)"] / df_hist.groupby("연")["공급량(MJ)"].transform("sum"))
            .groupby("요일")["비율"].mean()
        )
        df_plan.loc[df_plan["일별비율"].isna(), "일별비율"] = df_plan.loc[df_plan["일별비율"].isna(), "요일"].map(weekday_ratio)

    df_plan["일별비율"] = df_plan["일별비율"].fillna(1/len(df_plan))
    df_plan["일별비율"] = df_plan["일별비율"] / df_plan["일별비율"].sum()

    monthly_total_mj = gj_to_mj(monthly_total_gj)
    df_plan["예상공급량(MJ)"] = df_plan["일별비율"] * monthly_total_mj

    df_plan = df_plan[["일자", "요일", "요일구분", "n번째", "기준키", "일별비율", "예상공급량(MJ)", "연", "월", "일"]].copy()

    return df_plan, used_years

# =========================================================
# 5. 엑셀 다운로드 (서식 포함)
# =========================================================
def export_daily_plan_excel(df_plan: pd.DataFrame, sheet_name: str = "일일계획") -> bytes:
    out = BytesIO()
    with pd.ExcelWriter(out, engine="openpyxl") as writer:
        df_x = df_plan.copy()
        df_x["예상공급량(GJ)"] = df_x["예상공급량(MJ)"].apply(mj_to_gj)
        df_x["예상공급량(㎥)"] = df_x["예상공급량(MJ)"].apply(mj_to_m3)
        cols = ["일자", "요일", "요일구분", "n번째", "기준키", "일별비율", "예상공급량(GJ)", "예상공급량(㎥)"]
        df_x[cols].to_excel(writer, sheet_name=sheet_name, index=False)
        
        # 서식 적용
        ws = writer.book[sheet_name]
        thin = Side(style="thin", color="000000")
        for row in ws.iter_rows():
            for cell in row:
                cell.border = Border(top=thin, left=thin, right=thin, bottom=thin)
                cell.alignment = Alignment(horizontal='center', vertical='center')
    return out.getvalue()

# =========================================================
# 6. 메인 앱 (UI 구성)
# =========================================================
def main():
    st.set_page_config(page_title="도시가스 공급량 - 일별계획 예측", layout="wide")
    
    # 사이드바
    st.sidebar.title("데이터 로드")
    up_daily = st.sidebar.file_uploader("일일 실적(선택)", type=["xlsx"], key="daily_upload")
    
    # 탭 구성
    tab1, tab2 = st.tabs(["📅 Daily 공급량 분석", "📊 Daily·Monthly 비교"])
    
    # 데이터 로드 (파일 없어도 자동 탐색 시도)
    df_daily = load_daily_data(up_daily)
    
    # df_daily가 없으면 경고만 띄우고 중단 (KeyError 방지)
    if df_daily is None:
        st.warning("⚠️ '공급량(일일실적).xlsx' 파일을 찾을 수 없습니다.")
        st.write("로컬 폴더에 파일이 있는지, 혹은 깃허브 레포지토리에 파일이 올라가 있는지 확인해주세요.")
        return # 더 이상 진행 안 함
    
    # --- 탭 1 ---
    with tab1:
        st.subheader("🗓️ Daily공급량 분석 — 최근 N년 패턴 기반 일별 계획")
        up_plan = st.file_uploader("월별 계획(선택)", type=["xlsx"], key="plan_upload")
        
        df_plan_month = load_monthly_plan(up_plan)
        
        if df_plan_month is None:
            st.warning("⚠️ '월별계획.xlsx' 파일을 찾을 수 없습니다.")
            st.stop()
            
        # 설정 UI
        st.markdown("### ⚙️ 계획 연도/월 설정")
        
        # 여기서 KeyError 수정: df_plan의 '연도' 컬럼을 찾는게 아니라, 
        # df_daily(실적)의 연도를 기준으로 미래를 예측하도록 수정 (사용자님 원본 코드 로직 복구)
        years = sorted(df_daily["연"].dropna().unique().astype(int).tolist())
        default_year = max(years) + 1 if len(years) > 0 else 2026
        
        c1, c2, c3 = st.columns(3)
        with c1: target_year = st.selectbox("계획 연도", options=list(range(default_year - 5, default_year + 3)), index=5)
        with c2: target_month = st.selectbox("계획 월", options=list(range(1, 13)), index=0)
        with c3: n_years = st.slider("학습 기간(년)", 1, 5, 3)

        # 월 컬럼 찾기
        month_col = None
        for cand in [f"{target_month}월", str(target_month), f"{target_month:02d}"]:
            if cand in df_plan_month.columns:
                month_col = cand
                break
        
        if month_col is None:
            st.error(f"월별계획 파일에서 {target_month}월 데이터를 찾을 수 없습니다.")
            st.stop()
            
        # 월 합계 추출
        try:
            monthly_total_gj = float(df_plan_month.loc[0, month_col])
        except:
            st.error("월별 계획 파일 데이터 형식이 올바르지 않습니다.")
            st.stop()
            
        # 분석 실행
        df_res, used_years = make_daily_plan_table(df_daily, target_year, target_month, monthly_total_gj, n_years)
        
        if df_res is not None:
            st.success(f"✅ {used_years}년 실적을 기반으로 분석 완료")
            st.markdown(f"**{target_year}년 {target_month}월 목표**: {monthly_total_gj:,.0f} GJ")
            
            # 표 출력
            view = df_res.copy()
            view["예상공급량(GJ)"] = view["예상공급량(MJ)"].apply(mj_to_gj)
            st.dataframe(view[["일자", "요일", "일별비율", "예상공급량(GJ)"]].style.format({"일별비율": "{:.2%}", "예상공급량(GJ)": "{:,.0f}"}), use_container_width=True)
            
            # 그래프
            fig = go.Figure()
            fig.add_trace(go.Bar(x=df_res["일"], y=df_res["예상공급량(MJ)"].apply(mj_to_gj), name="예상(GJ)"))
            fig.add_trace(go.Scatter(x=df_res["일"], y=df_res["일별비율"], name="비율", yaxis="y2", line=dict(color='red')))
            fig.update_layout(yaxis2=dict(overlaying="y", side="right"), title=f"{target_year}년 {target_month}월 예측", legend=dict(orientation="h", y=1.1))
            st.plotly_chart(fig, use_container_width=True)
            
            # 다운로드
            st.download_button("📥 엑셀 다운로드", export_daily_plan_excel(df_res), f"Plan_{target_year}_{target_month}.xlsx")
            
        else:
            st.warning("분석할 과거 데이터가 부족합니다.")

    # --- 탭 2 ---
    with tab2:
        st.title("📊 데이터 비교 및 상관도")
        if "평균기온(°C)" in df_daily.columns:
            corr = df_daily[["공급량(MJ)", "평균기온(°C)"]].corr()
            c1, c2 = st.columns([1, 2])
            with c1: st.write("#### 상관계수", corr)
            with c2: 
                fig = px.scatter(df_daily, x="평균기온(°C)", y="공급량(MJ)", title="기온 vs 공급량")
                st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("기온 데이터가 없어 상관도 분석을 할 수 없습니다.")

if __name__ == "__main__":
    main()
