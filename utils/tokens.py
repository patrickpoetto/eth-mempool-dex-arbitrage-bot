import json
import os
from web3 import Web3
from config import get_rotating_http_provider, get_rotating_wss_provider
import asyncio
import random
from asyncio import to_thread
import time
from config import UNISWAP_V2_ROUTER, UNISWAP_ROUTER_ABI, WETH_ADDRESS, get_eth_usd_price

ERC20_SYMBOL_ABI = [{
    "name": "symbol",
    "outputs": [{"type": "string"}],
    "inputs": [],
    "stateMutability": "view",
    "type": "function"
}]

ERC20_DECIMALS_ABI = [{
    "constant": True,
    "inputs": [],
    "name": "decimals",
    "outputs": [{"name": "", "type": "uint8"}],
    "type": "function"
}]

TOKEN_MAP_PATH = os.path.join(os.path.dirname(__file__), "token_map.json")
symbol_cache = {}
decimals_cache = {}

# Carica mappa da file
def load_token_map():
    if os.path.exists(TOKEN_MAP_PATH):
        with open(TOKEN_MAP_PATH, "r") as f:
            return json.load(f)
    return {}

def save_token_map(token_map):
    with open(TOKEN_MAP_PATH, "w") as f:
        json.dump(token_map, f, indent=2)

def load_token_symbol(addr):
    """Solo lettura da file JSON senza on-chain."""
    token_map = load_token_map()
    return token_map.get(Web3.to_checksum_address(addr), {}).get("symbol", addr[-4:].upper())

def load_token_decimals(addr):
    token_map = load_token_map()
    return token_map.get(Web3.to_checksum_address(addr), {}).get("decimals", 18)

# Fetch simbolo token (con cache e fallback)
async def get_token_symbol_async(web3_unused, addr):

    addr = Web3.to_checksum_address(addr)

    if addr in symbol_cache:
        return symbol_cache[addr]

    token_map = load_token_map()
    if addr in token_map and "symbol" in token_map[addr]:
        symbol_cache[addr] = token_map[addr]["symbol"]
        return symbol_cache[addr]

    async def fetch_symbol_with_retry(max_attempts=4):
        for attempt in range(max_attempts):
            web3 = get_rotating_http_provider()

            def fetch():
                try:
                    contract = web3.eth.contract(address=addr, abi=ERC20_SYMBOL_ABI)
                    return contract.functions.symbol().call()
                except Exception as e:
                    return None

            symbol = await to_thread(fetch)
            if symbol:
                return symbol
            else:
                delay = 2 ** attempt + random.uniform(0, 0.5)
                print(f"[RETRY] Failed to fetch symbol for {addr[:6]} → retry in {delay:.1f}s")
                await asyncio.sleep(delay)
        return None

    symbol = await fetch_symbol_with_retry()

    if not symbol:
        symbol = addr[:6]

    # salva su cache + file
    symbol_cache[addr] = symbol
    if not isinstance(token_map.get(addr), dict):
        token_map[addr] = {}

    token_map[addr]["symbol"] = symbol
    save_token_map(token_map)

    return symbol

# Fetch decimali token (con cache e fallback)
async def get_token_decimals_cached(web3_unused, addr):
    from asyncio import to_thread
    import random
    import asyncio

    addr = Web3.to_checksum_address(addr)

    if addr in decimals_cache:
        return decimals_cache[addr]

    token_map = load_token_map()
    if addr in token_map:
        entry = token_map[addr]
        if isinstance(entry, dict) and "decimals" in entry:
            decimals_cache[addr] = entry["decimals"]
            return entry["decimals"]

    async def fetch_decimals_with_retry(max_attempts=4):
        for attempt in range(max_attempts):
            web3 = get_rotating_http_provider()

            def fetch():
                try:
                    contract = web3.eth.contract(address=addr, abi=ERC20_DECIMALS_ABI)
                    return contract.functions.decimals().call()
                except:
                    return None

            decimals = await to_thread(fetch)
            if decimals is not None:
                return decimals
            else:
                delay = 2 ** attempt + random.uniform(0, 0.5)
                print(f"[RETRY] Failed to fetch decimals for {addr[:6]} → retry in {delay:.1f}s")
                await asyncio.sleep(delay)
        return None

    decimals = await fetch_decimals_with_retry()

    if decimals is None:
        print(f"[WARN] Using fallback: 18 decimals for {addr[:6]}")
        decimals = 18

    # Salva cache + file
    decimals_cache[addr] = decimals
    if not isinstance(token_map.get(addr), dict):
        token_map[addr] = {}
    token_map[addr]["decimals"] = decimals
    save_token_map(token_map)

    return decimals
    
# Funzione per scalare amount_in con i decimali corretti
async def scale_amount_in(web3, token_address, raw_amount_wei):
    decimals = await get_token_decimals_cached(web3, token_address)
    if decimals < 18:
        return int(raw_amount_wei / (10 ** (18 - decimals)))
    return raw_amount_wei

_token_price_cache = {}
_token_price_time = {}

async def get_token_usd_price(web3, token_address, token_decimals=None, amount=1 * 10**18):
    now = time.time()
    token_address = Web3.to_checksum_address(token_address)

    # Routing WETH -> WETH through Uniswap reverts with
    # "UniswapV2Library: IDENTICAL_ADDRESSES" (bug found during review: this
    # silently killed USD pricing for the single most common case, since
    # WETH is almost always token_in for mempool-triggered swaps). WETH's
    # USD price is just the live ETH/USD price, no routing needed.
    if token_address == WETH_ADDRESS:
        return get_eth_usd_price(web3)

    if token_address in _token_price_cache and (now - _token_price_time[token_address] < 30):
        return _token_price_cache[token_address]

    if token_decimals is None:
        token_decimals = await get_token_decimals_cached(web3, token_address)

    try:
        router_contract = web3.eth.contract(address=UNISWAP_V2_ROUTER, abi=UNISWAP_ROUTER_ABI)
        scaled_amount = int(1 * (10 ** token_decimals))  # 1 unit in token_decimals
        amounts = router_contract.functions.getAmountsOut(
            scaled_amount, [token_address, WETH_ADDRESS]
        ).call()

        eth_amount = amounts[-1]
        eth_usd = get_eth_usd_price(web3)
        usd_price = float(eth_amount) / 1e18 * float(eth_usd)

        _token_price_cache[token_address] = usd_price
        _token_price_time[token_address] = now
        return usd_price
    except Exception as e:
        print(f"[WARN] USD price failed for token {token_address[:6]}: {e}")
        return None