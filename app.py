# app.py
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
    page_title="도시가스 공급량: 일/월 기온 기반 예측력 비교",
    layout="wide",
)


# ─────────────────────────────────────────────
# 데이터 불러오기
# ─────────────────────────────────────────────
@st.cache_data
def load_daily_data():
    """
    반환:
      df_model     : 공급량(MJ)와 평균기온 둘 다 있는 구간 (예측/R² 계산용)
      df_temp_all  : 평균기온만 있어도 되는 전체 구간
    """
    excel_path = Path(__file__).parent / "공급량(일일실적).xlsx"
    df_raw = pd.read_excel(excel_path)

    df_raw = df_raw[["일자", "공급량(MJ)", "공급량(M3)", "평균기온(℃)"]].copy()
    df_raw["일자"] = pd.to_datetime(df_raw["일자"], errors="coerce")

    df_raw["연도"] = df_raw["일자"].dt.year
    df_raw["월"] = df_raw["일자"].dt.month
    df_raw["일"] = df_raw["일자"].dt.day

    df_temp_all = df_raw.dropna(subset=["평균기온(℃)"]).copy()
    df_model = df_temp_all.dropna(subset=["공급량(MJ)"]).copy()

    return df_model, df_temp_all


@st.cache_data
def load_corr_data() -> pd.DataFrame | None:
    excel_path = Path(__file__).parent / "상관도분석.xlsx"
    if not excel_path.exists():
        return None
    return pd.read_excel(excel_path)


@st.cache_data
def load_monthly_plan() -> pd.DataFrame:
    """
    공급량(계획_실적).xlsx 중 '월별계획_실적' 시트 사용
    컬럼 예: 연, 월, 계획(사업계획제출_MJ)
    """
    excel_path = Path(__file__).parent / "공급량(계획_실적).xlsx"
    df = pd.read_excel(excel_path, sheet_name="월별계획_실적")
    df["연"] = df["연"].astype(int)
    df["월"] = df["월"].astype(int)
    return df


