"""Taxa de desqualificação por anúncio (leads que mentiram no formulário).

Lê a aba Leads (mesmo CSV do build), cruza com analises/desqualificados_hash.json
(sha256 de telefone canônico e e-mail — sem dados pessoais no repo) e imprime,
por anúncio, leads totais × desqualificados no recorte INI -> hoje (BRT).
"""
import hashlib, json, os, sys, collections
from datetime import datetime
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "build"))
import build as b

INI = os.environ.get("INI", "2026-10-19")
FIM = os.environ.get("FIM", datetime.now(b.BRT).strftime("%Y-%m-%d"))
MIN_AMOSTRA = 30
h = lambda s: hashlib.sha256(s.encode()).hexdigest()

bad = json.load(open(os.path.join(os.path.dirname(__file__), "desqualificados_hash.json")))
bad_ph = {x["ph"] for x in bad if x["ph"]}
bad_em = {x["em"] for x in bad}

rows = b.load_rows(b.EXPORT_URL.format(sid=b.SPREADSHEET_ID, gid=b.GID_CONVERSAS), None)
hd = rows[0]
ix = b.header_index(hd, {"d": ["data", "created", "criado"], "ph": b.PHONE_ALIASES, "em": ["e-mail", "email", "mail"],
    "camp": ["campanha"], "adset": ["conjunto"], "ad": ["anuncio", "utm_content", "ad name"]}, {})
print("colunas:", b.describe_idx(hd, ix))
print(f"Recorte: {INI} -> {FIM}")

tot, dq = collections.Counter(), collections.Counter()
found_ph, found_em, per_day = set(), set(), collections.Counter()
for r in rows[1:]:
    if not any((c or "").strip() for c in r) or b.is_test_lead(" ".join(map(str, r))):
        continue
    d = b.parse_date(b.cell(r, ix["d"]))
    if not d or not (INI <= d <= FIM):
        continue
    camp = b.cell(r, ix["camp"])
    key = (b.cell(r, ix["ad"]) or "(sem anúncio)", b.cell(r, ix["adset"]) or "(sem conjunto)") if b.valid_utm(camp) else ("(sem campanha/orgânico)", "")
    tot[key] += 1
    ph = h(b.canon_phone(b.cell(r, ix["ph"]))) if b.cell(r, ix["ph"]) else None
    em = h(b.cell(r, ix["em"]).lower()) if b.cell(r, ix["em"]) else None
    if (ph and ph in bad_ph) or (em and em in bad_em):
        dq[key] += 1
        if ph in bad_ph: found_ph.add(ph)
        if em in bad_em: found_em.add(em)

T, D = sum(tot.values()), sum(dq.values())
print(f"\nLeads BFR no período: {T} | linhas casadas com a lista: {D} de {len(bad)} informados")
print(f"Taxa geral de desqualificação: {D/T:.2%}\n" if T else "")
print(f"{'Anúncio':60} {'Leads':>6} {'Desq':>5} {'Taxa':>7}  {'%dos desq':>9}  obs")
for k, n in sorted(tot.items(), key=lambda kv: (-dq[kv[0]], -kv[1])):
    if dq[k] or n >= MIN_AMOSTRA:
        obs = "amostra pequena" if n < MIN_AMOSTRA else ""
        print(f"{k[0][:60]:60} {n:>6} {dq[k]:>5} {dq[k]/n:>7.1%}  {dq[k]/D if D else 0:>9.1%}  {obs}")
print("\nConjunto dos anúncios com desqualificados:")
for k in sorted((k for k in dq), key=lambda k: -dq[k]):
    print(f"  {k[0][:50]} | {k[1][:70]} | {dq[k]}/{tot[k]}")
