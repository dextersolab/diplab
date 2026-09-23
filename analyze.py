"""DIPLAB — карта уровней выхода по токену Robinhood Chain (Pons V2).

Один вход: адрес токена. Один ответ: где стены (киты выйдут) и где под ними дипы.
Оба слоя: бондинг-кривая и мигрировавший V4-пул (детектор внутри).

Read-only: только чтение чейна, ни ключей, ни подписи, ни отправки транзакций.
RPC берётся из окружения DIPLAB_RPC — в код не пишется (чтобы не утёк при заливке).
"""
from __future__ import annotations
from collections import defaultdict
from statistics import median
from dataclasses import dataclass, field
import math
import json
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as _CFTimeout
import chain as ch

WINDOW = 3_000_000
MIN_POSITIONS = 5
BUNDLE_ALERT_PCT = 10.0
GROUP_WIDTH = 1.25
TOP_HOLDERS = 10
MAX_TRADES = 20      # последних сделок на кошелёк — шире сэмпл, ловим завершённые позиции у активных ветеранов
TARGET_READABLE = 6
MIN_READABLE_FOR_SCORE = 3   # меньше стольких прочитанных китов -> НЕ выставляем скор (тонкий рид = "no read", не ложное CLEAN)  # добираем холдеров, пока не наберём столько читаемых
MAX_SCAN = 12        # но не сканируем больше стольких холдеров
SCAN_BUDGET = 22     # жёсткий потолок времени (сек) на всё чтение китов
PHASE2_BUDGET = 12   # из них максимум столько на топ-10 (gate свежести)
WORKERS = 20
FRESH_MAX_TOKENS = 3     # wallet that has ever traded <=3 distinct coins counts as "fresh"
DANGER_SCORE = 5         # score for withheld / not-analysable tokens (bundled or sybil cluster)
CURVE_COMPLETED = "0xf8d37a90738ae063b8b8058b66f5880cf3cf7ab0c5d4fa78219696591dfbfb67"
Q96 = 2 ** 96


def _signed(x):
    return x - (1 << 256) if x >= (1 << 255) else x


def _tail_logs(bn, topics, need=MAX_TRADES, floor_blocks=WINDOW, probe=3_000, address=None):
    """Последние ~need логов по фильтру. ПРОБА-ПОТОМ-ПРЫЖОК:
    1) пробуем крохотное окно в конце — у мега-активного кошелька (тысячи переводов)
       оно уже содержит >need сделок, берём и выходим мгновенно;
    2) если мало — берём весь остаток окна ОДНИМ запросом (у редкого кошелька логов
       там мало -> быстро). Быстро и для гигантов, и для редких — без хождения по
       десяткам пустых окон."""
    hi = bn; floor = bn - floor_blocks
    lo1 = max(hi - probe, floor)
    head = ch.get_logs(lo1, hi, address=address, topics=topics)
    if len(head) >= need:
        return head[-need:]
    rest = ch.get_logs(floor, lo1 - 1, address=address, topics=topics) if lo1 > floor else []
    return (rest + head)[-need:]


WETH_ADDR = "0x0bd7d308f8e1639fab988df18a8011f41eacad73"

import time as _time
_HABIT_CACHE = {}          # wallet -> (ts, median, n); привычка живёт час, кошельки повторяются
_HABIT_TTL = 3600

def _pool_state(bn, token, lblock):
    """(sqrtPriceX96, L, token_is_currency0) для мигрировавшего пула.
    Сторона токена детерминированно: currency0 < currency1 по адресу (пара token/WETH)."""
    token_is_0 = int(token, 16) < int(WETH_ADDR, 16)
    for lg in ch.get_logs(bn - 4000, bn, address=token)[::-1]:
        if not ch.parse_transfer(lg):
            continue
        rc = ch.rpc("eth_getTransactionReceipt", [lg["transactionHash"]])
        for l in rc["logs"]:
            if (l["address"].lower() == ch.V4_POOL_MGR and l.get("topics")
                    and l["topics"][0].lower() == ch.V4_SWAP_TOPIC):
                d = l["data"]
                return ch.u256(d, 2), ch.u256(d, 3), token_is_0
    return None, None, None


