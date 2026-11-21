
import os
import json
import requests
from datetime import datetime, timezone

BOT = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT = os.getenv("TELEGRAM_CHAT_ID")

MIN_LIQ = 5000
MIN_VOL = 1000
MIN_BUY = 10
MIN_RATIO = 1.3
MAX_AGE = 30

def send(msg):
    if not BOT or not CHAT:
        print("Telegram secrets belum diatur")
        return
    requests.post(
        f"https://api.telegram.org/bot{BOT}/sendMessage",
        json={"chat_id": CHAT, "text": msg},
        timeout=20
    ).raise_for_status()

def scan():
    url = "https://api.geckoterminal.com/api/v2/networks/solana/new_pools"
    data = requests.get(url, timeout=20).json()

    for pool in data.get("data", []):
        a = pool.get("attributes", {})
        address = a.get("address")
        if not address:
            continue

        try:
            detail = requests.get(
                f"https://api.dexscreener.com/latest/dex/pairs/solana/{address}",
                timeout=15
            ).json()
            pairs = detail.get("pairs") or []
            if not pairs:
                continue

            p = max(
                pairs,
                key=lambda x: float((x.get("liquidity") or {}).get("usd") or 0)
            )

            liq = float((p.get("liquidity") or {}).get("usd") or 0)
            vol = float((p.get("volume") or {}).get("m5") or 0)
            tx = (p.get("txns") or {}).get("m5") or {}
            buy = int(tx.get("buys") or 0)
            sell = int(tx.get("sells") or 0)
            ratio = buy / max(sell, 1)

            if not (
                liq >= MIN_LIQ
                and vol >= MIN_VOL
                and buy >= MIN_BUY
                and ratio >= MIN_RATIO
            ):
                continue

            base = p.get("baseToken") or {}
            price = float(p.get("priceUsd") or 0)
            if price <= 0:
                continue

            sl = price * 0.95
            risk = price - sl
            tp1 = price + risk * 1.5
            tp2 = price + risk * 2

            msg = f"""🚀 EARLY SNIPER SOLANA V3

🪙 {base.get('name')} ({base.get('symbol')})

💧 Liquidity: ${liq:,.2f}
📊 Volume 5m: ${vol:,.2f}
🟢 BUY: {buy}
🔴 SELL: {sell}
⚖️ BUY/SELL: {ratio:.2f}

📍 ENTRY SETUP BERSYARAT
Harga referensi: ${price}
SL model: ${sl}
TP1: ${tp1}
TP2: ${tp2}

⚠️ Belum termasuk konfirmasi chart dan pemeriksaan keamanan token.
🔗 {p.get('url')}
"""
            send(msg)

        except Exception as e:
            print("Pool error:", e)

if __name__ == "__main__":
    scan()
