"""
Chiến lược SMC đa khung:

1. HTF (H4): xác định xu hướng qua market structure (BOS/CHoCH).
2. LTF (M15): chỉ tìm lệnh THUẬN hướng HTF.
   Quy trình entry:
   a. Có liquidity sweep ngược hướng (stop hunt) trong sweep_lookback nến.
   b. Sau sweep, xuất hiện CHoCH trên LTF quay về hướng HTF → xác nhận.
   c. Entry tại OB hoặc FVG hình thành từ cú CHoCH đó (limit-zone, ở đây
      đơn giản hoá thành market khi giá quay về chạm vùng).
3. SL: ngoài biên OB/swing sweep + buffer. TP: pool thanh khoản đối diện
   (hoặc fixed R:R). Chỉ vào lệnh nếu R:R >= min_rr.
"""
import logging
import pandas as pd
from config import TradingConfig
from smc.structure import find_swings, detect_structure, current_trend_htf
from smc.order_blocks import find_order_blocks
from smc.fvg import find_fvgs
from smc.liquidity import build_liquidity_pools, detect_sweeps, nearest_target_pool
from risk import TradePlan, calc_lot_size, validate_rr
from indicators import adx, atr_percentile

log = logging.getLogger("strategy")


def analyze(htf_df: pd.DataFrame, ltf_df: pd.DataFrame, cfg: TradingConfig,
            balance: float, symbol_info: dict) -> TradePlan | None:
    # ── 1. Xu hướng HTF ─────────────────────────────────
    htf = current_trend_htf(htf_df, cfg.htf_swing_lookback, cfg.htf_trend_max_age_bars)
    trend = htf["trend"]
    if trend == "neutral":
        log.debug("HTF neutral — đứng ngoài.")
        return None

    # ── 2. Phân tích LTF ────────────────────────────────
    swings = find_swings(ltf_df, cfg.swing_lookback)
    events, _ = detect_structure(ltf_df, swings)
    pools = build_liquidity_pools(swings, cfg.eq_tolerance)
    sweeps = detect_sweeps(ltf_df, pools, cfg.sweep_lookback, cfg.sweep_pool_min_rank)

    # ── 3. Điều kiện sweep ngược hướng ──────────────────
    # Trend bullish → cần sellside sweep (quét đáy) trước khi buy.
    needed_sweep = "sellside_sweep" if trend == "bullish" else "buyside_sweep"
    recent_sweeps = [s for s in sweeps if s.kind == needed_sweep]
    if cfg.require_sweep and not recent_sweeps:
        return None
    last_sweep = recent_sweeps[-1] if recent_sweeps else None

    # ── 4. CHoCH/BOS xác nhận SAU sweep, thuận hướng HTF ─
    confirm = None
    for ev in reversed(events):
        if ev.direction == trend and (last_sweep is None or ev.index > last_sweep.index):
            confirm = ev
            break
    if confirm is None:
        return None

    # ── 4a. #12 ADX trend-strength gate (tùy chọn) ──────
    if cfg.adx_filter_enabled:
        adx_series = adx(ltf_df, cfg.adx_period)
        adx_at_confirm = adx_series.iloc[confirm.index]
        if pd.isna(adx_at_confirm) or adx_at_confirm < cfg.adx_min_threshold:
            log.debug("ADX tại điểm confirm quá yếu — trend không đủ lực, bỏ.")
            return None

    # ── 4a2. #13 ATR-regime gate (tùy chọn) — chặn khi biến động co hẹp ──
    if cfg.atr_regime_filter_enabled:
        pct_series = atr_percentile(ltf_df, cfg.atr_period, cfg.atr_regime_lookback)
        pct_at_confirm = pct_series.iloc[confirm.index]
        if pd.isna(pct_at_confirm) or pct_at_confirm < cfg.atr_regime_min_percentile:
            log.debug("ATR percentile tại điểm confirm quá thấp — biến động co hẹp/chop, bỏ.")
            return None

    # ── 4b. #9 Trần tuổi CẢ CHUỖI: sweep→confirm→entry phải còn tươi ──
    if cfg.max_setup_age_bars > 0:
        n_last = len(ltf_df) - 1
        if last_sweep is not None and n_last - last_sweep.index > cfg.max_setup_age_bars:
            log.debug("Sweep quá cũ so với hiện tại — chuỗi setup đã phân rã, bỏ.")
            return None
        if n_last - confirm.index > cfg.max_setup_age_bars:
            log.debug("Cú xác nhận quá cũ so với hiện tại — bỏ.")
            return None

    # ── 5. Vùng entry: OB / FVG từ cú xác nhận ──────────
    obs = find_order_blocks(ltf_df, [confirm], cfg.ob_require_imbalance, cfg.ob_max_age_bars)
    fvgs = [f for f in find_fvgs(ltf_df, cfg.fvg_min_size_points, cfg.fvg_max_age_bars)
            if f.direction == trend and f.index >= confirm.index - 3]

    current_price = ltf_df["close"].iloc[-1]
    zone = _pick_entry_zone(obs, fvgs, cfg.entry_mode, trend, current_price)
    if zone is None:
        return None
    zone_top, zone_bottom, zone_src = zone

    # ── 5b. #4 ĐẶT LIMIT TẠI BIÊN VÙNG (mitigation edge) ──
    # Thay vì market ở giá đóng (đã chạy khỏi vùng → entry xấu, RR ép), ta đặt LIMIT
    # nghỉ tại biên vùng và CHỈ khớp khi giá hồi về đúng mức. Điều kiện: giá hiện tại
    # còn ở phía đúng để chờ khớp (buy: giá trên đỉnh vùng; sell: giá dưới đáy vùng).
    if trend == "bullish":
        entry = zone_top          # buy limit tại đỉnh vùng (giá hồi xuống mới khớp)
        if current_price <= entry:
            log.debug(f"Giá đã ở trong/dưới vùng {zone_src} — không đặt được limit đẹp, bỏ.")
            return None
    else:
        entry = zone_bottom       # sell limit tại đáy vùng (giá hồi lên mới khớp)
        if current_price >= entry:
            log.debug(f"Giá đã ở trong/trên vùng {zone_src} — không đặt được limit đẹp, bỏ.")
            return None

    # ── 5c. #6 Chỉ mua DISCOUNT / bán PREMIUM (mốc equilibrium 50% của dải) ──
    if cfg.require_discount_premium:
        eq = _equilibrium(swings, cfg.eq_range_swings)
        if eq is not None:
            if trend == "bullish" and entry > eq:
                log.debug(f"Entry {entry:.2f} ở PREMIUM (>eq {eq:.2f}) — mua đắt, bỏ.")
                return None
            if trend == "bearish" and entry < eq:
                log.debug(f"Entry {entry:.2f} ở DISCOUNT (<eq {eq:.2f}) — bán rẻ, bỏ.")
                return None

    # ── 6. SL / TP (tính theo entry = biên vùng, nên RR xác thực = RR thật) ──
    if trend == "bullish":
        # #5: SL neo theo ĐÁY RÂU quét thật (extreme), không phải mức pool (đã bị chạm).
        sweep_low = last_sweep.extreme if last_sweep else zone_bottom
        sl = min(zone_bottom, sweep_low) - cfg.sl_buffer_points
        tp = None
        if cfg.tp_mode == "liquidity":
            htf_swings = find_swings(_prep(htf_df), cfg.htf_swing_lookback)
            htf_pools = build_liquidity_pools(htf_swings, cfg.eq_tolerance)
            tp = nearest_target_pool(htf_pools + pools, entry, "bullish")
        if tp is None or (tp - entry) / max(entry - sl, 1e-9) < cfg.min_rr:
            tp = entry + cfg.tp_rr * (entry - sl)
        # Trần R:R — không đuổi TP thanh khoản quá xa (khó chạm)
        tp = min(tp, entry + cfg.max_rr * (entry - sl))
        direction = "buy"
    else:
        # #5: SL neo theo ĐỈNH RÂU quét thật (extreme), không phải mức pool (đã bị chạm).
        sweep_high = last_sweep.extreme if last_sweep else zone_top
        sl = max(zone_top, sweep_high) + cfg.sl_buffer_points
        tp = None
        if cfg.tp_mode == "liquidity":
            htf_swings = find_swings(_prep(htf_df), cfg.htf_swing_lookback)
            htf_pools = build_liquidity_pools(htf_swings, cfg.eq_tolerance)
            tp = nearest_target_pool(htf_pools + pools, entry, "bearish")
        if tp is None or (entry - tp) / max(sl - entry, 1e-9) < cfg.min_rr:
            tp = entry - cfg.tp_rr * (sl - entry)
        # Trần R:R — không đuổi TP thanh khoản quá xa (khó chạm)
        tp = max(tp, entry - cfg.max_rr * (sl - entry))
        direction = "sell"

    # ── Lọc SL quá sát entry (lot phình to, bị quét ngay) ─
    if abs(entry - sl) < cfg.min_sl_distance_points:
        log.debug(f"SL cách entry {abs(entry-sl):.2f} < {cfg.min_sl_distance_points} — bỏ qua.")
        return None

    ok, rr = validate_rr(entry, sl, tp, cfg.min_rr)
    if not ok:
        log.debug(f"R:R {rr} < {cfg.min_rr} — bỏ qua.")
        return None

    # ── 7. Position sizing ──────────────────────────────
    lot = calc_lot_size(balance, cfg.risk_per_trade_pct, entry, sl,
                        symbol_info["contract_size"], symbol_info["volume_min"],
                        symbol_info["volume_step"], symbol_info["volume_max"])
    if lot <= 0:
        log.warning("Lot tính ra = 0 (SL quá xa so với tài khoản). Bỏ qua.")
        return None

    risk_amount = balance * cfg.risk_per_trade_pct / 100.0
    reason = (f"HTF {trend} | {needed_sweep} @ {last_sweep.swept_level:.2f} | "
              f"{confirm.kind} LTF | entry {zone_src}") if last_sweep else \
             (f"HTF {trend} | {confirm.kind} LTF | entry {zone_src}")

    d = cfg.price_digits
    return TradePlan(direction=direction, entry=round(entry, d), sl=round(sl, d),
                     tp=round(tp, d), lot=lot, rr=rr, risk_amount=round(risk_amount, 2),
                     reason=reason,
                     sweep_level=round(last_sweep.swept_level, d) if last_sweep else None,
                     order_kind="limit")


def _equilibrium(swings, n: int = 6):
    """Mốc cân bằng 50% của dải giao dịch, dựng từ N swing high/low gần nhất.
    Dùng để lọc premium/discount (#6). None nếu chưa đủ swing hai phía."""
    highs = [s.price for s in swings if s.kind == "high"][-n:]
    lows = [s.price for s in swings if s.kind == "low"][-n:]
    if not highs or not lows:
        return None
    return (max(highs) + min(lows)) / 2


def _pick_entry_zone(obs, fvgs, mode, trend, price):
    """Trả về (top, bottom, source) của vùng entry gần giá nhất."""
    candidates = []
    if mode in ("ob_only", "ob_or_fvg"):
        for ob in obs:
            if ob.direction == trend:
                candidates.append((ob.top, ob.bottom, "OB"))
    if mode in ("fvg_only", "ob_or_fvg"):
        for f in fvgs:
            candidates.append((f.top, f.bottom, "FVG"))
    if not candidates:
        return None
    # Chọn vùng gần giá hiện tại nhất
    mid = lambda z: (z[0] + z[1]) / 2
    return min(candidates, key=lambda z: abs(mid(z) - price))


def _prep(df):
    return df
