import os
import json
import time
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from datetime import datetime, timezone

# ==============================================================================
# 1. KONFIGURASI GLOBAL & FILE PENYIMPANAN MEMORI (ANTI-RESET)
# ==============================================================================
INITIAL_BALANCE = 12.50
MAX_OPEN_POSITIONS = 3
DEX_FEE_SLIPPAGE = 0.004
LOG_FILE = "hasil_6varian_48jam.txt"
STATE_FILE = "state_6varian.json"   # Menyimpan saldo & posisi agar tahan restart!
MAX_RUNTIME_SECONDS = 19800         # Jalan 5.5 jam per sesi GitHub Actions, lalu simpan & estafet

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
# 2. DEFINISI PARAMETER 6 METODE ALGORITMA TERBAIK
# ==============================================================================
VARIANTS_CONFIG = {
    'M1_PULLBACK_V2': {
        'mode': 'pullback', 'base_alloc': 0.25, 'min_liq': 12000, 'min_bs': 1.60, 'min_buys': 40,
        'min_avg_tx': 18.0, 'max_fdv_liq': 12.0, 'min_m5': 3.0, 'max_m5': 42.0,
        'pb_min': 2.5, 'pb_max': 10.0, 'cont_bs': 1.80, 'hard_sl': -15.0, 'be_trig': 14.0, 'be_lock': 1.5,
        'tp1_pct': 28.0, 'tp1_sell': 0.40, 'tp2_pct': 65.0, 'tp2_sell': 0.58,
        'base_trail': 0.14, 'tight_trail': 0.09, 'stagnant_m': 10.0
    },
    'M2_VELOCITY_HFT': {
        'mode': 'velocity', 'base_alloc': 0.25, 'min_liq': 10000, 'min_bs': 1.85, 'min_buys': 55,
        'min_avg_tx': 15.0, 'max_fdv_liq': 14.0, 'min_m5': 4.0, 'max_m5': 38.0,
        'pb_min': 0.5, 'pb_max': 6.5, 'cont_bs': 2.00, 'hard_sl': -11.0, 'be_trig': 9.0, 'be_lock': 2.0,
        'tp1_pct': 18.0, 'tp1_sell': 0.60, 'tp2_pct': 42.0, 'tp2_sell': 0.50,
        'base_trail': 0.10, 'tight_trail': 0.06, 'stagnant_m': 6.0
    },
    'M3_WHALE_SHADOW': {
        'mode': 'whale', 'base_alloc': 0.25, 'min_liq': 22000, 'min_bs': 1.55, 'min_buys': 35,
        'min_avg_tx': 42.0, 'max_fdv_liq': 8.5, 'min_m5': 3.0, 'max_m5': 48.0,
        'pb_min': 3.0, 'pb_max': 11.5, 'cont_bs': 1.75, 'hard_sl': -14.0, 'be_trig': 15.0, 'be_lock': 2.0,
        'tp1_pct': 38.0, 'tp1_sell': 0.35, 'tp2_pct': 85.0, 'tp2_sell': 0.50,
        'base_trail': 0.16, 'tight_trail': 0.10, 'stagnant_m': 14.0
    },
    'M4_AMM_ELASTICITY': {
        'mode': 'amm_shock', 'base_alloc': 0.25, 'min_liq': 14000, 'min_bs': 1.65, 'min_buys': 45,
        'min_avg_tx': 20.0, 'max_fdv_liq': 9.5, 'min_m5': 2.5, 'max_m5': 24.0,
        'pb_min': 1.5, 'pb_max': 8.5, 'cont_bs': 1.75, 'hard_sl': -13.5, 'be_trig': 12.0, 'be_lock': 2.0,
        'tp1_pct': 32.0, 'tp1_sell': 0.45, 'tp2_pct': 70.0, 'tp2_sell': 0.55,
        'base_trail': 0.13, 'tight_trail': 0.08, 'stagnant_m': 9.5
    },
    'M5_RATCHET_SHIELD': {
        'mode': 'ratchet', 'base_alloc': 0.25, 'min_liq': 13000, 'min_bs': 1.65, 'min_buys': 42,
        'min_avg_tx': 20.0, 'max_fdv_liq': 11.0, 'min_m5': 3.5, 'max_m5': 40.0,
        'pb_min': 2.5, 'pb_max': 9.5, 'cont_bs': 1.80, 'hard_sl': -13.0, 'be_trig': 10.0, 'be_lock': 2.0,
        'tp1_pct': 25.0, 'tp1_sell': 0.40, 'tp2_pct': 58.0, 'tp2_sell': 0.50,
        'base_trail': 0.13, 'tight_trail': 0.07, 'stagnant_m': 9.0
    },
    'M6_KELLY_GODMODE': {
        'mode': 'kelly_godmode', 'base_alloc': 0.25, 'min_liq': 15000, 'min_bs': 1.65, 'min_buys': 45,
        'min_avg_tx': 22.0, 'max_fdv_liq': 10.0, 'min_m5': 3.0, 'max_m5': 36.0,
        'pb_min': 2.0, 'pb_max': 9.0, 'cont_bs': 1.85, 'hard_sl': -12.5, 'be_trig': 10.0, 'be_lock': 2.0,
        'tp1_pct': 26.0, 'tp1_sell': 0.45, 'tp2_pct': 62.0, 'tp2_sell': 0.55,
        'base_trail': 0.12, 'tight_trail': 0.06, 'stagnant_m': 8.5
    }
}

