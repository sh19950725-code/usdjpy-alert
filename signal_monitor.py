#!/usr/bin/env python3
"""USD/JPY 1-hour breakout/pullback notifier using only the standard library."""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def load_config() -> dict:
    with (ROOT / "config.json").open(encoding="utf-8") as file:
        return json.load(file)


def request_json(url: str, *, headers: dict | None = None) -> dict:
    request = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"通信に失敗しました: {exc.reason}") from exc


def fetch_candles(config: dict, api_key: str) -> list[dict]:
    params = urllib.parse.urlencode(
        {
            "symbol": config["symbol"],
            "interval": config["interval"],
            "outputsize": 120,
            "timezone": "UTC",
            "apikey": api_key,
        }
    )
    payload = request_json(f"https://api.twelvedata.com/time_series?{params}")
    if payload.get("status") == "error" or "values" not in payload:
        raise RuntimeError(f"Twelve Dataエラー: {payload.get('message', payload)}")

    now = datetime.now(timezone.utc)
    candles = []
    for item in payload["values"]:
        started_at = datetime.fromisoformat(item["datetime"]).replace(tzinfo=timezone.utc)
        # Ignore the candle that is still forming.
        if started_at + timedelta(hours=1) > now:
            continue
        candles.append(
            {
                "time": started_at,
                "open": float(item["open"]),
                "high": float(item["high"]),
                "low": float(item["low"]),
                "close": float(item["close"]),
            }
        )
    candles.sort(key=lambda candle: candle["time"])
    return candles


def fetch_economic_calendar(now: datetime | None = None) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    params = urllib.parse.urlencode(
        {
            "from": (now - timedelta(days=2)).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "to": (now + timedelta(days=2)).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "countries": "US,JP",
        }
    )
    payload = request_json(
        f"https://economic-calendar.tradingview.com/events?{params}",
        headers={"Origin": "https://www.tradingview.com", "User-Agent": "Mozilla/5.0"},
    )
    events = payload.get("result") if isinstance(payload, dict) else None
    if not isinstance(events, list):
        raise RuntimeError("経済カレンダーの形式が想定と異なります。")
    return events


def parse_event_time(value: str) -> datetime:
    # Calendar values include a UTC offset. Accept a trailing Z as well.
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("経済指標の日時にタイムゾーンがありません。")
    return parsed.astimezone(timezone.utc)


def blocking_event(config: dict, now: datetime | None = None) -> dict | None:
    if not config.get("news_filter_enabled", True):
        return None
    now = now or datetime.now(timezone.utc)
    currencies = {str(item).upper() for item in config.get("news_currencies", ["USD", "JPY"])}
    impacts = {str(item).lower() for item in config.get("news_impacts", ["High"])}
    before = timedelta(minutes=float(config.get("news_pause_before_minutes", 60)))
    after = timedelta(minutes=float(config.get("news_pause_after_minutes", 60)))

    for event in fetch_economic_calendar(now):
        currency = str(event.get("country", event.get("currency", ""))).upper()
        raw_impact = event.get("impact", event.get("importance", ""))
        # TradingView represents high importance as 1; other providers may use "High".
        impact = "high" if raw_impact == 1 or str(raw_impact) == "1" else str(raw_impact).lower()
        if currency not in currencies or impact not in impacts:
            continue
        event_time = parse_event_time(str(event["date"]))
        if event_time - before <= now <= event_time + after:
            return {**event, "event_time_utc": event_time}
    return None


def sma(candles: list[dict], index: int, length: int) -> float | None:
    if index + 1 < length:
        return None
    return sum(candle["close"] for candle in candles[index - length + 1 : index + 1]) / length


def resistance(candles: list[dict], index: int, lookback: int) -> float | None:
    if index < lookback:
        return None
    return max(candle["high"] for candle in candles[index - lookback : index])


