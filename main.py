import asyncio
import json
import threading
import websockets
from web3 import Web3
from config import ROUTERS, get_eth_usd_price, get_rotating_http_provider, get_rotating_wss_provider, get_rotating_wss_url
from utils.dex import COMMON_TOKENS
from eth_abi import decode_abi
from utils.tokens import get_token_symbol_async, get_token_decimals_cached
from utils.arbitrage import check_arbitrage_opportunity
from utils.pathfinder import generate_paths, fetch_transaction_with_retry
from collections import deque
from dotenv import load_dotenv
import random
load_dotenv()       


ALL_ROUTERS = set(addr.lower() for addr in ROUTERS.values())

# Mapping di tutte le funzioni swap
SWAP_FUNCTIONS = {
    "0x38ed1739": {"name": "swapExactTokensForTokens", "abi": ['uint256', 'uint256', 'address[]', 'address', 'uint256']},
    "0x18cbafe5": {"name": "swapExactETHForTokens", "abi": ['uint256', 'address[]', 'address', 'uint256']},
    "0x7ff36ab5": {"name": "swapExactETHForTokensSupportingFeeOnTransferTokens", "abi": ['uint256', 'address[]', 'address', 'uint256']},
    "0x8803dbee": {"name": "swapTokensForExactTokens", "abi": ['uint256', 'uint256', 'address[]', 'address', 'uint256']},
}

stop_event = threading.Event()

def wait_for_exit_key():
    input("\n🔴 Premi INVIO per interrompere il BOT...\n")
    stop_event.set()

semaphore = asyncio.Semaphore(5)  # max 5 fetch simultanei per evitare rate limiting

async def process_tx(tx_hash):
    async with semaphore:
        # Piccolo delay tra richieste per evitare rate limiting
        await asyncio.sleep(0.1)
        
        tx = await fetch_transaction_with_retry(tx_hash)
        if tx is None:
            return
        
        web3 = get_rotating_http_provider()

        input_data = tx.get("input", "")
        if not input_data or not input_data.startswith("0x"):
            return

        sig = input_data[:10]
        if sig not in SWAP_FUNCTIONS:
            return

        func_info = SWAP_FUNCTIONS[sig]
        abi_types = func_info['abi']

        if len(input_data[10:]) < 64 * len(abi_types):
            print(f"[SKIP] {func_info['name']} | Input troppo corto → {tx_hash[:8]}")
            return

        try:
            decoded = decode_abi(abi_types, bytes.fromhex(input_data[10:]))

            # Extract path and amount_in
            if 'ETH' in func_info['name']:
                path = decoded[1]
                amount_in = tx.get("value", 0)
            elif func_info['name'] == "swapTokensForExactTokens":
                path = decoded[2]
                amount_in = decoded[1]
            else:
                path = decoded[2]
                amount_in = decoded[0]

            if not path or len(path) < 2 or int(amount_in) == 0:
                return

            token_in = Web3.to_checksum_address(path[0])
            token_out = Web3.to_checksum_address(path[-1])

            try:
                token_in_symbol = await get_token_symbol_async(web3, token_in)
            except:
                token_in_symbol = token_in[:6]

            try:
                token_out_symbol = await get_token_symbol_async(web3, token_out)
            except:
                token_out_symbol = token_out[:6]

            try:
                pretty_amount = Web3.from_wei(amount_in, 'ether')
            except:
                pretty_amount = amount_in

            amount_in_decimals = await get_token_decimals_cached(web3, token_in)
            pretty_amount = int(amount_in) / (10 ** amount_in_decimals)
            print(f"[TX] {func_info['name']} | {token_in_symbol} → {token_out_symbol} | In: {pretty_amount:.18f} {token_in_symbol}")

            eth_usd_price = get_eth_usd_price(web3)

            # ➤ Forward arbitrage (normal path)
            seen_paths_local = set()
            # ➤ Forward arbitrage
            forward_paths = generate_paths(token_in, token_out)
            for path in forward_paths:
                pair = (path[0], path[-1])
                if pair in seen_paths_local:
                    continue
                seen_paths_local.add(pair)
                await check_arbitrage_opportunity(web3, amount_in, path, eth_usd_price, direction="forward")

            # ➤ Reverse arbitrage
            reverse_paths = generate_paths(token_out, token_in)
            for path in reverse_paths:
                pair = (path[-1], path[0])  # attenzione, invertito
                if pair in seen_paths_local:
                    continue
                seen_paths_local.add(pair)
                await check_arbitrage_opportunity(web3, amount_in, path[::-1], eth_usd_price, direction="reverse")

        except Exception as e:
            print(f"[ERROR] Decode failed ({func_info['name']}) → {e}")

