# app.py ─ 도시가스 공급량 — 과거 실적 기반 "일별" 계획 (평일1/평일2 분리)
# - 기온/Poly 분석 제거
# - 월별 계획총량(MJ)은 기존 파일(공급량(계획_실적).xlsx)에서 읽고,
# - 일별 분배는 최근 N년 동일 월의 "공급량(MJ)" 패턴만 사용
#
# 카테고리 정의
# - 주말: 토/일 OR 공휴일 OR 명절(설/추석 등)
# - 평일1: (월, 금) AND (주말 아님)
# - 평일2: (화, 수, 목) AND (주말 아님)

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
    page_title="도시가스 공급량 — 과거 실적 기반 일별계획(평일1/2 분리)",
    layout="wide",
)

WEEKDAY_NAMES = ["월", "화", "수", "목", "금", "토", "일"]  # 0=월


# ─────────────────────────────────────────────
# 데이터 로드
# ─────────────────────────────────────────────
@st.cache_data
def load_daily_data():
    """
    공급량(일일실적).xlsx 에서 과거 공급량만 사용
    필수 컬럼: 일자, 공급량(MJ)
    """
    excel_path = Path(__file__).parent / "공급량(일일실적).xlsx"
    df_raw = pd.read_excel(excel_path)

    # 안전하게 컬럼명 맞추기
    need_cols = ["일자", "공급량(MJ)"]
    for c in need_cols:
        if c not in df_raw.columns:
            raise ValueError(f"'{c}' 컬럼이 없어. 파일 컬럼을 확인해줘: {list(df_raw.columns)}")

    df = df_raw[need_cols].copy()
    df["일자"] = pd.to_datetime(df["일자"])
    df["연도"] = df["일자"].dt.year
    df["월"] = df["일자"].dt.month
    df["일"] = df["일자"].dt.day
    df["weekday_idx"] = df["일자"].dt.weekday  # 0=월, 6=일

    # 공급량 결측 제거(계산용)
    df = df.dropna(subset=["공급량(MJ)"]).copy()

    return df


@st.cache_data
def load_monthly_plan() -> pd.DataFrame:
    """
    공급량(계획_실적).xlsx 중 '월별계획_실적' 시트 사용
    컬럼: 연, 월, 계획(사업계획제출_MJ)
    """
    excel_path = Path(__file__).parent / "공급량(계획_실적).xlsx"
    df = pd.read_excel(excel_path, sheet_name="월별계획_실적")

    for c in ["연", "월", "계획(사업계획제출_MJ)"]:
        if c not in df.columns:
            raise ValueError(f"'월별계획_실적' 시트에 '{c}' 컬럼이 없어. 현재: {list(df.columns)}")

    df["연"] = df["연"].astype(int)
    df["월"] = df["월"].astype(int)
    df["계획(사업계획제출_MJ)"] = pd.to_numeric(df["계획(사업계획제출_MJ)"], errors="coerce")
    return df


@st.cache_data
def load_effective_calendar() -> pd.DataFrame | None:
    """
    effective_days_calendar.xlsx
    - 날짜(YYYYMMDD) → 일자(datetime)
    - 공휴일여부, 명절여부(bool)
    """
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

    return df[["일자", "공휴일여부", "명절여부"]].dropna(subset=["일자"]).copy()


# ─────────────────────────────────────────────
# 표 포맷 유틸
# ─────────────────────────────────────────────
def format_table_generic(df, percent_cols=None):
    df = df.copy()
    if percent_cols is None:
        percent_cols = []

    def _fmt_no_comma(x):
        if pd.isna(x):
            return ""
        try:
            return f"{int(x)}"
        except Exception:
            return str(x)

    for col in df.columns:
        if df[col].dtype == bool:
            df[col] = df[col].map(lambda x: "공휴일" if x else "")
            continue

        if col in percent_cols:
            df[col] = df[col].map(lambda x: "" if pd.isna(x) else f"{x:.4f}")
        elif pd.api.types.is_numeric_dtype(df[col]):
            if col in ["연", "연도", "월", "일"]:
                df[col] = df[col].map(_fmt_no_comma)
            else:
                df[col] = df[col].map(lambda x: "" if pd.isna(x) else f"{x:,.0f}")
    return df


