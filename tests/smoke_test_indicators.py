"""Throwaway script: confirm ta and stockstats work against pandas 3.0.5 + yfinance output."""
import sys
import yfinance as yf

def main():
    df = yf.download("AAPL", period="60d", interval="1d", progress=False, auto_adjust=True)
    if df.empty:
        print("FAIL: yfinance returned no data")
        sys.exit(1)
    # yfinance 0.2.x returns MultiIndex columns for single-ticker download in some versions
    if isinstance(df.columns, __import__("pandas").MultiIndex):
        df.columns = df.columns.get_level_values(0)

    ta_ok = True
    try:
        import ta
        rsi = ta.momentum.RSIIndicator(close=df["Close"], window=14).rsi()
        macd = ta.trend.MACD(close=df["Close"]).macd()
        print("ta OK — last RSI:", rsi.iloc[-1], "last MACD:", macd.iloc[-1])
    except Exception as e:
        ta_ok = False
        print("ta FAILED:", repr(e))

    stockstats_ok = True
    try:
        from stockstats import StockDataFrame
        sdf = StockDataFrame.retype(df.copy())
        rsi_ss = sdf["rsi_14"]
        macd_ss = sdf["macd"]
        print("stockstats OK — last RSI:", rsi_ss.iloc[-1], "last MACD:", macd_ss.iloc[-1])
    except Exception as e:
        stockstats_ok = False
        print("stockstats FAILED:", repr(e))

    print(f"\nSUMMARY: ta={'OK' if ta_ok else 'FAIL'} stockstats={'OK' if stockstats_ok else 'FAIL'}")
    if not (ta_ok and stockstats_ok):
        sys.exit(1)

if __name__ == "__main__":
    main()