async def poll_pending_transactions():
    from config import _get_rotating_http_provider_with_url, _mark_provider_unhealthy
    
    print("\n📡 Polling mempool every 2s via eth_getBlockByNumber('pending')...\n")
    seen = set()
    seen_queue = deque(maxlen=1000)

    while not stop_event.is_set():
        url = None
        try:
            web3, url = _get_rotating_http_provider_with_url()
            block = web3.eth.get_block("pending", full_transactions=True)

            for tx in block.transactions:
                tx_hash = tx.hash.hex()
                if tx_hash not in seen:
                    seen.add(tx_hash)
                    seen_queue.append(tx_hash)
                    asyncio.create_task(process_tx(tx_hash))
                if len(seen_queue) > 1000:
                    oldest = seen_queue.popleft()
                    seen.discard(oldest)

        except Exception as e:
            error_str = str(e)
            # Marca provider come unhealthy per errori critici (solo se abbiamo l'URL)
            if url:
                if "403" in error_str or "Forbidden" in error_str:
                    _mark_provider_unhealthy(url, "403")
                elif "SSL" in error_str or "TLS" in error_str:
                    _mark_provider_unhealthy(url, "SSL")
                elif "429" in error_str:
                    _mark_provider_unhealthy(url, "429")
            # Silenzioso per evitare spam di log

        await asyncio.sleep(2)

async def listen_new_pending_txs(max_attempts=5):
    timeout_count = 0
    print("\n📡 Listening for new pending transactions via WebSocket...\n")
    attempt = 0
    while attempt < max_attempts:
        uri = get_rotating_wss_url()
        print(f"[WSS] Connecting to {uri}")
        try:
            async with websockets.connect(uri, ping_interval=None) as ws:
                await ws.send(json.dumps({
                    "id": 1,
                    "method": "eth_subscribe",
                    "params": ["newPendingTransactions"]
                }))
                print("[✅] Subscribed to newPendingTransactions")

                while True:
                    try:
                        msg = await asyncio.wait_for(ws.recv(), timeout=240)
                        data = json.loads(msg)
                        tx_hash = data.get("params", {}).get("result")
                        if tx_hash:
                            asyncio.create_task(process_tx(tx_hash))
                    except asyncio.TimeoutError:
                        print("[⚠️] Timeout ricezione WS — connessione ancora attiva?")
                        timeout_count += 1
                        if timeout_count >= 3:
                            print("[🔁] Timeout ripetuti → cambio provider WebSocket")
                            break  # esce dal while True e fa reconnect con provider diverso
                    except Exception as e:
                        print(f"[WS ERROR] durante recv: {e}")
                        break  # esce e fa retry
        except Exception as e:
            print(f"[❌] Errore connessione WebSocket: {e}")

        attempt += 1
        wait = 2 ** attempt + random.uniform(0, 1)
        print(f"[RETRY] Tentativo riconnessione WebSocket {attempt}/{max_attempts} in {wait:.1f}s...")
        await asyncio.sleep(wait)

    print("[FALLBACK] Raggiunto limite massimo di retry → passo a polling HTTP...")
    await poll_pending_transactions()

if __name__ == "__main__":
    threading.Thread(target=wait_for_exit_key, daemon=True).start()

    async def run_bot():
        max_attempts = 5
        for attempt in range(max_attempts):
            try:
                print(f"[INFO] Tentativo connessione WebSocket ({attempt + 1}/{max_attempts})...")
                await listen_new_pending_txs()
                return  # se WS parte correttamente, esci dal ciclo
            except Exception as e:
                wait = 2 ** attempt
                print(f"[RETRY] WebSocket fallito: {e} → nuovo tentativo in {wait}s...")
                await asyncio.sleep(wait)

        print("[FALLBACK] WebSocket fallito troppe volte → passo a polling HTTP...")
        await poll_pending_transactions()

    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        print("\n[INFO] Script manualmente interrotto.")