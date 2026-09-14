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
import json
from concurrent.futures import ThreadPoolExecutor
import chain as ch

WINDOW = 3_000_000
MIN_POSITIONS = 5
BUNDLE_ALERT_PCT = 10.0
GROUP_WIDTH = 1.25
TOP_HOLDERS = 10
MAX_TRADES = 60      # последних сделок на кошелёк — шире сэмпл, ловим завершённые позиции у активных ветеранов
TARGET_READABLE = 8  # добираем холдеров, пока не наберём столько читаемых
MAX_SCAN = 30        # но не сканируем больше стольких холдеров
WORKERS = 20
CURVE_COMPLETED = "0xf8d37a90738ae063b8b8058b66f5880cf3cf7ab0c5d4fa78219696591dfbfb67"
Q96 = 2 ** 96


def _signed(x):
    return x - (1 << 256) if x >= (1 << 255) else x


def _tail_logs(bn, topics, need=MAX_TRADES, floor_blocks=WINDOW, step=60_000):
    """Последние ~need логов по фильтру, идём окнами С КОНЦА. Не тащим всю историю:
    для кошелька-гиганта (миллионы переводов) get_logs за всё окно возвращал бы
    миллионы логов только чтобы взять последние 30 — вместо этого сканируем узкими
    окнами от текущего блока назад и останавливаемся, как только набрали need."""
    out = []; hi = bn; floor = bn - floor_blocks; st = step
    while hi > floor:
        lo = max(hi - st, floor)
        chunk = ch.get_logs(lo, hi, topics=topics)
        out = chunk + out                       # более старые логи идут впереди
        if len(out) >= need:
            break
        hi = lo - 1; st = min(st * 2, 500_000)  # окно пустое/редкое — расширяем шаг
    return out[-need:]


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
    logs = ch.get_logs(lblock, bn, address=token, topics=[ch.TRANSFER_TOPIC, None, wt])
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
    prices = []
    hi = bn; step = 60_000; floor = bn - 800_000
    while hi > floor and len(prices) < 12:
        lo = max(hi - step, floor)
        for lg in ch.get_logs(lo, hi, address=token):
            t = ch.parse_transfer(lg)
            if not t:
                continue
            rc = ch.rpc("eth_getTransactionReceipt", [lg["transactionHash"]])
            moved = sum(int(l["data"], 16) for l in rc["logs"]
                        if (p := ch.parse_transfer(l)) and p["token"] == token)
            q = ch.match_swap_quote(rc["logs"], moved)
            if q > 0 and moved > 0:
                prices.append(q / (moved / 1e18))
            if len(prices) >= 12:
                break
        hi = lo - 1; step = min(step * 2, 300_000)   # окно пустое — шагаем шире
    return median(prices) if prices else None


def _habit(bn, holder, skip_token):
    now = _time.time()
    hit = _HABIT_CACHE.get(holder)
    if hit and now - hit[0] < _HABIT_TTL:
        return hit[1], hit[2]
    med, n = _habit_compute(bn, holder, skip_token)
    _HABIT_CACHE[holder] = (now, med, n)
    return med, n


def _habit_compute(bn, holder, skip_token):
    wt = ch.topic_for(holder); frm = bn - WINDOW
    pos = defaultdict(lambda: {"qi": 0.0, "to": 0, "ti": 0, "qo": 0.0})
    # --- кривая (дёшево, без чеков) ---
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
    return (median(m) if m else None), len(m)




def analyze(token: str) -> Result:
    token = token.lower(); bn = ch.block_number()
    curve, lblock = _find_launch(bn, token)
    fallback = curve is None
    if fallback:
        # Нет Pons-launch в окне (не Pons-кривая, другой quote, или старше окна).
        # НЕ сдаёмся: холдеры берутся из Pons Portal API (launch не нужен), а привычки
        # китов — из кросс-токенной истории самих кошельков. Теряем только точный
        # блок запуска (берём окно WINDOW) и статус миграции/адрес кривой.
        lblock = bn - WINDOW
        migrated = False
    else:
        migrated = bool(ch.get_logs(lblock, bn, address=curve, topics=[CURVE_COMPLETED]))
    TS = int(ch.rpc("eth_call", [{"to": token, "data": "0x18160ddd"}, "latest"]), 16)
    all_holders = _holders(bn, token, curve, lblock)[:MAX_SCAN]
    if not all_holders:
        # реально нечего читать: и API пуст, и переводов в окне нет
        return Result(token, "unknown", False, 0.0,
                      alert="no holders readable — token not indexed and no transfers found in window")
    cur = _current_price(bn, token, curve, lblock)

    def work(item):
        a, v = item
        ep, is_buyer = _buys_of_token(bn, token, curve, lblock, a)
        if not is_buyer:
            return ("bundle", v / TS * 100, None)
        med, n = _habit(bn, a, token)
        if med is None or n < MIN_POSITIONS or ep is None or cur is None:
            return ("skip", 0, None)
        return ("level", (ep * med / cur, v / TS * 100), {"addr": a, "mult": round(med,2), "n": n, "supply_pct": round(v/TS*100,1), "exit_rel": round(ep*med/cur,2)})

    # сканируем холдеров пачками по убыванию доли, добирая до TARGET_READABLE читаемых
    bundle = 0.0; proj = []; readable = 0; scanned = 0; whales_acc = []
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        while scanned < len(all_holders) and readable < TARGET_READABLE:
            batch = all_holders[scanned:scanned + WORKERS]
            idx0 = scanned
            scanned += len(batch)
            for j, res_tuple in enumerate(ex.map(work, batch)):
                kind = res_tuple[0]; payload = res_tuple[1]
                is_top = (idx0 + j) < TOP_HOLDERS
                if kind == "bundle" and is_top: bundle += payload
                elif kind == "level":
                    proj.append(payload); readable += 1
                    whales_acc.append(res_tuple[2])

    layer = "unknown" if fallback else ("v4" if migrated else "curve")
    res = Result(token, layer, migrated, round(bundle, 2),
                 readable=readable, holders_seen=scanned)
    res.whales = whales_acc
    if fallback:
        # мягкая пометка: читали без launch-контекста (не блокирует дашборд)
        res.alert = "read without Pons launch context — holders via portal, entry/migration data limited"
    if bundle >= BUNDLE_ALERT_PCT:
        res.alert = f"{bundle:.1f}% of supply held by bundlers among top holders — exit forecast withheld, high risk"
        return res
    proj.sort()
    for rel, sh in proj:
        if res.levels and rel <= res.levels[-1].rel * GROUP_WIDTH:
            L = res.levels[-1]
            L.rel = (L.rel * L.wallets + rel) / (L.wallets + 1); L.supply_pct += sh; L.wallets += 1
        else:
            res.levels.append(Level(rel, sh, 1))

    # концентрация топ-10
    top10 = sum(v for _, v in all_holders[:10]) / TS * 100 if TS else 0.0
    res.top10_pct = round(top10, 1)
    # давление вниз: % сапплая китов, выходящих ниже текущей цены
    below = sum(sh for rel, sh in proj if rel < 1.0)
    res.exit_below_pct = round(below, 1)
    res.score, res.band = _score(res, proj)
    # глубина дипа считается в сервисе из ликвидности пула (GeckoTerminal, стабильно)
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
