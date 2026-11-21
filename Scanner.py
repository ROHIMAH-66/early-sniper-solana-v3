
import os
import requests
from datetime import datetime, timezone

BOT = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT = os.getenv("TELEGRAM_CHAT_ID")
EVENT = os.getenv("GITHUB_EVENT_NAME", "")

MIN_LIQ = 5000
MIN_VOL = 1000
MIN_BUY = 10
MIN_RATIO = 1.3
MAX_AGE = 30

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "EarlySniperSolanaV3/1.0"})


def send(msg):
    if not BOT or not CHAT:
        raise RuntimeError("Telegram secrets belum diatur!")

    response = SESSION.post(
        f"https://api.telegram.org/bot{BOT}/sendMessage",
        json={"chat_id": CHAT, "text": msg},
        timeout=20
    )
    response.raise_for_status()

    result = response.json()
    if not result.get("ok"):
        raise RuntimeError("Telegram menolak pesan: " + str(result))


def get_age_minutes(created):
    if not created:
        return None

    try:
        dt = datetime.fromisoformat(
            created.replace("Z", "+00:00")
        )

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        return (
            datetime.now(timezone.utc) - dt
        ).total_seconds() / 60

    except (ValueError, TypeError):
        return None


def scan():
    stats = {
        "pools": 0,
        "pairs": 0,
        "passed": 0,
        "old": 0,
        "low_liq": 0,
        "low_vol": 0,
        "low_buy": 0,
        "low_ratio": 0,
        "bad_price": 0,
        "errors": 0
    }

    # Tes Telegram hanya saat workflow dijalankan manual
    if EVENT == "workflow_dispatch":
        send(
            "TEST TELEGRAM BERHASIL!\n\n"
            "Early Sniper Solana V3 aktif.\n"
            "Koneksi bot Telegram berhasil."
        )

    url = (
        "https://api.geckoterminal.com/api/v2/"
        "networks/solana/new_pools"
    )

    response = SESSION.get(url, timeout=25)
    response.raise_for_status()

    pools = response.json().get("data", [])
    stats["pools"] = len(pools)

    for pool in pools:
        a = pool.get("attributes", {})
        address = a.get("address")

        if not address:
            continue

        age = get_age_minutes(a.get("pool_created_at"))

        if age is not None and age > MAX_AGE:
            stats["old"] += 1
            continue

        try:
            detail = SESSION.get(
                "https://api.dexscreener.com/latest/dex/pairs/"
                f"solana/{address}",
                timeout=20
            )
            detail.raise_for_status()

            pairs = detail.json().get("pairs") or []

            if not pairs:
                continue

            p = max(
                pairs,
                key=lambda x: float(
                    (x.get("liquidity") or {}).get("usd") or 0
                )
            )

            stats["pairs"] += 1

            liq = float(
                (p.get("liquidity") or {}).get("usd") or 0
            )
            vol = float(
                (p.get("volume") or {}).get("m5") or 0
            )

            tx = (p.get("txns") or {}).get("m5") or {}
            buy = int(tx.get("buys") or 0)
            sell = int(tx.get("sells") or 0)
            ratio = buy / max(sell, 1)

            if liq < MIN_LIQ:
                stats["low_liq"] += 1

            if vol < MIN_VOL:
                stats["low_vol"] += 1

            if buy < MIN_BUY:
                stats["low_buy"] += 1

            if ratio < MIN_RATIO:
                stats["low_ratio"] += 1

            if not (
                liq >= MIN_LIQ
                and vol >= MIN_VOL
                and buy >= MIN_BUY
                and ratio >= MIN_RATIO
            ):
                continue

            price = float(p.get("priceUsd") or 0)

            if price <= 0:
                stats["bad_price"] += 1
                continue

            stats["passed"] += 1

            base = p.get("baseToken") or {}

            sl = price * 0.95
            risk = price - sl
            tp1 = price + risk * 1.5
            tp2 = price + risk * 2

            msg = f"""EARLY SNIPER SOLANA V3

TOKEN: {base.get('name', 'Unknown')}
SYMBOL: {base.get('symbol', 'Unknown')}

Liquidity: ${liq:,.2f}
Volume 5m: ${vol:,.2f}
BUY: {buy}
SELL: {sell}
BUY/SELL: {ratio:.2f}

ENTRY SETUP BERSYARAT
Harga referensi: ${price}
SL model: ${sl}
TP1: ${tp1}
TP2: ${tp2}

Usia pool: {age:.1f} menit
Link: {p.get('url', '')}

PERHATIAN:
Bukan jaminan profit.
Periksa keamanan token dan chart
sebelum mengambil keputusan.
"""
            send(msg)

        except Exception as e:
            stats["errors"] += 1
            print("Pool error:", str(e))

    print("HASIL SCAN:", stats)

    # Laporan lengkap hanya saat dijalankan manual
    if EVENT == "workflow_dispatch":
        report = f"""LAPORAN EARLY SNIPER V3

Pool ditemukan: {stats['pools']}
Pair cocok: {stats['pairs']}
LOLOS FILTER: {stats['passed']}

Pool terlalu tua: {stats['old']}
Likuiditas kurang: {stats['low_liq']}
Volume kurang: {stats['low_vol']}
Buy kurang: {stats['low_buy']}
Rasio kurang: {stats['low_ratio']}
Harga tidak valid: {stats['bad_price']}
Error pool: {stats['errors']}

Filter:
Liquidity >= $5,000
Volume 5m >= $1,000
Buy >= 10
Buy/Sell >= 1.3
Usia pool maksimal 30 menit

Scanner selesai.
"""
        send(report)


if __name__ == "__main__":
    scan()