def _dip(sqrtP, L, token_is_0, dy_raw):
    """Механическая глубина: продажа dy токенов в пул -> доля падения цены.
    Нижняя граница (чистая AMM-механика); паника на рынке углубляет."""
    if not L or L <= 0:
        return None
    if token_is_0:
        denom = L + dy_raw * sqrtP // Q96
        sp2 = L * sqrtP // denom if denom > 0 else 0
    else:
        sp2 = sqrtP - dy_raw * Q96 // L
    if sp2 <= 0:
        return 0.99
    r = (sp2 / sqrtP) ** 2
    return max(0.0, min(0.99, 1 - (1 / r if token_is_0 else r)))


@dataclass
class Level:
    rel: float; supply_pct: float; wallets: int; dip: float | None = None

@dataclass
class Result:
    token: str; layer: str; migrated: bool; bundle_pct: float
    alert: str | None = None
    levels: list = field(default_factory=list)
    readable: int = 0
    holders_seen: int = 0
    top10_pct: float = 0.0
    exit_below_pct: float = 0.0
    score: int | None = None
    band: str | None = None
    whales: list = field(default_factory=list)
    withheld: bool = False


def _find_launch(bn, token):
    for lg in ch.get_logs(bn - 2_500_000, bn, address=ch.FACTORY, topics=[None, ch.topic_for(token)]):
        tp = lg["topics"]
        if len(tp) >= 3 and ch.addr_from_topic(tp[1]) == token:
            return ch.addr_from_topic(tp[2]), int(lg["blockNumber"], 16)
    return None, None


def _holders(bn, token, curve, lblock):
    """Топ-холдеры по балансу. Быстрый путь — Pons Portal API (готовый список,
    отсортирован); откат — реконструкция из всех переводов (медленно, но надёжно)."""
    skip = ch.INFRA | {curve} | ch.ROUTERS | {ch.V4_POOL_MGR}
    # быстрый путь: холдер-API
    try:
        import urllib.request
        req = urllib.request.Request(
            f"https://api.ponsportal.fun/token/{token}/holders?limit=50",
            headers={"accept": "application/json", "user-agent": "diplab/0.1"})
        with urllib.request.urlopen(req, timeout=12) as r:
            data = json.loads(r.read())
        if data.get("ok") and data.get("holders"):
            hs = [(h["address"].lower(), int(h["balance"])) for h in data["holders"]
                  if h["address"].lower() not in skip and int(h.get("balance", 0)) > 0]
            if hs:
                return hs                         # уже отсортированы по балансу
    except Exception:
        pass
    # откат: реконструкция из переводов
    bal = defaultdict(int)
    for lg in ch.get_logs(lblock, bn, address=token):
        t = ch.parse_transfer(lg)
        if t:
            v = int(lg["data"], 16); bal[t["frm"]] -= v; bal[t["to"]] += v
    return sorted(((a, v) for a, v in bal.items() if v > 0 and a not in skip), key=lambda x: -x[1])


def _buys_of_token(bn, token, curve, lblock, holder):
    """(weth_per_token_entry, is_buyer) — покупки холдером этого токена, чеки батчем."""
    wt = ch.topic_for(holder)
    logs = _tail_logs(bn, [ch.TRANSFER_TOPIC, None, wt], MAX_TRADES, address=token)
    txs = list({lg["transactionHash"] for lg in logs})
    receipts = {}
    for i in range(0, len(txs), 100):
        chunk = txs[i:i + 100]
        for tx, rc in zip(chunk, ch.rpc_batch([("eth_getTransactionReceipt", [t]) for t in chunk])):
            receipts[tx] = rc
    tok = q = 0; is_buyer = False
    for tx, rc in receipts.items():
        if not rc:
            continue
        ti = 0; qi = 0; cbuy = False
        for l in rc["logs"]:
            t0 = (l["topics"][0].lower() if l["topics"] else "")
            if t0 == ch.CURVE_BUY:
                cbuy = True; qi += ch.u256(l["data"], 0)
            p = ch.parse_transfer(l)
            if p and p["token"] == token and p["to"] == holder:
                ti += int(l["data"], 16)
        if ti <= 0:
            continue
        qv = ch.match_swap_quote(rc["logs"], ti)
        if qv > 0:  is_buyer = True; tok += ti; q += qv
        elif cbuy:  is_buyer = True; tok += ti; q += qi
    price = (q / (tok / 1e18)) if tok > 0 else None
    return price, is_buyer




