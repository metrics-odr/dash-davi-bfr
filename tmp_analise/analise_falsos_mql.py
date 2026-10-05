"""Analise pontual (temporaria): anuncios de origem dos leads sinalizados como falso MQL.
Os e-mails entram apenas como SHA-256; o log mostra e-mails mascarados."""
import hashlib, sys, collections
sys.path.insert(0, "build")
import build as b

HASHES = {
 "a19ba40014f9fd0cd79c23ade7bbc9d7c438720bd9e8508432f349f2ee62038f","e2063f4492f10b0feacd8eb0a55fe77341d5166d16bd8ab9df7bb3b17bd17c54",
 "6c82719f4e20fcea3874760bc0797e8382e13c04be4a8475fd5dd5d6c718b16c","633b5850beb4cace4f92b63e80867218e21c97f9ce9779d2acb3399dfb4df7c8",
 "381fa080c7ccaf080c1a10fffbb3a112fc64805d993737c21d4bd70a7506a76d","7a657855e9523a95da0aed7194b3bc4db7d61fda11391deaf07e3cc6f51d1e10",
 "74cec06c60ca921043c69aa2d3449e8f41ba03dea28c5753a946b5336ed8437d","8f32bdba48819e28794fcb605abff740f8cb6e903ae5ea039300a5a91c62c536",
 "f4da9af7f6d37317e5a18485288d7ece2664c550a9f24f5eec66b3d4e0a9f3c6","d225f1a7ff517d790fa284d97d2f4ff5c66dcfc44b9cb85fe3de3626b40c7c0e",
 "726e7dc6237301bfe085b5371eb4e78bafc38d270d2165683fe84159230efaab"}

rows = b.load_rows(b.EXPORT_URL.format(sid=b.SPREADSHEET_ID, gid=b.GID_CONVERSAS), None)
h = rows[0]
idx = b.header_index(h, {"email": ["e-mail", "email", "mail"], "created": ["data"], "faturamento": ["faturamento"],
        "camp": ["campanha"], "adset": ["conjunto"], "ad": ["anuncio"]}, {})
print("MAPA:", b.describe_idx(h, idx))

def mask(e):
    u, _, d = e.partition("@"); return u[:3] + "***@" + d

found, allmql, tot = [], [], 0
for r in rows[1:]:
    if not any((c or "").strip() for c in r): continue
    tot += 1
    em = b.cell(r, idx["email"]).strip().lower()
    fat = b.cell(r, idx["faturamento"])
    rec = dict(em=em, d=b.parse_date(b.cell(r, idx["created"])), fat=fat,
               camp=b.cell(r, idx["camp"]) or "(sem campanha)", adset=b.cell(r, idx["adset"]) or "(sem conjunto)",
               ad=b.cell(r, idx["ad"]) or "(sem anúncio)", mql=b.is_mql_faturamento(fat))
    if rec["mql"]: allmql.append(rec)
    if em and hashlib.sha256(em.encode()).hexdigest() in HASHES: found.append(rec)

seen = {x["em"] for x in found}
print(f"\nleads: {tot} | MQLs: {len(allmql)} | e-mails da lista encontrados: {len(seen)}/11 ({len(found)} linhas)")
print("\n== LEAD A LEAD ==")
for x in sorted(found, key=lambda x: (x["em"], x["d"] or "")):
    print(f"{mask(x['em'])} | {x['d']} | resp='{x['fat']}' | MQL={x['mql']} | {x['camp']} | {x['adset']} | {x['ad']}")

byemail = {}
for x in sorted(found, key=lambda x: x["d"] or ""): byemail.setdefault(x["em"], x)
fake = list(byemail.values()); n = len(fake)
d0 = min((x["d"] for x in fake if x["d"]), default="")
allmql_total = len(allmql)
allmql = [x for x in allmql if x["d"] and x["d"] >= d0]   # janela: do falso MQL mais antigo em diante (igual ao painel da dash)
tm = len(allmql)
print(f"\nfalso MQL mais antigo: {d0} | MQLs desde essa data: {tm} (de {allmql_total} no total)")
for dim, label in (("camp", "CAMPANHA"), ("adset", "CONJUNTO"), ("ad", "ANUNCIO")):
    print(f"\n== {label}: falsos (% dos {n}) | MQLs do item | % falso no item | share MQLs ==")
    fc = collections.Counter(x[dim] for x in fake); mc = collections.Counter(x[dim] for x in allmql)
    for k, c in fc.most_common():
        print(f"{c:2d} ({c/n:5.1%}) | MQLs: {mc[k]} | falso/item: {c/max(mc[k],1):.2%} | share: {mc[k]/tm:.1%} | {k}")
print("\nbase geral: falsos/MQLs totais = %.3f%%" % (100*n/tm))
print("\n== RESPOSTAS DE FATURAMENTO ==")
for k, c in collections.Counter(x["fat"] for x in fake).most_common(): print(c, k)
