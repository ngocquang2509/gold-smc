"""
Wrapper quanh thư viện MetaTrader5.
LƯU Ý: thư viện này CHỈ chạy trên Windows nơi terminal MT5 được cài đặt.
Cài: pip install MetaTrader5 pandas
"""
import calendar
import logging
import pandas as pd

try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    mt5 = None
    MT5_AVAILABLE = False

log = logging.getLogger("mt5")

TIMEFRAME_MAP = {}
if MT5_AVAILABLE:
    TIMEFRAME_MAP = {
        "M1": mt5.TIMEFRAME_M1, "M5": mt5.TIMEFRAME_M5, "M15": mt5.TIMEFRAME_M15,
        "M30": mt5.TIMEFRAME_M30, "H1": mt5.TIMEFRAME_H1, "H4": mt5.TIMEFRAME_H4,
        "D1": mt5.TIMEFRAME_D1,
    }


class MT5Client:
    def __init__(self, symbol: str, magic: int, deviation: int):
        self.symbol = symbol
        self.magic = magic
        self.deviation = deviation

    # ── Kết nối ──────────────────────────────────────────
    def connect(self, login: int | None = None, password: str | None = None,
                server: str | None = None) -> bool:
        if not MT5_AVAILABLE:
            raise RuntimeError("Thư viện MetaTrader5 chưa cài hoặc không chạy trên Windows.")
        kwargs = {}
        if login:
            kwargs = {"login": login, "password": password, "server": server}
        if not mt5.initialize(**kwargs):
            log.error(f"initialize() thất bại: {mt5.last_error()}")
            return False
        if not mt5.symbol_select(self.symbol, True):
            log.error(f"Không chọn được symbol {self.symbol}. Kiểm tra tên symbol của broker.")
            return False
        info = mt5.account_info()
        log.info(f"Kết nối OK — Account {info.login}, balance {info.balance} {info.currency}")
        return True

    def shutdown(self):
        if MT5_AVAILABLE:
            mt5.shutdown()

    # ── Dữ liệu ──────────────────────────────────────────
    def get_rates(self, timeframe: str, bars: int) -> pd.DataFrame:
        tf = TIMEFRAME_MAP[timeframe]
        rates = mt5.copy_rates_from_pos(self.symbol, tf, 0, bars)
        if rates is None or len(rates) == 0:
            raise RuntimeError(f"Không lấy được dữ liệu {self.symbol} {timeframe}: {mt5.last_error()}")
        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s")
        df.set_index("time", inplace=True)
        return df[["open", "high", "low", "close", "tick_volume"]]

    def get_balance(self) -> float:
        return mt5.account_info().balance

    def get_symbol_info(self) -> dict:
        si = mt5.symbol_info(self.symbol)
        return {
            "contract_size": si.trade_contract_size,
            "volume_min": si.volume_min,
            "volume_step": si.volume_step,
            "volume_max": si.volume_max,
            "point": si.point,
            "digits": si.digits,
        }

    def get_tick(self):
        return mt5.symbol_info_tick(self.symbol)

    def _digits(self) -> int:
        """Số chữ số thập phân giá của symbol (2 cho vàng, 5 cho EURUSD...).
        Không hardcode 2 — SL/TP forex bị làm tròn sai nếu dùng số chữ số của vàng."""
        return mt5.symbol_info(self.symbol).digits

    def server_time(self):
        """Giờ SERVER của broker (không phải giờ máy local).
        Bộ lọc phiên PHẢI dùng giờ này để khớp backtest (nến MT5 là giờ server)."""
        from datetime import datetime, timezone
        t = mt5.symbol_info_tick(self.symbol)
        return datetime.fromtimestamp(t.time, tz=timezone.utc).replace(tzinfo=None)

    # ── Vị thế ───────────────────────────────────────────
    def open_positions(self):
        pos = mt5.positions_get(symbol=self.symbol)
        return [p for p in (pos or []) if p.magic == self.magic]

    # ── Đặt lệnh ─────────────────────────────────────────
    def market_order(self, direction: str, lot: float, sl: float, tp: float,
                     comment: str = "SMC") -> int | None:
        """Đặt lệnh thị trường. Trả về TICKET vị thế (position_id) nếu thành công, None nếu
        thất bại — dùng để ghi nhật ký/tra cứu lịch sử đóng lệnh sau này."""
        tick = self.get_tick()
        price = tick.ask if direction == "buy" else tick.bid
        order_type = mt5.ORDER_TYPE_BUY if direction == "buy" else mt5.ORDER_TYPE_SELL
        digits = self._digits()
        sl, tp = round(sl, digits), round(tp, digits)
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": self.symbol,
            "volume": lot,
            "type": order_type,
            "price": price,
            "sl": sl,
            "tp": tp,
            "deviation": self.deviation,
            "magic": self.magic,
            "comment": comment,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        result = mt5.order_send(request)
        if result is None:
            log.error(f"Đặt lệnh thất bại: order_send() trả về None — {mt5.last_error()}")
            return None
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            log.error(f"Đặt lệnh thất bại: retcode={result.retcode}, {result.comment}")
            return None
        # Tài khoản Market Execution có thể trả result.order = 0 (broker không gán
        # order id đồng bộ). result.deal thì luôn có — tra ngược ra position_id thật
        # qua lịch sử deal, đây mới là mã khớp với position.ticket xuyên suốt vòng đời lệnh.
        ticket = result.order or self._deal_position_id(result.deal)
        if not ticket:
            log.error(f"Đặt lệnh thành công (deal {result.deal}) nhưng không tra được "
                     f"ticket vị thế — sẽ không ghi nhật ký/thông báo cho lệnh này.")
            return None
        log.info(f"✅ {direction.upper()} {lot} lot @ {price} | SL {sl} | TP {tp} | ticket {ticket}")
        return ticket

    def _deal_position_id(self, deal_ticket: int) -> int | None:
        deals = mt5.history_deals_get(ticket=deal_ticket)
        return deals[0].position_id if deals else None

    # ── Lệnh chờ (LIMIT) — #4 ────────────────────────────
    def pending_orders(self):
        """Các lệnh CHỜ (chưa khớp) của bot — lọc theo magic."""
        orders = mt5.orders_get(symbol=self.symbol)
        return [o for o in (orders or []) if o.magic == self.magic]

    def pending_order(self, direction: str, lot: float, price: float, sl: float, tp: float,
                      expiry_dt, comment: str = "SMC-limit") -> int | None:
        """Đặt LIMIT nghỉ tại biên vùng, broker tự HẾT HẠN ở `expiry_dt` (ORDER_TIME_SPECIFIED)
        nên không cần tự huỷ theo nến. price/sl/tp đã được làm tròn đúng digits ở strategy.
        Trả về order ticket (== position ticket khi khớp) hoặc None."""
        order_type = mt5.ORDER_TYPE_BUY_LIMIT if direction == "buy" else mt5.ORDER_TYPE_SELL_LIMIT
        request = {
            "action": mt5.TRADE_ACTION_PENDING,
            "symbol": self.symbol,
            "volume": lot,
            "type": order_type,
            "price": price,
            "sl": sl,
            "tp": tp,
            "deviation": self.deviation,
            "magic": self.magic,
            "comment": comment,
            "type_time": mt5.ORDER_TIME_SPECIFIED,
            "expiration": calendar.timegm(expiry_dt.timetuple()),
            "type_filling": mt5.ORDER_FILLING_RETURN,
        }
        result = mt5.order_send(request)
        if result is None:
            log.error(f"Đặt LIMIT thất bại: order_send() trả về None — {mt5.last_error()}")
            return None
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            log.error(f"Đặt LIMIT thất bại: retcode={result.retcode}, {result.comment}")
            return None
        log.info(f"⏳ LIMIT {direction.upper()} {lot} @ {price} | SL {sl} | TP {tp} | "
                 f"order {result.order} (hết hạn {expiry_dt})")
        return result.order

    def cancel_order(self, order_ticket: int) -> bool:
        result = mt5.order_send({"action": mt5.TRADE_ACTION_REMOVE, "order": int(order_ticket)})
        if result is None:
            log.error(f"Huỷ lệnh chờ {order_ticket} thất bại: order_send() trả về None — {mt5.last_error()}")
            return False
        ok = result.retcode == mt5.TRADE_RETCODE_DONE
        if ok:
            log.info(f"🗑️  Huỷ lệnh chờ {order_ticket}")
        return ok

    def position_close_info(self, ticket: int):
        """Tra lịch sử để biết một vị thế đã đóng thế nào.
        Trả về (result, close_price, profit) hoặc None nếu chưa có lịch sử đóng.
        result: 'success' (chạm TP) | 'failed' (chạm SL) | 'breakeven' (SL dời về entry)
                | 'closed' (đóng tay/lý do khác).
        profit = tổng lãi/lỗ ròng của mọi deal thoát (gồm swap + commission)."""
        deals = mt5.history_deals_get(position=int(ticket))
        if not deals:
            return None
        entry_in = [d for d in deals if d.entry == mt5.DEAL_ENTRY_IN]
        exits = [d for d in deals if d.entry in (mt5.DEAL_ENTRY_OUT, mt5.DEAL_ENTRY_OUT_BY)]
        if not exits:
            return None   # vị thế vẫn còn mở (chỉ có deal vào)
        last = max(exits, key=lambda d: d.time_msc)
        profit = round(sum(d.profit + d.swap + d.commission for d in exits), 2)

        if last.reason == mt5.DEAL_REASON_TP:
            result = "success"
        elif last.reason == mt5.DEAL_REASON_SL:
            # SL có thể đã được dời về entry (breakeven) → phân biệt bằng giá đóng.
            entry_price = entry_in[0].price if entry_in else None
            tol = mt5.symbol_info(self.symbol).point * 5
            if entry_price is not None and abs(last.price - entry_price) <= tol:
                result = "breakeven"
            else:
                result = "failed"
        else:
            result = "closed"
        return result, round(last.price, self._digits()), profit

    def modify_sl(self, position, new_sl: float) -> bool:
        new_sl = round(new_sl, self._digits())
        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "symbol": self.symbol,
            "position": position.ticket,
            "sl": new_sl,
            "tp": position.tp,
        }
        result = mt5.order_send(request)
        ok = result.retcode == mt5.TRADE_RETCODE_DONE
        if ok:
            log.info(f"🔒 Dời SL vị thế {position.ticket} → {new_sl}")
        return ok

    def close_partial(self, position, lot_to_close: float) -> bool:
        tick = self.get_tick()
        is_buy = position.type == mt5.POSITION_TYPE_BUY
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": self.symbol,
            "volume": round(lot_to_close, 2),
            "type": mt5.ORDER_TYPE_SELL if is_buy else mt5.ORDER_TYPE_BUY,
            "position": position.ticket,
            "price": tick.bid if is_buy else tick.ask,
            "deviation": self.deviation,
            "magic": self.magic,
            "comment": "partial-close",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        result = mt5.order_send(request)
        ok = result.retcode == mt5.TRADE_RETCODE_DONE
        if ok:
            log.info(f"💰 Chốt {lot_to_close} lot vị thế {position.ticket}")
        return ok
