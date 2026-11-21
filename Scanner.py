
import os
import json
import requests
from pathlib import Path
from datetime import datetime, timezone

BOT = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT = os.getenv("TELEGRAM_CHAT_ID")
EVENT = os.getenv("GITHUB_EVENT_NAME", "")

MIN_LIQ = 5000
MIN_VOL = 1000
MIN_BUY = 10
MIN_RATIO = 1.3
MAX_AGE = 30

MAX_TOP1 = 20
MAX_TOP10 = 50
MIN_LP_LOCK = 80

STATE = Path(".cache/seen_tokens.json")
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "EarlySniperSolanaV4/1.0"})


def send(message):
    if not BOT or not CHAT:
        raise RuntimeError("Telegram secrets belum diatur")

    r = SESSION.post(
        f"https://api.telegram.org/bot{BOT}/sendMessage",
        json={"chat_id": CHAT, "text": message},
        timeout=20
    )
    r.raise_for_status()

    result = r.json()
    if not result.get("ok"):
        raise RuntimeError("Telegram gagal mengirim pesan")


def load_seen():
    STATE.parent.mkdir(parents=True, exist_ok=True)

    try:
        data = json.loads(STATE.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_seen(seen):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(seen, indent=2))


def age_minutes(created):
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


def authority_state(data, key, token):
    if key in data:
        value = data[key]
    elif key in token:
        value = token[key]
    else:
        return "UNKNOWN"

    return "ACTIVE" if value else "REVOKED"


def rugcheck(mint):
    base = f"https://api.rugcheck.xyz/v1/tokens/{mint}"

    full_response = SESSION.get(
        base + "/report", timeout=25
    )
    full_response.raise_for_status()
    full = full_response.json()

    summary_response = SESSION.get(
        base + "/report/summary", timeout=25
    )
    summary_response.raise_for_status()
    summary = summary_response.json()

    if not isinstance(full, dict) or not isinstance(summary, dict):
        raise ValueError("Format laporan RugCheck tidak valid")

    token = full.get("token") or {}

    mint_auth = authority_state(
        full, "mintAuthority", token
    )
    freeze_auth = authority_state(
        full, "freezeAuthority", token
    )

    holders = full.get("topHolders") or []
    holder_known = bool(holders)

    percentages = []
    for holder in holders:
        try:
            percentages.append(float(holder["pct"]))
        except (KeyError, TypeError, ValueError):
            continue

    holder_known = bool(percentages)

    top1 = max(percentages) if percentages else None
    top10 = sum(sorted(percentages, reverse=True)[:10]) if percentages else None

    lp = summary.get("lpLockedPct")
    try:
        lp = float(lp) if lp is not None else None
    except (TypeError, ValueError):
        lp = None

    risks = full.get("risks") or summary.get("risks") or []

    danger = any(
        str(r.get("level", "")).lower()
        in ("danger", "critical")
        for r in risks
        if isinstance(r, dict)
    )

    warnings = []

    if mint_auth == "ACTIVE":
        warnings.append("Mint authority masih aktif")
    elif mint_auth == "UNKNOWN":
        warnings.append("Mint authority tidak diketahui")

    if freeze_auth == "ACTIVE":
        warnings.append("Freeze authority masih aktif")
    elif freeze_auth == "UNKNOWN":
        warnings.append("Freeze authority tidak diketahui")

    if not holder_known:
        warnings.append("Data holder tidak tersedia")
    else:
        if top1 > MAX_TOP1:
            warnings.append("Top holder terlalu dominan")
        if top10 > MAX_TOP10:
            warnings.append("Konsentrasi top 10 tinggi")

    if lp is None:
        warnings.append("Data LP lock tidak tersedia")
    elif lp < MIN_LP_LOCK:
        warnings.append("LP lock di bawah batas")

    for risk in risks:
        if not isinstance(risk, dict):
            continue
        if str(risk.get("level", "")).lower() in ("warn", "warning"):
            warnings.append(risk.get("name", "Peringatan RugCheck"))

    return {
        "danger": danger,
        "warnings": list(dict.fromkeys(warnings)),
        "mint": mint_auth,
        "freeze": freeze_auth,
        "top1": top1,
        "top10": top10,
        "lp": lp,
        "score": summary.get("score_normalised"),
        "risks": risks
    }