engines = {}
total_elapsed_sec = 0.0

def write_log(text):
    print(text, flush=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(text + "\n")

# ==============================================================================
# 3. SISTEM LOAD & SAVE MEMORI OTOMATIS (JSON CHECKPOINT)
# ==============================================================================
def init_or_load_state():
    global engines, global_blacklist, total_elapsed_sec
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
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
                    'max_dd_pct': s_eng.get('max_dd_pct', 0.0)
                }
            write_log(f"♻️ [CHECKPOINT LOADED] Melanjutkan sesi sebelumnya (Total Durasi: {total_elapsed_sec/3600:.2f} Jam)")
            return
        except Exception as e:
            write_log(f"[WARNING] Gagal memuat state lama, memulai baru: {e}")

    for v_name, cfg in VARIANTS_CONFIG.items():
        engines[v_name] = {
            'cfg': cfg, 'cash_usd': INITIAL_BALANCE, 'positions': {}, 'watchlist': {},
            'blacklist': set(), 'wins': 0, 'losses': 0, 'gross_profit': 0.0,
            'gross_loss': 0.0, 'peak_equity': INITIAL_BALANCE, 'max_dd_pct': 0.0
        }

def save_state(session_elapsed):
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
            'max_dd_pct': eng['max_dd_pct']
        }
    data = {
        'total_elapsed_sec': total_elapsed_sec + session_elapsed,
        'global_blacklist': list(global_blacklist),
        'engines': serializable_engines
    }
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f)

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
        if score > 600:
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
# 5. MESIN EVALUASI POSISI & EKSEKUSI PER VARIAN
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

    if net_profit >= 0:
        eng['wins'] += 1
        eng['gross_profit'] += net_profit
    else:
        eng['losses'] += 1
        eng['gross_loss'] += abs(net_profit)

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
        f"🏁 [{now_str}] [{v_name:<17}] {reason:<18} | {pos['symbol']:<10} | "
        f"PnL: {net_profit:+.2f} USD ({roi_pct:+.1f}%) | "
        f"Ekuitas: ${tot_eq:.2f} ({growth:+.1f}%) | WR: {eng['wins']}W/{eng['losses']}L ({wr:.0f}%)"
    )