def _current_price(bn, token, curve, lblock):
    """Текущая цена: WETH_raw за 1 токен, по медиане свежих сделок.
    Шагаем окнами С КОНЦА и расширяемся, пока не наберём хватает свопов: активный
    токен наберёт 12 в первом же узком окне (быстро), а тихий (мало торговался
    недавно) — не отвалится с None, а найдёт цену чуть глубже в истории."""
    prices = []; checked = 0
    hi = bn; step = 60_000; floor = bn - 400_000
    while hi > floor and len(prices) < 6 and checked < 40:
        lo = max(hi - step, floor)
        txs = []
        for lg in ch.get_logs(lo, hi, address=token):
            if ch.parse_transfer(lg):
                txs.append(lg["transactionHash"])
        txs = list(dict.fromkeys(txs))[: max(0, 40 - checked)]   # чеки одним батчем, с потолком
        if txs:
            for rc in ch.rpc_batch([("eth_getTransactionReceipt", [t]) for t in txs]):
                checked += 1
                if not rc:
                    continue
                moved = sum(int(l["data"], 16) for l in rc["logs"]
                            if (p := ch.parse_transfer(l)) and p["token"] == token)
                q = ch.match_swap_quote(rc["logs"], moved)
                if q > 0 and moved > 0:
                    prices.append(q / (moved / 1e18))
                if len(prices) >= 6:
                    break
        hi = lo - 1; step = min(step * 2, 300_000)
    return median(prices) if prices else None


def _habit(bn, holder, skip_token):
    now = _time.time()
    hit = _HABIT_CACHE.get(holder)
    if hit and now - hit[0] < _HABIT_TTL:
        return hit[1], hit[2], hit[3]
    med, n, traded = _habit_compute(bn, holder, skip_token)
    _HABIT_CACHE[holder] = (now, med, n, traded)
    return med, n, traded


def _habit_compute(bn, holder, skip_token):
    wt = ch.topic_for(holder); frm = bn - WINDOW
    pos = defaultdict(lambda: {"qi": 0.0, "to": 0, "ti": 0, "qo": 0.0})
    # --- кривая: один get_logs на роль по всему окну. На мигрировавших токенах у
    # холдера тут пусто -> запрос возвращается мгновенно (одним вызовом). Прежняя
    # попытка читать "хвостом" на пустой кривой расширяла окно и делала десятки
    # пустых get_logs на каждого холдера -> сотни лишних запросов и тормоза. ---
    for role in ([ch.CURVE_BUY, wt], [ch.CURVE_BUY, None, wt]):
        for lg in ch.get_logs(frm, bn, topics=role):
            d = lg["data"]; k = ("c", lg["address"].lower())
            pos[k]["qi"] += ch.u256(d, 0); pos[k]["to"] += ch.u256(d, 1)
    for role in ([ch.CURVE_SELL, wt], [ch.CURVE_SELL, None, wt]):
        for lg in ch.get_logs(frm, bn, topics=role):
            d = lg["data"]; k = ("c", lg["address"].lower())
            pos[k]["ti"] += ch.u256(d, 0); pos[k]["qo"] += ch.u256(d, 1)
    # --- V4: последние MAX_TRADES переводов, чеки одним батчем ---
    # tail_logs берёт хвост окнами с конца (иначе для кошелька-гиганта get_logs
    # тащил бы миллионы логов только ради последних MAX_TRADES).
    tin = _tail_logs(bn, [ch.TRANSFER_TOPIC, None, wt], MAX_TRADES)
    tout = _tail_logs(bn, [ch.TRANSFER_TOPIC, wt, None], MAX_TRADES)
    items = [(lg["address"].lower(), lg["transactionHash"], "in", int(lg["data"], 16)) for lg in tin if len(lg["data"]) > 2]
    items += [(lg["address"].lower(), lg["transactionHash"], "out", int(lg["data"], 16)) for lg in tout if len(lg["data"]) > 2]
    items = [it for it in items if it[0] != skip_token]
    txs = list({it[1] for it in items})
    receipts = {}
    for i in range(0, len(txs), 100):
        chunk = txs[i:i + 100]
        for tx, rc in zip(chunk, ch.rpc_batch([("eth_getTransactionReceipt", [t]) for t in chunk])):
            receipts[tx] = rc
    seen = set()
    for tok, tx, dr, amt in items:
        if (tx, tok, dr) in seen:
            continue
        seen.add((tx, tok, dr))
        rc = receipts.get(tx)
        if not rc:
            continue
        q = ch.match_swap_quote(rc["logs"], amt)
        if q <= 0:
            continue
        k = ("v", tok)
        if dr == "in": pos[k]["qi"] += q; pos[k]["to"] += amt
        else:          pos[k]["qo"] += q; pos[k]["ti"] += amt
    m = [p["qo"] / p["qi"] for p in pos.values()
         if p["to"] > 0 and p["qi"] > 0 and p["ti"] / p["to"] >= 0.9]
    traded = len({k[1] for k in pos})   # distinct token contracts this wallet actually traded
    return (median(m) if m else None), len(m), traded