def scan():
    stats = {
        "pools": 0,
        "pairs": 0,
        "basic_pass": 0,
        "security_pass": 0,
        "watchlist": 0,
        "blocked": 0,
        "duplicates": 0,
        "security_errors": 0,
        "errors": 0,
        "alerts": 0
    }

    seen = load_seen()

    if EVENT == "workflow_dispatch":
        send(
            "EARLY SNIPER SOLANA V4\n\n"
            "Tes Telegram berhasil.\n"
            "Scanner dan pemeriksaan keamanan aktif."
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
        pair_address = a.get("address")

        if not pair_address:
            continue

        age = age_minutes(a.get("pool_created_at"))

        if age is None or age < 0 or age > MAX_AGE:
            continue

        try:
            detail = SESSION.get(
                "https://api.dexscreener.com/latest/dex/"
                f"pairs/solana/{pair_address}",
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

            if not (
                liq >= MIN_LIQ
                and vol >= MIN_VOL
                and buy >= MIN_BUY
                and ratio >= MIN_RATIO
            ):
                continue

            stats["basic_pass"] += 1

            base = p.get("baseToken") or {}
            mint = base.get("address")

            if not mint:
                continue

            if mint in seen:
                stats["duplicates"] += 1
                continue

            price = float(p.get("priceUsd") or 0)
            if price <= 0:
                continue

            try:
                security = rugcheck(mint)
            except Exception as e:
                stats["security_errors"] += 1
                print("RugCheck error:", str(e))
                continue

                     if security["danger"]:
            stats["blocked"] += 1

            print("\nTOKEN DIBLOKIR:")
            print("Mint:", mint)
            print("Risiko RugCheck:")

            for risk in security["risks"]:
                if isinstance(risk, dict):
                    print(
                        "Level:", risk.get("level"),
                        "| Nama:", risk.get("name"),
                        "| Deskripsi:", risk.get("description")
                    )

            continue

        warnings = security["warnings"]

            warnings = security["warnings"]

            warnings = security["warnings"]

            if warnings:
                status = "WATCHLIST"
                stats["watchlist"] += 1
            else:
                status = "ENTRY SETUP"
                stats["security_pass"] += 1

            sl = price * 0.95
            tp1 = price * 1.075
            tp2 = price * 1.10

            top1 = security["top1"]
            top10 = security["top10"]
            lp = security["lp"]

            msg = f"""EARLY SNIPER SOLANA V4

STATUS: {status}

TOKEN: {base.get('name', 'Unknown')}
SYMBOL: {base.get('symbol', 'Unknown')}

Mint:
{mint}

Liquidity: ${liq:,.2f}
Volume 5m: ${vol:,.2f}
BUY: {buy}
SELL: {sell}
BUY/SELL: {ratio:.2f}
Pool age: {age:.1f} menit

RUGCHECK
Score normalized: {security['score']}
Mint authority: {security['mint']}
Freeze authority: {security['freeze']}
Top holder: {f'{top1:.2f}%' if top1 is not None else 'N/A'}
Top 10 holders: {f'{top10:.2f}%' if top10 is not None else 'N/A'}
LP locked: {f'{lp:.2f}%' if lp is not None else 'N/A'}

Peringatan:
{chr(10).join(warnings) if warnings else 'Tidak ada peringatan dari pemeriksaan ini'}

LEVEL MODEL (bukan sinyal otomatis)
Harga referensi: ${price}
SL model: ${sl}
TP1 model: ${tp1}
TP2 model: ${tp2}

Chart:
{p.get('url', '')}

Risiko tetap ada. Periksa chart dan token
secara manual sebelum mengambil keputusan.
"""

            send(msg)

            seen[mint] = datetime.now(timezone.utc).isoformat()
            stats["alerts"] += 1

        except Exception as e:
            stats["errors"] += 1
            print("Pool error:", str(e))

    save_seen(seen)

    print("HASIL SCAN V4:", stats)

    if EVENT == "workflow_dispatch":
        report = f"""LAPORAN EARLY SNIPER V4

Pool ditemukan: {stats['pools']}
Pair cocok: {stats['pairs']}
Lolos filter dasar: {stats['basic_pass']}

ENTRY SETUP: {stats['security_pass']}
WATCHLIST: {stats['watchlist']}
DIBLOKIR RUGCHECK: {stats['blocked']}

Duplikat: {stats['duplicates']}
Security error: {stats['security_errors']}
Error pool: {stats['errors']}
Alert terkirim: {stats['alerts']}

Scanner selesai.
"""
        send(report)


if __name__ == "__main__":
    scan()
        
