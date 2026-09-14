# DIPLAB

Read-only exit-level terminal for Robinhood Chain (Pons V2) memecoins.
Paste a token address, get the map of levels where top holders are likely to
exit, how deep the dip under each wall is, and an exit-risk score.

Read-only: no keys, no signing, no transactions. The RPC endpoint is read from
the DIPLAB_RPC environment variable and is never committed.

## Files
- `chain.py`     — chain reader (batched RPC, both curve and V4 pool layers)
- `analyze.py`   — engine: holders, whale habits, exit levels, bundle, score
- `server.py`    — HTTP server: /api/analyze, /api/recent, serves the page
- `index.html`   — frontend
- `Procfile`     — start command for Railway (`web: python server.py`)

## Run locally
    export DIPLAB_RPC="https://robinhood-mainnet.g.alchemy.com/v2/<YOUR_KEY>"
    python server.py            # http://localhost:8000

## Deploy (Railway)
1. Push this repo to GitHub.
2. Railway → New Project → Deploy from GitHub repo → pick this repo.
3. Add a variable: DIPLAB_RPC = your archive RPC URL.
4. Railway builds and gives a public URL.

No third-party dependencies — Python standard library only.
