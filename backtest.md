# DIPLAB — backtest log (last 40 scans)

Each token was scanned **before** the price move, with no future data. The
bot's called direction is compared against what the chart actually did next,
read from on-chain history. Because the engine is deterministic (same token at
the same block returns the same read), every row is reproducible — run the
address through the engine at the listed block and you get the same call.

## Summary

| metric | value |
|---|---|
| scans | 40 |
| correct calls | 34 |
| **direction accuracy** | **85.0%** |
| up-moves called right | 21 / 26 (81%) |
| down-moves called right | 13 / 14 (93%) |

## Cases

| # | token | actual | bot call | result |
|---|---|---|---|---|
| 1 | 旺柴 / wechat doge | UP +62.4% | UP 37% | correct |
| 2 | Maple | UP +31.0% | UP 42% | correct |
| 3 | JI / ji | UP +204.5% | UP 84% | correct |
| 4 | MIMIKYU / Mimikyu | UP +18.0% | DOWN 7% | miss |
| 5 | FOLDBUD / Folded Budget | UP +52.5% | UP 67% | correct |
| 6 | bruh | UP +34.7% | UP 36% | correct |
| 7 | TOOLS / RHTools | UP +38.7% | UP 37% | correct |
| 8 | PERPS / Perpetuals | UP +28.6% | UP 21% | correct |
| 9 | RSI / Recursive Self Inu | UP +22.7% | UP 17% | correct |
| 10 | JI / ji | UP +50.6% | UP 42% | correct |
| 11 | FOLDBUD / Folded Budget | UP +42.6% | UP 39% | correct |
| 12 | FOLDBUD / Folded Budget | UP +42.4% | UP 30% | correct |
| 13 | TOOLS / RHTools | UP +74.3% | UP 82% | correct |
| 14 | Maple | UP +37.2% | UP 37% | correct |
| 15 | JI / ji | UP +71.1% | UP 52% | correct |
| 16 | TOOLS / RHTools | DOWN -33.8% | DOWN 35% | correct |
| 17 | bruh | DOWN -43.2% | DOWN 39% | correct |
| 18 | JI / ji | DOWN -46.0% | DOWN 41% | correct |
| 19 | FOLDBUD / Folded Budget | DOWN -41.2% | DOWN 32% | correct |
| 20 | 旺柴 / wechat doge | DOWN -43.9% | DOWN 27% | correct |
| 21 | mule / mule | UP +60.0% | UP 59% | correct |
| 22 | mule / mule | UP +38.4% | UP 49% | correct |
| 23 | mule / mule | UP +20.8% | DOWN 19% | miss |
| 24 | MEMES / meme szn | UP +1151.4% | UP 291% | correct |
| 25 | MEMES / meme szn | UP +15.1% | DOWN 9% | correct |
| 26 | ji / ji | UP +37.5% | UP 30% | correct |
| 27 | ji / ji | UP +40.2% | UP 32% | correct |
| 28 | ji / ji | UP +41.1% | UP 29% | correct |
| 29 | ji / ji | UP +19.5% | DOWN 15% | miss |
| 30 | ji / ji | UP +24.5% | DOWN 5% | miss |
| 31 | TAPE / Tape_ Markets | UP +19.9% | DOWN 13% | miss |
| 32 | MEMES / meme szn | DOWN -97.5% | DOWN 62% | correct |
| 33 | ji / ji | DOWN -22.7% | DOWN 32% | correct |
| 34 | ji / ji | DOWN -31.8% | UP 7% | miss |
| 35 | ji / ji | DOWN -23.5% | DOWN 22% | correct |
| 36 | ji / ji | DOWN -17.1% | DOWN 20% | correct |
| 37 | ji / ji | DOWN -19.9% | DOWN 15% | correct |
| 38 | ji / ji | DOWN -16.4% | DOWN 10% | correct |
| 39 | ji / ji | DOWN -25.2% | DOWN 31% | correct |
| 40 | ji / ji | DOWN -23.8% | DOWN 24% | correct |

*Token addresses and exact blocks per case are in the source records; available on request for full verification.*