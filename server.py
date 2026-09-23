"""DIPLAB backend — read-only. Serves the page and runs analyze() per token.

  DIPLAB_RPC=<archive rpc> python server.py           # http://localhost:8000
Analysis is live per token (~1-3 min), so results are cached in-memory.
No keys or signing here; RPC comes from the DIPLAB_RPC env only.
"""
import json, time, threading, os, urllib.request, urllib.error
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from dataclasses import asdict
from analyze import analyze

GT = "https://api.geckoterminal.com/api/v2/networks/robinhood"

def _gt(path, _tries=4):
    # GeckoTerminal free tier rate-limits (HTTP 429); retry with backoff so a
    # transient limit doesn't blank out price/candles.
    last = None
    for i in range(_tries):
        req = urllib.request.Request(GT + path, headers={"accept": "application/json", "user-agent": "diplab/0.1"})
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            last = e
            if e.code == 429:
                time.sleep(1.2 * (i + 1))  # 1.2s, 2.4s, 3.6s...
                continue
            raise
        except Exception as e:
            last = e
            time.sleep(0.6 * (i + 1))
    if last:
        raise last
    return {}

def fetch_market(token):
    """Текущая цена в USD + свечи для графика (GeckoTerminal). Ошибки не фатальны."""
    try:
        pools = _gt(f"/tokens/{token}/pools").get("data", [])
        if not pools:
            return {}
        # перебрать пулы по убыванию ликвидности и взять ПЕРВЫЙ, у которого реально
        # есть свечи (у токена может быть много пулов против разных quote-токенов —
        # самый ликвидный обычно с графиком, но подстрахуемся перебором).
        def _liq(pl):
            try:
                return float(pl["attributes"].get("reserve_in_usd") or 0)
            except Exception:
                return 0.0
        pools.sort(key=_liq, reverse=True)

        def _ohlcv(pool_addr, res, lim):
            return _gt(f"/pools/{pool_addr}/ohlcv/{res}?limit={lim}").get("data", {}).get("attributes", {}).get("ohlcv_list", [])

        p = None; oc = []
        for cand in pools[:5]:                    # не больше 5 попыток
            addr = cand["attributes"]["address"]
            try:
                got = _ohlcv(addr, "minute", 120)
            except Exception:
                got = []
            if len(got) < 5:
                try:
                    got = _ohlcv(addr, "hour", 48) or got
                except Exception:
                    pass
            if len(got) >= 5:                     # нашли пул с графиком
                p = cand; oc = got; break
        if p is None:                             # ни у одного нет свечей — берём самый ликвидный как есть
            p = pools[0]; oc = []
        pool = p["attributes"]["address"]
        price = float(p["attributes"].get("base_token_price_usd") or 0)
        name = p["attributes"].get("name", "")
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
                "market_cap_usd": float(p["attributes"].get("market_cap_usd") or p["attributes"].get("fdv_usd") or 0),
                "volume_24h": float(vol.get("h24") or 0), "age_h": age_h}
    except Exception:
        return {}

CACHE = {}                 # token -> (ts, dict)  готовый результат
JOBS = {}                  # token -> {"status": "pending"|"done"|"error", "data"/"error", "ts"}
RECENT = []                # последние проверки: {token,pair,score,band,ts}
SCANNED_FILE = os.path.join(os.path.dirname(__file__), "scanned_count.txt")
_scanned_seen = set()      # уникальные токены за жизнь процесса

def _read_scanned():
    try:
        with open(SCANNED_FILE) as f:
            return int(f.read().strip() or 0)
    except Exception:
        return 0

def _bump_scanned(token):
    """Считаем уникальные токены. +1 к файлу только на первый анализ токена."""
    with _lock:
        if token in _scanned_seen:
            return
        _scanned_seen.add(token)
        n = _read_scanned() + 1
        try:
            with open(SCANNED_FILE, "w") as f:
                f.write(str(n))
        except Exception:
            pass
CACHE_TTL = 300            # 5 min
_lock = threading.Lock()
MAX_CONCURRENT = int(os.environ.get("DIPLAB_MAX_CONCURRENT", "2"))  # макс. одновременных чтений чейна -> держит пик RPC/s под лимитом Alchemy
_scan_sem = threading.BoundedSemaphore(MAX_CONCURRENT)
import concurrent.futures as _cf
ENGINE_VERSION = "v10-freenode-20260923"  # штамп версии: видно в /api/result
HARD_JOB_LIMIT = 55   # жёсткий потолок (сек) на ВЕСЬ скан. Дольше -> "busy", не виснем.