def evaluate_latest(candles: list[dict], config: dict) -> tuple[dict | None, str]:
    sma_length = int(config["sma_length"])
    lookback = int(config["resistance_lookback"])
    max_bars = int(config["max_pullback_bars"])
    tolerance = float(config["retest_tolerance_pips"]) * float(config["pip_size"])

    waiting = False
    broken_level = None
    breakout_index = None
    signals = []
    latest_reason = "条件を確認できませんでした。"

    for index, candle in enumerate(candles):
        is_latest = index == len(candles) - 1
        average = sma(candles, index, sma_length)
        level = resistance(candles, index, lookback)
        previous_level = resistance(candles, index - 1, lookback) if index else None
        previous_close = candles[index - 1]["close"] if index else None

        crossed = (
            average is not None
            and level is not None
            and previous_level is not None
            and previous_close is not None
            and candle["close"] > average
            and candle["close"] > level
            and previous_close <= previous_level
        )
        if crossed and not waiting:
            waiting = True
            broken_level = level
            breakout_index = index
            if is_latest:
                latest_reason = "レジスタンスを上抜けた直後です。次の足以降の押し目を待っています。"

        if not waiting:
            if is_latest:
                if average is None or level is None:
                    latest_reason = "判定に必要なローソク足が不足しています。"
                elif candle["close"] <= average:
                    latest_reason = f"終値 {candle['close']:.3f} が20SMA {average:.3f} 以下です。"
                elif candle["close"] <= level:
                    latest_reason = f"20SMAより上ですが、レジスタンス {level:.3f} をまだ上抜けていません。"
                else:
                    latest_reason = "新しいレジスタンス上抜けではないため、見送ります。"
            continue

        bars_since = index - breakout_index
        if bars_since > max_bars or candle["close"] < broken_level - tolerance:
            if is_latest:
                if bars_since > max_bars:
                    latest_reason = f"上抜け後{max_bars}本以内に押し目が成立せず、待機期限を超えました。"
                else:
                    latest_reason = f"終値が上抜け水準 {broken_level:.3f} を下回り、パターンが無効になりました。"
            waiting = False
            broken_level = None
            breakout_index = None
            continue

        if bars_since < 1:
            continue

        touches_level = candle["low"] <= broken_level + tolerance and candle["low"] >= broken_level - tolerance
        holds_level = average is not None and candle["close"] > broken_level and candle["close"] > average
        bullish = not config["require_bullish_candle"] or candle["close"] > candle["open"]
        if touches_level and holds_level and bullish:
            entry = candle["close"]
            stop = entry - float(config["stop_pips"]) * float(config["pip_size"])
            target = entry + (entry - stop) * float(config["risk_reward"])
            signals.append(
                {
                    "time": candle["time"],
                    "entry": entry,
                    "stop": stop,
                    "target": target,
                    "level": broken_level,
                }
            )
            waiting = False
            broken_level = None
            breakout_index = None
        elif is_latest:
            if candle["low"] > broken_level + tolerance:
                latest_reason = f"上抜け済みですが、安値 {candle['low']:.3f} が押し目水準 {broken_level:.3f} まで戻っていません。"
            elif candle["low"] < broken_level - tolerance:
                latest_reason = f"押し目が許容幅を超えて深くなりました（基準 {broken_level:.3f}）。"
            elif candle["close"] <= broken_level:
                latest_reason = f"押し目には到達しましたが、終値が上抜け水準 {broken_level:.3f} を回復していません。"
            elif average is not None and candle["close"] <= average:
                latest_reason = f"押し目には到達しましたが、終値が20SMA {average:.3f} 以下です。"
            elif not bullish:
                latest_reason = "押し目には到達しましたが、確定足が陽線ではありません。"
            else:
                latest_reason = "押し目反発の全条件がまだ揃っていません。"

    if signals and signals[-1]["time"] == candles[-1]["time"]:
        return signals[-1], "買い条件が成立しました。"
    return None, latest_reason


def notify_no_signal(config: dict, topic: str, message: str) -> None:
    if config.get("notify_on_no_signal", True):
        publish_ntfy(topic, "USD/JPY 見送り", message, "no_entry")


def publish_ntfy(topic: str, title: str, message: str, tags: str = "chart_with_upwards_trend") -> None:
    body = json.dumps(
        {"topic": topic, "title": title, "message": message, "tags": [tags]},
        ensure_ascii=False,
    ).encode("utf-8")
    request = urllib.request.Request(
        "https://ntfy.sh",
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30):
        pass


def main() -> int:
    api_key = os.environ.get("TWELVE_DATA_API_KEY", "").strip()
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if not api_key or not topic:
        print("TWELVE_DATA_API_KEY と NTFY_TOPIC を設定してください。", file=sys.stderr)
        return 2

    if os.environ.get("TEST_NOTIFICATION", "false").lower() == "true":
        publish_ntfy(topic, "USD/JPY 通知テスト", "通知設定は正常です。", "white_check_mark")
        print("テスト通知を送信しました。")
        return 0

    if os.environ.get("SIGNAL_ENABLED", "true").lower() != "true":
        print("重要指標用の停止スイッチがOFFのため、判定を停止しました。")
        return 0

    config = load_config()
    try:
        event = blocking_event(config)
    except Exception as exc:
        if config.get("calendar_fail_closed", True):
            reason = f"経済カレンダーを確認できないため、安全のため見送ります。\n詳細: {exc}"
            print(reason)
            notify_no_signal(config, topic, reason)
            return 0
        print(f"警告: 経済カレンダーを確認できませんでした: {exc}", file=sys.stderr)
        event = None

    if event is not None:
        event_jst = event["event_time_utc"].astimezone(timezone(timedelta(hours=9)))
        event_name = event.get("title", event.get("event", "重要指標"))
        reason = f"重要指標フィルターで見送ります。\n{event_jst:%Y-%m-%d %H:%M} JST {event_name}"
        print(reason)
        notify_no_signal(config, topic, reason)
        return 0

    candles = fetch_candles(config, api_key)
    required = max(int(config["sma_length"]), int(config["resistance_lookback"])) + int(config["max_pullback_bars"]) + 2
    if len(candles) < required:
        raise RuntimeError(f"ローソク足が不足しています: {len(candles)}本")

    signal, reason = evaluate_latest(candles, config)
    if signal is None:
        candle_jst = candles[-1]["time"].astimezone(timezone(timedelta(hours=9)))
        message = f"確定足: {candle_jst:%Y-%m-%d %H:%M} JST\n理由: {reason}"
        print(f"買い条件なし（見送り）: {reason}")
        notify_no_signal(config, topic, message)
        return 0

    jst = signal["time"].astimezone(timezone(timedelta(hours=9)))
    message = (
        f"確定足: {jst:%Y-%m-%d %H:%M} JST\n"
        f"エントリー候補: {signal['entry']:.3f}\n"
        f"損切り候補: {signal['stop']:.3f}（-{config['stop_pips']} pips）\n"
        f"利確候補: {signal['target']:.3f}（RR 1:{config['risk_reward']}）\n"
        f"上抜け水準: {signal['level']:.3f}\n"
        "根拠: 20SMA上・レジスタンス上抜け後の押し目反発\n"
        "注意: 売買推奨ではありません。指標・スプレッド・相場状況を確認してください。"
    )
    publish_ntfy(topic, "USD/JPY 買い候補", message)
    print("買い候補を通知しました。")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        raise SystemExit(1)
