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

- **Fixed since the original prototype: the profit-calculation bug.**
  `utils/arbitrage.py` used to subtract `amount_in` (token A, in its own
  native on-chain decimals) from `adjusted_out` (token B, a *different*
  token, already re-scaled to a pretend-18-decimals basis by
  `normalize_to_18_decimals` so quotes from different DEXs could be
  compared), then ran that mixed-asset/mixed-scale difference through
  `Web3.from_wei(x, 'ether')` as if it were literally wei of ETH, and
  multiplied the result by the live ETH/USD price. Concretely: 1 USDC
  needs ~1e12x fewer raw units than 1 ETH to represent the same dollar
  value, so padding a USDC quote up to an 18-decimal integer and then
  pricing that integer as ETH inflates the "profit" by roughly the
  ETH/USD rate on top of the already-wrong unit — which is exactly how a
  spread worth pennies turned into "net profit: $340,911,005.52" in
  testing. Reproduced during this review with live mainnet quotes (1 WETH
  vs. ~1812 USDC): the old code reported **$3,267,091.34** "profit" on a
  trade that is actually worth roughly **-$19.53** once gas and slippage
  are accounted for. **Fixed**: both legs are now converted to actual USD
  via `utils.tokens.get_token_usd_price` (which routes the token through
  Uniswap V2 to WETH and prices it off the live Chainlink ETH/USD feed)
  before any subtraction happens, so the comparison is a plain USD-vs-USD
  number. Fixing this also surfaced a second real bug: that USD-pricing
  helper tried to route WETH through itself (`[WETH, WETH]`), which
  reverts with `UniswapV2Library: IDENTICAL_ADDRESSES` — silently killing
  USD pricing for the single most common case, since WETH is almost
  always `token_in` for mempool-triggered swaps. Also fixed (WETH's USD
  price is just the live ETH/USD price, no routing needed).
  **What this fix does *not* do**: it does not turn this into a real
  arbitrage engine. The comparison is still "best DEX quote vs. worst DEX
  quote for one swap direction," not a verified two-leg round trip back to
  the original token — so a nonzero "net profit" here is a necessary, not
  sufficient, signal of a real opportunity.
- **Simulations will essentially always fail** with
  `TransferHelper: TRANSFER_FROM_FAILED` — verified during this review
  against live mainnet state via a public RPC. The root cause is more
  specific than "no balance": `utils/simulate.py` builds the swap
  transaction and then explicitly strips the `from` field before calling
  `eth_call`, so the simulated call always executes as `address(0)`. On
  mainnet, `address(0)` holds no Uniswap-router allowance for any ERC-20
  (confirmed: `allowance(0x0, UniswapV2Router) == 0` for WETH), so
  `transferFrom` reverts every time — independent of whether
  `ARBITRAGE_ADDRESS` (which is only ever used as the swap's recipient,
  never as the caller) has any balance. `eth_call` against live state also
  can't account for quotes going stale between fetch and simulation, and
  `simulate.py` only knows the `swapExactTokensForTokens` ABI — it never
  attempts the ETH-input variant.
- **No real execution path.** The bot only ever simulates via `eth_call`;
  there is no nonce management, dynamic gas pricing, transaction
  submission, or Flashbots/private-order-flow integration. It cannot
  place trades even if the profit math were fixed.
- No persistent logging (console only), no alerting, and several
  parameters that should be dynamic (gas price, slippage, min-profit
  threshold) are hardcoded.
- `utils/security.py` defines `is_token_contract_safe()` (checks a token
  has bytecode and answers `symbol()`/`decimals()`) but nothing in the
  codebase ever calls it — the mempool/pathfinding pipeline never actually
  screens out non-contract or malformed "tokens" before quoting them.

In short: the infrastructure pieces (mempool subscription, multi-protocol
decoding, pathfinding, concurrent quoting, simulation plumbing) work, and
after this review the profit math is at least internally consistent
(USD-vs-USD, verified against live mainnet quotes) instead of nonsensical.
What's still missing to make this an actual arbitrage bot is a real
round-trip profitability check, execution, and the simulation accurately
reflecting a funded wallet. I'm leaving the original bug's mechanism
documented above (not just "it was broken") because the process of hitting
and diagnosing it was a big part of what I learned about building this kind
of system — unit and decimals consistency across multi-hop/multi-asset
paths is the easy thing to get wrong, and it's wrong in a way that doesn't
throw an exception, it just quietly produces a number that looks like a
real dollar figure.

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

### Verifying the core logic without a paid RPC subscription

You don't need an Alchemy/Infura API key to exercise most of this code —
only `main.py`'s *live* `eth_subscribe("newPendingTransactions")` mempool
feed needs a WSS endpoint from one of those providers. Everything
downstream of "I have a pending transaction" works against any public
HTTP RPC, no key required (e.g. `https://ethereum-rpc.publicnode.com` or
`https://eth.llamarpc.com`). During this review, pointing `HTTP_ALCHEMY` in
`.env` at a public endpoint like that was enough to, against live mainnet
state:

- fetch real `getAmountsOut` (Uniswap V2 / Sushiswap) and
  `quoteExactInputSingle` (Uniswap V3) quotes for a WETH→USDC path,
- run `test_simulate.py` end-to-end and get real `eth_call` reverts
  (`execution reverted`) back from mainnet for randomly generated paths,
  and
- reproduce the profit-calculation bug (see below) with real numbers,
  confirming both the diagnosis and the fix.

`pip install -r requirements.txt` as originally committed could not
resolve (`web3==5.31.4` requires `websockets<10`, but the file pinned
`websockets==15.0.1`); it's now pinned to `websockets==9.1`, which
satisfies that constraint and is what was used for everything above.

## License

MIT — see [LICENSE](./LICENSE).