def run_cached(token):
    token = token.lower().strip()
    now = time.time()
    with _lock:
        hit = CACHE.get(token)
        if hit and now - hit[0] < CACHE_TTL:
            hit[1]["ver"] = ENGINE_VERSION
            return hit[1], True
    with _scan_sem:                      # <= MAX_CONCURRENT одновременно: пик RPC/s не пробивает лимит Alchemy
        now = time.time()
        with _lock:                      # пока ждали слот — токен мог уже закешироваться другим сканом
            hit = CACHE.get(token)
            if hit and now - hit[0] < CACHE_TTL:
                return hit[1], True
        res = analyze(token)
        d = asdict(res)
        d["levels"] = [asdict(l) for l in res.levels]
        # market с GeckoTerminal иногда сбоит (rate-limit/таймаут) и возвращает пусто.
        d["market"] = fetch_market(token)
        for _ in range(2):
            if d["market"].get("candles"):
                break
            time.sleep(1.0)
            d["market"] = fetch_market(token)
        market_ok = bool(d["market"].get("candles"))
        # стабильная глубина дипа: constant-product по реальной ликвидности пула
        mk = d["market"]; reserve = mk.get("reserve_usd") or 0; fdv = mk.get("fdv_usd") or 0
        if reserve > 0 and fdv > 0:
            for lv in d["levels"]:
                ratio = (lv["supply_pct"] / 100 * fdv) / (reserve / 2)
                lv["dip"] = 1 - (1 / (1 + ratio)) ** 2
        # DEPTH-компонент score по реальной ликвидности
        if reserve > 0 and d.get("score") is not None and not d.get("withheld"):
            depth_full = 15 * min(reserve / 50000, 1)
            sc = max(0, min(100, round(d["score"] - 7.5 + depth_full)))
            d["score"] = sc
            d["band"] = ("CLEAN" if sc >= 80 else "OK" if sc >= 60 else
                         "RISKY" if sc >= 40 else "DANGER")
        with _lock:
            if market_ok:
                CACHE[token] = (now, d)
            RECENT[:] = [r for r in RECENT if r["token"] != token]
            RECENT.insert(0, {"token": token, "pair": d.get("market", {}).get("pair"),
                              "score": d.get("score"), "band": d.get("band"), "ts": int(now)})
            del RECENT[30:]
    d["ver"] = ENGINE_VERSION
    _bump_scanned(token)
    return d, False

def _run_job(token):
    # ЖЁСТКИЙ ПОТОЛОК на весь скан: что бы движок ни делал (детект/цена/киты), сервер
    # обязан ответить за HARD_JOB_LIMIT сек. Затянулось (RPC перегружен) -> отдаём мягкий
    # "busy", а не вечную загрузку. Фоновый скан дочитается и закеширует сам, поэтому
    # повторный запрос обычно уже мгновенный из кэша.
    ex = _cf.ThreadPoolExecutor(max_workers=1)
    fut = ex.submit(run_cached, token)
    try:
        d, _ = fut.result(timeout=HARD_JOB_LIMIT)
        with _lock:
            JOBS[token] = {"status": "done", "data": d, "ts": time.time()}
    except _cf.TimeoutError:
        with _lock:
            JOBS[token] = {"status": "done", "data": {
                "token": token, "score": None, "band": None, "levels": [], "whales": [],
                "readable": 0, "holders_seen": 0, "withheld": False, "bundle_pct": 0.0,
                "top10_pct": 0.0, "exit_below_pct": 0.0, "market": {}, "ver": ENGINE_VERSION,
                "alert": "scan is busy (high RPC load) - try again in a few seconds"},
                "ts": time.time()}
    except Exception as e:
        with _lock:
            JOBS[token] = {"status": "error", "error": str(e), "ts": time.time()}
    finally:
        ex.shutdown(wait=False)


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
                return self._send(200, json.dumps({"recent": list(RECENT), "scanned": _read_scanned()}))
        if u.path == "/api/stats":
            return self._send(200, json.dumps({"scanned": _read_scanned()}))
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
        if u.path.startswith("/assets/"):
            # serve static assets (mascot, runner sprite, poses, video) safely
            rel = u.path.lstrip("/")
            base = os.path.dirname(os.path.abspath(__file__))
            fp = os.path.normpath(os.path.join(base, rel))
            if not fp.startswith(os.path.join(base, "assets")) or not os.path.isfile(fp):
                return self._send(404, json.dumps({"error": "not found"}))
            ext = os.path.splitext(fp)[1].lower()
            ctype = {
                ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".webp": "image/webp", ".mp4": "video/mp4", ".svg": "image/svg+xml",
                ".gif": "image/gif",
            }.get(ext, "application/octet-stream")
            with open(fp, "rb") as f:
                return self._send(200, f.read(), ctype)
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
