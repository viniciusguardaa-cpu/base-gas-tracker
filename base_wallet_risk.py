#!/usr/bin/env python3
"""Base Wallet Risk - x402-gated Base wallet portfolio and token-risk summary."""
import json, os, re, time, urllib.request, urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler

PORT = int(os.environ.get("PORT", "8850"))
PAY_TO = "0x65aa59025C4AcD8CD7ba2051ecb9152abaE4fcC6"
PRICE_USDC = 0.005
PRICE_UNITS = int(PRICE_USDC * 1e6)
CHAIN_ID = 8453
ASSET = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
USED_TXS_FILE = os.path.join(os.path.dirname(__file__), "used_txs.json")
BLOCKSCOUT = "https://base.blockscout.com/api/v2"
DEXSCREENER = "https://api.dexscreener.com/latest/dex/tokens/"
WETH = "0x4200000000000000000000000000000000000006"
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"


def load_used_txs():
    try:
        with open(USED_TXS_FILE) as f:
            return set(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def save_used_txs(s):
    with open(USED_TXS_FILE, "w") as f:
        json.dump(sorted(s), f)


def fetch_json(url, timeout=12):
    req = urllib.request.Request(url, headers={"User-Agent": "base-wallet-risk/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def safe_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def eth_price_usd():
    data = fetch_json(DEXSCREENER + WETH)
    pairs = data.get("pairs", [])
    if not pairs:
        return None
    best = max(pairs, key=lambda p: safe_float(p.get("liquidity", {}).get("usd", 0)))
    return safe_float(best.get("priceUsd"))


def token_market_data(address):
    data = fetch_json(DEXSCREENER + address)
    pairs = [p for p in data.get("pairs", []) if p.get("chainId") == "base"]
    if not pairs:
        return {}
    best = max(pairs, key=lambda p: safe_float(p.get("liquidity", {}).get("usd", 0)))
    return {
        "price_usd": best.get("priceUsd"),
        "liquidity_usd": best.get("liquidity", {}).get("usd"),
        "volume_24h_usd": best.get("volume", {}).get("h24"),
        "price_change_24h": best.get("priceChange", {}).get("h24"),
        "dex": best.get("dexId"),
    }


def wallet_profile(address):
    info = fetch_json(f"{BLOCKSCOUT}/addresses/{address}")
    raw_balance = info.get("coin_balance", "0")
    native_wei = int(raw_balance, 16) if str(raw_balance).startswith("0x") else int(raw_balance or 0)
    native_eth = native_wei / 1e18

    token_resp = fetch_json(f"{BLOCKSCOUT}/addresses/{address}/tokens")
    items = token_resp.get("items", [])
    parsed = []
    for item in items:
        token = item.get("token", {}) or {}
        decimals = int(token.get("decimals") or 0)
        raw_value = item.get("value", "0")
        amount = int(raw_value) / (10 ** decimals) if decimals >= 0 else float(raw_value)
        parsed.append({
            "address": token.get("address"),
            "name": token.get("name"),
            "symbol": token.get("symbol"),
            "decimals": decimals,
            "amount": amount,
            "fiat_value": safe_float(item.get("fiat_value")),
        })

    parsed.sort(key=lambda x: x["fiat_value"], reverse=True)
    top = parsed[:10]
    for holding in top:
        if holding["address"]:
            holding["market"] = token_market_data(holding["address"])
        else:
            holding["market"] = {}

    eth_px = eth_price_usd()
    native_usd = native_eth * eth_px if eth_px else 0.0
    token_usd = sum(h["fiat_value"] for h in parsed)
    total_usd = native_usd + token_usd

    risks = []
    if total_usd > 0 and native_usd / total_usd > 0.85:
        risks.append("high_native_asset_concentration")
    if top and total_usd > 0 and top[0]["fiat_value"] / total_usd > 0.6:
        risks.append("single_token_concentration")
    low_liquidity = [h["symbol"] for h in top if safe_float((h.get("market") or {}).get("liquidity_usd")) < 10000 and h.get("fiat_value", 0) > 0]
    if low_liquidity:
        risks.append("low_liquidity_holdings:" + ",".join(low_liquidity[:5]))
    if len(parsed) > 25:
        risks.append("many_small_token_positions")

    return {
        "address": address,
        "native_eth": round(native_eth, 6),
        "native_usd": round(native_usd, 2),
        "eth_price_usd": eth_px,
        "token_count": len(parsed),
        "token_value_usd": round(token_usd, 2),
        "total_value_usd": round(total_usd, 2),
        "top_holdings": [
            {
                "address": h["address"],
                "symbol": h["symbol"],
                "name": h["name"],
                "amount": round(h["amount"], 6),
                "value_usd": round(h["fiat_value"], 2),
                "market": h.get("market", {}),
            }
            for h in top
        ],
        "risk_flags": risks,
        "updated_at": int(time.time()),
    }


def verify_tx(tx_hash):
    payload = json.dumps({"jsonrpc": "2.0", "method": "eth_getTransactionReceipt", "params": [tx_hash], "id": 1}).encode()
    req = urllib.request.Request("https://mainnet.base.org", data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.loads(r.read().decode())
    receipt = data.get("result")
    if not receipt or receipt.get("status") != "0x1":
        return False, "tx_not_found_or_failed"
    for log in receipt.get("logs", []):
        if log.get("address", "").lower() == ASSET.lower():
            topics = log.get("topics", [])
            if len(topics) >= 3 and topics[0].lower() == TRANSFER_TOPIC:
                to_addr = "0x" + topics[2][-40:]
                if to_addr.lower() == PAY_TO.lower():
                    amount = int(log.get("data", "0x0"), 16)
                    if amount >= PRICE_UNITS:
                        return True, "verified"
    return False, "no_matching_transfer"


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        params = urllib.parse.parse_qs(parsed.query)

        if path == "/health":
            self.send_json(200, {"status": "ok", "service": "base-wallet-risk", "price_usdc": PRICE_USDC, "chain": "base"})
            return

        address = params.get("address", [""])[0].strip()
        if path != "/analyze":
            self.send_json(404, {"error": "not_found", "endpoints": ["/health", "/analyze?address=0x..."]})
            return
        if not re.fullmatch(r"0x[a-fA-F0-9]{40}", address):
            self.send_json(400, {"error": "invalid_address", "usage": "/analyze?address=0x..."})
            return

        tx_hash = self.headers.get("X-Payment-Tx", "").strip()
        if not tx_hash:
            self.send_json(402, {
                "error": "payment_required",
                "message": f"Pay {PRICE_USDC} USDC on Base to {PAY_TO}",
                "pay_to": PAY_TO, "asset": ASSET, "chain": "base", "chain_id": CHAIN_ID,
                "amount_usdc": PRICE_USDC, "amount_units": PRICE_UNITS,
                "instructions": "Send USDC on Base to pay_to, then retry with X-Payment-Tx: <tx_hash>",
            })
            return

        used = load_used_txs()
        if tx_hash in used:
            self.send_json(402, {"error": "tx_already_used"})
            return
        ok, reason = verify_tx(tx_hash)
        if not ok:
            self.send_json(402, {"error": "payment_not_verified", "reason": reason})
            return
        used.add(tx_hash)
        save_used_txs(used)

        try:
            self.send_json(200, wallet_profile(address))
        except Exception as e:
            self.send_json(500, {"error": "internal_error", "message": str(e)})

    def send_json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    print(f"Base Wallet Risk starting on port {PORT}")
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
