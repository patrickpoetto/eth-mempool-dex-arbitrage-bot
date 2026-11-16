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
import time

_SEEN_DEX_QUOTES = set()

def is_profitable_arbitrage(amount_in_usd, amount_out_usd, eth_usd_price, slippage=0.005, gas_price_gwei=30, gas_limit=200_000, min_profit_usd=10):
    """
    Compare the USD value of what goes in vs. what comes out of a candidate
    arbitrage leg, net of estimated gas.

    HISTORICAL BUG (see README "Known limitations" / STATO_OPERATIVO.md):
    this function used to take `amount_in` and `amount_out` as raw on-chain
    integers for two DIFFERENT tokens (token_in's native-decimals amount,
    and token_out's amount padded to a pretend-18-decimals basis via
    normalize_to_18_decimals), subtract them directly, then feed that
    mixed-asset/mixed-scale difference through `Web3.from_wei(x, 'ether')`
    as if it were a literal wei-of-ETH quantity, and multiply by the
    ETH/USD price. Concretely: normalize_to_18_decimals(usdc_raw, 6) pads a
    USDC integer by 1e12 so it can be compared to other DEX quotes for the
    same token_out - that's fine for THAT comparison - but is_profitable_
    arbitrage then re-interpreted that padded USDC integer as ETH wei and
    priced it at the ETH/USD rate. Since 1 USDC needs ~1e12x fewer raw
    units than 1 ETH to represent the same ~$1-$2000 of value, this
    manufactured a bogus ~1e12x-scale number before even multiplying by
    the ETH/USD price, which is exactly how trivial trades produced
    "net profit: $340,911,005.52"-style nonsense.

    FIX: both sides are now converted to actual USD (via
    utils.tokens.get_token_usd_price, which prices a token through its
    Uniswap V2 WETH route and the live ETH/USD Chainlink feed) by the
    caller before this function ever sees them, so the comparison here is
    a plain USD-vs-USD subtraction. This does NOT turn the bot into a real
    arbitrage engine - it's still a single-leg "best quote vs. worst quote"
    comparison, not a verified round-trip - see README for that caveat.
    """
    if amount_in_usd is None or amount_out_usd is None:
        return False, 0

    amount_in_usd = float(amount_in_usd)
    amount_out_usd = float(amount_out_usd) * (1 - slippage)
    eth_usd_price = float(eth_usd_price)

    gas_cost_eth = float(Web3.from_wei(gas_price_gwei * gas_limit * 10**9, 'ether'))
    gas_cost_usd = gas_cost_eth * eth_usd_price

    net_profit = amount_out_usd - amount_in_usd - gas_cost_usd
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

    token_in_decimals = await get_token_decimals_cached(web3, token_in)
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

    # Both quotes were normalized to a pretend-18-decimals basis already
    # (utils.dex.normalize_to_18_decimals), so dividing by 1e18 recovers the
    # human-readable amount of token_out regardless of its real decimals.
    output_token = Web3.from_wei(out_best, 'ether')

    try:
        token_in_usd_price = await get_token_usd_price(web3, token_in, token_in_decimals)
        token_usd_price = await get_token_usd_price(web3, token_out, token_out_decimals)
    except Exception as e:
        print(f"[WARN] USD price lookup failed: {e}")
        token_in_usd_price = None
        token_usd_price = None

    amount_in_human = float(amount_in) / (10 ** token_in_decimals)
    amount_in_usd = amount_in_human * token_in_usd_price if token_in_usd_price else None
    amount_out_usd = float(output_token) * token_usd_price if token_usd_price else None

    is_prof, net_profit = is_profitable_arbitrage(amount_in_usd, amount_out_usd, eth_usd_price)

    if is_prof:
        usd_str = f"${amount_out_usd:.2f}" if amount_out_usd else "≈ N/A"

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