def center_style(df: pd.DataFrame):
    styler = (
        df.style
        .set_table_styles(
            [
                dict(selector="th", props=[("text-align", "center")]),
                dict(selector="td", props=[("text-align", "center")]),
            ]
        )
        .set_properties(**{"text-align": "center"})
    )
    try:
        styler = styler.hide(axis="index")
    except Exception:
        try:
            styler = styler.hide_index()
        except Exception:
            pass
    return styler


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


# ─────────────────────────────────────────────
# 핵심 로직: 월별 카테고리 비중 → 카테고리 내부 분배
# ─────────────────────────────────────────────
def _attach_calendar_flags(df: pd.DataFrame, cal_df: pd.DataFrame | None) -> pd.DataFrame:
    out = df.copy()
    if cal_df is None:
        out["공휴일여부"] = False
        out["명절여부"] = False
    else:
        out = out.merge(cal_df, on="일자", how="left")
        if "공휴일여부" not in out.columns:
            out["공휴일여부"] = False
        if "명절여부" not in out.columns:
            out["명절여부"] = False
        out["공휴일여부"] = out["공휴일여부"].fillna(False).astype(bool)
        out["명절여부"] = out["명절여부"].fillna(False).astype(bool)

    out["is_holiday"] = out["공휴일여부"] | out["명절여부"]
    out["is_weekend"] = (out["weekday_idx"] >= 5) | out["is_holiday"]
    return out


def _category_label(weekday_idx: int, is_weekend: bool) -> str:
    if is_weekend:
        return "주말/공휴일"
    # 평일만 여기로 내려옴
    if weekday_idx in (0, 4):  # 월, 금
        return "평일1(월/금)"
    return "평일2(화/수/목)"  # 1,2,3


