# utils/arbitrage.py

from web3 import Web3
from utils.simulate import simulate_tx_call
from utils.tokens import get_token_symbol_async, get_token_decimals_cached, get_token_usd_price
from utils.dex import (
    get_expected_out, get_expected_out_univ3, get_expected_out_1inch,
    get_expected_out_kyber, get_expected_out_paraswap, safe_call,
    normalize_to_18_decimals, ROUTER_ABI
    )
from config import ARBITRAGE_ADDRESS, ROUTERS, get_rotating_http_provider, get_eth_usd_price
from decimal import Decimal
import time

_SEEN_DEX_QUOTES = set()

def is_profitable_arbitrage(amount_in, amount_out_a, amount_out_b, eth_usd_price, slippage=0.005, gas_price_gwei=30, gas_limit=200_000, min_profit_usd=10):
    amount_in = float(amount_in)
    amount_out_a = float(amount_out_a)
    amount_out_b = float(amount_out_b)
    eth_usd_price = float(eth_usd_price)

    better_out = max(amount_out_a, amount_out_b)
    adjusted_out = better_out * (1 - slippage)
    profit_tokens = adjusted_out - amount_in
    if profit_tokens <= 0:
        return False, 0

    gas_cost_eth = float(Web3.from_wei(gas_price_gwei * gas_limit * 10**9, 'ether'))
    gas_cost_usd = gas_cost_eth * eth_usd_price
    profit_eth = float(Web3.from_wei(profit_tokens, 'ether'))
    profit_usd = profit_eth * eth_usd_price

    net_profit = profit_usd - gas_cost_usd
    return net_profit > min_profit_usd, net_profit

seen_pairs = set()

async def check_arbitrage_opportunity(web3, amount_in, path, eth_usd_price, direction="forward"):
    path = [Web3.to_checksum_address(p) for p in path]
    token_in, token_out = path[0], path[-1]
    pair = (token_in, token_out) if direction == "forward" else (token_out, token_in)

    if pair in seen_pairs:
        return
    seen_pairs.add(pair)

    label_key = f"{direction}|{token_in}->{token_out}"
    if label_key in _SEEN_DEX_QUOTES:
        return
    _SEEN_DEX_QUOTES.add(label_key)

    token_out_decimals = await get_token_decimals_cached(web3, token_out)
    token_in_symbol = await get_token_symbol_async(web3, token_in)
    token_out_symbol = await get_token_symbol_async(web3, token_out)
    label_display = f"{token_in_symbol}-{token_out_symbol}-{'Copy' if direction == 'forward' else 'Invert'}"

    if getattr(check_arbitrage_opportunity, "_last", None) == label_display:
        return
    check_arbitrage_opportunity._last = label_display

    print(f"\n[DEX QUOTES] {'Copy' if direction == 'forward' else 'Invert'} | {token_in_symbol} → {token_out_symbol}")

    dex_quote_funcs = {
        "UniswapV2": lambda: get_expected_out("UniswapV2", amount_in, path),
        "Sushiswap": lambda: get_expected_out("Sushiswap", amount_in, path),
        "UniswapV3": lambda: get_expected_out_univ3(token_in, token_out, amount_in),
        "1inch":     lambda: get_expected_out_1inch(token_in, token_out, amount_in),
        "Kyber":     lambda: get_expected_out_kyber(token_in, token_out, amount_in),
        "Paraswap":  lambda: get_expected_out_paraswap(token_in, token_out, amount_in),
    }

    quotes = {}
    for dex, func in dex_quote_funcs.items():
        try:
            out = safe_call(func, dex)
            if out and int(out) > 0:
                normalized = normalize_to_18_decimals(out, token_out_decimals)
                quotes[dex] = normalized
                out_readable = Web3.from_wei(normalized, 'ether')
                print(f"→ {dex:10}: {out_readable:.6f} {token_out_symbol}")
            else:
                print(f"→ {dex:10}: N/A (empty pool or unknown token)")
        except Exception as e:
            print(f"→ {dex:10}: N/A")
            print(f"[WARN] {dex} error: {e}")

    if len(quotes) < 2:
        return

    best_dex = max(quotes, key=quotes.get)
    worst_dex = min(quotes, key=quotes.get)
    out_best = quotes[best_dex]
    out_worst = quotes[worst_dex]

    is_prof, net_profit = is_profitable_arbitrage(amount_in, out_best, out_worst, eth_usd_price)

    if is_prof:
        output_token = Web3.from_wei(out_best, 'ether')

        try:
            token_usd_price = await get_token_usd_price(web3, token_out, token_out_decimals)
            usd_val = output_token * token_usd_price if token_usd_price else None
            usd_str = f"${usd_val:.2f}" if usd_val else "≈ N/A"
        except Exception as e:
            print(f"[WARN] USD price lookup failed: {e}")
            usd_str = "≈ N/A"

        print(f"[💸 ARBITRAGGIO] {'Copy' if direction == 'forward' else 'Invert'} | Buy on {worst_dex}, Sell on {best_dex} | Net profit: ${net_profit:.2f}")
        print(f"→ Output: {output_token:.6f} {token_out_symbol} ≈ {usd_str}")

        router = ROUTERS.get(best_dex)
        if router:
            adjusted_out = int(out_best * (1 - 0.005))
            res = simulate_tx_call(router, amount_in, adjusted_out, path, ARBITRAGE_ADDRESS)

            if res["success"]:
                print(f"[✅ SIMULATION OK] → Output: {res['output_token_amount']}")
            else:
                print(f"[❌ SIMULATION FAIL] {res['error_type']} → {res['error_detail']}")
        else:
            print(f"[SIMULATE] Skipped: no router available for {best_dex}")