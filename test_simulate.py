# test_simulate_advanced.py

import json
import os
import asyncio
from web3 import Web3
from config import ARBITRAGE_ADDRESS, ROUTERS, get_rotating_http_provider
from utils.simulate import simulate_tx_call
from utils.tokens import get_token_symbol_async, get_token_decimals_cached
import random

# CONFIG
AMOUNT_IN_ETH = 1.0
ROUTERS_TO_TEST = ["UniswapV2", "Sushiswap"]
MAX_PATHS = 50
MAX_HOPS = 3

TOKEN_MAP_PATH = os.path.join("utils", "token_map.json")

def load_tokens_second(max_tokens=100):
    with open(TOKEN_MAP_PATH) as f:
        data = json.load(f)
    filtered = {
        addr: info for addr, info in data.items()
        if "decimals" in info and "symbol" in info and len(info["symbol"]) >= 2
    }
    # Limita a un massimo di token (es. 100)
    return dict(list(filtered.items())[:max_tokens])

def generate_paths_second(tokens, max_hops=2, max_paths=50):
    import random

    addresses = list(tokens.keys())
    paths = set()

    while len(paths) < max_paths:
        hop = random.randint(1, max_hops)
        if len(addresses) < hop + 1:
            break  # evitare errore con sample su lista troppo corta

        try:
            path = tuple(random.sample(addresses, hop + 1))
            if path[0] != path[-1]:  # evitare loop inutili
                paths.add(path)
        except:
            continue

    return [list(p) for p in paths]

def load_tokens():
    with open(TOKEN_MAP_PATH) as f:
        data = json.load(f)
    return {
        addr: info for addr, info in data.items()
        if "decimals" in info and "symbol" in info and len(info["symbol"]) >= 2
    }

def generate_paths(tokens, max_hops=3, max_paths=100):
    addresses = list(tokens.keys())
    paths = set()

    while len(paths) < max_paths:
        hop = random.randint(1, max_hops)
        path = tuple(random.sample(addresses, hop + 1))
        if path[0] != path[-1]:  # evitiamo path tipo X→X
            paths.add(path)

    return [list(p) for p in paths]

def generate_paths_final(tokens, max_hops=2):
    addresses = list(tokens.keys())
    paths = []

    for i in range(len(addresses)):
        for j in range(len(addresses)):
            if i == j:
                continue
            token_in = addresses[i]
            token_out = addresses[j]

            paths.append([token_in, token_out])  # 1-hop

            if max_hops >= 2:
                for mid in addresses:
                    if mid not in (token_in, token_out):
                        paths.append([token_in, mid, token_out])

            if max_hops >= 3:
                for mid1 in addresses:
                    for mid2 in addresses:
                        if len({token_in, mid1, mid2, token_out}) == 4:
                            paths.append([token_in, mid1, mid2, token_out])
    return paths

async def run_simulations():
    web3 = get_rotating_http_provider()
    tokens = load_tokens()
    paths = generate_paths(tokens, max_hops=MAX_HOPS)
    tested = 0

    print(f"[DEBUG] Loaded {len(tokens)} token validi dal token_map.json")
    print(f"[DEBUG] Generati {len(paths)} path da testare")

    print(f"\n[🔬 SIMULATION START] Testing max {MAX_PATHS} paths ({len(paths)} generated)\n")

    for path in paths:
        if tested >= MAX_PATHS:
            break

        try:
            token_in, token_out = path[0], path[-1]
            decimals_in = await get_token_decimals_cached(web3, token_in)
            decimals_out = await get_token_decimals_cached(web3, token_out)
            symbol_in = await get_token_symbol_async(web3, token_in)
            symbol_out = await get_token_symbol_async(web3, token_out)
        except Exception as e:
            print(f"[SKIP] Path {path[:3]} → errore: {e}")
            continue

        amount_in_wei = int(AMOUNT_IN_ETH * (10 ** decimals_in))
        amount_out_min = int(amount_in_wei * 0.95)

        for router_name in ROUTERS_TO_TEST:
            router_addr = ROUTERS.get(router_name)
            if not router_addr:
                continue

            print(f"[SIM] {router_name:<10} | {symbol_in} → {symbol_out} | path_len={len(path)}", end="")

            result = simulate_tx_call(
                router_address=router_addr,
                amount_in=amount_in_wei,
                amount_out_min=amount_out_min,
                path=path,
                to_address=ARBITRAGE_ADDRESS
            )

            print(f"[DEBUG] Risultato simulate_tx_call: {result}")

            if result["success"] and result["output_token_amount"]:
                out_human = Web3.from_wei(result["output_token_amount"], 'ether')
                print(f" | ✅ OK → Output: {out_human:.6f} {symbol_out}")
            elif result["success"]:
                print(f" | ✅ CALL OK → output non decodificato")
            else:
                print(f" | ❌ FAIL → {result['error_type']} | {result['error_detail'][:60]}...")

        tested += 1

if __name__ == "__main__":
    asyncio.run(run_simulations())