"""Минимальный ридер чейна Robinhood под DIPLAB.
Логика определения сделки (купля/продажа по ноге с роутером) — из radar.
Адаптивное окно getLogs (делим пополам при отказе) — из radar/GEMHOG.
Read-only: ни ключей, ни подписи, ни отправки транзакций.
"""
import os, time, json, random, urllib.request

RPC = os.environ["DIPLAB_RPC"]  # Alchemy PAYG endpoint

FACTORY  = "0x7ed598bcef8bd9edd8c97a195c6d13f40801ec7e"
ROUTERS  = {  # набор роутеров: получил токен ОТ роутера = купил, отдал роутеру = продал
    "0xb92fe925dc43a0ecde6c8b1a2709c170ec4fff4f",
    "0x65050a9b7e5075a2ba5ced7b1b64ee66262c40dc",
    "0x8876789976decbfcbbbe364623c63652db8c0904",
}
INFRA = {
    "0x000000000000000000000000000000000000dead",  # burn
    "0x8366a39cc670b4001a1121b8f6a443a643e40951",  # V4 Pool Manager
    "0xe5e702641ea86f4ae6cc3cdaed2b886f976be044",  # Pons V2 Hook
    "0xe33e9e479df8802cb0866d5d05258bec4cf62948",  # LaunchAndBuy
    "0x000000000022d473030f116ddee9f6b43ac78ba3",  # Permit2
    "0xe68d0bbc023de3febda04f413db23ce9c5ea1934",  # Gaslite drop
    "0x0bd7d308f8e1639fab988df18a8011f41eacad73",  # WETH
    "0x0000000000000000000000000000000000000000",
}
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
CURVE_BUY  = "0xec36bf571f136799e8dc0b0b8bea4b04d8bd3d43de838aab0d5fc21d4cbfc455"
CURVE_SELL = "0x8113d738abdcb6b38357e9d53a54a7157861a09031b453651f0fe7fe151f59df"

def u256(data, i):
    return int(data[2 + i*64 : 2 + (i+1)*64], 16)

def _is_rate_limited(body):
    """True, если ответ ноды = rate-limit / превышение пропускной способности."""
    def hit(err):
        if not isinstance(err, dict):
            return False
        code = err.get("code")
        msg = str(err.get("message", "")).lower()
        # ТОЛЬКО настоящие признаки rate-limit. НЕ матчим "exceeded"/"too many"/
        # "capacity" — они есть в ошибке размера окна, которую get_logs обрабатывает
        # разбивкой. Иначе ретрай зря крутит эти запросы по 5 раз -> скан висит.
        return code == 429 or any(k in msg for k in (
            "rate limit", "rate-limit", "per second", "too many requests"))
    if isinstance(body, dict) and "error" in body:
        return hit(body["error"])
    if isinstance(body, list):
        return any(isinstance(x, dict) and hit(x.get("error", {})) for x in body)
    return False

def _post(payload, _tries=5):
    """POST с ретраями и бэкоффом при rate-limit/сбое: один отбитый запрос
    во время нагрузки не должен ронять весь скан."""
    last = None
    for a in range(_tries):
        req = urllib.request.Request(RPC, data=json.dumps(payload).encode(),
                                     headers={"content-type": "application/json", "User-Agent": "Mozilla/5.0 DIPLAB"})
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                body = json.loads(r.read())
            if _is_rate_limited(body) and a < _tries - 1:
                last = body; time.sleep(0.35 * (2 ** a) + random.random() * 0.3); continue
            return body
        except urllib.error.HTTPError as e:
            try:
                body = json.loads(e.read())
            except Exception:
                body = None
            if (e.code == 429 or (body is not None and _is_rate_limited(body))) and a < _tries - 1:
                last = body if body is not None else e
                time.sleep(0.35 * (2 ** a) + random.random() * 0.3); continue
            if body is not None:
                return body
            raise
        except Exception as e:
            last = e
            if a < _tries - 1:
                time.sleep(0.35 * (2 ** a) + random.random() * 0.3); continue
            raise
    if isinstance(last, Exception):
        raise last
    return last if last is not None else {}

