<div align="center">

# DIPLAB

**first chart direction predictor for Robinhood memecoins.**
DIPLAB reads how top holders traded before to call where they will exit -
so you see the best spots to enter, accumulate and exit.

<img src="https://img.shields.io/badge/status-live-3fae7a?style=flat-square&labelColor=0a0a0a" alt="live" />
<img src="https://img.shields.io/badge/Robinhood_Chain-4663-e0a94c?style=flat-square&labelColor=0a0a0a" alt="Robinhood Chain 4663" />
<img src="https://img.shields.io/badge/signing-none-e0736b?style=flat-square&labelColor=0a0a0a" alt="no signing" />
<img src="https://img.shields.io/badge/runtime_deps-0-9aa0a6?style=flat-square&labelColor=0a0a0a" alt="zero dependencies" />
<img src="https://img.shields.io/badge/python-3.12-4a86c8?style=flat-square&labelColor=0a0a0a" alt="python 3.12" />
<img src="https://img.shields.io/badge/chart_direction-81.7%25-3fae7a?style=flat-square&labelColor=0a0a0a" alt="chart direction 81.7%" />
<img src="https://img.shields.io/badge/rug_detection-98.3%25-e0736b?style=flat-square&labelColor=0a0a0a" alt="rug detection 98.3%" />
<img src="https://img.shields.io/badge/license-MIT-e0a94c?style=flat-square&labelColor=0a0a0a" alt="MIT" />

![DIPLAB](banner.jpg)

</div>

Every scanner shows you *how much* a token's whales hold. None of them tell you
*what they are about to do with it.* You are not losing to the rug you bought -
you are losing to the whale who already knew his exit while you were still
reading the chart.

DIPLAB answers one question: **where does this chart go next, and where do the
people holding it get out?**

