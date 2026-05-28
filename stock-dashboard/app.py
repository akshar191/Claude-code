import json
import os
from flask import Flask, jsonify, render_template, request
from flask_cors import CORS
import yfinance as yf
from datetime import datetime, timedelta

app = Flask(__name__)
CORS(app)

WATCHLIST_FILE = os.path.join(os.path.dirname(__file__), "watchlist.json")

DEFAULT_TICKERS = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "TSLA",
    "NVDA", "META", "NFLX", "AMD", "JPM",
    "DIS", "BABA", "UBER", "SNAP", "COIN"
]

DEFAULT_WATCHLIST = ["AAPL", "MSFT", "TSLA", "NVDA", "GOOGL"]


def load_watchlist():
    if os.path.exists(WATCHLIST_FILE):
        with open(WATCHLIST_FILE, "r") as f:
            data = json.load(f)
            return data.get("tickers", DEFAULT_WATCHLIST)
    return DEFAULT_WATCHLIST[:]


def save_watchlist(tickers):
    with open(WATCHLIST_FILE, "w") as f:
        json.dump({"tickers": tickers}, f)


def format_market_cap(mc):
    if mc is None:
        return "N/A"
    if mc >= 1e12:
        return f"${mc/1e12:.2f}T"
    if mc >= 1e9:
        return f"${mc/1e9:.2f}B"
    if mc >= 1e6:
        return f"${mc/1e6:.2f}M"
    return f"${mc:,.0f}"


def get_quote(ticker_symbol):
    try:
        t = yf.Ticker(ticker_symbol)
        info = t.fast_info
        # fast_info attributes
        price = getattr(info, "last_price", None)
        prev_close = getattr(info, "previous_close", None)
        market_cap = getattr(info, "market_cap", None)

        if price is None or prev_close is None:
            # fallback to history
            hist = t.history(period="2d")
            if hist.empty:
                return None
            price = float(hist["Close"].iloc[-1])
            prev_close = float(hist["Close"].iloc[-2]) if len(hist) > 1 else price

        price = float(price)
        prev_close = float(prev_close)
        change = price - prev_close
        change_pct = (change / prev_close * 100) if prev_close else 0

        return {
            "ticker": ticker_symbol.upper(),
            "price": round(price, 2),
            "change": round(change, 2),
            "change_pct": round(change_pct, 2),
            "market_cap": format_market_cap(market_cap),
        }
    except Exception as e:
        print(f"Error fetching {ticker_symbol}: {e}")
        return None


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/watchlist", methods=["GET"])
def get_watchlist():
    return jsonify({"tickers": load_watchlist()})


@app.route("/api/watchlist/add", methods=["POST"])
def add_to_watchlist():
    data = request.get_json()
    ticker = data.get("ticker", "").upper().strip()
    if not ticker:
        return jsonify({"error": "No ticker provided"}), 400

    # validate ticker exists
    try:
        t = yf.Ticker(ticker)
        hist = t.history(period="1d")
        if hist.empty:
            return jsonify({"error": f"Ticker '{ticker}' not found"}), 404
    except Exception:
        return jsonify({"error": "Failed to validate ticker"}), 500

    tickers = load_watchlist()
    if ticker not in tickers:
        tickers.append(ticker)
        save_watchlist(tickers)
    return jsonify({"tickers": tickers})


@app.route("/api/watchlist/remove", methods=["POST"])
def remove_from_watchlist():
    data = request.get_json()
    ticker = data.get("ticker", "").upper().strip()
    tickers = load_watchlist()
    if ticker in tickers:
        tickers.remove(ticker)
        save_watchlist(tickers)
    return jsonify({"tickers": tickers})


@app.route("/api/quotes", methods=["GET"])
def get_quotes():
    tickers_param = request.args.get("tickers", "")
    if tickers_param:
        tickers = [t.strip().upper() for t in tickers_param.split(",") if t.strip()]
    else:
        tickers = load_watchlist()

    quotes = []
    for t in tickers:
        q = get_quote(t)
        if q:
            quotes.append(q)

    return jsonify({"quotes": quotes})


@app.route("/api/quote/<ticker>", methods=["GET"])
def single_quote(ticker):
    q = get_quote(ticker.upper())
    if q is None:
        return jsonify({"error": "Ticker not found"}), 404
    return jsonify(q)


@app.route("/api/chart/<ticker>", methods=["GET"])
def get_chart(ticker):
    period = request.args.get("period", "1mo")
    try:
        t = yf.Ticker(ticker.upper())
        hist = t.history(period=period, interval="1d")
        if hist.empty:
            return jsonify({"error": "No data"}), 404

        labels = [str(d.date()) for d in hist.index]
        opens = [round(float(v), 2) for v in hist["Open"]]
        highs = [round(float(v), 2) for v in hist["High"]]
        lows = [round(float(v), 2) for v in hist["Low"]]
        closes = [round(float(v), 2) for v in hist["Close"]]
        volumes = [int(v) for v in hist["Volume"]]

        # get company name
        try:
            name = t.info.get("shortName") or t.info.get("longName") or ticker.upper()
        except Exception:
            name = ticker.upper()

        return jsonify({
            "ticker": ticker.upper(),
            "name": name,
            "labels": labels,
            "opens": opens,
            "highs": highs,
            "lows": lows,
            "closes": closes,
            "volumes": volumes,
        })
    except Exception as e:
        print(f"Chart error {ticker}: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/movers", methods=["GET"])
def get_movers():
    quotes = []
    for ticker in DEFAULT_TICKERS:
        q = get_quote(ticker)
        if q:
            quotes.append(q)

    gainers = sorted(quotes, key=lambda x: x["change_pct"], reverse=True)[:5]
    losers = sorted(quotes, key=lambda x: x["change_pct"])[:5]

    return jsonify({"gainers": gainers, "losers": losers})


@app.route("/api/search/<ticker>", methods=["GET"])
def search_ticker(ticker):
    q = get_quote(ticker.upper())
    if q is None:
        return jsonify({"error": "Ticker not found"}), 404
    return jsonify(q)


if __name__ == "__main__":
    app.run(debug=True, port=5000)
