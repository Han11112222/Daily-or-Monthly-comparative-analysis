def make_daily_plan_table(
    df_daily: pd.DataFrame,
    df_plan: pd.DataFrame,
    target_year: int = 2026,
    target_month: int = 1,
    recent_window: int = 3,
) -> tuple[pd.DataFrame | None, pd.DataFrame | None, list[int]]:
    """
    최근 recent_window년 같은 월의 일별 공급 패턴으로
    target_year/target_month 일별 비율과 일별 계획 공급량을 계산.

    토·일 + 공휴일 + 명절(설날/추석 등)을 모두 '주말' 패턴으로 묶어서 사용.
    """
    cal_df = load_effective_calendar()

    # 사용 가능한 연도 범위
    all_years = sorted(df_daily["연도"].unique())
    start_year = target_year - recent_window
    recent_years = [y for y in range(start_year, target_year) if y in all_years]

    if len(recent_years) == 0:
        return None, None, []

    # 최근 N년 + 대상 월 데이터
    df_recent = df_daily[
        (df_daily["연도"].isin(recent_years)) & (df_daily["월"] == target_month)
    ].copy()
    if df_recent.empty:
        return None, None, recent_years

    df_recent = df_recent.sort_values(["연도", "일"]).copy()
    df_recent["weekday_idx"] = df_recent["일자"].dt.weekday  # 0=월, 6=일

    # ── 캘린더 정보를 머지해서 공휴일/명절 붙이기 ──
    if cal_df is not None:
        df_recent = df_recent.merge(
            cal_df,
            on="일자",
            how="left",
        )
        df_recent["공휴일여부"] = df_recent["공휴일여부"].fillna(False).astype(bool)
        df_recent["명절여부"] = df_recent["명절여부"].fillna(False).astype(bool)
    else:
        df_recent["공휴일여부"] = False
        df_recent["명절여부"] = False

    df_recent["is_holiday"] = df_recent["공휴일여부"] | df_recent["명절여부"]
    # 주말 정의: 토/일 OR 공휴일/명절
    df_recent["is_weekend"] = (df_recent["weekday_idx"] >= 5) | df_recent["is_holiday"]

    # 연도별 월 합계
    df_recent["month_total"] = (
        df_recent.groupby("연도")["공급량(MJ)"].transform("sum")
    )
    df_recent["ratio"] = df_recent["공급량(MJ)"] / df_recent["month_total"]

    # 같은 연도·요일(월~일) 내에서 몇 번째 요일인지 (1번째 토요일, 2번째 토요일 ...)
    df_recent["nth_dow"] = (
        df_recent.sort_values(["연도", "일"])
        .groupby(["연도", "weekday_idx"])
        .cumcount()
        + 1
    )

    weekday_mask = ~df_recent["is_weekend"]
    weekend_mask = df_recent["is_weekend"]

    # 평일: 일자 기준 평균 비율 / 요일 기준 백업 비율
    ratio_by_day = (
        df_recent[weekday_mask].groupby("일")["ratio"].mean()
        if df_recent[weekday_mask].size > 0
        else pd.Series(dtype=float)
    )
    ratio_weekday_by_dow = (
        df_recent[weekday_mask].groupby("weekday_idx")["ratio"].mean()
        if df_recent[weekday_mask].size > 0
        else pd.Series(dtype=float)
    )

    # 주말(토·일 + 공휴일/명절): (요일, nth_dow) 기준 평균 비율 / 요일 기준 백업 비율
    ratio_weekend_group = (
        df_recent[weekend_mask]
        .groupby(["weekday_idx", "nth_dow"])["ratio"]
        .mean()
        if df_recent[weekend_mask].size > 0
        else pd.Series(dtype=float)
    )
    ratio_weekend_by_dow = (
        df_recent[weekend_mask].groupby("weekday_idx")["ratio"].mean()
        if df_recent[weekend_mask].size > 0
        else pd.Series(dtype=float)
    )

    # dict 로 변환
    ratio_by_day_dict = ratio_by_day.to_dict()
    ratio_weekday_by_dow_dict = ratio_weekday_by_dow.to_dict()
    ratio_weekend_group_dict = ratio_weekend_group.to_dict()
    ratio_weekend_by_dow_dict = ratio_weekend_by_dow.to_dict()

    # 대상 연·월 날짜 생성
    last_day = calendar.monthrange(target_year, target_month)[1]
    date_range = pd.date_range(
        f"{target_year}-{target_month:02d}-01", periods=last_day, freq="D"
    )

    df_target = pd.DataFrame({"일자": date_range})
    df_target["연"] = target_year
    df_target["월"] = target_month
    df_target["일"] = df_target["일자"].dt.day
    df_target["weekday_idx"] = df_target["일자"].dt.weekday

    # 캘린더 붙이기 (대상월)
    if cal_df is not None:
        df_target = df_target.merge(
            cal_df,
            on="일자",
            how="left",
        )
        df_target["공휴일여부"] = df_target["공휴일여부"].fillna(False).astype(bool)
        df_target["명절여부"] = df_target["명절여부"].fillna(False).astype(bool)
    else:
        df_target["공휴일여부"] = False
        df_target["명절여부"] = False

    df_target["is_holiday"] = df_target["공휴일여부"] | df_target["명절여부"]
    df_target["is_weekend"] = (df_target["weekday_idx"] >= 5) | df_target["is_holiday"]

    weekday_names = ["월", "화", "수", "목", "금", "토", "일"]
    df_target["요일"] = df_target["weekday_idx"].map(lambda i: weekday_names[i])

    # 대상 월에서도 요일별로 몇 번째인지 계산
    df_target["nth_dow"] = (
        df_target.sort_values("일")
        .groupby("weekday_idx")
        .cumcount()
        + 1
    )

    def _label(row):
        return "주말" if row["is_weekend"] else "평일"

    df_target["구분(평일/주말)"] = df_target.apply(_label, axis=1)

    # 1단계: 주말 비율 확정
    def _weekend_ratio(row):
        dow = row["weekday_idx"]
        nth = row["nth_dow"]
        key = (dow, nth)

        val = ratio_weekend_group_dict.get(key, None)
        if val is None or pd.isna(val):
            val = ratio_weekend_by_dow_dict.get(dow, None)
        return val

    def _weekday_ratio(row):
        day = row["일"]
        dow = row["weekday_idx"]

        val = ratio_by_day_dict.get(day, None)
        if val is None or pd.isna(val):
            val = ratio_weekday_by_dow_dict.get(dow, None)
        return val

    df_target["weekend_raw"] = 0.0
    df_target["weekday_raw"] = 0.0

    for idx, row in df_target.iterrows():
        if row["is_weekend"]:
            val = _weekend_ratio(row)
            df_target.at[idx, "weekend_raw"] = val if val is not None else np.nan
        else:
            val = _weekday_ratio(row)
            df_target.at[idx, "weekday_raw"] = val if val is not None else np.nan

    # NaN 처리
    if df_target["weekend_raw"].notna().any():
        mean_wend = df_target["weekend_raw"].dropna().mean()
        df_target["weekend_raw"] = df_target["weekend_raw"].fillna(mean_wend)
    else:
        df_target["weekend_raw"] = 0.0

    if df_target["weekday_raw"].notna().any():
        mean_wday = df_target["weekday_raw"].dropna().mean()
        df_target["weekday_raw"] = df_target["weekday_raw"].fillna(mean_wday)
    else:
        df_target["weekday_raw"] = 0.0

    weekend_raw_sum = df_target["weekend_raw"].sum()
    weekday_raw_sum = df_target["weekday_raw"].sum()

    # 전체 비율 합(주말+평일)이 0이면 균등 분배
    if weekend_raw_sum + weekday_raw_sum <= 0:
        df_target["일별비율"] = 1.0 / last_day
    else:
        total_raw = weekend_raw_sum + weekday_raw_sum
        scale_all = 1.0 / total_raw

        df_target["weekend_scaled"] = df_target["weekend_raw"] * scale_all
        weekend_total_share = df_target["weekend_scaled"].sum()

        # 남은 비율(평일 몫)
        rest_share = max(1.0 - weekend_total_share, 0.0)

        if weekday_raw_sum > 0 and rest_share > 0:
            weekday_norm = df_target["weekday_raw"] / weekday_raw_sum
            df_target["weekday_scaled"] = weekday_norm * rest_share
        else:
            df_target["weekday_scaled"] = rest_share / last_day

        df_target["일별비율"] = df_target["weekend_scaled"] + df_target["weekday_scaled"]

        total_ratio = df_target["일별비율"].sum()
        if total_ratio > 0:
            df_target["일별비율"] = df_target["일별비율"] / total_ratio
        else:
            df_target["일별비율"] = 1.0 / last_day

    # ── (추가) 12월 25일 비율 스무딩 ─────────────────────────────
    #   - 12월이고,
    #   - 25일의 일별비율이 주변(23~27일) 평균의 60%보다 작으면
    #     → 60% 수준까지 끌어올리고, 다시 합이 1이 되도록 정규화.
    if target_month == 12:
        mask_25 = df_target["일"] == 25
        if mask_25.any():
            neighbor_mask = df_target["일"].between(23, 27) & (~mask_25)
            if neighbor_mask.any():
                neighbor_mean = df_target.loc[neighbor_mask, "일별비율"].mean()
                if not np.isnan(neighbor_mean):
                    min_factor = 0.6  # 필요하면 0.5~0.8 사이로 조정
                    min_ratio_25 = neighbor_mean * min_factor

                    current_25 = df_target.loc[mask_25, "일별비율"].iloc[0]
                    if current_25 < min_ratio_25:
                        df_target.loc[mask_25, "일별비율"] = min_ratio_25
                        # 다시 합이 1이 되도록 정규화
                        df_target["일별비율"] = (
                            df_target["일별비율"] / df_target["일별비율"].sum()
                        )

    # 최근 N년 기준 총·평균 공급량
    month_total_all = df_recent["공급량(MJ)"].sum()
    df_target["최근N년_총공급량(MJ)"] = df_target["일별비율"] * month_total_all
    df_target["최근N년_평균공급량(MJ)"] = (
        df_target["최근N년_총공급량(MJ)"] / len(recent_years)
    )

    # 대상 연도의 월 계획 총량
    row_plan = df_plan[
        (df_plan["연"] == target_year) & (df_plan["월"] == target_month)
    ]
    if row_plan.empty:
        plan_total = np.nan
    else:
        plan_total = float(row_plan["계획(사업계획제출_MJ)"].iloc[0])

    # 일별 예상 공급량 (계획 기준)
    df_target["예상공급량(MJ)"] = (df_target["일별비율"] * plan_total).round(0)

    # 정렬 및 컬럼 순서
    df_target = df_target.sort_values("일").reset_index(drop=True)
    df_result = df_target[
        [
            "연",
            "월",
            "일",
            "일자",
            "요일",
            "구분(평일/주말)",
            "공휴일여부",
            "최근N년_평균공급량(MJ)",
            "최근N년_총공급량(MJ)",
            "일별비율",
            "예상공급량(MJ)",
        ]
    ].copy()

    # 최근 N년 일별 실적 매트릭스 (Heatmap)
    df_mat = (
        df_recent.pivot_table(
            index="일", columns="연도", values="공급량(MJ)", aggfunc="sum"
        )
        .sort_index()
        .sort_index(axis=1)
    )

    return df_result, df_mat, recent_years
