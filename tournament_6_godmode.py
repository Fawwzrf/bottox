import os
import json
import time
import subprocess
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from datetime import datetime, timezone

# ==============================================================================
# 1. KONFIGURASI GLOBAL & PENYIMPANAN REAL-TIME ANTI-LOST CHECKPOINT
# ==============================================================================
INITIAL_BALANCE = 12.50
MAX_OPEN_POSITIONS = 3
DEX_FEE_SLIPPAGE = 0.004
LOG_FILE = "hasil_6varian_48jam.txt"
STATE_FILE = "state_6varian.json"
STATE_VERSION = "V3_5ELITE_METHODS"     # Reset bersih jika file lama masih versi V2 (6 metode lama)
MAX_RUNTIME_SECONDS = 18000             # 5 Jam per sesi, lalu estafet ke antrean berikutnya

_retry = Retry(total=2, backoff_factor=0.6, status_forcelist=[429, 500, 502, 503, 504])
_adapter = HTTPAdapter(max_retries=_retry)
SESSION = requests.Session()
SESSION.mount('https://', _adapter)
SESSION.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36',
    'Accept': 'application/json'
})
REQUEST_TIMEOUT = 10

audit_cache = {}
global_blacklist = set()

# ==============================================================================
# 2. DEFINISI 5 METODE ELIT (M1, M2, HYBRID M1+M2, & 2 METODE BARU)
# ==============================================================================
VARIANTS_CONFIG = {
    'M1_PULLBACK_PRO': {
        'desc': 'Refined Pullback V2 (Diskon 3-9.5% + Zero-Negative Trail)',
        'mode': 'm1_pullback',
        'base_alloc': 0.25, 'min_liq': 15000, 'min_bs': 1.65, 'min_buys': 45,
        'min_avg_tx': 20.0, 'max_fdv_liq': 10.5, 'min_m5': 4.0, 'max_m5': 38.0,
        'pb_min': 3.0, 'pb_max': 9.5, 'cont_bs': 1.95,
        'hard_sl': -13.5, 'be_trig': 10.5, 'be_lock': 1.8,
        'tp1_pct': 26.0, 'tp1_sell': 0.45, 'tp2_pct': 60.0, 'tp2_sell': 0.55,
        'base_trail': 0.12, 'tight_trail': 0.08, 'stagnant_m': 9.0
    },
    'M2_VELOCITY_PRO': {
        'desc': 'Refined Velocity HFT (Tick Accel + Fast 60% TP-1)',
        'mode': 'm2_velocity',
        'base_alloc': 0.25, 'min_liq': 14000, 'min_bs': 1.85, 'min_buys': 55,
        'min_avg_tx': 18.0, 'max_fdv_liq': 12.0, 'min_m5': 4.5, 'max_m5': 35.0,
        'pb_min': 1.0, 'pb_max': 6.5, 'cont_bs': 2.10,
        'hard_sl': -11.5, 'be_trig': 9.0, 'be_lock': 2.0,
        'tp1_pct': 18.5, 'tp1_sell': 0.60, 'tp2_pct': 45.0, 'tp2_sell': 0.50,
        'base_trail': 0.09, 'tight_trail': 0.06, 'stagnant_m': 6.5
    },
    'M3_HYBRID_APEX': {
        'desc': 'Kombinasi M1+M2 (Wajib Pullback Diskon + Tick Acceleration)',
        'mode': 'm3_hybrid',
        'base_alloc': 0.25, 'min_liq': 15000, 'min_bs': 1.75, 'min_buys': 50,
        'min_avg_tx': 20.0, 'max_fdv_liq': 10.0, 'min_m5': 4.0, 'max_m5': 36.0,
        'pb_min': 2.0, 'pb_max': 8.5, 'cont_bs': 2.05,
        'hard_sl': -12.0, 'be_trig': 9.5, 'be_lock': 2.0,
        'tp1_pct': 21.0, 'tp1_sell': 0.50, 'tp2_pct': 52.0, 'tp2_sell': 0.55,
        'base_trail': 0.10, 'tight_trail': 0.06, 'stagnant_m': 7.5
    },
    'M4_TEMPORAL_FRACTAL': {
        'desc': 'Multi-Timeframe Surge (Vol m5 >= 2.2x Vol h1 + Buyer Shift)',
        'mode': 'm4_fractal',
        'base_alloc': 0.25, 'min_liq': 16000, 'min_bs': 1.70, 'min_buys': 48,
        'min_avg_tx': 22.0, 'max_fdv_liq': 9.5, 'min_m5': 4.0, 'max_m5': 34.0,
        'pb_min': 1.5, 'pb_max': 8.0, 'cont_bs': 1.90,
        'hard_sl': -12.5, 'be_trig': 10.0, 'be_lock': 2.0,
        'tp1_pct': 24.0, 'tp1_sell': 0.50, 'tp2_pct': 58.0, 'tp2_sell': 0.50,
        'base_trail': 0.11, 'tight_trail': 0.07, 'stagnant_m': 8.0
    },
    'M5_VORTEX_ANTIFRAGILE': {
        'desc': 'Whale Absorption Wall + Instant 12% Free-Ride (High WinRate)',
        'mode': 'm5_vortex',
        'base_alloc': 0.25, 'min_liq': 16000, 'min_bs': 1.75, 'min_buys': 50,
        'min_avg_tx': 24.0, 'max_fdv_liq': 9.0, 'min_m5': 3.5, 'max_m5': 28.0,
        'pb_min': 1.5, 'pb_max': 7.5, 'cont_bs': 1.95,
        'hard_sl': -11.0, 'be_trig': 8.0, 'be_lock': 2.5,
        'tp1_pct': 12.5, 'tp1_sell': 0.55, 'tp2_pct': 40.0, 'tp2_sell': 0.50,
        'base_trail': 0.09, 'tight_trail': 0.05, 'stagnant_m': 6.5
    }
}

