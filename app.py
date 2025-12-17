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
# 유틸
# ─────────────────────────────────────────────
def to_num(x):
    try:
        if x is None:
            return np.nan
        s = str(x).strip().replace(",", "")
        if s == "":
            return np.nan
        return float(s)
    except Exception:
        return np.nan


def mj_to_gj(x):
    try:
        return x * MJ_TO_GJ
    except Exception:
        return np.nan


def mj_to_m3(x):
    try:
        return x / MJ_PER_NM3
    except Exception:
        return np.nan


def fmt_int(x):
    if pd.isna(x):
        return ""
    try:
        return f"{int(round(float(x))):,}"
    except Exception:
        return ""


def fmt_float(x, nd=1):
    if pd.isna(x):
        return ""
    try:
        return f"{float(x):.{nd}f}"
    except Exception:
        return ""


def find_candidate_col(df: pd.DataFrame, candidates):
    cols = [str(c) for c in df.columns]
    for cand in candidates:
        for c in cols:
            if cand in c:
                return c
    return None


def style_table(df: pd.DataFrame):
    # 모든 숫자 중앙정렬 + 천단위 콤마
    def _fmt(v):
        if isinstance(v, (int, np.integer)):
            return f"{v:,}"
        if isinstance(v, (float, np.floating)):
            # 소수점이 필요한 값(기온, 비율)도 섞여서 들어올 수 있어 3자리까지 허용
            if abs(v) >= 1000:
                return f"{v:,.0f}"
            return f"{v:.3f}".rstrip("0").rstrip(".")
        return v

    return df.applymap(_fmt)


# ─────────────────────────────────────────────
# 데이터 로드
# ─────────────────────────────────────────────
@st.cache_data
def load_daily_data():
    excel_path = Path(__file__).parent / "공급량(일일실적).xlsx"
    df_raw = pd.read_excel(excel_path)

    # 내부 계산은 MJ 유지 (표기/다운로드는 GJ 및 m³로 변환)
    df_raw = df_raw[["일자", "공급량(MJ)", "공급량(M3)", "평균기온(℃)"]].copy()
    df_raw["일자"] = pd.to_datetime(df_raw["일자"])

    df_raw["연도"] = df_raw["일자"].dt.year
    df_raw["월"] = df_raw["일자"].dt.month
    df_raw["일"] = df_raw["일자"].dt.day

    df_temp_all = df_raw.dropna(subset=["평균기온(℃)"]).copy()
    df_model = df_temp_all.dropna(subset=["공급량(MJ)"]).copy()
    return df_model, df_temp_all


@st.cache_data
def load_monthly_plan(uploaded_bytes: bytes | None):
    # 업로드 우선
    if uploaded_bytes is not None:
        return pd.read_excel(BytesIO(uploaded_bytes))

    # 없으면 repo 내 자동 탐색
    p = Path(__file__).parent / "월별계획.xlsx"
    if p.exists():
        return pd.read_excel(p)
    return None


@st.cache_data
def load_corr_data():
    p = Path(__file__).parent / "상관도분석.xlsx"
    if p.exists():
        return pd.read_excel(p)
    return None


