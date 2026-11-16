# ETH Mempool DEX Arbitrage Bot (Prototype)

A Python prototype that explores real-time MEV/arbitrage mechanics on Ethereum
mainnet: subscribing to the pending-transaction mempool over WebSocket,
decoding DEX swap calls across multiple protocols, generating multi-hop
trade paths, and simulating execution via `eth_call` to estimate profit
after gas and slippage.

**This is a learning project, not a working trading bot.** It was built to
understand the mechanics of mempool monitoring, multi-DEX quoting, and
pathfinding — not to generate real profit. See
[Known Limitations](#known-limitations) below before assuming otherwise.

## What it actually does

- Subscribes to `eth_subscribe("newPendingTransactions")` over WebSocket
  (with automatic rotation across multiple RPC providers: Alchemy,
  QuickNode, Infura, Blast, NodeReal), with HTTP polling as a fallback.
- Decodes pending transactions addressed to known DEX routers
  (`eth-abi`), recognizing the common `swapExactTokensForTokens`-style
  call signatures.
- Generates 1-, 2-, and 3-hop candidate trade paths between the tokens
  involved and a set of common intermediates (WETH, USDC, DAI, ...).
- Fetches quotes for each path from multiple DEXs/aggregators in
  parallel: `getAmountsOut` (Uniswap V2 / Sushiswap),
  `quoteExactInputSingle` (Uniswap V3), Curve's REST quote endpoint,
  Balancer v2 (GraphQL), and 1inch (aggregator API).
- Compares quotes across venues to estimate a spread, then simulates
  the winning route with `eth_call` (no real transaction is ever sent;
  no funds are moved).
- Tracks provider health and rotates away from providers that start
  erroring or rate-limiting.

## Known limitations

This project's own operational notes
([`STATO_OPERATIVO.md`](./STATO_OPERATIVO.md), written in Italian) are kept
in the repo intentionally, as an honest record of where the prototype stands.
The highlights:

- **Critical bug: profit calculation is wrong.** `utils/arbitrage.py`
  currently subtracts `amount_in` (token A, the input) from `adjusted_out`
  (token B, a *different* token from a different hop) and treats the
  result as if it were an ETH-denominated profit. Because the two amounts
  aren't in the same unit or even the same asset, this produces
  nonsensical numbers in testing (e.g. "net profit: $340,911,005.52").
  **The bot has never produced a trustworthy profit estimate.** A correct
  fix needs to convert both legs to a common unit (e.g. USD) before
  comparing them, and that rewrite hasn't been done yet.
- **Simulations frequently fail** (`TRANSFER_FROM_FAILED`,
  `INSUFFICIENT_OUTPUT_AMOUNT`) because the simulated wallet has no real
  token balance/approval and quotes can go stale between fetch and
  simulation.
- **No real execution path.** The bot only ever simulates via `eth_call`;
  there is no nonce management, dynamic gas pricing, transaction
  submission, or Flashbots/private-order-flow integration. It cannot
  place trades even if the profit math were fixed.
- No persistent logging (console only), no alerting, and several
  parameters that should be dynamic (gas price, slippage, min-profit
  threshold) are hardcoded.

In short: the infrastructure pieces (mempool subscription, multi-protocol
decoding, pathfinding, concurrent quoting, simulation plumbing) work. The
piece that would make this an actual arbitrage bot — correct, trustworthy
profit accounting — does not, and real execution was never attempted. I'm
leaving that documented rather than polished away, because the process of
hitting and diagnosing that bug was a big part of what I learned about
building this kind of system (unit consistency across multi-hop/multi-asset
paths is the easy thing to get wrong).

## Tech stack

- Python 3.10
- [`web3.py`](https://github.com/ethereum/web3.py) — JSON-RPC / contract calls
- `eth-abi`, `eth-account` — ABI decoding and signing
- `websockets` — mempool subscription
- `aiohttp` — async HTTP for REST/GraphQL quote sources
- `asyncio` — concurrent quote fetching across DEXs/providers

## Project structure

```
.
├── main.py              # Entry point: WebSocket listener, async event loop
├── config.py            # Env var loading, Web3 provider setup, provider rotation
├── test_simulate.py     # Ad hoc tests for the simulation path
├── STATO_OPERATIVO.md   # Honest operational status notes (Italian)
└── utils/
    ├── dex.py           # Per-DEX quote functions (V2/V3, Curve, Balancer, 1inch)
    ├── pathfinder.py    # Multi-hop path generation
    ├── arbitrage.py     # Spread/profit calculation (see Known Limitations)
    ├── simulate.py       # eth_call-based swap simulation
    ├── tokens.py        # Async ERC-20 symbol/decimals resolution
    ├── security.py      # Misc input validation helpers
    └── token_map.json   # Cached token metadata
```

## Running it

This is a prototype meant for local experimentation against mainnet RPC
providers, not a deployable service.

```bash
python3.10 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# fill in at least one HTTP+WSS provider pair, e.g. HTTP_ALCHEMY/WSS_ALCHEMY

python main.py
```

The wallet key (`PRIVATE_KEY`/`ADDRESS`) is only ever used to build and
sign simulated (`eth_call`) transactions — no transaction is broadcast and
no funds move. Still, use a throwaway key, never a wallet holding real
funds.

## License

MIT — see [LICENSE](./LICENSE).