engines = {}
total_elapsed_sec = 0.0

def write_log(text):
    print(text, flush=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(text + "\n")

# ==============================================================================
# 3. SISTEM CHECKPOINT & LIVE GITHUB PUSH (ANTI-HILANG DATA ANTAR LOOP)
# ==============================================================================
def init_or_load_state():
    global engines, global_blacklist, total_elapsed_sec
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
            if saved.get('version') == STATE_VERSION:
                total_elapsed_sec = saved.get('total_elapsed_sec', 0.0)
                global_blacklist = set(saved.get('global_blacklist', []))
                for v_name, cfg in VARIANTS_CONFIG.items():
                    s_eng = saved.get('engines', {}).get(v_name, {})
                    engines[v_name] = {
                        'cfg': cfg,
                        'cash_usd': s_eng.get('cash_usd', INITIAL_BALANCE),
                        'positions': s_eng.get('positions', {}),
                        'watchlist': s_eng.get('watchlist', {}),
                        'blacklist': set(s_eng.get('blacklist', [])),
                        'wins': s_eng.get('wins', 0),
                        'losses': s_eng.get('losses', 0),
                        'gross_profit': s_eng.get('gross_profit', 0.0),
                        'gross_loss': s_eng.get('gross_loss', 0.0),
                        'peak_equity': s_eng.get('peak_equity', INITIAL_BALANCE),
                        'max_dd_pct': s_eng.get('max_dd_pct', 0.0),
                        'consec_losses': s_eng.get('consec_losses', 0),
                        'cooldown_until': s_eng.get('cooldown_until', 0.0)
                    }
                write_log(f"♻️ [CHECKPOINT V3 LOADED] Melanjutkan sesi secara akurat (Durasi Akumulasi: {total_elapsed_sec/3600:.2f} Jam)")
                return
            else:
                write_log("🔄 [UPGRADE DETECTED] Memulai turnamen bersih untuk 5 Metode Elit V3.0...")
        except Exception as e:
            write_log(f"[WARNING] Gagal memuat state lama, memulai baru: {e}")

    for v_name, cfg in VARIANTS_CONFIG.items():
        engines[v_name] = {
            'cfg': cfg, 'cash_usd': INITIAL_BALANCE, 'positions': {}, 'watchlist': {},
            'blacklist': set(), 'wins': 0, 'losses': 0, 'gross_profit': 0.0,
            'gross_loss': 0.0, 'peak_equity': INITIAL_BALANCE, 'max_dd_pct': 0.0,
            'consec_losses': 0, 'cooldown_until': 0.0
        }

def save_state(session_elapsed, push_to_git=False):
    serializable_engines = {}
    for v_name, eng in engines.items():
        serializable_engines[v_name] = {
            'cash_usd': eng['cash_usd'],
            'positions': eng['positions'],
            'watchlist': eng['watchlist'],
            'blacklist': list(eng['blacklist']),
            'wins': eng['wins'],
            'losses': eng['losses'],
            'gross_profit': eng['gross_profit'],
            'gross_loss': eng['gross_loss'],
            'peak_equity': eng['peak_equity'],
            'max_dd_pct': eng['max_dd_pct'],
            'consec_losses': eng['consec_losses'],
            'cooldown_until': eng['cooldown_until']
        }
    data = {
        'version': STATE_VERSION,
        'total_elapsed_sec': total_elapsed_sec + session_elapsed,
        'global_blacklist': list(global_blacklist),
        'engines': serializable_engines
    }
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f)

    if push_to_git:
        try:
            subprocess.run(["git", "add", LOG_FILE, STATE_FILE], check=False)
            subprocess.run(["git", "commit", "-m", f"Live sync {((total_elapsed_sec + session_elapsed)/3600):.2f}h"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run(["git", "pull", "--rebase", "-X", "theirs", "origin", "main"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run(["git", "push", "origin", "main"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass

# ==============================================================================
# 4. AUDIT KONTRAK & SINGLE DATA INGESTION
# ==============================================================================
def verify_contract_safety(token_address):
    if token_address in audit_cache:
        return audit_cache[token_address]
    if token_address in global_blacklist:
        return False, 9999, "Blacklisted"
    try:
        url = f"https://api.rugcheck.xyz/v1/tokens/{token_address}/report/summary"
        resp = SESSION.get(url, timeout=REQUEST_TIMEOUT)
        if resp.status_code != 200:
            return False, 9999, "Audit tidak tersedia"
        data = resp.json()
        score = data.get('score', 9999)
        for r in data.get('risks', []):
            level = r.get('level', '')
            name = r.get('name', '').lower()
            if level == 'danger' or 'mint' in name or 'freeze' in name or 'honeypot' in name:
                res = (False, score, f"Bahaya: {r.get('name')}")
                audit_cache[token_address] = res
                global_blacklist.add(token_address)
                return res
        if score > 500:
            res = (False, score, f"Skor Tinggi ({score})")
            audit_cache[token_address] = res
            global_blacklist.add(token_address)
            return res
        res = (True, score, f"Aman (Skor: {score})")
        audit_cache[token_address] = res
        return res
    except Exception as e:
        return False, 9999, f"Error Audit: {e}"

def fetch_shared_market_data():
    token_addrs = []
    for url in ["https://api.dexscreener.com/token-profiles/latest/v1", "https://api.dexscreener.com/token-boosts/latest/v1"]:
        try:
            resp = SESSION.get(url, timeout=REQUEST_TIMEOUT)
            if resp.status_code == 200:
                for item in resp.json():
                    if item.get('chainId') == 'solana':
                        addr = item.get('tokenAddress')
                        if addr and addr not in token_addrs and addr not in global_blacklist:
                            token_addrs.append(addr)
        except Exception:
            pass

    watchlist_addrs = set()
    for eng in engines.values():
        watchlist_addrs.update(eng['watchlist'].keys())

    all_tokens = list(watchlist_addrs.union(set(token_addrs[:25])))
    best_pairs_by_token = {}
    if all_tokens:
        try:
            tokens_csv = ",".join(all_tokens[:30])
            resp = SESSION.get(f"https://api.dexscreener.com/latest/dex/tokens/{tokens_csv}", timeout=REQUEST_TIMEOUT)
            if resp.status_code == 200:
                for p in resp.json().get('pairs', []):
                    if p.get('chainId') != 'solana':
                        continue
                    t_addr = p['baseToken']['address']
                    liq = p.get('liquidity', {}).get('usd', 0)
                    if t_addr not in best_pairs_by_token or liq > best_pairs_by_token[t_addr].get('liquidity', {}).get('usd', 0):
                        best_pairs_by_token[t_addr] = p
        except Exception:
            pass

    open_pair_addrs = set()
    for eng in engines.values():
        open_pair_addrs.update(eng['positions'].keys())

    open_pairs_data = {}
    if open_pair_addrs:
        try:
            pairs_csv = ",".join(list(open_pair_addrs)[:30])
            resp = SESSION.get(f"https://api.dexscreener.com/latest/dex/pairs/solana/{pairs_csv}", timeout=REQUEST_TIMEOUT)
            if resp.status_code == 200:
                for p in resp.json().get('pairs', []):
                    if 'pairAddress' in p:
                        open_pairs_data[p['pairAddress']] = p
        except Exception:
            pass

    return best_pairs_by_token, open_pairs_data

# ==============================================================================
# 5. MESIN EVALUASI POSISI & EKSEKUSI 5 METODE ELIT
# ==============================================================================
def get_total_equity(v_name):
    eng = engines[v_name]
    locked_usd = sum(p['invested_usd'] * (p['tokens'] / p['init_tokens']) for p in eng['positions'].values())
    return eng['cash_usd'] + locked_usd

def close_variant_position(v_name, pair_addr, curr_price, reason):
    eng = engines[v_name]
    pos = eng['positions'][pair_addr]
    remaining_usd = (pos['tokens'] * curr_price) * (1 - DEX_FEE_SLIPPAGE)
    eng['cash_usd'] += remaining_usd

    total_returned = pos['realized_usd'] + remaining_usd
    net_profit = total_returned - pos['invested_usd']
    roi_pct = (net_profit / pos['invested_usd']) * 100
    now_ts = datetime.now(timezone.utc).timestamp()

    if net_profit >= 0:
        eng['wins'] += 1
        eng['gross_profit'] += net_profit
        eng['consec_losses'] = 0
    else:
        eng['losses'] += 1
        eng['gross_loss'] += abs(net_profit)
        eng['consec_losses'] += 1
        # GLOBAL SCAM IMMUNITY: Jika rugpull/stop loss, blokir koin ini untuk semua metode!
        global_blacklist.add(pos['token_address'])
        # CIRCUIT BREAKER: Jika 2x kalah beruntun, istirahat 25 menit dari pasar buruk
        if eng['consec_losses'] >= 2:
            eng['cooldown_until'] = now_ts + 1500
            write_log(f"🛡️ [{v_name}] Mengaktifkan Cooldown 25 Menit (Proteksi Pasar Buruk)")

    eng['blacklist'].add(pos['token_address'])
    del eng['positions'][pair_addr]

    tot_eq = get_total_equity(v_name)
    if tot_eq > eng['peak_equity']:
        eng['peak_equity'] = tot_eq
    dd = ((eng['peak_equity'] - tot_eq) / eng['peak_equity']) * 100
    if dd > eng['max_dd_pct']:
        eng['max_dd_pct'] = dd

    tot_t = eng['wins'] + eng['losses']
    wr = (eng['wins'] / tot_t) * 100 if tot_t > 0 else 0.0
    growth = ((tot_eq - INITIAL_BALANCE) / INITIAL_BALANCE) * 100

    now_str = datetime.now().strftime('%H:%M:%S')
    write_log(
        f"🏁 [{now_str}] [{v_name:<21}] {reason:<18} | {pos['symbol']:<10} | "
        f"PnL: {net_profit:+.2f} USD ({roi_pct:+.1f}%) | "
        f"Ekuitas: ${tot_eq:.2f} ({growth:+.1f}%) | WR: {eng['wins']}W/{eng['losses']}L ({wr:.0f}%)"
    )

def process_variant(v_name, best_pairs_by_token, open_pairs_data):
    eng = engines[v_name]
    cfg = eng['cfg']
    mode = cfg['mode']
    now_ts = datetime.now(timezone.utc).timestamp()
    now_ms = now_ts * 1000

    # A. KAWAL POSISI AKTIF
    for p_addr, pos in list(eng['positions'].items()):
        p_data = open_pairs_data.get(p_addr) or best_pairs_by_token.get(pos['token_address'])
        if not p_data:
            continue

        curr_price = float(p_data.get('priceUsd', pos['entry_price']))
        curr_liq = p_data.get('liquidity', {}).get('usd', pos['entry_liq'])
        txns_5m = p_data.get('txns', {}).get('m5', {})
        buys = txns_5m.get('buys', 1)
        sells = max(1, txns_5m.get('sells', 1))
        bs_ratio = buys / sells

        if curr_price > pos['highest_price']:
            pos['highest_price'] = curr_price

        pnl_pct = ((curr_price - pos['entry_price']) / pos['entry_price']) * 100
        max_pnl_pct = ((pos['highest_price'] - pos['entry_price']) / pos['entry_price']) * 100
        drop_from_peak = (pos['highest_price'] - curr_price) / pos['highest_price']
        liq_drop = (pos['entry_liq'] - curr_liq) / pos['entry_liq']
        hold_mins = (now_ts - pos['entry_time']) / 60.0

        # ZERO-NEGATIVE TRAILING & BREAKEVEN LOCK
        if max_pnl_pct >= cfg['be_trig'] and not pos['breakeven_locked']:
            pos['breakeven_locked'] = True
            pos['dynamic_sl_pct'] = max(pos['dynamic_sl_pct'], cfg['be_lock'])

        # 1. Deteksi Darurat Anti-Rug
        if liq_drop >= 0.12 or (sells > buys * 2.1 and pnl_pct < -3.5):
            close_variant_position(v_name, p_addr, curr_price, "🚨 ANTI-RUG EXIT")
            continue

        # 2. Take Profit Tier 1
        if pnl_pct >= cfg['tp1_pct'] and not pos['tp1_hit']:
            sell_tokens = pos['tokens'] * cfg['tp1_sell']
            pos['tokens'] -= sell_tokens
            net_usd = (sell_tokens * curr_price) * (1 - DEX_FEE_SLIPPAGE)
            eng['cash_usd'] += net_usd
            pos['realized_usd'] += net_usd
            pos['tp1_hit'] = True
            pos['breakeven_locked'] = True
            pos['dynamic_sl_pct'] = max(pos['dynamic_sl_pct'], 2.5)
            write_log(f"💵 [{v_name:<21}] TP-1 (+{pnl_pct:.1f}%) pada {pos['symbol']} | Cair: +${net_usd:.2f}")

        # 3. Take Profit Tier 2
        if pnl_pct >= cfg['tp2_pct'] and not pos['tp2_hit']:
            sell_tokens = pos['tokens'] * cfg['tp2_sell']
            pos['tokens'] -= sell_tokens
            net_usd = (sell_tokens * curr_price) * (1 - DEX_FEE_SLIPPAGE)
            eng['cash_usd'] += net_usd
            pos['realized_usd'] += net_usd
            pos['tp2_hit'] = True
            write_log(f"🔥 [{v_name:<21}] TP-2 (+{pnl_pct:.1f}%) pada {pos['symbol']} | Cair: +${net_usd:.2f}")

        # 4. Time-Stop
        if hold_mins >= cfg['stagnant_m'] and pnl_pct < 4.5 and bs_ratio < 1.20:
            close_variant_position(v_name, p_addr, curr_price, "⏳ TIME-STOP")
            continue

        # 5. Trailing Stop (Wajib hanya mengeksekusi jika posisi masih di zona untung / pnl_pct >= 1.0%)
        active_trail = cfg['tight_trail'] if pos['tp1_hit'] else cfg['base_trail']
        if max_pnl_pct >= 12.0 and drop_from_peak >= active_trail and pnl_pct >= 1.0:
            close_variant_position(v_name, p_addr, curr_price, "🎯 TRAILING PROFIT")
            continue

        # 6. Breakeven Lock / Hard Stop Loss
        if pnl_pct <= pos['dynamic_sl_pct']:
            reason = "🛡️ BREAKEVEN PROFIT" if pos['breakeven_locked'] else "❌ HARD STOP LOSS"
            close_variant_position(v_name, p_addr, curr_price, reason)
            continue

    # B. CEK COOLDOWN & SELEKSI WATCHLIST
    if now_ts < eng['cooldown_until']:
        return

    for t_addr in list(eng['watchlist'].keys()):
        if now_ts - eng['watchlist'][t_addr]['added_time'] > 720:
            del eng['watchlist'][t_addr]

    if len(eng['positions']) >= MAX_OPEN_POSITIONS or eng['cash_usd'] < 2.0:
        return

    for token_addr, pair in best_pairs_by_token.items():
        if len(eng['positions']) >= MAX_OPEN_POSITIONS:
            break
        pair_addr = pair.get('pairAddress')
        symbol = pair['baseToken']['symbol']
        if token_addr in eng['blacklist'] or token_addr in global_blacklist or pair_addr in eng['positions']:
            continue

        created_at = pair.get('pairCreatedAt', 0)
        age_minutes = (now_ms - created_at) / 60000.0 if created_at else 9999
        liq_usd = pair.get('liquidity', {}).get('usd', 0)
        fdv_usd = pair.get('fdv', 0) or liq_usd * 2
        price_usd = float(pair.get('priceUsd', 0) or 0)

        if liq_usd < cfg['min_liq'] or age_minutes > 120 or price_usd <= 0:
            continue

        txns_5m = pair.get('txns', {}).get('m5', {})
        buys_5m = txns_5m.get('buys', 0)
        sells_5m = max(1, txns_5m.get('sells', 1))
        vol_5m = pair.get('volume', {}).get('m5', 0)

        txns_h1 = pair.get('txns', {}).get('h1', {})
        buys_h1 = max(1, txns_h1.get('buys', 1))
        sells_h1 = max(1, txns_h1.get('sells', 1))
        vol_h1 = max(1.0, pair.get('volume', {}).get('h1', 1.0))

        m5_chg = float(pair.get('priceChange', {}).get('m5', 0) or 0)
        h1_chg = float(pair.get('priceChange', {}).get('h1', 0) or 0)

        bs_ratio = buys_5m / sells_5m
        vol_to_liq = vol_5m / liq_usd
        avg_tx_usd = vol_5m / max(1, (buys_5m + sells_5m))
        fdv_to_liq = fdv_usd / liq_usd

        # --- METRIK KHUSUS METODE BARU M4 (TEMPORAL FRACTAL) ---
        avg_5m_vol_in_h1 = vol_h1 / 12.0
        temporal_vol_surge = vol_5m / max(1.0, avg_5m_vol_in_h1)
        buy_share_m5 = buys_5m / max(1, (buys_5m + sells_5m))
        buy_share_h1 = buys_h1 / max(1, (buys_h1 + sells_h1))
        buyer_dominance_shift = buy_share_m5 - buy_share_h1

        # --- METRIK KHUSUS METODE BARU M5 (VORTEX ABSORPTION) ---
        # Dollar-Per-Percent (DPP): seberapa besar uang masuk per 1% perubahan harga
        dpp_absorption = (vol_5m / max(1.0, abs(m5_chg))) / liq_usd

        # EVALUASI KOIN DI WATCHLIST (TAHAP 2)
        if token_addr in eng['watchlist']:
            w = eng['watchlist'][token_addr]
            w['checks'] += 1
            if price_usd > w['peak_price']:
                w['peak_price'] = price_usd

            pullback_pct = ((w['peak_price'] - price_usd) / w['peak_price']) * 100
            liq_growth_pct = ((liq_usd - w['init_liq']) / w['init_liq']) * 100

            # Wajib tren 5m masih positif (>= +3%) dan likuiditas tidak menyusut!
            if bs_ratio < 1.35 or m5_chg < 3.0 or liq_usd < w['init_liq'] * 0.96:
                del eng['watchlist'][token_addr]
                continue

            trigger_buy = False
            trigger_desc = ""

            if mode == 'm1_pullback':
                # M1: Fokus murni pada Pullback Diskon Sehat dengan likuiditas stabil/naik
                if (cfg['pb_min'] <= pullback_pct <= cfg['pb_max']) and bs_ratio >= cfg['min_bs'] and liq_growth_pct >= -1.0:
                    trigger_buy = True
                    trigger_desc = f"Pullback -{pullback_pct:.1f}% (B/S {bs_ratio:.2f}x)"

            elif mode == 'm2_velocity':
                # M2: Fokus pada Tick Acceleration beruntun
                if (bs_ratio >= w['last_bs'] and buys_5m > w['last_buys'] and price_usd >= w['last_price'] * 1.004 and bs_ratio >= 1.90) or (1.5 <= pullback_pct <= 6.0 and bs_ratio >= 2.15):
                    trigger_buy = True
                    trigger_desc = f"Tick Accel (B/S {bs_ratio:.2f}x)"

            elif mode == 'm3_hybrid':
                # M3 HYBRID (M1 + M2): WAJIB sedang diskon Pullback DAN mengalami Tick Acceleration!
                is_discounted = (cfg['pb_min'] <= pullback_pct <= cfg['pb_max'])
                is_accelerating = (price_usd > w['last_price'] and (bs_ratio >= w['last_bs'] or bs_ratio >= 2.0))
                if is_discounted and is_accelerating and liq_growth_pct >= 0.0:
                    trigger_buy = True
                    trigger_desc = f"Hybrid Pullback -{pullback_pct:.1f}% + Rebound"

            elif mode == 'm4_fractal':
                # M4 TEMPORAL FRACTAL: Ledakan volume 5m >= 2.2x rata-rata 1 jam + Pergeseran Pembeli >= +6%
                if temporal_vol_surge >= 2.2 and buyer_dominance_shift >= 0.06 and (1.5 <= pullback_pct <= 8.0 or price_usd > w['last_price']):
                    trigger_buy = True
                    trigger_desc = f"Fractal Surge {temporal_vol_surge:.1f}x (Shift +{buyer_dominance_shift*100:.0f}%)"

            elif mode == 'm5_vortex':
                # M5 VORTEX ANTIFRAGILE: Penyerapan likuiditas tinggi (DPP >= 0.025) + Likuiditas kolam bertambah
                if dpp_absorption >= 0.025 and liq_growth_pct >= 0.5 and bs_ratio >= 1.80 and pullback_pct <= 7.5:
                    trigger_buy = True
                    trigger_desc = f"Vortex Absorb (DPP {dpp_absorption:.3f} | Liq +{liq_growth_pct:.1f}%)"

            w['last_bs'] = bs_ratio
            w['last_buys'] = buys_5m
            w['last_price'] = price_usd

            if trigger_buy:
                invest_usd = eng['cash_usd'] * cfg['base_alloc']
                if invest_usd >= 1.4:
                    eng['cash_usd'] -= invest_usd
                    eff_usd = invest_usd * (1 - DEX_FEE_SLIPPAGE)
                    tokens_bought = eff_usd / price_usd
                    eng['positions'][pair_addr] = {
                        'token_address': token_addr, 'symbol': symbol,
                        'entry_price': price_usd, 'highest_price': price_usd,
                        'entry_liq': liq_usd, 'entry_time': now_ts,
                        'invested_usd': invest_usd, 'realized_usd': 0.0,
                        'tokens': tokens_bought, 'init_tokens': tokens_bought,
                        'dynamic_sl_pct': cfg['hard_sl'], 'breakeven_locked': False,
                        'tp1_hit': False, 'tp2_hit': False
                    }
                    now_str = datetime.now().strftime('%H:%M:%S')
                    write_log(f"🚀 [{now_str}] [{v_name:<21}] BELI {symbol:<10} (${price_usd:.6f}) | Alokasi: ${invest_usd:.2f} | {trigger_desc}")
                del eng['watchlist'][token_addr]
            continue

        # SELEKSI MASUK KE WATCHLIST (TAHAP 1)
        if not (cfg['min_m5'] <= m5_chg <= cfg['max_m5'] and h1_chg > 0):
            continue

        if (bs_ratio >= cfg['min_bs'] and vol_to_liq >= 0.22 and avg_tx_usd >= cfg['min_avg_tx'] and fdv_to_liq <= cfg['max_fdv_liq'] and buys_5m >= cfg['min_buys']):
            is_safe, score, _ = verify_contract_safety(token_addr)
            if is_safe:
                eng['watchlist'][token_addr] = {
                    'symbol': symbol, 'init_price': price_usd, 'peak_price': price_usd,
                    'last_price': price_usd, 'init_liq': liq_usd, 'last_bs': bs_ratio,
                    'last_buys': buys_5m, 'added_time': now_ts, 'checks': 0, 'audit_score': score
                }
            else:
                eng['blacklist'].add(token_addr)

# ==============================================================================
# 6. CETAK KLASEMEN & SINKRONISASI BERKALA
# ==============================================================================
def print_leaderboard(session_elapsed):
    elapsed_h = (total_elapsed_sec + session_elapsed) / 3600.0
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    lines = [
        "\n==============================================================================================================",
        f"🏆 KLASEMEN TURNAMEN 5-METODE ELIT V3.0 | Total Durasi Akumulasi: {elapsed_h:.2f} Jam ({now_str})",
        "--------------------------------------------------------------------------------------------------------------",
        f"{'METODE VARIAN':<22} | {'EKUITAS':<10} | {'ROI (%)':<9} | {'WIN/LOSS':<10} | {'WIN RATE':<9} | {'PROFIT FACTOR':<13} | {'MAX DD':<8} | {'AKTIF'}",
        "--------------------------------------------------------------------------------------------------------------"
    ]
    ranking = []
    for v_name, eng in engines.items():
        tot_eq = get_total_equity(v_name)
        roi = ((tot_eq - INITIAL_BALANCE) / INITIAL_BALANCE) * 100
        tot_t = eng['wins'] + eng['losses']
        wr = (eng['wins'] / tot_t) * 100 if tot_t > 0 else 0.0
        pf = (eng['gross_profit'] / eng['gross_loss']) if eng['gross_loss'] > 0 else (99.9 if eng['gross_profit'] > 0 else 0.0)
        ranking.append((tot_eq, v_name, roi, eng['wins'], eng['losses'], wr, pf, eng['max_dd_pct'], len(eng['positions'])))

    ranking.sort(reverse=True, key=lambda x: x[0])
    for eq, v_name, roi, w, l, wr, pf, dd, act in ranking:
        pf_str = "INF (0 Loss)" if (l == 0 and w > 0) else f"{pf:.2f}x"
        lines.append(f"{v_name:<22} | ${eq:<9.2f} | {roi:+8.1f}% | {w:>3}W/{l:<3}L  | {wr:>7.1f}% | {pf_str:<13} | -{dd:>5.1f}% | {act} koin")
    lines.append("==============================================================================================================\n")
    write_log("\n".join(lines))

def main():
    init_or_load_state()
    session_start = datetime.now(timezone.utc).timestamp()
    write_log("🦅 BOT QUANT V3.0 AKTIF (5 Metode Elit + Live Git Sync 15m + Anti-Overlap Queue)")

    cycle = 0
    while True:
        cycle += 1
        now_ts = datetime.now(timezone.utc).timestamp()
        session_elapsed = now_ts - session_start

        best_pairs, open_pairs = fetch_shared_market_data()
        for v_name in engines.keys():
            process_variant(v_name, best_pairs, open_pairs)

        # Simpan ke file lokal setiap 1 menit (10 siklus)
        if cycle % 10 == 0:
            save_state(session_elapsed, push_to_git=False)

        # Cetak klasemen setiap 3 menit (30 siklus)
        if cycle % 30 == 0:
            print_leaderboard(session_elapsed)

        # LIVE GIT PUSH setiap 15 menit (150 siklus) agar checkpoint di GitHub selalu real-time!
        if cycle % 150 == 0:
            save_state(session_elapsed, push_to_git=True)

        if session_elapsed >= MAX_RUNTIME_SECONDS:
            print_leaderboard(session_elapsed)
            save_state(session_elapsed, push_to_git=True)
            write_log("🔄 [ESTAFET SESI SEMPURNA] State berhasil di-push ke GitHub, siap dilanjutkan sesi berikutnya!")
            break

        time.sleep(6)

if __name__ == '__main__':
    main()