def make_daily_plan_table(
    df_daily: pd.DataFrame,
    df_plan: pd.DataFrame,
    target_year: int,
    target_month: int,
    recent_window: int,
):
    """
    1) 최근 N년 동일 월에서 카테고리(평일1/평일2/주말) 비중을 먼저 평균으로 구함
       - share_y = category_total_y / month_total_y
       - 최종 share = mean(share_y)  (연도별 월합이 달라도 "비중" 평균이라 안정적)
    2) 카테고리 내부 분배는 (weekday_idx, nth_dow) 패턴으로 평균
       - 카테고리 내부 비율은 daily / category_total 로 만든 뒤 평균
    3) 최종 일별비율 = share(category) * within_ratio(day)
    """
    cal_df = load_effective_calendar()

    # 사용 가능한 연도
    all_years = sorted(df_daily["연도"].unique())
    recent_years = [y for y in range(target_year - recent_window, target_year) if y in all_years]
    if len(recent_years) == 0:
        return None, None, [], None

    # 최근 N년 해당 월
    df_recent = df_daily[(df_daily["연도"].isin(recent_years)) & (df_daily["월"] == target_month)].copy()
    if df_recent.empty:
        return None, None, recent_years, None

    df_recent = _attach_calendar_flags(df_recent, cal_df)
    df_recent["구분"] = df_recent.apply(lambda r: _category_label(int(r["weekday_idx"]), bool(r["is_weekend"])), axis=1)

    # nth_dow: 연도별/요일별 n번째
    df_recent = df_recent.sort_values(["연도", "일자"]).copy()
    df_recent["nth_dow"] = df_recent.groupby(["연도", "weekday_idx"]).cumcount() + 1

    # 연도별 월합
    month_total_y = df_recent.groupby("연도")["공급량(MJ)"].sum()

    # 연도별 카테고리 합 / 비중
    cat_total_y = df_recent.groupby(["연도", "구분"])["공급량(MJ)"].sum().unstack(fill_value=0.0)
    # 누락된 컬럼 보정
    for c in ["평일1(월/금)", "평일2(화/수/목)", "주말/공휴일"]:
        if c not in cat_total_y.columns:
            cat_total_y[c] = 0.0
    cat_total_y = cat_total_y[["평일1(월/금)", "평일2(화/수/목)", "주말/공휴일"]].copy()

    cat_share_y = cat_total_y.div(month_total_y, axis=0).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    # 최종 카테고리 비중: 최근N년 평균
    cat_share = cat_share_y.mean(axis=0)

    # (진단용) 최근N년 해당월: 카테고리별 "일평균 MJ"도 같이 제공
    cat_daily_mean = (
        df_recent.groupby(["연도", "구분"])["공급량(MJ)"].mean().groupby("구분").mean()
    )

    diag = pd.DataFrame(
        {
            "최근N년_카테고리비중평균": cat_share,
            "최근N년_카테고리일평균MJ": cat_daily_mean.reindex(cat_share.index).fillna(0.0),
        }
    ).reset_index().rename(columns={"index": "구분"})

    # ── 카테고리 내부 분배(최근N년 평균) 만들기 ──
    # 카테고리 내부 비율 = 공급량 / (해당연도-해당월-해당카테고리 합)
    df_recent["cat_total_y"] = df_recent.groupby(["연도", "구분"])["공급량(MJ)"].transform("sum")
    df_recent["within_ratio_y"] = np.where(df_recent["cat_total_y"] > 0, df_recent["공급량(MJ)"] / df_recent["cat_total_y"], 0.0)

    # key: (구분, weekday_idx, nth_dow)
    within_key_mean = (
        df_recent.groupby(["구분", "weekday_idx", "nth_dow"])["within_ratio_y"].mean()
    )
    # fallback1: (구분, weekday_idx)
    within_dow_mean = df_recent.groupby(["구분", "weekday_idx"])["within_ratio_y"].mean()
    # fallback2: (구분) 전체 균등
    # (결측 많을 때를 대비)

    within_key_dict = within_key_mean.to_dict()
    within_dow_dict = within_dow_mean.to_dict()

    # ── 대상 월 날짜 테이블 ──
    last_day = calendar.monthrange(target_year, target_month)[1]
    date_range = pd.date_range(f"{target_year}-{target_month:02d}-01", periods=last_day, freq="D")

    df_target = pd.DataFrame({"일자": date_range})
    df_target["연"] = target_year
    df_target["월"] = target_month
    df_target["일"] = df_target["일자"].dt.day
    df_target["weekday_idx"] = df_target["일자"].dt.weekday
    df_target["요일"] = df_target["weekday_idx"].map(lambda i: WEEKDAY_NAMES[i])

    df_target = _attach_calendar_flags(df_target, cal_df)
    df_target["구분"] = df_target.apply(lambda r: _category_label(int(r["weekday_idx"]), bool(r["is_weekend"])), axis=1)
    df_target = df_target.sort_values("일").copy()
    df_target["nth_dow"] = df_target.groupby("weekday_idx").cumcount() + 1

    # ── 대상 월: 카테고리 내부 raw(미정규) ──
    def _within_raw(row):
        cat = row["구분"]
        dow = int(row["weekday_idx"])
        nth = int(row["nth_dow"])
        v = within_key_dict.get((cat, dow, nth), None)
        if v is None or pd.isna(v):
            v = within_dow_dict.get((cat, dow), None)
        return v

    df_target["within_raw"] = df_target.apply(_within_raw, axis=1)

    # 카테고리별 within 정규화(카테고리 합=1)
    df_target["within_ratio"] = 0.0
    for cat in ["평일1(월/금)", "평일2(화/수/목)", "주말/공휴일"]:
        m = df_target["구분"] == cat
        if m.sum() == 0:
            continue

        s = df_target.loc[m, "within_raw"].astype(float)
        # 결측이면 균등
        if s.notna().sum() == 0:
            df_target.loc[m, "within_ratio"] = 1.0 / m.sum()
            continue

        s = s.fillna(s.dropna().mean() if s.dropna().size > 0 else 0.0)
        if s.sum() <= 0:
            df_target.loc[m, "within_ratio"] = 1.0 / m.sum()
        else:
            df_target.loc[m, "within_ratio"] = s / s.sum()

    # ── 최종 일별비율 = 카테고리비중 * 카테고리내비율 ──
    cat_share_map = {k: float(v) for k, v in cat_share.to_dict().items()}
    df_target["카테고리비중"] = df_target["구분"].map(cat_share_map).fillna(0.0)
    df_target["일별비율"] = df_target["카테고리비중"] * df_target["within_ratio"]

    # 정규화(합=1)
    tot = df_target["일별비율"].sum()
    if tot > 0:
        df_target["일별비율"] = df_target["일별비율"] / tot
    else:
        df_target["일별비율"] = 1.0 / last_day

    # 월 계획 총량
    row_plan = df_plan[(df_plan["연"] == target_year) & (df_plan["월"] == target_month)]
    plan_total = float(row_plan["계획(사업계획제출_MJ)"].iloc[0]) if not row_plan.empty else np.nan

    df_target["예상공급량(MJ)"] = (df_target["일별비율"] * plan_total).round(0) if pd.notna(plan_total) else np.nan

    df_result = df_target[
        [
            "연",
            "월",
            "일",
            "일자",
            "요일",
            "구분",
            "공휴일여부",
            "일별비율",
            "예상공급량(MJ)",
        ]
    ].copy()

    # 최근 N년 일별 실적 매트릭스(원자료)
    df_mat = (
        df_recent.pivot_table(index="일", columns="연도", values="공급량(MJ)", aggfunc="sum")
        .sort_index()
        .sort_index(axis=1)
    )

    return df_result, df_mat, recent_years, diag