def process_variant(v_name, best_pairs_by_token, open_pairs_data):
    eng = engines[v_name]
    cfg = eng['cfg']
    mode = cfg['mode']
    now_ts = datetime.now(timezone.utc).timestamp()
    now_ms = now_ts * 1000

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

        if max_pnl_pct >= cfg['be_trig'] and not pos['breakeven_locked']:
            pos['breakeven_locked'] = True
            pos['dynamic_sl_pct'] = max(pos['dynamic_sl_pct'], cfg['be_lock'])

        if mode in ['ratchet', 'kelly_godmode']:
            if max_pnl_pct >= 50.0: pos['dynamic_sl_pct'] = max(pos['dynamic_sl_pct'], 38.0)
            elif max_pnl_pct >= 32.0: pos['dynamic_sl_pct'] = max(pos['dynamic_sl_pct'], 22.0)
            elif max_pnl_pct >= 20.0: pos['dynamic_sl_pct'] = max(pos['dynamic_sl_pct'], 11.0)
            elif max_pnl_pct >= 10.0: pos['dynamic_sl_pct'] = max(pos['dynamic_sl_pct'], 2.0)

        if liq_drop >= 0.12 or (sells > buys * 2.2 and pnl_pct < -4.0):
            close_variant_position(v_name, p_addr, curr_price, "🚨 ANTI-RUG EXIT")
            continue

        if pnl_pct >= cfg['tp1_pct'] and not pos['tp1_hit']:
            sell_tokens = pos['tokens'] * cfg['tp1_sell']
            pos['tokens'] -= sell_tokens
            net_usd = (sell_tokens * curr_price) * (1 - DEX_FEE_SLIPPAGE)
            eng['cash_usd'] += net_usd
            pos['realized_usd'] += net_usd
            pos['tp1_hit'] = True
            write_log(f"💵 [{v_name:<17}] TP-1 (+{pnl_pct:.1f}%) pada {pos['symbol']} | Cair: +${net_usd:.2f}")

        if pnl_pct >= cfg['tp2_pct'] and not pos['tp2_hit']:
            sell_tokens = pos['tokens'] * cfg['tp2_sell']
            pos['tokens'] -= sell_tokens
            net_usd = (sell_tokens * curr_price) * (1 - DEX_FEE_SLIPPAGE)
            eng['cash_usd'] += net_usd
            pos['realized_usd'] += net_usd
            pos['tp2_hit'] = True
            write_log(f"🔥 [{v_name:<17}] TP-2 (+{pnl_pct:.1f}%) pada {pos['symbol']} | Cair: +${net_usd:.2f}")

        if hold_mins >= cfg['stagnant_m'] and pnl_pct < 5.0 and bs_ratio < 1.15:
            close_variant_position(v_name, p_addr, curr_price, "⏳ TIME-STOP")
            continue

        active_trail = cfg['tight_trail'] if pos['tp1_hit'] else cfg['base_trail']
        if mode in ['ratchet', 'kelly_godmode'] and max_pnl_pct >= 80.0:
            active_trail = 0.05

        if max_pnl_pct >= 11.0 and drop_from_peak >= active_trail:
            close_variant_position(v_name, p_addr, curr_price, "🎯 TRAILING PROFIT")
            continue

        if pnl_pct <= pos['dynamic_sl_pct']:
            reason = "🛡️ RATCHET/BE LOCK" if pos['breakeven_locked'] else "❌ HARD STOP LOSS"
            close_variant_position(v_name, p_addr, curr_price, reason)
            continue

    for t_addr in list(eng['watchlist'].keys()):
        if now_ts - eng['watchlist'][t_addr]['added_time'] > 900:
            del eng['watchlist'][t_addr]

    if len(eng['positions']) >= MAX_OPEN_POSITIONS or eng['cash_usd'] < 2.0:
        return

    for token_addr, pair in best_pairs_by_token.items():
        if len(eng['positions']) >= MAX_OPEN_POSITIONS:
            break
        pair_addr = pair.get('pairAddress')
        symbol = pair['baseToken']['symbol']
        if token_addr in eng['blacklist'] or pair_addr in eng['positions']:
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
        m5_chg = float(pair.get('priceChange', {}).get('m5', 0) or 0)
        h1_chg = float(pair.get('priceChange', {}).get('h1', 0) or 0)

        bs_ratio = buys_5m / sells_5m
        vol_to_liq = vol_5m / liq_usd
        avg_tx_usd = vol_5m / max(1, (buys_5m + sells_5m))
        fdv_to_liq = fdv_usd / liq_usd
        net_order_imbalance = (buys_5m - sells_5m) / max(1, (buys_5m + sells_5m))
        lai_score = vol_to_liq * net_order_imbalance

        if token_addr in eng['watchlist']:
            w = eng['watchlist'][token_addr]
            w['checks'] += 1
            if price_usd > w['peak_price']:
                w['peak_price'] = price_usd

            pullback_pct = ((w['peak_price'] - price_usd) / w['peak_price']) * 100
            liq_growth_pct = ((liq_usd - w['init_liq']) / w['init_liq']) * 100

            if bs_ratio < 1.25 or liq_usd < w['init_liq'] * 0.92:
                del eng['watchlist'][token_addr]
                continue

            trigger_buy = False
            trigger_desc = ""

            if mode == 'velocity':
                if (bs_ratio >= w['last_bs'] and buys_5m > w['last_buys'] and price_usd >= w['last_price'] * 1.005) or (1.0 <= pullback_pct <= 6.0 and bs_ratio >= 2.0):
                    trigger_buy = True
                    trigger_desc = f"Tick Accel (B/S {bs_ratio:.2f}x)"
            elif mode == 'amm_shock':
                if lai_score >= 0.08 and liq_growth_pct >= 0.0 and (1.5 <= pullback_pct <= 8.5 or price_usd >= w['init_price'] * 1.01):
                    trigger_buy = True
                    trigger_desc = f"AMM Shock (LAI {lai_score:.2f})"
            else:
                is_pb = (cfg['pb_min'] <= pullback_pct <= cfg['pb_max']) and (bs_ratio >= cfg['min_bs'])
                is_cont = (w['checks'] >= 2) and (price_usd >= w['init_price'] * 1.02) and (bs_ratio >= cfg['cont_bs']) and (pullback_pct < 3.0)
                if is_pb or is_cont:
                    trigger_buy = True
                    trigger_desc = f"Pullback -{pullback_pct:.1f}%" if is_pb else "Momentum Confirmed"

            w['last_bs'] = bs_ratio
            w['last_buys'] = buys_5m
            w['last_price'] = price_usd

            if trigger_buy:
                alloc_pct = cfg['base_alloc']
                if mode == 'kelly_godmode':
                    conviction = 0.0
                    if bs_ratio >= 2.1: conviction += 0.35
                    elif bs_ratio >= 1.75: conviction += 0.20
                    if fdv_to_liq <= 5.5: conviction += 0.30
                    if liq_growth_pct > 1.0: conviction += 0.20
                    if lai_score >= 0.08: conviction += 0.15
                    alloc_pct = round(0.16 + (0.19 * min(1.0, conviction)), 2)
                    trigger_desc += f" [Kelly {int(alloc_pct*100)}%]"

                invest_usd = eng['cash_usd'] * alloc_pct
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
                    write_log(f"🚀 [{now_str}] [{v_name:<17}] BELI {symbol:<10} (${price_usd:.6f}) | Alokasi: ${invest_usd:.2f} | {trigger_desc}")
                del eng['watchlist'][token_addr]
            continue

        if not (cfg['min_m5'] <= m5_chg <= cfg['max_m5'] and h1_chg >= 0):
            continue
        if mode == 'amm_shock' and lai_score < 0.07:
            continue

        if (bs_ratio >= cfg['min_bs'] and vol_to_liq >= 0.20 and avg_tx_usd >= cfg['min_avg_tx'] and fdv_to_liq <= cfg['max_fdv_liq'] and buys_5m >= cfg['min_buys']):
            is_safe, score, _ = verify_contract_safety(token_addr)
            if is_safe:
                eng['watchlist'][token_addr] = {
                    'symbol': symbol, 'init_price': price_usd, 'peak_price': price_usd,
                    'last_price': price_usd, 'init_liq': liq_usd, 'last_bs': bs_ratio,
                    'last_buys': buys_5m, 'added_time': now_ts, 'checks': 0, 'audit_score': score
                }
            else:
                eng['blacklist'].add(token_addr)

