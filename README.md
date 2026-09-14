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
<img src="https://img.shields.io/badge/license-MIT-e0a94c?style=flat-square&labelColor=0a0a0a" alt="MIT" />

![DIPLAB](banner.jpg)

</div>

## What it is

Every scanner shows you *how much* a token's whales hold. DIPLAB shows you
*what they will do with it*. It reads the real trade history of a token's top
holders - how they entered, dipped and exited across every token they touched
before - and projects that behaviour onto the token you're looking at.

The output is a map: the market-cap levels where holders are likely to sell
(the walls), how deep the pullback under each wall runs, and a single
exit-risk score for the token. Paste a contract address, read the walls,
know where to enter, accumulate and exit.

Read-only. No wallet connect. No keys, no signing, no transactions - the
engine only reads the chain.

## How it works

- **01 - SCAN** - pulls the top holders of any token, filters out
  infrastructure, curve and bundle wallets.
- **02 - TRACE** - reads how each whale actually traded before: their median
  exit multiple across past tokens, buy-side and sell-side, on the bonding
  curve and in migrated V4 pools.
- **03 - MAP** - projects each whale's habit onto this token: entry x habit
  becomes an exit level. Levels group into walls; a scenario arrow traces the
  likely path through them, with the dip depth under each.
- **04 - SCORE** - weighs exit pressure, bundling, holder concentration,
  liquidity and psychological levels into one 0-100 exit-risk score.

## What it reads

- **Exit walls** - where top holders are likely to sell, as market-cap levels,
  each tagged with the share of supply behind it.
- **Dip depth** - how far a wall's sell-off pushes price, from the pool's real
  liquidity. A mechanical estimate; a panic dump can run deeper.
- **Whale breakdown** - each readable whale, their habit multiple, and their
  contribution to the walls.
- **Bundle alert** - supply held by wallets that received tokens without ever
  buying. Past a threshold, the forecast is withheld and the token flagged.
- **Exit-risk score** - the token's risk in one number, 100 clean, 0 run.

## Two layers

Robinhood Chain tokens launch on a Pons V2 bonding curve, then graduate to a
Uniswap V4 pool. DIPLAB reads both: a migration detector routes each token to
the right engine, so a fresh curve token and a graduated one are both read
correctly - including the trader attribution that a relayer-and-router chain
makes non-obvious.

## Read-only

No private keys. No signing. No transaction path. The engine only reads the
chain, and a CI job fails the build if any signer-like code ever appears. The
RPC endpoint lives in an environment variable and is never committed.

## Roadmap

**LIVE**
- exit-level map from real whale trade history
- bundle detection and exit-risk score
- works on both curve and migrated V4 tokens

**IN PROGRESS** *(shipping this week)*
- the dashboard: token score, holder stats, whale breakdown
- psychological levels (round market-cap walls)
- faster scans and habit caching

**NEXT** *(this month)*
- $DIPLAB token and holder perks
- live feed of scanned tokens
- alerts and watchlists
- an API for builders

## $DIPLAB

$DIPLAB - soon. Holders get deeper scans, premium features and priority
analysis.

A burn mechanic ties the token to real usage.

## Credits

Approach and read-layer patterns studied from open Robinhood Chain
repositories. MIT.