def _build_year_daily_plan(df_daily: pd.DataFrame, df_plan: pd.DataFrame, target_year: int, recent_window: int):
    all_rows = []
    for m in range(1, 13):
        df_res, _, _, _ = make_daily_plan_table(
            df_daily=df_daily,
            df_plan=df_plan,
            target_year=target_year,
            target_month=m,
            recent_window=recent_window,
        )

        # fallback: 균등 분배
        if df_res is None:
            last_day = calendar.monthrange(target_year, m)[1]
            dr = pd.date_range(f"{target_year}-{m:02d}-01", periods=last_day, freq="D")
            tmp = pd.DataFrame({"일자": dr})
            tmp["연"] = target_year
            tmp["월"] = m
            tmp["일"] = tmp["일자"].dt.day
            tmp["weekday_idx"] = tmp["일자"].dt.weekday
            tmp["요일"] = tmp["weekday_idx"].map(lambda i: WEEKDAY_NAMES[i])
            tmp["구분"] = tmp["weekday_idx"].map(lambda i: "주말/공휴일" if i >= 5 else "평일2(화/수/목)")
            tmp["공휴일여부"] = False
            tmp["일별비율"] = 1.0 / last_day

            row_plan = df_plan[(df_plan["연"] == target_year) & (df_plan["월"] == m)]
            plan_total = float(row_plan["계획(사업계획제출_MJ)"].iloc[0]) if not row_plan.empty else np.nan
            tmp["예상공급량(MJ)"] = (tmp["일별비율"] * plan_total).round(0) if pd.notna(plan_total) else np.nan

            df_res = tmp[["연", "월", "일", "일자", "요일", "구분", "공휴일여부", "일별비율", "예상공급량(MJ)"]].copy()

        all_rows.append(df_res)

    df_year = pd.concat(all_rows, ignore_index=True).sort_values(["월", "일"]).reset_index(drop=True)

    total_row = {
        "연": "",
        "월": "",
        "일": "",
        "일자": "",
        "요일": "합계",
        "구분": "",
        "공휴일여부": False,
        "일별비율": df_year["일별비율"].sum(skipna=True),
        "예상공급량(MJ)": df_year["예상공급량(MJ)"].sum(skipna=True),
    }
    df_year_with_total = pd.concat([df_year, pd.DataFrame([total_row])], ignore_index=True)
    return df_year_with_total