@st.cache_data
def load_effective_calendar() -> pd.DataFrame | None:
    """
    effective_days_calendar.xlsx:
      - 날짜(YYYYMMDD) 필수
      - 공휴일여부(bool) / 명절여부(bool) 기본
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
        df[col] = df[col].fillna(False).astype(bool)

    keep = ["일자", "공휴일여부", "명절여부"]
    return df[keep].copy()


# ─────────────────────────────────────────────
# 유틸
# ─────────────────────────────────────────────
def format_table_generic(df, percent_cols=None, temp_cols=None):
    df = df.copy()
    if percent_cols is None:
        percent_cols = []
    if temp_cols is None:
        temp_cols = []

    def _fmt_no_comma(x):
        if pd.isna(x):
            return ""
        try:
            return f"{int(x)}"
        except Exception:
            return str(x)

    for col in df.columns:
        if df[col].dtype == bool:
            df[col] = df[col].map(lambda x: "Y" if x else "")
            continue

        if col in percent_cols:
            df[col] = df[col].map(lambda x: f"{x:.4f}" if pd.notna(x) else "")
        elif col in temp_cols:
            df[col] = df[col].map(lambda x: f"{x:.2f}" if pd.notna(x) else "")
        elif pd.api.types.is_numeric_dtype(df[col]):
            if col in ["연", "연도", "월", "일", "월일수"]:
                df[col] = df[col].map(_fmt_no_comma)
            else:
                df[col] = df[col].map(lambda x: f"{x:,.0f}" if pd.notna(x) else "")
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


def _week_of_month(dt_series: pd.Series) -> pd.Series:
    """week_of_month = 1..6 (월요일 시작 기준)"""
    first_day = dt_series.dt.to_period("M").dt.start_time
    first_w = first_day.dt.weekday  # 0=월
    return ((dt_series.dt.day + first_w - 1) // 7) + 1


def _korean_dow_name(weekday_idx: int) -> str:
    names = ["월", "화", "수", "목", "금", "토", "일"]
    return names[int(weekday_idx)]


def _classify_weekday_group(weekday_idx: int) -> str:
    # 평일만 들어온다는 가정(0~4)
    return "평일_1(월/금)" if weekday_idx in (0, 4) else "평일_2(화/수/목)"


def _make_category_labels(df: pd.DataFrame) -> pd.DataFrame:
    """
    df에 weekday_idx, 공휴일여부, 명절여부가 있다고 가정(없으면 생성해서 처리)
    카테고리: 평일_1(월/금), 평일_2(화/수/목), 주말/공휴일
    """
    df = df.copy()
    if "weekday_idx" not in df.columns:
        df["weekday_idx"] = df["일자"].dt.weekday

    if "공휴일여부" not in df.columns:
        df["공휴일여부"] = False
    if "명절여부" not in df.columns:
        df["명절여부"] = False

    df["공휴일여부"] = df["공휴일여부"].fillna(False).astype(bool)
    df["명절여부"] = df["명절여부"].fillna(False).astype(bool)

    df["is_holiday"] = df["공휴일여부"] | df["명절여부"]
    df["is_weekend"] = (df["weekday_idx"] >= 5) | df["is_holiday"]

    df["카테고리"] = np.where(
        df["is_weekend"],
        "주말/공휴일",
        df["weekday_idx"].map(lambda x: _classify_weekday_group(int(x))),
    )
    return df


# ─────────────────────────────────────────────
# 핵심: 패턴 기반 Daily 계획 (평일1/2 비중을 "먼저" 고정)
# ─────────────────────────────────────────────
def make_daily_plan_table_pattern(
    df_daily: pd.DataFrame,
    df_plan: pd.DataFrame,
    target_year: int = 2026,
    target_month: int = 1,
    recent_window: int = 3,
):
    """
    개선 포인트(중요):
      - Step1: 최근 N년 기준 월 전체 대비 카테고리(평일1/평일2/주말) 비중을 먼저 계산
      - Step2: 카테고리 내부 일별 분포를 만든 뒤, (카테고리 비중 × 내부분포)로 최종 일별비율 생성
    """
    cal_df = load_effective_calendar()

    all_years = sorted(df_daily["연도"].dropna().unique())
    recent_years = [y for y in range(target_year - recent_window, target_year) if y in all_years]
    if len(recent_years) == 0:
        return None, None, [], None

    # ── 최근 N년 해당월 데이터 ──────────────────
    df_recent = df_daily[(df_daily["연도"].isin(recent_years)) & (df_daily["월"] == target_month)].copy()
    df_recent = df_recent.dropna(subset=["일자", "공급량(MJ)"]).copy()
    if df_recent.empty:
        return None, None, recent_years, None

    df_recent["weekday_idx"] = df_recent["일자"].dt.weekday
    df_recent["week_of_month"] = _week_of_month(df_recent["일자"])

    # 캘린더 merge(공휴일/명절)
    if cal_df is not None:
        df_recent = df_recent.merge(cal_df, on="일자", how="left")
    df_recent = _make_category_labels(df_recent)

    # 주말/공휴일 분포용 nth_dow
    df_recent = df_recent.sort_values(["연도", "일자"]).copy()
    df_recent["nth_dow"] = (
        df_recent.groupby(["연도", "weekday_idx"]).cumcount() + 1
    )

    # ── Step1: 카테고리 비중(월 전체 대비) ───────
    # 월합계(연도별)
    df_recent["month_total"] = df_recent.groupby("연도")["공급량(MJ)"].transform("sum")
    # 카테고리 합계(연도별)
    df_recent["cat_total"] = df_recent.groupby(["연도", "카테고리"])["공급량(MJ)"].transform("sum")
    # 연도별 카테고리 비중(카테고리/월합)
    df_recent["cat_share_year"] = np.where(df_recent["month_total"] > 0, df_recent["cat_total"] / df_recent["month_total"], np.nan)

    # 카테고리 비중(최근N년 평균)  ※ (연도,카테고리) 중복 제거 후 평균
    cat_share = (
        df_recent[["연도", "카테고리", "cat_share_year"]]
        .drop_duplicates()
        .groupby("카테고리")["cat_share_year"]
        .mean()
        .to_dict()
    )

    # 안전 보정: 세 카테고리 합이 1이 되도록 정규화(결측/누락 대비)
    keys = ["평일_1(월/금)", "평일_2(화/수/목)", "주말/공휴일"]
    total_share = sum([cat_share.get(k, 0.0) for k in keys])
    if total_share <= 0:
        cat_share = {k: (1.0 / len(keys)) for k in keys}
    else:
        cat_share = {k: (cat_share.get(k, 0.0) / total_share) for k in keys}

    # ── Step2: 카테고리 내부 분포(= within-cat) ──
    # within_ratio = supply / cat_total  (연도별 카테고리 안에서 상대분포)
    df_recent["within_ratio"] = np.where(df_recent["cat_total"] > 0, df_recent["공급량(MJ)"] / df_recent["cat_total"], np.nan)

    # 평일 내부 패턴: (카테고리, weekday_idx, week_of_month) 평균
    wk_mask = df_recent["카테고리"].isin(["평일_1(월/금)", "평일_2(화/수/목)"])
    within_wk_a = df_recent[wk_mask].groupby(["카테고리", "weekday_idx", "week_of_month"])["within_ratio"].mean().to_dict()
    within_wk_b = df_recent[wk_mask].groupby(["카테고리", "weekday_idx"])["within_ratio"].mean().to_dict()
    within_wk_c = df_recent[wk_mask].groupby(["카테고리"])["within_ratio"].mean().to_dict()

    # 주말/공휴일 내부 패턴: (weekday_idx, nth_dow) 평균 (카테고리는 1개로 묶음)
    we_mask = df_recent["카테고리"].eq("주말/공휴일")
    within_we_a = df_recent[we_mask].groupby(["weekday_idx", "nth_dow"])["within_ratio"].mean().to_dict()
    within_we_b = df_recent[we_mask].groupby(["weekday_idx"])["within_ratio"].mean().to_dict()
    within_we_c = df_recent[we_mask]["within_ratio"].mean()
    within_we_c = float(within_we_c) if pd.notna(within_we_c) else np.nan

    # ── 대상월 프레임 생성 ──────────────────────
    last_day = calendar.monthrange(target_year, target_month)[1]
    date_range = pd.date_range(f"{target_year}-{target_month:02d}-01", periods=last_day, freq="D")

    df_target = pd.DataFrame({"일자": date_range})
    df_target["연"] = target_year
    df_target["월"] = target_month
    df_target["일"] = df_target["일자"].dt.day
    df_target["weekday_idx"] = df_target["일자"].dt.weekday
    df_target["요일"] = df_target["weekday_idx"].map(_korean_dow_name)
    df_target["week_of_month"] = _week_of_month(df_target["일자"])
    df_target["nth_dow"] = df_target.sort_values("일").groupby("weekday_idx").cumcount() + 1

    if cal_df is not None:
        df_target = df_target.merge(cal_df, on="일자", how="left")
    df_target = _make_category_labels(df_target)

    # ── 카테고리 내부 raw(within) 산출 ───────────
    raw_within = []
    for _, r in df_target.iterrows():
        cat = r["카테고리"]
        wd = int(r["weekday_idx"])
        wom = int(r["week_of_month"])
        nth = int(r["nth_dow"])

        if cat in ("평일_1(월/금)", "평일_2(화/수/목)"):
            v = within_wk_a.get((cat, wd, wom), np.nan)
            if pd.isna(v):
                v = within_wk_b.get((cat, wd), np.nan)
            if pd.isna(v):
                v = within_wk_c.get(cat, np.nan)
            raw_within.append(v)
        else:
            # 주말/공휴일
            v = within_we_a.get((wd, nth), np.nan)
            if pd.isna(v):
                v = within_we_b.get(wd, np.nan)
            if pd.isna(v):
                v = within_we_c
            raw_within.append(v)

    df_target["raw_within"] = raw_within

    # 내부 raw NaN 보정: 카테고리 평균 → 전체 평균 → 1
    if df_target["raw_within"].notna().any():
        overall_mean = df_target["raw_within"].dropna().mean()
        df_target["raw_within"] = df_target.groupby("카테고리")["raw_within"].transform(
            lambda s: s.fillna(s.dropna().mean() if s.notna().any() else overall_mean)
        )
        df_target["raw_within"] = df_target["raw_within"].fillna(overall_mean)
    else:
        df_target["raw_within"] = 1.0

    # ── 카테고리 내부 정규화(= within share) ─────
    df_target["within_norm"] = df_target.groupby("카테고리")["raw_within"].transform(lambda s: s / s.sum() if s.sum() > 0 else 1.0 / len(s))

    # ── 최종 일별비율 = 카테고리비중 × within_norm ─
    df_target["카테고리비중(최근N년평균)"] = df_target["카테고리"].map(lambda k: float(cat_share.get(k, 0.0)))
    df_target["일별비율"] = df_target["카테고리비중(최근N년평균)"] * df_target["within_norm"]

    # 합계가 1이 되도록 최종 정규화(수치 안정)
    s = float(df_target["일별비율"].sum())
    if s <= 0:
        df_target["일별비율"] = 1.0 / last_day
    else:
        df_target["일별비율"] = df_target["일별비율"] / s

    # 최근 N년 총/평균(비율로 배분)
    month_total_all = df_recent["공급량(MJ)"].sum()
    df_target["최근N년_총공급량(MJ)"] = df_target["일별비율"] * month_total_all
    df_target["최근N년_평균공급량(MJ)"] = df_target["최근N년_총공급량(MJ)"] / len(recent_years)

    # 월 계획총량
    row_plan = df_plan[(df_plan["연"] == target_year) & (df_plan["월"] == target_month)]
    plan_total = float(row_plan["계획(사업계획제출_MJ)"].iloc[0]) if not row_plan.empty else np.nan
    df_target["예상공급량(MJ)"] = (df_target["일별비율"] * plan_total).round(0)

    df_result = df_target[
        [
            "연", "월", "일", "일자", "요일",
            "카테고리", "카테고리비중(최근N년평균)",
            "공휴일여부", "명절여부",
            "최근N년_평균공급량(MJ)", "최근N년_총공급량(MJ)",
            "일별비율", "예상공급량(MJ)",
        ]
    ].copy()

    # 최근 N년 매트릭스
    df_mat = (
        df_recent.pivot_table(index="일", columns="연도", values="공급량(MJ)", aggfunc="sum")
        .sort_index()
        .sort_index(axis=1)
    )

    # 디버그용 카테고리 비중 요약표
    share_tbl = pd.DataFrame(
        [{"카테고리": k, "월비중(최근N년평균)": cat_share.get(k, 0.0)} for k in ["평일_1(월/금)", "평일_2(화/수/목)", "주말/공휴일"]]
    )

    return df_result, df_mat, recent_years, share_tbl


# ─────────────────────────────────────────────
# 공통 렌더링(표/그래프/매트릭스/요약/엑셀다운)
# ─────────────────────────────────────────────
def _render_daily_plan_ui(
    df_result: pd.DataFrame,
    df_mat: pd.DataFrame | None,
    recent_years: list[int],
    target_year: int,
    target_month: int,
    recent_window: int,
    plan_total_raw: float | np.floating | None,
    share_tbl: pd.DataFrame | None,
):
    st.markdown("#### 0. 카테고리별 월 비중(최근 N년 평균)")
    if share_tbl is not None and not share_tbl.empty:
        share_view = share_tbl.copy()
        share_view = format_table_generic(share_view, percent_cols=["월비중(최근N년평균)"])
        st.table(center_style(share_view))

    st.markdown("#### 1. 일별 비율·예상 공급량 테이블")

    view = df_result.copy()

    total_row = {
        "연": "",
        "월": "",
        "일": "",
        "일자": "",
        "요일": "합계",
        "카테고리": "",
        "카테고리비중(최근N년평균)": view["카테고리비중(최근N년평균)"].mean(),
        "공휴일여부": False,
        "명절여부": False,
        "최근N년_평균공급량(MJ)": view["최근N년_평균공급량(MJ)"].sum(),
        "최근N년_총공급량(MJ)": view["최근N년_총공급량(MJ)"].sum(),
        "일별비율": view["일별비율"].sum(),
        "예상공급량(MJ)": view["예상공급량(MJ)"].sum(),
    }
    view_with_total = pd.concat([view, pd.DataFrame([total_row])], ignore_index=True)

    cols = [
        "연", "월", "일", "요일",
        "카테고리", "카테고리비중(최근N년평균)",
        "공휴일여부", "명절여부",
        "최근N년_평균공급량(MJ)", "최근N년_총공급량(MJ)",
        "일별비율", "예상공급량(MJ)",
    ]
    view_for_format = view_with_total[cols].copy()
    view_for_format = format_table_generic(view_for_format, percent_cols=["카테고리비중(최근N년평균)", "일별비율"])
    st.table(center_style(view_for_format))

    # ── 그래프 ─────────────────────────────────
    st.markdown("#### 2. 일별 예상 공급량 & 비율 그래프")

    fig = go.Figure()

    cat_order = ["평일_1(월/금)", "평일_2(화/수/목)", "주말/공휴일"]
    cats = [c for c in cat_order if c in view["카테고리"].unique()]
    for c in sorted(set(view["카테고리"].unique()) - set(cats)):
        cats.append(c)

    for c in cats:
        sub = view[view["카테고리"] == c]
        fig.add_bar(x=sub["일"], y=sub["예상공급량(MJ)"], name=c)

    fig.add_trace(
        go.Scatter(
            x=view["일"],
            y=view["일별비율"],
            mode="lines+markers",
            name=f"일별비율 (최근{recent_window}년)",
            yaxis="y2",
        )
    )

    fig.update_layout(
        title=f"{target_year}년 {target_month}월 일별 공급량 계획 (평일1/2 분리 반영)",
        xaxis_title="일",
        yaxis=dict(title="예상 공급량 (MJ)"),
        yaxis2=dict(title="일별비율", overlaying="y", side="right"),
        barmode="group",
        margin=dict(l=20, r=20, t=60, b=40),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
    )
    st.plotly_chart(fig, use_container_width=True)

    # ── 매트릭스 ───────────────────────────────
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
        )
        st.plotly_chart(fig_hm, use_container_width=False)

    # ── 요약 ───────────────────────────────────
    st.markdown("#### 4. 카테고리 비중 요약(이번 달 배분 결과)")
    summary = (
        view.groupby("카테고리", as_index=False)[["일별비율", "예상공급량(MJ)"]]
        .sum()
        .rename(columns={"일별비율": "일별비율합계"})
    )
    total_row_sum = {
        "카테고리": "합계",
        "일별비율합계": summary["일별비율합계"].sum(),
        "예상공급량(MJ)": summary["예상공급량(MJ)"].sum(),
    }
    summary = pd.concat([summary, pd.DataFrame([total_row_sum])], ignore_index=True)
    summary = format_table_generic(summary, percent_cols=["일별비율합계"])
    st.table(center_style(summary))

    # ── 엑셀 다운로드(월) ───────────────────────
    st.markdown("#### 5. 일별 계획 엑셀 다운로드")

    buffer = BytesIO()
    sheet_name = f"{target_year}_{target_month:02d}_일별계획"
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        view_with_total.to_excel(writer, index=False, sheet_name=sheet_name)

        wb = writer.book
        ws_in = wb.create_sheet("INPUT")
        ws_in["A1"] = "항목"
        ws_in["B1"] = "값"
        ws_in["C1"] = "비고"
        for cell in ("A1", "B1", "C1"):
            ws_in[cell].font = Font(bold=True)

        rows = [
            ("대상연도", target_year, ""),
            ("대상월", target_month, ""),
            ("최근N년(설정)", recent_window, ""),
            ("실제 사용된 연도", ", ".join([str(y) for y in recent_years]), ""),
            ("월 계획총량(MJ) (사업계획제출)", plan_total_raw if plan_total_raw is not None else "", "공급량(계획_실적).xlsx → 월별계획_실적"),
            ("로직", "카테고리비중(평일1/평일2/주말) → 카테고리내부패턴 분배", ""),
        ]
        r0 = 2
        for i, (k, v, note) in enumerate(rows):
            rr = r0 + i
            ws_in.cell(rr, 1, k)
            ws_in.cell(rr, 2, v)
            ws_in.cell(rr, 3, note)

        _format_excel_sheet(
            wb[sheet_name],
            freeze="A2",
            center=True,
            width_map={
                "A": 6, "B": 4, "C": 4, "D": 14, "E": 6, "F": 18,
                "G": 18, "H": 12, "I": 12, "J": 20, "K": 20, "L": 12, "M": 18,
            },
        )
        _format_excel_sheet(ws_in, freeze="A2", center=True, width_map={"A": 24, "B": 30, "C": 55})

        ws_main = wb[sheet_name]
        for c in range(1, ws_main.max_column + 1):
            ws_main.cell(1, c).font = Font(bold=True)

    st.download_button(
        label=f"📥 {target_year}년 {target_month}월 일별공급계획 다운로드 (Excel)",
        data=buffer.getvalue(),
        file_name=f"{target_year}_{target_month:02d}_일별공급계획.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


def _build_year_daily_plan(df_daily: pd.DataFrame, df_plan: pd.DataFrame, target_year: int, recent_window: int):
    cal_df = load_effective_calendar()

    all_rows = []
    month_summary_rows = []

    for m in range(1, 13):
        row_plan = df_plan[(df_plan["연"] == target_year) & (df_plan["월"] == m)]
        plan_total = float(row_plan["계획(사업계획제출_MJ)"].iloc[0]) if not row_plan.empty else np.nan

        df_res, _, used_years, _ = make_daily_plan_table_pattern(
            df_daily=df_daily, df_plan=df_plan, target_year=target_year, target_month=m, recent_window=recent_window
        )

        if df_res is None:
            # fallback: 균등분배
            last_day = calendar.monthrange(target_year, m)[1]
            dr = pd.date_range(f"{target_year}-{m:02d}-01", periods=last_day, freq="D")
            tmp = pd.DataFrame({"일자": dr})
            tmp["연"] = target_year
            tmp["월"] = m
            tmp["일"] = tmp["일자"].dt.day
            tmp["weekday_idx"] = tmp["일자"].dt.weekday
            tmp["요일"] = tmp["weekday_idx"].map(_korean_dow_name)

            if cal_df is not None:
                tmp = tmp.merge(cal_df, on="일자", how="left")
            tmp = _make_category_labels(tmp)

            tmp["카테고리비중(최근N년평균)"] = np.nan
            tmp["일별비율"] = 1.0 / last_day
            tmp["최근N년_총공급량(MJ)"] = np.nan
            tmp["최근N년_평균공급량(MJ)"] = np.nan
            tmp["예상공급량(MJ)"] = (tmp["일별비율"] * plan_total).round(0) if pd.notna(plan_total) else np.nan

            df_res = tmp[
                [
                    "연", "월", "일", "일자", "요일",
                    "카테고리", "카테고리비중(최근N년평균)",
                    "공휴일여부", "명절여부",
                    "최근N년_평균공급량(MJ)", "최근N년_총공급량(MJ)",
                    "일별비율", "예상공급량(MJ)",
                ]
            ].copy()

        all_rows.append(df_res)
        month_summary_rows.append({"월": m, "월간 계획(MJ)": plan_total})

    df_year = pd.concat(all_rows, ignore_index=True).sort_values(["월", "일"]).reset_index(drop=True)

    total_row = {
        "연": "",
        "월": "",
        "일": "",
        "일자": "",
        "요일": "합계",
        "카테고리": "",
        "카테고리비중(최근N년평균)": "",
        "공휴일여부": False,
        "명절여부": False,
        "최근N년_평균공급량(MJ)": df_year["최근N년_평균공급량(MJ)"].sum(skipna=True),
        "최근N년_총공급량(MJ)": df_year["최근N년_총공급량(MJ)"].sum(skipna=True),
        "일별비율": df_year["일별비율"].sum(skipna=True),
        "예상공급량(MJ)": df_year["예상공급량(MJ)"].sum(skipna=True),
    }
    df_year_with_total = pd.concat([df_year, pd.DataFrame([total_row])], ignore_index=True)

    df_month_sum = pd.DataFrame(month_summary_rows).sort_values("월").reset_index(drop=True)
    df_month_sum_total = pd.DataFrame([{"월": "소계", "월간 계획(MJ)": df_month_sum["월간 계획(MJ)"].sum(skipna=True)}])
    df_month_sum = pd.concat([df_month_sum, df_month_sum_total], ignore_index=True)

    return df_year_with_total, df_month_sum


# ─────────────────────────────────────────────
# 탭: Daily 공급량 분석(개선 버전)
# ─────────────────────────────────────────────
def tab_daily_plan_pattern(df_daily: pd.DataFrame):
    st.subheader("📅 Daily 공급량 분석 — 최근 N년 패턴 기반 (평일1/2 비중 우선 반영)")

    df_plan = load_monthly_plan()

    years_plan = sorted(df_plan["연"].unique())
    default_year_idx = years_plan.index(2026) if 2026 in years_plan else len(years_plan) - 1

    col_y, col_m, _ = st.columns([1, 1, 2])
    with col_y:
        target_year = st.selectbox("계획 연도 선택", years_plan, index=default_year_idx, key="pat_year")
    with col_m:
        months_plan = sorted(df_plan[df_plan["연"] == target_year]["월"].unique())
        default_month_idx = months_plan.index(1) if 1 in months_plan else 0
        target_month = st.selectbox("계획 월 선택", months_plan, index=default_month_idx, format_func=lambda m: f"{m}월", key="pat_month")

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
            "최근 몇 년 기준으로 비율을 계산할까?",
            min_value=slider_min,
            max_value=slider_max,
            value=min(3, slider_max),
            step=1,
            key="pat_recent",
        )

    st.caption(
        "이번 버전은 '평일1/평일2/주말' 월 비중을 최근 N년 데이터로 먼저 고정한 뒤, "
        "각 카테고리 내부의 일별 패턴을 적용해서 배분해."
    )

    df_result, df_mat, recent_years, share_tbl = make_daily_plan_table_pattern(
        df_daily=df_daily,
        df_plan=df_plan,
        target_year=int(target_year),
        target_month=int(target_month),
        recent_window=int(recent_window),
    )
    if df_result is None or len(recent_years) == 0:
        st.warning("해당 연도/월에 대해 선택한 최근 N년 기준으로 계산할 수 있는 데이터가 없어.")
        return

    st.markdown(f"- 실제 사용된 과거 연도: {min(recent_years)}년 ~ {max(recent_years)}년 (총 {len(recent_years)}개)")

    row_plan = df_plan[(df_plan["연"] == target_year) & (df_plan["월"] == target_month)]
    plan_total_raw = float(row_plan["계획(사업계획제출_MJ)"].iloc[0]) if not row_plan.empty else None

    plan_total_sum = float(df_result["예상공급량(MJ)"].sum())
    st.markdown(f"**{target_year}년 {target_month}월 사업계획 제출 공급량 합계:** `{plan_total_sum:,.0f} MJ`")

    _render_daily_plan_ui(
        df_result=df_result,
        df_mat=df_mat,
        recent_years=recent_years,
        target_year=int(target_year),
        target_month=int(target_month),
        recent_window=int(recent_window),
        plan_total_raw=plan_total_raw,
        share_tbl=share_tbl,
    )

    # 연간 다운로드
    st.markdown("#### 6. 일일계획 다운로드(연간)")
    col_ay, col_btn = st.columns([1, 3])
    with col_ay:
        annual_year = st.selectbox("연간 계획 연도 선택", years_plan, index=years_plan.index(target_year), key="pat_annual_year")
    with col_btn:
        st.caption("선택한 연도(1/1~12/31) 일별계획을 '연간' 시트로, '월 요약 계획' 시트에 월합계를 내려받을 수 있어.")

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

        _format_excel_sheet(ws_y, freeze="A2", center=True, width_map={"A": 6, "B": 4, "C": 4, "D": 14, "E": 6, "F": 18, "G": 18, "H": 12, "I": 12, "J": 20, "K": 20, "L": 12, "M": 18})
        _format_excel_sheet(ws_m, freeze="A2", center=True, width_map={"A": 10, "B": 18})

        for c in range(1, ws_y.max_column + 1):
            ws_y.cell(1, c).font = Font(bold=True)
        for c in range(1, ws_m.max_column + 1):
            ws_m.cell(1, c).font = Font(bold=True)

    st.download_button(
        label=f"📥 {annual_year}년 연간 일별공급계획 다운로드 (Excel)",
        data=buffer_year.getvalue(),
        file_name=f"{annual_year}_연간_일별공급계획(패턴_평일1-2).xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key="pat_download_annual_excel",
    )


# ─────────────────────────────────────────────
# 탭: Daily·Monthly 공급량 비교 (원 코드 유지)
# ─────────────────────────────────────────────
def fit_poly3_and_r2(x: pd.Series, y: pd.Series):
    x = np.asarray(x, dtype="float64")
    y = np.asarray(y, dtype="float64")
    if len(x) < 4:
        return None, None, None

    coef = np.polyfit(x, y, 3)
    y_pred = np.polyval(coef, x)

    ss_res = np.sum((y - y_pred) ** 2)
    ss_tot = np.sum((y - np.mean(y)) ** 2)
    r2 = np.nan if ss_tot == 0 else 1 - ss_res / ss_tot
    return coef, y_pred, r2


def plot_poly_fit(x, y, coef, title, x_label, y_label):
    x = np.asarray(x, dtype="float64")
    y = np.asarray(y, dtype="float64")

    x_grid = np.linspace(x.min(), x.max(), 200)
    y_grid = np.polyval(coef, x_grid)

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=x, y=y, mode="markers", name="실적"))
    fig.add_trace(go.Scatter(x=x_grid, y=y_grid, mode="lines", name="3차 다항식 예측"))
    fig.update_layout(title=title, xaxis_title=x_label, yaxis_title=y_label, margin=dict(l=20, r=20, t=40, b=20))
    return fig


def tab_daily_monthly_compare(df: pd.DataFrame, df_temp_all: pd.DataFrame):
    min_year_model = int(df["연도"].min())
    max_year_model = int(df["연도"].max())

    min_year_temp = int(df_temp_all["연도"].min())
    max_year_temp = int(df_temp_all["연도"].max())

    st.subheader("📊 0. 상관도 분석 (공급량 vs 주요 변수)")
    df_corr_raw = load_corr_data()
    if df_corr_raw is None:
        st.caption("상관도분석.xlsx 파일이 없어서 상관도 매트릭스를 표시하지 못했어.")
    else:
        num_df = df_corr_raw.select_dtypes(include=["number"]).copy()
        num_cols = list(num_df.columns)
        if len(num_cols) >= 2:
            corr = num_df.corr()
            z = corr.values
            z_display = np.clip(z, -0.7, 0.7)
            text = corr.round(2).astype(str).values

            fig_corr = go.Figure(
                data=go.Heatmap(
                    z=z_display,
                    x=corr.columns,
                    y=corr.index,
                    colorscale="RdBu_r",
                    zmin=-0.7,
                    zmax=0.7,
                    zmid=0,
                    colorbar_title="상관계수",
                    text=text,
                    texttemplate="%{text}",
                )
            )
            fig_corr.update_layout(
                xaxis_title="변수",
                yaxis_title="변수",
                xaxis=dict(side="top", tickangle=45),
                yaxis=dict(autorange="reversed"),
                width=600,
                height=600,
                margin=dict(l=80, r=20, t=80, b=80),
            )

            target_col = None
            for c in num_cols:
                if "공급량" in str(c):
                    target_col = c
                    break
            if target_col is None:
                target_col = num_cols[0]

            if target_col in corr.columns:
                target_series = corr[target_col].drop(target_col)
                target_series = target_series.reindex(target_series.abs().sort_values(ascending=False).index)

                tbl_df = target_series.to_frame(name="상관계수")
                tbl_df_disp = tbl_df.copy()
                tbl_df_disp["상관계수"] = tbl_df_disp["상관계수"].map(lambda x: f"{x:.2f}")

                col_hm, col_tbl = st.columns([3, 2])
                with col_hm:
                    st.plotly_chart(fig_corr, use_container_width=True)
                with col_tbl:
                    st.markdown(f"**기준 변수: `{target_col}` 과(와) 다른 변수들의 상관계수**")
                    st.table(center_style(tbl_df_disp))
        else:
            st.caption("숫자 컬럼이 2개 미만이라 상관도 분석을 할 수 없어.")

    st.subheader("📚 ① 데이터 학습기간 선택 (3차 다항식 R² 계산용)")
    train_default_start = max(min_year_model, max_year_model - 4)
    col_train, _ = st.columns([1, 1])
    with col_train:
        train_start, train_end = st.slider(
            "학습에 사용할 연도 범위",
            min_value=min_year_model,
            max_value=max_year_model,
            value=(train_default_start, max_year_model),
            step=1,
        )
    st.caption(f"현재 학습 구간: **{train_start}년 ~ {train_end}년**")
    df_window = df[df["연도"].between(train_start, train_end)].copy()

    df_month = (
        df_window.groupby(["연도", "월"], as_index=False)
        .agg(공급량_MJ=("공급량(MJ)", "sum"), 평균기온=("평균기온(℃)", "mean"))
    )

    coef_m, y_pred_m, r2_m = fit_poly3_and_r2(df_month["평균기온"], df_month["공급량_MJ"])
    df_month["예측공급량_MJ"] = y_pred_m if y_pred_m is not None else np.nan

    coef_d, y_pred_d, r2_d = fit_poly3_and_r2(df_window["평균기온(℃)"], df_window["공급량(MJ)"])
    df_window["예측공급량_MJ"] = y_pred_d if y_pred_d is not None else np.nan

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
            st.plotly_chart(
                plot_poly_fit(
                    df_month["평균기온"], df_month["공급량_MJ"], coef_m,
                    title="월단위: 월평균 기온 vs 월별 공급량(MJ)",
                    x_label="월평균 기온 (℃)", y_label="월별 공급량 합계 (MJ)",
                ),
                use_container_width=True,
            )
    with col4:
        if coef_d is not None:
            st.plotly_chart(
                plot_poly_fit(
                    df_window["평균기온(℃)"], df_window["공급량(MJ)"], coef_d,
                    title="일단위: 일평균 기온 vs 일별 공급량(MJ)",
                    x_label="일평균 기온 (℃)", y_label="일별 공급량 (MJ)",
                ),
                use_container_width=True,
            )

    st.subheader("🧊 ② 기온 시나리오 연도 범위 선택 (월평균 vs 일평균 예측 비교용)")
    scen_default_start = max(min_year_temp, max_year_temp - 4)
    col_scen, _ = st.columns([1, 1])
    with col_scen:
        scen_start, scen_end = st.slider(
            "기온 시나리오에 사용할 연도 범위",
            min_value=min_year_temp,
            max_value=max_year_temp,
            value=(scen_default_start, max_year_temp),
            step=1,
        )
    st.caption(f"선택한 기온 시나리오 연도: **{scen_start}년 ~ {scen_end}년** (각 월별 평균기온 사용)")

    df_scen = df_temp_all[df_temp_all["연도"].between(scen_start, scen_end)].copy()
    if df_scen.empty:
        st.write("선택한 기온 시나리오 구간에 데이터가 없어.")
        return

    temp_month = df_scen.groupby("월")["평균기온(℃)"].mean().sort_index()

    monthly_pred_from_month_model = None
    if coef_m is not None:
        monthly_pred_vals = np.polyval(coef_m, temp_month.values)
        monthly_pred_from_month_model = pd.Series(monthly_pred_vals, index=temp_month.index, name=f"월단위 Poly-3 예측(MJ) - 기온 {scen_start}~{scen_end}년 평균")

    monthly_pred_from_daily_model = None
    if coef_d is not None:
        df_scen = df_scen.copy()
        df_scen["예측일공급량_MJ_from_daily"] = np.polyval(coef_d, df_scen["평균기온(℃)"].to_numpy())
        monthly_daily_by_year = df_scen.groupby(["연도", "월"])["예측일공급량_MJ_from_daily"].sum().reset_index()
        monthly_pred_from_daily_model = monthly_daily_by_year.groupby("월")["예측일공급량_MJ_from_daily"].mean().sort_index()
        monthly_pred_from_daily_model.name = f"일단위 Poly-3 예측합(MJ) - 기온 {scen_start}~{scen_end}년 평균"

    st.markdown("##### 예측/실적 연도 선택")
    year_options = sorted(df["연도"].unique())
    col_pred_year, _ = st.columns([1, 3])
    with col_pred_year:
        pred_year = st.selectbox("실제 월별 공급량을 확인할 연도", options=year_options, index=len(year_options) - 1)

    df_actual_year = df[df["연도"] == pred_year].copy()
    monthly_actual = None
    if not df_actual_year.empty:
        monthly_actual = df_actual_year.groupby("월")["공급량(MJ)"].sum().sort_index()
        monthly_actual.name = f"{pred_year}년 실적(MJ)"

    st.subheader("🔥 월별 예측 vs 실적 — 월단위 Poly-3 vs 일단위 Poly-3(합산)")
    month_index = list(range(1, 13))
    compare_dict = {}
    if monthly_actual is not None:
        compare_dict[monthly_actual.name] = monthly_actual
    if monthly_pred_from_month_model is not None:
        compare_dict[monthly_pred_from_month_model.name] = monthly_pred_from_month_model
    if monthly_pred_from_daily_model is not None:
        compare_dict[monthly_pred_from_daily_model.name] = monthly_pred_from_daily_model
    df_compare = pd.DataFrame(compare_dict, index=month_index)

    r2_m_txt = f"{r2_m:.3f}" if r2_m is not None else "N/A"
    r2_d_txt = f"{r2_d:.3f}" if r2_d is not None else "N/A"

    fig_line = go.Figure()
    for col in df_compare.columns:
        fig_line.add_trace(go.Scatter(x=list(df_compare.index), y=df_compare[col], mode="lines+markers", name=col))

    fig_line.update_layout(
        title=(f"{pred_year}년 월별 공급량: 실적 vs 예측 (기온 시나리오 {scen_start}~{scen_end}년 평균, Poly-3)"
               f"<br><sup>월평균 R²={r2_m_txt}, 일평균 R²={r2_d_txt}</sup>"),
        xaxis_title="월",
        yaxis_title="공급량 (MJ)",
        xaxis=dict(tickmode="array", tickvals=month_index, ticktext=[f"{m}월" for m in month_index]),
        margin=dict(l=20, r=20, t=40, b=20),
    )
    st.plotly_chart(fig_line, use_container_width=True)

    st.markdown("##### 월별 실적/예측 수치표")
    df_compare_view = df_compare.copy()
    df_compare_view.index = [f"{m}월" for m in df_compare_view.index]
    df_compare_view = format_table_generic(df_compare_view)
    st.table(center_style(df_compare_view))


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
        tab_daily_plan_pattern(df_daily=df)
    else:
        st.title("도시가스 공급량 — 일별 vs 월별 예측 검증")
        tab_daily_monthly_compare(df=df, df_temp_all=df_temp_all)


if __name__ == "__main__":
    main()