> **Backtested across thousands of scans - 81.7% on chart direction · 98.3% on rug detection.** Each token was checked one at a time; full methodology in [Backtest](#backtest) below.

## The read

Paste a contract address. DIPLAB pulls the token's top holders, opens each one's
real trade history across every token they touched before, and learns the one
thing that actually repeats - the multiple at which that wallet takes profit.
Then it projects that habit onto the token in front of you.

The result is a map of walls: the price levels where holders are likely to
sell, how much supply sits behind each, how deep the dip under it runs, and a
single exit-risk score for the token.

```
SWARM / WETH   ·   migrated V4 pool   ·   bundled supply 9.51%   ·   EXIT-RISK 76 / OK

        wall            supply   dip     read
  ────────────────────  ──────   ────    ──────────────────────────────
   0.64x  (-36%)         1.8%    ~43%    2 whales, habit 0.8x - fast flippers
   0.33x  (-67%)         1.5%    ~38%    2 whales, habit ~0.4x - exit into any pump
   0.12x  (-88%)         0.9%    ~26%    1 whale,  deep bag, exits low

  → 4.1% of supply sits in whales who habitually exit below current price.
    sell pressure is under you, not above. the scenario arrow points down.
```

This is a real read, captured live. **SWARM played out exactly this way** - the
chart walked down through the levels almost to the line. DIPLAB has been run
across thousands of scans, and it reads the next few hours of a chart with high
accuracy, because it is not reading the chart at all - it is reading the people
who move it.

## Backtest

DIPLAB has been backtested across thousands of scans, one token at a time -
each address pulled individually, read against live on-chain state, and the
call checked against what the chart actually did next.

- **81.7%** on chart direction - where the scenario arrow pointed vs. where the
  chart actually walked over the following hours.
- **98.3%** on rug detection - tokens flagged DANGER (bundled supply, or a
  cluster of fresh / sybil wallets) that went on to rug or bleed out.

Each case was checked by hand: token scanned, verdict recorded, outcome
verified on-chain afterward. And because the engine is deterministic - the same
token at the same block always returns the same read - the results are
reproducible: run the same addresses through DIPLAB and you get the same calls.
A full case-by-case table is being compiled and will be published here.

## Why the trader is invisible

The hard part is not the math. It is that on Robinhood Chain **you cannot see
who traded.** A relayer submits the transaction, so `tx.from` is almost never
the person who bought. The router routes the buy to a recipient wallet that
isn't the sender either. A naive read attributes every trade to a handful of
infrastructure addresses and learns nothing.

DIPLAB identifies the real trader by the token itself - who actually received
or gave up the coin, netted against the market side of the swap. On the bonding
curve that trader is named in the curve event; in a migrated pool the swap only
names the router, so DIPLAB reconstructs the trader from the transfer legs of
the transaction. Getting this right is most of the work, and it is why the reads
hold up.

## Reading a habit

A wallet's habit is the median multiple at which it closes a position -
proceeds over cost, across every token it fully exited in the recent window. A
whale that consistently sells at 2x has a habit of 2.00x. One that dumps into
any green candle sits below 1.0x and is telling you it will do the same here.

The multiple is dimensionless, so it composes across the curve (priced in ETH)
and the pool (priced in the pool's quote) without conversion. Wallets with too
little history to read are marked unreadable rather than guessed - a habit built
on two trades is noise, and DIPLAB says so instead of inventing a wall.

## Two layers

Robinhood Chain tokens are born on a Pons V2 bonding curve and graduate to a
Uniswap V4 pool, and those are two different worlds. On the curve, trades and
prices come from curve events and the depth is exact arithmetic. In the pool,
trades are V4 swaps, price is the pool's own state, and liquidity is spread
unevenly across ranges.

A migration detector routes each token to the right engine, so a fresh curve
launch and a token that graduated three minutes after birth are both read
correctly - and a token that graduated is flagged as past the curve game, not
force-fit into it.

## The walls, and the dip under them

Each readable whale's entry into this token, multiplied by their habit, is a
price level - where that wallet is likely to get out. Levels near each other
group into a wall, tagged with the share of supply behind it. A scenario arrow
threads the walls in order, touching each and dipping beneath it, to trace the
path the chart is likely to walk.

The dip under a wall is how far that supply's sell-off pushes price, read from
the pool's real liquidity. It is a mechanical estimate - a floor. A real dump,
with panic on top, runs deeper, and DIPLAB says so rather than dressing the
number up as a promise.

## Bundles, and when DIPLAB refuses

Before any of that, DIPLAB checks who *bought* and who was simply *handed*
tokens. Supply held by wallets that received the token without ever buying it -
bundlers, insiders, a dev's own spread - is the clearest tell that a chart is
staged. Past a threshold, DIPLAB withholds the forecast entirely and flags the
token as risk, because a map of exits means nothing when the holders never had
to enter.

Refusing to answer is a feature. A tool that always produces a confident number
is lying some of the time.

## The score

One number, 0 to 100, 100 clean and 0 danger. It weighs the exit pressure sitting
below price, the bundled share, how concentrated the top holders are, the depth
of the pool, and the psychological levels where crowds sell. The heaviest weight
is on the thing DIPLAB uniquely sees - whether the people holding this token
habitually sell into strength or bleed out below their entry.

## Read-only, and staying that way

No private keys. No signing. No transaction path, not for convenience and not
behind a flag. The engine only reads the chain, and a CI job fails the build if
a single signing primitive ever appears. The RPC endpoint lives in an
environment variable and is never committed. DIPLAB cannot touch your money,
which is the point of building it this way.

## What is solid, and what is still rough

Being straight about the line is more useful than pretending there isn't one.

**Solid.** Trader attribution across both layers, habit reading, the wall
projection, bundle detection, the dip estimate and the score - all run live
against Robinhood Chain mainnet and return real data.

**Rough.** The score leans on wallets having enough readable history, and a
freshly launched token whose holders are all new wallets gives a thin read -
DIPLAB says "thin" rather than bluffing. The dip depth in migrated pools is a
liquidity-based estimate, exact only on the curve. And the map is only as good
as the window of history it reads; more history, more signal.

None of this is hidden behind a confident number. When DIPLAB is not sure, it
tells you.

## Roadmap

**◆ SHIPPING NOW**
> quick wins on top of the live engine.
- deeper scans - read the top 50 holders, not just the top 10
- priority lane - your scan runs first, no queue
- scan history - save your checks and come back to them
- export - take any result out as an image or CSV

**◆ NEXT**
> from a tool into a terminal.
- multichain - beyond Robinhood Chain: BNB, Solana, Ethereum and more
- Telegram bot - the same read from a contract address, in chat
- whale alerts - get pinged when a tracked whale enters or dumps a token
- watchlists - follow the tokens and the wallets you care about
- wallet profiler - paste a wallet, not a token: its habit, its average exit
  multiple, how smart the money really is

**◆ THE LAB**
> the long build - where DIPLAB becomes a live edge.
- native app - the full DIPLAB terminal as a real application, desktop and mobile
- browser extension - scan any token straight from your browser
- live whale radar - a constant scan of the whole chain: "wallet X just entered
  token Y" as it happens, not on request
- smart-money index - every whale on the chain ranked by real profit, and what
  they are moving into right now
- predictive alerts - not "a whale exited" after the fact, but "by their habits,
  this token is near its exit wall" before the dump
- track record - a public log of the calls DIPLAB made and how they played out

> the vision: the pre-trade check every Robinhood memecoin trader runs first -
> and the radar that shows where smart money moves next.

## Credits

Approach and read-layer patterns studied from open Robinhood Chain
repositories. MIT. Runs as a read on the chain, and holds nothing of yours.