# ─────────────────────────────────────────────
# 화면: Daily 공급량 분석
# ─────────────────────────────────────────────
def tab_daily_plan(df_daily: pd.DataFrame):
    st.subheader("📅 Daily 공급량 분석 — 과거 실적 기반 일별계획 (평일1/2 분리)")

    df_plan = load_monthly_plan()

    years_plan = sorted(df_plan["연"].unique())
    default_year = 2026 if 2026 in years_plan else years_plan[-1]

    col_y, col_m, _ = st.columns([1, 1, 2])
    with col_y:
        target_year = st.selectbox("계획 연도 선택", years_plan, index=years_plan.index(default_year))
    with col_m:
        months_plan = sorted(df_plan[df_plan["연"] == target_year]["월"].unique())
        default_month = 12 if 12 in months_plan else months_plan[0]
        target_month = st.selectbox("계획 월 선택", months_plan, index=months_plan.index(default_month), format_func=lambda m: f"{m}월")

    all_years = sorted(df_daily["연도"].unique())
    hist_years = [y for y in all_years if y < target_year]
    if len(hist_years) < 1:
        st.warning("해당 연도는 과거 데이터가 없어 최근 N년 분석을 할 수 없어.")
        return

    slider_min = 1
    slider_max = min(10, len(hist_years))
    recent_window = st.slider(
        "최근 몇 년 평균(비중/패턴)을 쓸까?",
        min_value=slider_min,
        max_value=slider_max,
        value=min(3, slider_max),
        step=1,
        help="예: 3년이면 대상연도 직전 3개 연도의 '해당 월'만 사용",
    )

    st.caption(
        f"최근 {recent_window}년({target_year-recent_window}~{target_year-1}) "
        f"{target_month}월 실적으로 '카테고리 비중(평일1/평일2/주말)'을 먼저 만들고, "
        f"카테고리 내부는 (요일+n번째) 패턴으로 일별 분배해."
    )

    df_result, df_mat, recent_years, diag = make_daily_plan_table(
        df_daily=df_daily,
        df_plan=df_plan,
        target_year=int(target_year),
        target_month=int(target_month),
        recent_window=int(recent_window),
    )

    if df_result is None or len(recent_years) == 0:
        st.warning("선택한 연도/월에 대해 최근 N년 기준으로 계산할 데이터가 없어.")
        return

    st.markdown(f"- 실제 사용된 과거 연도: {min(recent_years)}년 ~ {max(recent_years)}년 (총 {len(recent_years)}개)")

    # 월 계획총량
    row_plan = df_plan[(df_plan["연"] == int(target_year)) & (df_plan["월"] == int(target_month))]
    plan_total_raw = float(row_plan["계획(사업계획제출_MJ)"].iloc[0]) if not row_plan.empty else np.nan
    st.markdown(f"**{target_year}년 {target_month}월 사업계획 제출 공급량 합계:** `{plan_total_raw:,.0f} MJ`" if pd.notna(plan_total_raw) else "**월 계획총량을 찾지 못했어(NaN)**")

    # 1) 테이블
    st.markdown("#### 1. 일별 비율·예상 공급량 테이블")

    view = df_result.copy()
    total_row = {
        "연": "",
        "월": "",
        "일": "",
        "일자": "",
        "요일": "합계",
        "구분": "",
        "공휴일여부": False,
        "일별비율": view["일별비율"].sum(),
        "예상공급량(MJ)": view["예상공급량(MJ)"].sum(),
    }
    view_with_total = pd.concat([view, pd.DataFrame([total_row])], ignore_index=True)

    view_show = format_table_generic(
        view_with_total[["연", "월", "일", "요일", "구분", "공휴일여부", "일별비율", "예상공급량(MJ)"]],
        percent_cols=["일별비율"],
    )
    st.table(center_style(view_show))

    # 2) 그래프
    st.markdown("#### 2. 일별 예상 공급량 & 비율 그래프")

    df_w1 = view[view["구분"] == "평일1(월/금)"]
    df_w2 = view[view["구분"] == "평일2(화/수/목)"]
    df_we = view[view["구분"] == "주말/공휴일"]

    fig = go.Figure()
    fig.add_bar(x=df_w1["일"], y=df_w1["예상공급량(MJ)"], name="평일_1(월/금)")
    fig.add_bar(x=df_w2["일"], y=df_w2["예상공급량(MJ)"], name="평일_2(화/수/목)")
    fig.add_bar(x=df_we["일"], y=df_we["예상공급량(MJ)"], name="주말/공휴일")
    fig.add_trace(
        go.Scatter(
            x=view["일"],
            y=view["일별비율"],
            mode="lines+markers",
            name=f"일별비율(최근{recent_window}년)",
            yaxis="y2",
        )
    )
    fig.update_layout(
        title=f"{target_year}년 {target_month}월 일별 공급량 계획 (평일1/2 분리 반영)",
        xaxis_title="일",
        yaxis=dict(title="예상 공급량(MJ)"),
        yaxis2=dict(title="일별비율", overlaying="y", side="right"),
        barmode="group",
        margin=dict(l=20, r=20, t=60, b=40),
    )
    st.plotly_chart(fig, use_container_width=True)

    # 3) 최근 N년 매트릭스(실적)
    st.markdown("#### 3. 최근 N년 일별 실적 매트릭스")

    if df_mat is not None and not df_mat.empty:
        fig_hm = go.Figure(
            data=go.Heatmap(
                z=df_mat.values,
                x=[str(c) for c in df_mat.columns],
                y=df_mat.index,
                colorbar_title="공급량(MJ)",
                colorscale="RdBu_r",
            )
        )
        fig_hm.update_layout(
            title=f"최근 {len(recent_years)}년 {target_month}월 일별 실적 공급량(MJ) 매트릭스",
            xaxis=dict(title="연도", type="category"),
            yaxis=dict(title="일", autorange="reversed"),
            margin=dict(l=40, r=40, t=60, b=40),
            width=900,
            height=650,
        )
        st.plotly_chart(fig_hm, use_container_width=False)

    # 4) 카테고리 비중(진단)
    st.markdown("#### 4. (진단) 최근 N년 해당 월의 카테고리 비중/평균")

    if diag is not None and not diag.empty:
        # "평일1이 평일2보다 낮아야 한다" 기대 검증을 여기서 바로 볼 수 있게
        diag_show = diag.copy()
        diag_show["최근N년_카테고리비중평균"] = diag_show["최근N년_카테고리비중평균"].map(lambda x: f"{x:.4f}")
        diag_show["최근N년_카테고리일평균MJ"] = diag_show["최근N년_카테고리일평균MJ"].map(lambda x: f"{x:,.0f}")
        st.table(center_style(diag_show))

        # 경고(데이터가 실제로 반대면)
        s_map = diag.set_index("구분")["최근N년_카테고리비중평균"].to_dict()
        if s_map.get("평일1(월/금)", 0) > s_map.get("평일2(화/수/목)", 0):
            st.warning(
                "최근 N년 '해당 월' 실적 기준으로는 평일1(월/금) 비중이 평일2(화/수/목)보다 크게 나왔어. "
                "이 경우는 데이터 자체가 그렇게 기록된 거라(예: 월/금에 공장/대수선/특이수요가 몰린 달), "
                "로직이 아니라 원자료/기간 영향을 먼저 확인해야 해."
            )

    # 5) 카테고리별 계획 합계 요약
    st.markdown("#### 5. 평일1/평일2/주말 계획 합계 요약")
    sum_df = (
        view.groupby("구분", as_index=False)[["일별비율", "예상공급량(MJ)"]]
        .sum()
        .rename(columns={"일별비율": "일별비율합계"})
    )
    total_row2 = {
        "구분": "합계",
        "일별비율합계": sum_df["일별비율합계"].sum(),
        "예상공급량(MJ)": sum_df["예상공급량(MJ)"].sum(),
    }
    sum_df = pd.concat([sum_df, pd.DataFrame([total_row2])], ignore_index=True)
    sum_show = format_table_generic(sum_df, percent_cols=["일별비율합계"])
    st.table(center_style(sum_show))

    # 6) 엑셀 다운로드(월)
    st.markdown("#### 6. 일별 계획 엑셀 다운로드(월)")

    buffer = BytesIO()
    sheet_name = f"{target_year}_{int(target_month):02d}_일별계획"
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        view_with_total.to_excel(writer, index=False, sheet_name=sheet_name)

        # 진단 시트
        if diag is not None:
            diag.to_excel(writer, index=False, sheet_name="진단_카테고리비중")

        # 원자료(최근N년) 매트릭스
        if df_mat is not None and not df_mat.empty:
            df_mat.to_excel(writer, sheet_name="최근N년_일별실적매트릭스")

        wb = writer.book
        ws = wb[sheet_name]

        # 서식
        _format_excel_sheet(
            ws,
            freeze="A2",
            center=True,
            width_map={
                "A": 6,   # 연
                "B": 4,   # 월
                "C": 4,   # 일
                "D": 14,  # 일자
                "E": 6,   # 요일
                "F": 16,  # 구분
                "G": 10,  # 공휴일여부
                "H": 12,  # 일별비율
                "I": 18,  # 예상공급량
            },
        )
        for c in range(1, ws.max_column + 1):
            ws.cell(1, c).font = Font(bold=True)

    st.download_button(
        label=f"📥 {target_year}년 {target_month}월 일별공급계획 다운로드 (Excel)",
        data=buffer.getvalue(),
        file_name=f"{target_year}_{int(target_month):02d}_일별공급계획.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    # 7) 연간 다운로드
    st.markdown("#### 7. 일일계획 다운로드(연간)")

    col_ay, col_btn = st.columns([1, 3])
    with col_ay:
        annual_year = st.selectbox(
            "연간 계획 연도 선택",
            years_plan,
            index=years_plan.index(int(target_year)) if int(target_year) in years_plan else 0,
            key="annual_year_select",
        )
    with col_btn:
        st.caption("선택한 연도(1/1~12/31) 일별계획을 한 시트로 내려받을 수 있어.")

    buffer_year = BytesIO()
    df_year_daily = _build_year_daily_plan(
        df_daily=df_daily,
        df_plan=df_plan,
        target_year=int(annual_year),
        recent_window=int(recent_window),
    )

    with pd.ExcelWriter(buffer_year, engine="openpyxl") as writer:
        df_year_daily.to_excel(writer, index=False, sheet_name="연간")
        wb = writer.book
        ws_y = wb["연간"]

        _format_excel_sheet(
            ws_y,
            freeze="A2",
            center=True,
            width_map={
                "A": 6,   # 연
                "B": 4,   # 월
                "C": 4,   # 일
                "D": 14,  # 일자
                "E": 6,   # 요일
                "F": 16,  # 구분
                "G": 10,  # 공휴일여부
                "H": 12,  # 일별비율
                "I": 18,  # 예상공급량
            },
        )
        for c in range(1, ws_y.max_column + 1):
            ws_y.cell(1, c).font = Font(bold=True)

    st.download_button(
        label=f"📥 {annual_year}년 연간 일별공급계획 다운로드 (Excel)",
        data=buffer_year.getvalue(),
        file_name=f"{annual_year}_연간_일별공급계획.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key="download_annual_excel",
    )


# ─────────────────────────────────────────────
# 메인
# ─────────────────────────────────────────────
def main():
    df_daily = load_daily_data()

    st.title("도시가스 공급량 — 과거 실적 기반 일별계획(평일1/2 분리)")
    tab_daily_plan(df_daily=df_daily)

    st.caption(
        "필수 파일: 공급량(일일실적).xlsx, 공급량(계획_실적).xlsx\n"
        "선택 파일: effective_days_calendar.xlsx (공휴일/명절 반영)"
    )


if __name__ == "__main__":
    main()