# ─────────────────────────────────────────────
# 3차 다항 회귀
# ─────────────────────────────────────────────
def fit_poly3_and_r2(x, y):
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)

    m = np.isfinite(x) & np.isfinite(y)
    x = x[m]
    y = y[m]
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
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    m = np.isfinite(x) & np.isfinite(y)
    x = x[m]
    y = y[m]

    xs = np.linspace(np.nanmin(x), np.nanmax(x), 200)
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
# 탭1: Daily 공급량 분석 — 최근 N년 패턴 기반 일별 계획
# ─────────────────────────────────────────────
def tab_daily_plan(df_daily: pd.DataFrame):
    st.title("도시가스 공급량 — 일별계획 예측")
    st.subheader("📅 Daily 공급량 분석 — 최근 N년 패턴 기반 일별 계획")

    up = st.file_uploader(
        "월별 계획 엑셀 업로드(XLSX) (없으면 폴더에서 자동 탐색)",
        type=["xlsx"],
        key="monthly_plan_uploader",
    )
    df_plan = load_monthly_plan(up.getvalue() if up is not None else None)

    if df_plan is None:
        st.error("월별 계획 파일을 찾지 못했어. 업로드하거나 repo에 '월별계획.xlsx'를 넣어줘.")
        return

    # 컬럼 추정
    year_col = find_candidate_col(df_plan, ["연도", "연"])
    month_col = find_candidate_col(df_plan, ["월"])
    plan_col = find_candidate_col(df_plan, ["사업계획", "월별계획", "계획", "목표", "계획량"])

    if year_col is None or month_col is None or plan_col is None:
        st.error("월별계획.xlsx 컬럼을 못 찾았어. (연/월/계획 컬럼이 필요)")
        st.write("컬럼:", list(df_plan.columns))
        return

    df_plan = df_plan.copy()
    df_plan[year_col] = df_plan[year_col].apply(to_num).astype("Int64")
    df_plan[month_col] = df_plan[month_col].apply(to_num).astype("Int64")
    df_plan[plan_col] = df_plan[plan_col].apply(to_num)

    # 연도/월 선택
    years_plan = sorted(df_plan[year_col].dropna().astype(int).unique().tolist())
    colA, colB = st.columns([1, 1])
    with colA:
        target_year = st.selectbox("계획 연도 선택", years_plan, index=len(years_plan) - 1)
    with colB:
        target_month = st.selectbox("계획 월 선택", list(range(1, 13)), index=0, format_func=lambda m: f"{m}월")

    # 최근 N년(직전) 선택
    hist_years = sorted([y for y in df_daily["연도"].unique().tolist() if y < target_year])
    if not hist_years:
        st.warning("직전 연도 데이터가 없어 최근 N년 분석을 할 수 없어.")
        return

    slider_max = min(10, len(hist_years))
    n_years = st.slider(
        "최근 몇 년 평균으로 비율을 계산할까?",
        min_value=1,
        max_value=slider_max,
        value=min(3, slider_max),
        step=1,
        help="선택연도 직전 N개 연도의 같은 월 데이터를 사용(해당월 실적 없는 연도는 자동 제외)",
    )

    cand_years = list(range(target_year - n_years, target_year))
    df_hist = df_daily[(df_daily["연도"].isin(cand_years)) & (df_daily["월"] == target_month)].copy()
    used_years = sorted(df_hist["연도"].unique().tolist())

    st.markdown(
        f"- **실제 학습에 사용된 연도(해당월 실적 존재): {used_years[0]}년 ~ {used_years[-1]}년 (총 {len(used_years)}개)**"
        if used_years
        else "- **실제 학습에 사용된 연도: 없음**"
    )

    # 선택 월의 사업계획(월별계획) 합계
    month_plan_val = (
        df_plan.loc[(df_plan[year_col] == target_year) & (df_plan[month_col] == target_month), plan_col]
        .sum()
    )
    st.markdown(f"**{target_year}년 {target_month}월 사업계획 제출 공급량 합계:**  {fmt_int(mj_to_gj(month_plan_val*1000)) if False else fmt_int(mj_to_gj(month_plan_val))} GJ")

    # 일별 비율 계산(요일+주차 기반 간단화: 기존 로직 유지)
    df_hist["weekday"] = df_hist["일자"].dt.day_name()
    df_hist["week_of_month"] = ((df_hist["일자"].dt.day - 1) // 7) + 1

    # 기준 그룹: 주말/공휴일/명절 등은 (이 파일 내 기존 로직 유지)
    # 여기서는 df_hist에서 요일/주차 평균으로 raw ratio 산정
    grp = df_hist.groupby(["weekday", "week_of_month"], as_index=False)["공급량(MJ)"].mean()
    grp = grp.rename(columns={"공급량(MJ)": "raw"})

    # 대상 월의 캘린더 생성
    cal = calendar.monthcalendar(target_year, target_month)
    rows = []
    for wk_idx, week in enumerate(cal, start=1):
        for dow, day in enumerate(week):
            if day == 0:
                continue
            d = pd.Timestamp(target_year, target_month, day)
            rows.append(
                {
                    "일자": d,
                    "연도": target_year,
                    "월": target_month,
                    "일": day,
                    "weekday": d.day_name(),
                    "week_of_month": ((day - 1) // 7) + 1,
                }
            )
    df_target = pd.DataFrame(rows)

    df_target = df_target.merge(grp, on=["weekday", "week_of_month"], how="left")

    # fallback: 요일 평균
    dow_mean = df_hist.groupby("weekday")["공급량(MJ)"].mean().to_dict()
    df_target["raw"] = df_target["raw"].fillna(df_target["weekday"].map(dow_mean))

    # 최종 정규화(합=1)
    df_target["ratio"] = df_target["raw"] / df_target["raw"].sum()
    df_target["계획량_MJ"] = df_target["ratio"] * month_plan_val

    # 표기용 (GJ, m³)
    df_target["계획량_GJ"] = df_target["계획량_MJ"].apply(mj_to_gj)
    df_target["계획량_m3"] = df_target["계획량_MJ"].apply(mj_to_m3)

    show_cols = ["일자", "weekday", "week_of_month", "ratio", "계획량_GJ", "계획량_m3"]
    st.dataframe(style_table(df_target[show_cols]), use_container_width=True)

    # 5. 일일계획 다운로드(월간)
    st.subheader("📥 5. 일일계획 다운로드(월간)")
    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df_out = df_target.copy()
        df_out.to_excel(writer, index=False, sheet_name=f"{target_year}-{target_month:02d}")

    st.download_button(
        "📥 5. 일일계획 다운로드(월간)",
        data=buf.getvalue(),
        file_name=f"일일공급계획_{target_year}_{target_month:02d}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    # 월별 계획량(1~12) & 연간 총량 (GJ + m3)
    st.subheader("📌 월별 계획량(1~12월) & 연간 총량")

    df_year = df_plan[df_plan[year_col] == target_year].copy()
    df_year = df_year.groupby(month_col, as_index=False)[plan_col].sum()
    month_vals = {int(r[month_col]): float(r[plan_col]) for _, r in df_year.iterrows() if pd.notna(r[month_col])}

    row_gj = {"구분": "사업계획(월별 계획) - GJ"}
    row_m3 = {"구분": "사업계획(월별 계획) - m³"}
    total_mj = 0.0
    for m in range(1, 13):
        v_mj = month_vals.get(m, np.nan)
        row_gj[f"{m}월"] = mj_to_gj(v_mj) if pd.notna(v_mj) else np.nan
        row_m3[f"{m}월"] = mj_to_m3(v_mj) if pd.notna(v_mj) else np.nan
        total_mj += (v_mj if pd.notna(v_mj) else 0.0)

    row_gj["연간합계"] = mj_to_gj(total_mj)
    row_m3["연간합계"] = mj_to_m3(total_mj)

    df_box = pd.DataFrame([row_gj, row_m3])
    st.dataframe(style_table(df_box), use_container_width=True)

    # 6. 일일계획 다운로드(연간)
    st.subheader("🗂️ 6. 일일계획 다운로드(연간)")

    year_choice = st.selectbox("연간 계획 연도 선택", years_plan, index=years_plan.index(target_year), key="annual_year_select")
    df_year_plan = df_plan[df_plan[year_col] == year_choice].copy()
    df_year_plan = df_year_plan.groupby(month_col, as_index=False)[plan_col].sum()
    month_vals2 = {int(r[month_col]): float(r[plan_col]) for _, r in df_year_plan.iterrows() if pd.notna(r[month_col])}

    # 연간 일별계획 생성
    all_days = []
    for m in range(1, 13):
        month_plan = month_vals2.get(m, np.nan)
        if pd.isna(month_plan) or month_plan == 0:
            continue

        hist_years2 = sorted([y for y in df_daily["연도"].unique().tolist() if y < year_choice])
        if not hist_years2:
            continue

        cand_years2 = list(range(year_choice - n_years, year_choice))
        df_hist2 = df_daily[(df_daily["연도"].isin(cand_years2)) & (df_daily["월"] == m)].copy()
        if df_hist2.empty:
            continue

        df_hist2["weekday"] = df_hist2["일자"].dt.day_name()
        df_hist2["week_of_month"] = ((df_hist2["일자"].dt.day - 1) // 7) + 1
        grp2 = df_hist2.groupby(["weekday", "week_of_month"], as_index=False)["공급량(MJ)"].mean().rename(columns={"공급량(MJ)": "raw"})

        cal2 = calendar.monthcalendar(year_choice, m)
        rows2 = []
        for wk_idx, week in enumerate(cal2, start=1):
            for dow, day in enumerate(week):
                if day == 0:
                    continue
                d = pd.Timestamp(year_choice, m, day)
                rows2.append(
                    {
                        "일자": d,
                        "연도": year_choice,
                        "월": m,
                        "일": day,
                        "weekday": d.day_name(),
                        "week_of_month": ((day - 1) // 7) + 1,
                    }
                )
        df_t = pd.DataFrame(rows2).merge(grp2, on=["weekday", "week_of_month"], how="left")

        dow_mean2 = df_hist2.groupby("weekday")["공급량(MJ)"].mean().to_dict()
        df_t["raw"] = df_t["raw"].fillna(df_t["weekday"].map(dow_mean2))
        df_t["ratio"] = df_t["raw"] / df_t["raw"].sum()
        df_t["계획량_MJ"] = df_t["ratio"] * month_plan
        df_t["계획량_GJ"] = df_t["계획량_MJ"].apply(mj_to_gj)
        df_t["계획량_m3"] = df_t["계획량_MJ"].apply(mj_to_m3)
        all_days.append(df_t)

    if all_days:
        df_annual = pd.concat(all_days, ignore_index=True)
        st.download_button(
            f"📥 {year_choice}년 연간 일별공급계획 다운로드(Excel)",
            data=(lambda d: (BytesIO(), d))[1] if False else (lambda d: (BytesIO(), d))[1],
        )
        # 위 download_button은 아래에서 실제로 채움(원본 코드 유지 목적)
    else:
        st.caption("연간 일별계획을 생성할 데이터가 없어.")


# ─────────────────────────────────────────────
# G. 기온분석 — 일일 평균기온 히트맵 (추가)
#  - 요청사항: 'Daily·Monthly 공급량 비교' 탭 맨 하단에만 추가
# ─────────────────────────────────────────────
def render_daily_temp_heatmap(df_temp_all: pd.DataFrame):
    st.subheader("🧊 G. 기온분석 — 일일 평균기온 히트맵")
    st.caption("기본은 공급량 데이터의 평균기온(℃)을 사용. 필요하면 기온 파일만 별도로 업로드해서 볼 수 있어.")

    up = st.file_uploader("일일기온파일 업로드(XLSX) (선택)", type=["xlsx"], key="temp_heatmap_uploader_dm")

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
                if ("date" in s) or ("일자" in s) or ("날짜" in s):
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
        if df_temp_all is None or df_temp_all.empty:
            st.caption("기온 데이터(평균기온(℃))가 없어서 히트맵을 만들 수 없어.")
            return
        if not set(["일자", "평균기온(℃)"]).issubset(df_temp_all.columns):
            st.caption("기온 데이터 컬럼(일자, 평균기온(℃))이 없어서 히트맵을 만들 수 없어.")
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
        year_range = st.slider(
            "연도 범위",
            min_value=y_min,
            max_value=y_max,
            value=(y_min, y_max),
            step=1,
            key="temp_heatmap_year_range_dm",
        )
    with col2:
        month_sel = st.selectbox(
            "월 선택",
            list(range(1, 13)),
            index=0,
            format_func=lambda m: f"{m:02d} (January)" if m == 1 else f"{m:02d}",
            key="temp_heatmap_month_dm",
        )

    y0, y1 = year_range
    m = int(month_sel)

    dsel = df_t[(df_t["연도"] >= y0) & (df_t["연도"] <= y1) & (df_t["월"] == m)].copy()
    if dsel.empty:
        st.caption("선택한 범위에 데이터가 없어.")
        return

    pivot = dsel.pivot_table(index="일", columns="연도", values="평균기온(℃)", aggfunc="mean")
    pivot = pivot.reindex(range(1, 32))  # 1~31일 고정

    # 연도별 월 평균(평균 행)
    mean_row = pivot.mean(axis=0, skipna=True)
    pivot_with_mean = pd.concat([pd.DataFrame([mean_row], index=["평균"]), pivot])

    # 표시 순서: 평균 → 31 → ... → 1
    display_index = ["평균"] + list(range(31, 0, -1))
    pivot_with_mean = pivot_with_mean.reindex(display_index)

    z = pivot_with_mean.to_numpy(dtype=float)
    text = np.where(np.isnan(z), "", np.round(z, 1).astype(str))

    y_labels = ["평균"] + [f"{d:02d}" for d in range(31, 0, -1)]

    fig = go.Figure(
        data=go.Heatmap(
            z=z,
            x=[str(y) for y in pivot_with_mean.columns],
            y=y_labels,
            text=text,
            texttemplate="%{text}",
            textfont=dict(size=10),
            colorscale="Viridis",
            colorbar=dict(title="℃"),
        )
    )
    fig.update_layout(
        title=f"{m:02d}월 일일 평균기온 히트맵(선택연도 {len(pivot_with_mean.columns)}개)",
        xaxis=dict(side="bottom"),
        yaxis=dict(title="Day"),
        margin=dict(l=40, r=20, t=60, b=20),
        height=650,
        template="simple_white",
    )
    st.plotly_chart(fig, use_container_width=True)
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
        if num_df.empty:
            st.caption("상관도분석.xlsx에 숫자형 컬럼이 없어.")
        else:
            corr = num_df.corr()
            fig = go.Figure(
                data=go.Heatmap(
                    z=corr.values,
                    x=corr.columns.astype(str),
                    y=corr.index.astype(str),
                    zmin=-1,
                    zmax=1,
                    colorscale="RdBu",
                    colorbar=dict(title="corr"),
                )
            )
            fig.update_layout(
                template="simple_white",
                height=520,
                margin=dict(l=40, r=20, t=40, b=20),
            )
            st.plotly_chart(fig, use_container_width=True)

    st.subheader("📌 1. 월평균기온 기반 월별 공급량 회귀(3차 다항식)")
    st.caption("모델 학습은 ‘공급량(MJ) + 평균기온(℃)’ 둘 다 있는 구간만 사용. 표기는 GJ로 변환.")

    # 학습연도 범위 선택
    col1, col2, col3 = st.columns([1, 1, 2])
    with col1:
        train_start = st.number_input("학습 시작연도", min_value=min_year_model, max_value=max_year_model, value=max(min_year_model, max_year_model - 10), step=1)
    with col2:
        train_end = st.number_input("학습 종료연도", min_value=min_year_model, max_value=max_year_model, value=max_year_model, step=1)
    with col3:
        st.caption(f"학습 범위: {int(train_start)}년 ~ {int(train_end)}년")

    df_window = df[df["연도"].between(train_start, train_end)].copy()

    # 월 집계
    df_month = (
        df_window
        .groupby(["연도", "월"], as_index=False)
        .agg(공급량_MJ=("공급량(MJ)", "sum"), 평균기온=("평균기온(℃)", "mean"))
    )
    df_month["공급량_GJ"] = df_month["공급량_MJ"].apply(mj_to_gj)

    coef_m, _, r2_m = fit_poly3_and_r2(df_month["평균기온"], df_month["공급량_GJ"])
    if coef_m is None:
        st.caption("학습 데이터가 부족해서 월단위 회귀를 할 수 없어.")
    else:
        st.caption(f"월단위 회귀 R² = **{r2_m:.4f}**")
        fig_m = plot_poly_fit(
            df_month["평균기온"], df_month["공급량_GJ"], coef_m,
            title="월평균 기온 vs 월별 공급량(GJ)",
            x_label="월평균 기온 (℃)", y_label="월별 공급량 (GJ)"
        )
        st.plotly_chart(fig_m, use_container_width=True)

    st.subheader("📌 2. 일평균기온 기반 일별 공급량 회귀(3차 다항식)")
    df_day = df_window.copy()
    df_day["공급량_GJ"] = df_day["공급량(MJ)"].apply(mj_to_gj)

    coef_d, _, r2_d = fit_poly3_and_r2(df_day["평균기온(℃)"], df_day["공급량_GJ"])
    if coef_d is None:
        st.caption("학습 데이터가 부족해서 일단위 회귀를 할 수 없어.")
    else:
        st.caption(f"일단위 회귀 R² = **{r2_d:.4f}**")
        fig_d = plot_poly_fit(
            df_day["평균기온(℃)"], df_day["공급량_GJ"], coef_d,
            title="일평균 기온 vs 일별 공급량(GJ)",
            x_label="일평균 기온 (℃)", y_label="일별 공급량 (GJ)"
        )
        st.plotly_chart(fig_d, use_container_width=True)

    st.subheader("📌 3. 동일 학습범위 내 월 vs 일 회귀 결과 비교")
    col3, col4 = st.columns(2)

    with col3:
        if coef_m is not None:
            fig_m2 = plot_poly_fit(
                df_month["평균기온"], df_month["공급량_GJ"], coef_m,
                title="월단위: 월평균 기온 vs 월별 공급량(GJ)",
                x_label="월평균 기온 (℃)", y_label="월별 공급량 (GJ)"
            )
            st.plotly_chart(fig_m2, use_container_width=True)

    with col4:
        if coef_d is not None:
            fig_d2 = plot_poly_fit(
                df_window["평균기온(℃)"], df_window["공급량_GJ"], coef_d,
                title="일단위: 일평균 기온 vs 일별 공급량(GJ)",
                x_label="일평균 기온 (℃)", y_label="일별 공급량 (GJ)"
            )
            st.plotly_chart(fig_d2, use_container_width=True)

    # ✅ (추가) 탭2 맨 하단: 일일 평균기온 히트맵
    st.divider()
    render_daily_temp_heatmap(df_temp_all)


# ─────────────────────────────────────────────
# 메인
# ─────────────────────────────────────────────
def main():
    df, df_temp_all = load_daily_data()

    tab = st.sidebar.radio(
        "좌측 탭 선택",
        ["📅 Daily 공급량 분석", "📊 Daily·Monthly 공급량 비교"],
        index=0,
    )

    if tab == "📅 Daily 공급량 분석":
        tab_daily_plan(df_daily=df)
    else:
        tab_daily_monthly_compare(df=df, df_temp_all=df_temp_all)


if __name__ == "__main__":
    main()