def rpc(method, params):
    d = _post({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    if "error" in d:
        raise RuntimeError(d["error"].get("message", str(d["error"])))
    return d["result"]

def rpc_batch(calls):
    """Много JSON-RPC вызовов ОДНИМ HTTP-запросом. calls=[(method,params),...].
    Возвращает список result в исходном порядке (None если ошибка на элементе)."""
    if not calls:
        return []
    payload = [{"jsonrpc": "2.0", "id": i, "method": m, "params": p}
               for i, (m, p) in enumerate(calls)]
    resp = _post(payload)
    if isinstance(resp, dict):
        resp = [resp]
    by_id = {r.get("id"): r for r in resp}
    return [by_id.get(i, {}).get("result") for i in range(len(calls))]

def block_number():
    return int(rpc("eth_blockNumber", []), 16)

def topic_for(address):
    return '0x'+'0'*24+address[2:].lower()

def addr_from_topic(t):  # 32-байтовый топик -> адрес
    return "0x" + t[-40:].lower()

def get_logs(from_block, to_block, address=None, topics=None):
    """getLogs с адаптивным окном: при 'range too large' делим пополам."""
    flt = {"fromBlock": hex(from_block), "toBlock": hex(to_block)}
    if address: flt["address"] = address
    if topics:  flt["topics"] = topics
    try:
        return rpc("eth_getLogs", [flt])
    except RuntimeError as e:
        msg = str(e).lower()
        if any(k in msg for k in ("range","too large","too many","limited","response","size","exceed","more than")) and to_block > from_block:
            mid = (from_block + to_block) // 2
            return (get_logs(from_block, mid, address, topics)
                    + get_logs(mid + 1, to_block, address, topics))
        raise

def parse_transfer(log):
    tp = log.get("topics") or []
    if len(tp) < 3 or tp[0].lower() != TRANSFER_TOPIC:
        return None
    return {"token": log["address"].lower(),
            "frm": addr_from_topic(tp[1]),
            "to":  addr_from_topic(tp[2]),
            "tx":  log["transactionHash"],
            "block": int(log["blockNumber"], 16)}

def token_curve(token, lookback=6000):
    """Адрес бондинг-кривой токена из его события запуска (topic[2])."""
    bn = block_number()
    for lg in get_logs(bn - lookback, bn, address=FACTORY):
        tp = lg.get("topics") or []
        if len(tp) >= 3 and addr_from_topic(tp[1]) == token.lower():
            return addr_from_topic(tp[2])
    return None

def classify_leg(t, market):
    """Купля/продажа по ноге с рынком токена (его кривая + глобальные роутеры).
    Грабли №1: подписант всегда релеер, поэтому смотрим движение токена, не from tx."""
    if t["frm"] in market and t["to"] not in market:
        return ("buy", t["to"])     # токен пришёл трейдеру с рынка
    if t["to"] in market and t["frm"] not in market:
        return ("sell", t["frm"])   # трейдер отдал токен на рынок
    return (None, None)


V4_SWAP_TOPIC = "0x40e9cecb9f5f1f1c5b9c97dec2917b7ee92e57ba5563708daca94dd84ad7112f"
V4_POOL_MGR   = "0x8366a39cc670b4001a1121b8f6a443a643e40951"
WETH_ADDR     = "0x0bd7d308f8e1639fab988df18a8011f41eacad73"
def _signed(x): return x-(1<<256) if x>=(1<<255) else x

def match_swap_quote(receipt_logs, tok_amount):
    """Из логов транзакции найти V4-своп, чья сторона токена ~= tok_amount,
    и вернуть котировочную сторону (raw) этого свопа. Так выбирается нужный пул
    и определяется, какая из amount0/amount1 — токен, а какая — котировка.
    Возвращает 0, если совпадения нет (не торговый перевод)."""
    if tok_amount<=0: return 0
    best=None; best_err=0.06  # допускаем 6% (комиссии/налог)
    for l in receipt_logs:
        if l["address"].lower()!=V4_POOL_MGR: continue
        tp=l.get("topics") or []
        if not tp or tp[0].lower()!=V4_SWAP_TOPIC: continue
        a0=abs(_signed(u256(l["data"],0))); a1=abs(_signed(u256(l["data"],1)))
        for tok_side,quote_side in ((a0,a1),(a1,a0)):
            if tok_side<=0: continue
            err=abs(tok_side-tok_amount)/tok_amount
            if err<best_err: best_err=err; best=quote_side
    if best:
        return best
    # FALLBACK: пул, чьё событие свопа мы не декодим (не наш pool manager / другой AMM).
    # Сторона котировки = крупнейший перевод WETH в этой же транзакции. DEX-независимо,
    # поэтому цена читается и на незнакомых пулах, а не падает в None.
    weth = [int(l["data"], 16) for l in receipt_logs
            if (p := parse_transfer(l)) and p["token"] == WETH_ADDR]
    weth = [a for a in weth if a > 0]
    return max(weth) if weth else 0