def _read_before(items, fn, deadline):
    """Гоняем fn(item) параллельно, ЗАБИРАЕМ только тех, кто успел до deadline.
    Зависших не ждём (дочитаются в фоне, но скан не блокируется). Кит, медленный
    в чтении = активный трейдер с огромной историей -> не свежий и не критичен для
    карты, так что бросить его безопасно. Это делает скан неубиваемым по времени."""
    ex = ThreadPoolExecutor(max_workers=WORKERS)
    futs = [ex.submit(fn, it) for it in items]
    out = []
    try:
        for f in as_completed(futs, timeout=max(0.05, deadline - _time.time())):
            try:
                r = f.result()
                if r is not None:
                    out.append(r)
            except Exception:
                pass
    except _CFTimeout:
        pass
    try:
        ex.shutdown(wait=False, cancel_futures=True)
    except TypeError:
        ex.shutdown(wait=False)
    return out


def analyze(token: str) -> Result:
    token = token.lower(); bn = ch.block_number()
    curve, lblock = _find_launch(bn, token)
    fallback = curve is None
    if fallback:
        lblock = bn - WINDOW
        migrated = False
    else:
        migrated = bool(ch.get_logs(lblock, bn, address=curve, topics=[CURVE_COMPLETED]))
    TS = int(ch.rpc("eth_call", [{"to": token, "data": "0x18160ddd"}, "latest"]), 16)
    all_holders = _holders(bn, token, curve, lblock)[:MAX_SCAN]
    if not all_holders:
        return Result(token, "unknown", False, 0.0,
                      alert="no holders readable - token not indexed and no transfers found in window")
    layer = "unknown" if fallback else ("v4" if migrated else "curve")

    top = all_holders[:TOP_HOLDERS]

    # --- PHASE 1 (cheap): buyer-detection on the top holders -> bundle share. ---
    def buyer_check(item):
        a, v = item
        ep, is_buyer = _buys_of_token(bn, token, curve, lblock, a)
        return {"addr": a, "sp": v / TS * 100, "buyer": is_buyer, "ep": ep}
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        top_prof = list(ex.map(buyer_check, top))
    n_top = len(top_prof)
    bundle = round(sum(p["sp"] for p in top_prof if not p["buyer"]), 2)
    res = Result(token, layer, migrated, bundle, readable=0, holders_seen=n_top)

    # GATE 1 - bundled supply: not analysable. bail BEFORE any cross-token history
    #          reads, which is what keeps bundled tokens fast.
    if bundle >= BUNDLE_ALERT_PCT:
        res.withheld = True
        res.score, res.band = DANGER_SCORE, "DANGER"
        res.alert = (f"{bundle:.1f}% of supply is held by bundler wallets among the top holders - "
                     "not analysable, high rug risk. exit forecast withheld.")
        return res

    # --- PHASE 2: cross-token history of the top holders -> exit habit + how many
    #     distinct coins each has ever traded (only runs when GATE 1 passed). ---
    _t0 = _time.time()                       # старт time-бюджета на чтение китов
    def enrich(p):
        med, n, traded = _habit(bn, p["addr"], token)
        p["med"] = med; p["n"] = n; p["traded"] = traded
        return p
    top_prof = _read_before(top_prof, enrich, _t0 + PHASE2_BUDGET)   # не ждём зависших китов
    fresh = sum(1 for p in top_prof if p["traded"] <= FRESH_MAX_TOKENS)
    fresh_need = math.ceil(0.8 * n_top)       # 8 of 10; scales down for tokens with fewer holders

    # GATE 2 - cluster of fresh wallets (one operator / sybil): not analysable.
    if n_top >= 5 and fresh >= fresh_need:
        res.withheld = True
        res.score, res.band = DANGER_SCORE, "DANGER"
        res.alert = (f"{fresh} of {n_top} top holders are fresh wallets that have only ever traded "
                     f"{FRESH_MAX_TOKENS} coins or fewer - likely one operator / sybil cluster, "
                     "high rug risk. exit forecast withheld.")
        return res

    # --- no gate: build the exit map. only now (past the gates) do we need the live
    #     price - withheld tokens above never pay for this read. ---
    cur = _current_price(bn, token, curve, lblock)
    proj = []; whales_acc = []; readable = 0; scanned = n_top

    def scan_rest(item):
        a, v = item
        ep, is_buyer = _buys_of_token(bn, token, curve, lblock, a)
        if not is_buyer:
            return None
        med, n, traded = _habit(bn, a, token)
        return {"addr": a, "sp": v / TS * 100, "buyer": True, "ep": ep, "med": med, "n": n}

    def consume(prof):
        nonlocal readable
        if not prof or not prof.get("buyer"):
            return
        if prof.get("med") is None or prof.get("n", 0) < MIN_POSITIONS or prof.get("ep") is None or cur is None:
            return
        rel = prof["ep"] * prof["med"] / cur
        proj.append((rel, prof["sp"])); readable += 1
        whales_acc.append({"addr": prof["addr"], "mult": round(prof["med"], 2), "n": prof["n"],
                           "supply_pct": round(prof["sp"], 1), "exit_rel": round(rel, 2)})

    for p in top_prof:
        consume(p)

    # добираем остальных китов, пока не наберём достаточно ИЛИ не выйдет общий бюджет.
    # ни один медленный кит не может подвесить скан — дедлайн его отсечёт.
    rest = all_holders[n_top:]
    if rest and readable < TARGET_READABLE and _time.time() < _t0 + SCAN_BUDGET:
        for prof in _read_before(rest, scan_rest, _t0 + SCAN_BUDGET):
            consume(prof)
        scanned = n_top + len(rest)

    res.readable = readable; res.holders_seen = scanned; res.whales = whales_acc
    # THIN / FAILED READ GUARD: нет живой цены ИЛИ прочитано слишком мало китов ->
    # честно оценить токен нельзя. Оставляем БЕЗ скора, фронт покажет "no read",
    # а не ложно-чистое число (штраф EXIT срабатывает только на прочитанных китах).
    if cur is None or readable < MIN_READABLE_FOR_SCORE:
        res.whales = []
        res.score = None; res.band = None
        return res
    if fallback:
        res.alert = "read without Pons launch context - holders via portal, entry/migration data limited"

    proj.sort()
    for rel, sh in proj:
        if res.levels and rel <= res.levels[-1].rel * GROUP_WIDTH:
            L = res.levels[-1]
            L.rel = (L.rel * L.wallets + rel) / (L.wallets + 1); L.supply_pct += sh; L.wallets += 1
        else:
            res.levels.append(Level(rel, sh, 1))

    top10 = sum(v for _, v in all_holders[:10]) / TS * 100 if TS else 0.0
    res.top10_pct = round(top10, 1)
    below = sum(sh for rel, sh in proj if rel < 1.0)
    res.exit_below_pct = round(below, 1)
    res.score, res.band = _score(res, proj)
    return res

