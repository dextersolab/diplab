"""DIPLAB backend — read-only. Serves the page and runs analyze() per token.

  DIPLAB_RPC=<archive rpc> python server.py           # http://localhost:8000
Analysis is live per token (~1-3 min), so results are cached in-memory.
No keys or signing here; RPC comes from the DIPLAB_RPC env only.
"""
import json, time, threading, os, urllib.request
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from dataclasses import asdict
from analyze import analyze

GT = "https://api.geckoterminal.com/api/v2/networks/robinhood"

def _gt(path):
    req = urllib.request.Request(GT + path, headers={"accept": "application/json", "user-agent": "diplab/0.1"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())

def fetch_market(token):
    """Текущая цена в USD + свечи для графика (GeckoTerminal). Ошибки не фатальны."""
    try:
        pools = _gt(f"/tokens/{token}/pools").get("data", [])
        if not pools:
            return {}
        p = pools[0]
        pool = p["attributes"]["address"]
        price = float(p["attributes"].get("base_token_price_usd") or 0)
        name = p["attributes"].get("name", "")
        oc = _gt(f"/pools/{pool}/ohlcv/hour?limit=48").get("data", {}).get("attributes", {}).get("ohlcv_list", [])
        candles = [[int(c[0]), float(c[1]), float(c[2]), float(c[3]), float(c[4])] for c in oc]  # ts,o,h,l,c
        vol = p["attributes"].get("volume_usd") or {}
        created = p["attributes"].get("pool_created_at")
        age_h = None
        if created:
            import datetime as _dt
            try:
                dt = _dt.datetime.fromisoformat(created.replace("Z", "+00:00"))
                age_h = round((_dt.datetime.now(_dt.timezone.utc) - dt).total_seconds() / 3600, 1)
            except Exception:
                pass
        return {"price_usd": price, "candles": candles, "pair": name,
                "reserve_usd": float(p["attributes"].get("reserve_in_usd") or 0),
                "fdv_usd": float(p["attributes"].get("fdv_usd") or 0),
                "volume_24h": float(vol.get("h24") or 0), "age_h": age_h}
    except Exception:
        return {}

CACHE = {}                 # token -> (ts, dict)  готовый результат
JOBS = {}                  # token -> {"status": "pending"|"done"|"error", "data"/"error", "ts"}
RECENT = []                # последние проверки: {token,pair,score,band,ts}
CACHE_TTL = 300            # 5 min
_lock = threading.Lock()

def run_cached(token):
    token = token.lower().strip()
    now = time.time()
    with _lock:
        hit = CACHE.get(token)
        if hit and now - hit[0] < CACHE_TTL:
            return hit[1], True
    res = analyze(token)
    d = asdict(res)
    d["levels"] = [asdict(l) for l in res.levels]
    d["market"] = fetch_market(token)
    # стабильная глубина дипа: constant-product по реальной ликвидности пула
    mk = d["market"]; reserve = mk.get("reserve_usd") or 0; fdv = mk.get("fdv_usd") or 0
    if reserve > 0 and fdv > 0:
        for lv in d["levels"]:
            ratio = (lv["supply_pct"] / 100 * fdv) / (reserve / 2)
            lv["dip"] = 1 - (1 / (1 + ratio)) ** 2
    # DEPTH-компонент score по реальной ликвидности (заменяет базовые 7.5 из движка)
    if reserve > 0 and d.get("score") is not None:
        depth_full = 15 * min(reserve / 50000, 1)          # 0..15 (50k+ = полный балл)
        sc = max(0, min(100, round(d["score"] - 7.5 + depth_full)))
        d["score"] = sc
        d["band"] = ("CLEAN" if sc >= 80 else "OK" if sc >= 60 else
                     "RISKY" if sc >= 40 else "DANGER")
    with _lock:
        CACHE[token] = (now, d)
        RECENT[:] = [r for r in RECENT if r["token"] != token]      # дедуп
        RECENT.insert(0, {"token": token, "pair": d.get("market", {}).get("pair"),
                          "score": d.get("score"), "band": d.get("band"), "ts": int(now)})
        del RECENT[30:]                                             # держим 30
    return d, False

def _run_job(token):
    try:
        d, _ = run_cached(token)
        with _lock:
            JOBS[token] = {"status": "done", "data": d, "ts": time.time()}
    except Exception as e:
        with _lock:
            JOBS[token] = {"status": "error", "error": str(e), "ts": time.time()}


def start_job(token):
    """Запустить анализ в фоне, если он ещё не идёт и не готов. Вернёт текущий статус."""
    token = token.lower().strip()
    now = time.time()
    with _lock:
        hit = CACHE.get(token)
        if hit and now - hit[0] < CACHE_TTL:
            return {"status": "done", "data": hit[1]}
        job = JOBS.get(token)
        if job and job["status"] == "pending":
            return {"status": "pending"}
        if job and job["status"] in ("done", "error") and now - job["ts"] < 15:
            return job                              # свежий результат/ошибка
        JOBS[token] = {"status": "pending", "ts": now}
    threading.Thread(target=_run_job, args=(token,), daemon=True).start()
    return {"status": "pending"}


class H(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        b = body if isinstance(body, bytes) else body.encode()
        self.send_response(code)
        self.send_header("content-type", ctype)
        self.send_header("content-length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/health":
            return self._send(200, json.dumps({"ok": True}))
        if u.path == "/api/recent":
            with _lock:
                return self._send(200, json.dumps({"recent": list(RECENT)}))
        if u.path in ("/api/analyze", "/api/result"):
            q = parse_qs(u.query)
            token = (q.get("token") or [""])[0]
            if not token.startswith("0x") or len(token) != 42:
                return self._send(400, json.dumps({"error": "bad token address"}))
            job = start_job(token)
            if job["status"] == "done":
                out = dict(job["data"]); out["status"] = "done"
                return self._send(200, json.dumps(out))
            if job["status"] == "error":
                return self._send(200, json.dumps({"status": "error", "error": job["error"]}))
            return self._send(200, json.dumps({"status": "pending"}))
        if u.path in ("/", "/index.html"):
            try:
                with open(os.path.join(os.path.dirname(__file__), "index.html"), "rb") as f:
                    return self._send(200, f.read(), "text/html; charset=utf-8")
            except FileNotFoundError:
                return self._send(200, "<h1>DIPLAB</h1><p>index.html not built yet</p>", "text/html")
        return self._send(404, json.dumps({"error": "not found"}))

    def log_message(self, *a):  # тише в консоли
        pass

if __name__ == "__main__":
    if not os.environ.get("DIPLAB_RPC"):
        raise SystemExit("set DIPLAB_RPC")
    port = int(os.environ.get("PORT", 8000))
    print(f"DIPLAB on http://localhost:{port}")
    ThreadingHTTPServer(("", port), H).serve_forever()