def print_leaderboard(session_elapsed):
    elapsed_h = (total_elapsed_sec + session_elapsed) / 3600.0
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    lines = [
        "\n==========================================================================================================",
        f"🏆 KLASEMEN TURNAMEN 6-METODE QUANT SOLANA | Total Durasi: {elapsed_h:.2f} Jam ({now_str})",
        "----------------------------------------------------------------------------------------------------------",
        f"{'METODE VARIAN':<18} | {'EKUITAS':<10} | {'ROI (%)':<9} | {'WIN/LOSS':<10} | {'WIN RATE':<9} | {'PROFIT FACTOR':<13} | {'MAX DD':<8} | {'AKTIF'}",
        "----------------------------------------------------------------------------------------------------------"
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
        lines.append(f"{v_name:<18} | ${eq:<9.2f} | {roi:+8.1f}% | {w:>3}W/{l:<3}L  | {wr:>7.1f}% | {pf_str:<13} | -{dd:>5.1f}% | {act} koin")
    lines.append("==========================================================================================================\n")
    write_log("\n".join(lines))

def main():
    init_or_load_state()
    session_start = datetime.now(timezone.utc).timestamp()
    write_log("🦅 BOT AKTIF DI CLOUD SERVER (Tahan Laptop Mati & Auto-Checkpoint Aktif)")

    cycle = 0
    while True:
        cycle += 1
        now_ts = datetime.now(timezone.utc).timestamp()
        session_elapsed = now_ts - session_start

        best_pairs, open_pairs = fetch_shared_market_data()
        for v_name in engines.keys():
            process_variant(v_name, best_pairs, open_pairs)

        if cycle % 10 == 0:
            save_state(session_elapsed)

        if cycle % 30 == 0:
            print_leaderboard(session_elapsed)

        # Jika sudah berjalan 5.5 jam di GitHub Actions, simpan state dan keluar dengan rapi agar disambung sesi berikutnya
        if session_elapsed >= MAX_RUNTIME_SECONDS:
            save_state(session_elapsed)
            print_leaderboard(session_elapsed)
            write_log("🔄 [ESTAFET SESI CLOUD] Menyimpan state & menyerahkan ke sesi jadwal berikutnya...")
            break

        time.sleep(6)

if __name__ == '__main__':
    main()