def _score(res, proj):
    """DIPLAB score 0-100 (100=чисто). EXIT(40)+BUNDLE(25)+CONCENTRATION(20)+DEPTH(15).
    DEPTH-часть (ликвидность/возраст) добавляет сервис из Gecko; тут ядро без него."""
    # EXIT (40): штраф за сапплай, выходящий ниже цены (усилен, если привычка <1)
    below_pen = min(res.exit_below_pct / 15.0, 1.0)      # 15%+ сапплая ниже -> максимум
    exit_c = 40 * (1 - below_pen)
    # BUNDLE (25): штраф за бандл-сапплай
    bundle_pen = min(res.bundle_pct / 20.0, 1.0)         # 20%+ бандла -> максимум
    bundle_c = 25 * (1 - bundle_pen)
    # CONCENTRATION (20): штраф за концентрацию топ-10
    conc_pen = min(max(res.top10_pct - 20, 0) / 50.0, 1.0)  # 20% ок, 70%+ -> максимум
    conc_c = 20 * (1 - conc_pen)
    # DEPTH (15) — сервис досчитает по ликвидности; пока даём половину как базу
    depth_c = 7.5
    score = round(exit_c + bundle_c + conc_c + depth_c)
    band = ("CLEAN" if score >= 80 else "OK" if score >= 60 else
            "RISKY" if score >= 40 else "DANGER")
    return score, band
