#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Gera a dashboard estatica (index.html) a partir de 3 abas da planilha central
"Davi | BRF | Planilha Central":

  - Leads (gid 1895619916): fonte de leads (formulario nativo / pagina de captura),
    com a faixa de faturamento anual declarada. Usada em TODOS os
    graficos/cards/tabelas/calculos de conversao.
  - "Meta Ads" (gid 1245628405): investimento/impressoes/cliques do gerenciador.
  - "Check-In Realizado" (gid 686867257): quem fez check-in no evento presencial
    Business For Real. Cruzada por TELEFONE com os Leads para atribuir o
    Check-in ao anuncio de origem.

Funil: Leads > MQLs > Checkins (o funil termina nos Checkins).

Criterio de Lead Qualificado (MQL): faturamento anual ACIMA de R$ 2 milhoes
(is_mql_faturamento(), tolerante a variacoes de escrita: 2mm, 2 milhoes, 2 m...).

Este script apenas LE as planilhas (export CSV publico) e emite os REGISTROS
BRUTOS (leads[], meta[] e sales[]) dentro do HTML. Por compatibilidade com o
front-end, sales[] carrega os CHECK-INS (campo "vendas" = 1 por check-in,
"fat"/"receita" = 0), um registro por check-in, com a DATA do check-in — camp/
adset/ad vem do 1o lead daquele telefone (atribuicao do anuncio de origem). Todos os filtros, agregacoes, KPIs, tabelas e graficos sao
calculados no navegador (client-side). Nunca escreve nada de volta.

Teste local: --leads-file / --meta-file / --checkins-file apontando para CSVs.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from datetime import datetime, timezone, timedelta

SPREADSHEET_ID = "16FOsujjmymRJdj0tMdFy08qfRJtvXyXIw8HCwHDwwAA"
GID_CONVERSAS = "1895619916"   # aba de Leads (fonte principal de leads; nome da constante mantido do template)
GID_META = "1245628405"        # aba Meta Ads
GID_SALES = "686867257"        # aba Check-In Realizado (carregada em sales[] como "vendas" = checkins)
GID_COMPRADORES = "1411358971" # aba Compradores — RESERVADA, nao usada (o funil termina nos Checkins)
EXPORT_URL = "https://docs.google.com/spreadsheets/d/{sid}/export?format=csv&gid={gid}"

# Identificação do cliente/conta (usada só em textos/relatórios — não afeta o cruzamento de dados).
CLIENT_NAME = "Davi Braga"
MAIN_PRODUCT = "Business For Real"
# Prefixo comum a TODAS as campanhas da conta (so referencia; nao filtra nada).
MAIN_PRODUCT_PREFIX = "BFR"

# Faturamento anual minimo (R$) para o lead ser MQL: ACIMA de 2 milhoes.
MQL_MIN_FATURAMENTO = 2_000_000

BRT = timezone(timedelta(hours=-3))   # horario de Brasilia (exibicao)
TAX_FACTOR = 1.13806   # fator padrão de imposto/taxa sobre o gasto de mídia paga (Meta Ads) = 13,806%.
                       # Default do template para toda nova dash criada a partir dele; ajuste apenas
                       # se o cliente tiver um fator diferente, ou use 1.0 se não houver imposto.

# --------------------------------------------------------------------------- #
# Regras da aba Relatório (Top/Piores anúncios)
# --------------------------------------------------------------------------- #
# Amostra mínima para julgar um anúncio como "vencedor" ou "ruim". Abaixo disso
# ele entra como "Em observação" (dado insuficiente) — nunca é classificado só
# porque teve 1 resultado com pouco investimento. Ajuste conforme o ticket/CAC.
SAMPLE_MIN_SPEND = 100.0   # gasto mínimo (R$) para amostra relevante
SAMPLE_MIN_MQLS = 3        # MQLs mínimos para julgar qualidade profunda
TOP_ADS_N = 10             # nº de linhas em Top / Piores anúncios

# Metas & parâmetros da conta (DEFAULTS do painel editável da aba Relatório).
# São só o valor inicial: o usuário edita no navegador (persistido em
# localStorage) e as tabelas de anúncios recoram CPMQL/CAC e reavaliam a
# amostra ao vivo. None = "meta não definida" (métrica aparece sem cor até o
# gestor preencher).
META_CPMQL = None          # meta de CPMQL (R$/MQL); None = não definida
META_CAC = None            # meta de CAC (R$/venda); None = não definida
VOLUME_MIN_AMOSTRAL = SAMPLE_MIN_MQLS  # conversões (MQLs) mínimas p/ amostra confiável
N_DIAS_CORTE = 5           # dias consecutivos acima do teto p/ considerar corte


# --------------------------------------------------------------------------- #
# Leitura
# --------------------------------------------------------------------------- #
FETCH_RETRIES = 3       # tentativas totais em caso de timeout/erro de rede no export CSV
FETCH_RETRY_DELAY = 15  # segundos entre tentativas (o Google Sheets às vezes trava a resposta)


def fetch_csv(url: str) -> list[list[str]]:
    req = urllib.request.Request(url, headers={"User-Agent": "dash-template-bot/1.0"})
    last_err: Exception | None = None
    for attempt in range(1, FETCH_RETRIES + 1):
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
            return list(csv.reader(io.StringIO(raw)))
        except (TimeoutError, urllib.error.URLError) as exc:
            last_err = exc
            if attempt < FETCH_RETRIES:
                print(f"[fetch_csv] tentativa {attempt}/{FETCH_RETRIES} falhou ({exc!r}); "
                      f"tentando de novo em {FETCH_RETRY_DELAY}s...", file=sys.stderr)
                time.sleep(FETCH_RETRY_DELAY)
    raise last_err


def read_csv_file(path: str) -> list[list[str]]:
    with open(path, "r", encoding="utf-8", errors="replace", newline="") as f:
        return list(csv.reader(f))


def load_rows(url: str, local: str | None) -> list[list[str]]:
    return read_csv_file(local) if local else fetch_csv(url)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def norm(s: str | None) -> str:
    return strip_accents((s or "").strip().lower())


def to_float(v) -> float:
    if v is None:
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    s = re.sub(r"[^\d,.\-]", "", str(v).strip())
    if not s:
        return 0.0
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return 0.0


def parse_date(v: str) -> str | None:
    if not v:
        return None
    s = str(v).strip()
    if not s:
        return None
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    for fmt in ("%d/%m/%Y", "%m/%d/%Y", "%d/%m/%y", "%b %d, %Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def is_test_lead(rowtext: str) -> bool:
    return "<test lead" in rowtext.lower()


_NUM_RE = re.compile(
    r"(\d{1,3}(?:\.\d{3})+|\d+(?:[.,]\d+)?)\s*"
    r"(milhoes|milhao|mi\b|mm\b|m\b|bilhoes|bilhao|bi\b|mil\b|k\b)?")
_UNIT = {"milhoes": 1e6, "milhao": 1e6, "mi": 1e6, "mm": 1e6, "m": 1e6,
         "bilhoes": 1e9, "bilhao": 1e9, "bi": 1e9, "mil": 1e3, "k": 1e3}
_UPPER_ONLY = ("ate ", "abaixo", "menos de", "menor", "inferior", "nao fatur", "ainda nao", "<")


def parse_faturamento_min(v: str | None) -> float | None:
    """Limite INFERIOR (R$/ano) da faixa de faturamento declarada, ou None se
    nao der pra ler. Cobre variacoes: "2mm", "2 milhoes", "2 m", "R$ 2.000.000",
    "Acima de 2 milhoes", "De 1 a 2 milhoes" (limite inferior = 1 milhao —
    unidade herdada do numero seguinte), "500 mil a 1 milhao". Faixas so com
    teto ("ate 2 milhoes", "menos de 2 milhoes") devolvem 0."""
    t = norm(v)
    if not t:
        return None
    toks = []
    for m in _NUM_RE.finditer(t):
        raw, unit = m.group(1), m.group(2)
        if re.fullmatch(r"\d{1,3}(?:\.\d{3})+", raw):
            val = float(raw.replace(".", ""))
        else:
            val = float(raw.replace(",", "."))
        toks.append([val, unit.rstrip(".") if unit else None])
    if not toks:
        return None
    for i in range(len(toks) - 2, -1, -1):          # herda a unidade do proximo numero
        if toks[i][1] is None and toks[i + 1][1] is not None:
            toks[i][1] = toks[i + 1][1]
    val, unit = toks[0]
    val *= _UNIT.get(unit, 1)
    if any(k in t for k in _UPPER_ONLY):
        return 0.0
    return val


def is_mql_faturamento(v: str | None) -> bool:
    """Criterio de MQL: faturamento anual declarado a partir de R$ 2 milhoes
    (limite inferior da faixa >= MQL_MIN_FATURAMENTO)."""
    lo = parse_faturamento_min(v)
    return lo is not None and lo >= MQL_MIN_FATURAMENTO


def pretty_faixa(v: str) -> str:
    s = (v or "").strip()
    return s if s else "Sem resposta"


def mask_email(e: str) -> str:
    e = (e or "").strip()
    if "@" not in e:
        return "—"
    user, dom = e.split("@", 1)
    keep = user[:2] if len(user) > 2 else user[:1]
    return f"{keep}****@{dom}"


def mask_phone(p: str) -> str:
    digits = re.sub(r"\D", "", p or "")
    return f"…{digits[-4:]}" if len(digits) >= 4 else "—"


def norm_phone(p: str) -> str:
    return re.sub(r"\D", "", p or "")


def canon_phone(p: str) -> str:
    """Chave CANÔNICA de telefone p/ cruzar Compradores × Conversas, robusta às
    3 variações que faziam o mesmo número não bater quando comparado só por
    dígitos (norm_phone):
      - DDI "55" presente de um lado e ausente do outro
        (5511988887777 vs 11988887777);
      - 9º dígito do celular presente/ausente
        (11988887777 vs 1188887777);
      - máscara/espacos/parênteses (já removidos por norm_phone).
    Estratégia: remove o DDI 55 (quando sobra DDD+número) e usa DDD (2 díg.) +
    ÚLTIMOS 8 DÍGITOS — que é o mesmo com ou sem o 9. Devolve chave de 10 díg.
    (DDD+8). Números curtos/estrangeiros (< 10 díg. após limpar) voltam como
    estão, pra não colidir à toa."""
    d = norm_phone(p)
    if len(d) > 11 and d.startswith("55"):
        d = d[2:]            # tira DDI do Brasil, sobrando DDD + local
    if len(d) >= 10:
        return d[:2] + d[-8:]   # DDD + últimos 8 (drop do 9º dígito, se houver)
    return d


def first_last_initial(name: str) -> str:
    parts = (name or "").strip().split()
    if not parts:
        return "—"
    return parts[0] if len(parts) == 1 else f"{parts[0]} {parts[-1][:1]}."


def valid_utm(campaign: str) -> bool:
    c = norm(campaign)
    return bool(c) and c not in ("-", "—", "nao encontrado")


# --------------------------------------------------------------------------- #
# Indexacao das colunas
# --------------------------------------------------------------------------- #
def header_index(header, wanted, fallback):
    idx = {}
    hn = [norm(h) for h in header]
    for key, aliases in wanted.items():
        found = None
        for a in aliases:
            a = norm(a)
            for i, h in enumerate(hn):
                if h == a or (a and a in h):
                    found = i
                    break
            if found is not None:
                break
        idx[key] = found if found is not None else fallback.get(key)
    return idx


def cell(row, i):
    if i is None or i < 0 or i >= len(row):
        return ""
    return (row[i] or "").strip()


# Check-In Realizado -> indice por telefone
# --------------------------------------------------------------------------- #
PHONE_ALIASES = ["telefone", "whatsapp", "celular", "phone", "fone"]


def build_checkin_index(rows):
    """Le a aba Check-In Realizado e devolve {telefone: [{"d":..,"nm":..}, ...]},
    UM REGISTRO POR CHECK-IN (linhas duplicadas de mesma data+telefone contam 1)."""
    header = rows[0] if rows else []
    idx = header_index(
        header,
        {"phone": PHONE_ALIASES, "date": ["data do check", "data check", "check-in", "checkin", "data"],
         "name": ["nome", "name"]},
        {"phone": None, "date": None, "name": None},
    )
    print(f"  [checkins] colunas: {describe_idx(header, idx)}", file=sys.stderr)
    if idx["phone"] is None:
        raise SystemExit("ERRO: aba Check-In sem coluna de telefone reconhecivel "
                         f"(cabecalho: {header}). Ajuste PHONE_ALIASES em build.py.")
    out: dict[str, list] = {}
    seen: set = set()
    for row in rows[1:]:
        if not any((c or "").strip() for c in row):
            continue
        phone = norm_phone(cell(row, idx["phone"]))
        if not phone:
            continue
        d = parse_date(cell(row, idx["date"]))
        key = (canon_phone(phone), d)
        if key in seen:
            continue
        seen.add(key)
        out.setdefault(phone, []).append({"d": d, "nm": cell(row, idx["name"])})
    return out


def describe_idx(header, idx):
    return ", ".join(f"{k}={'«'+header[i]+'»' if i is not None and i < len(header) else 'NÃO ENCONTRADA'}"
                     for k, i in idx.items())


def log_unmatched_checkins(checkin_index, phone_attrib):
    """Diagnostico (stderr): check-ins cujo telefone nao bate com nenhum lead
    (canon_phone). Eles AINDA contam nos totais, como "(sem campanha)"."""
    matched = sum(1 for ph in checkin_index if canon_phone(ph) in phone_attrib)
    print(f"  checkins atribuidos a anuncio: {matched}/{len(checkin_index)} telefones "
          f"(cruzamento canonico Check-in x Leads)", file=sys.stderr)


# --------------------------------------------------------------------------- #
# Processamento -> registros brutos
# --------------------------------------------------------------------------- #
def process(conversas_rows, meta_rows, sales_rows):
    """conversas_rows = aba de Leads; sales_rows = aba Check-In Realizado."""
    sales_index = build_checkin_index(sales_rows)

    cheader = conversas_rows[0] if conversas_rows else []
    cidx = header_index(
        cheader,
        {"created": ["data", "created", "criado"], "phone": PHONE_ALIASES, "name": ["nome", "name"],
         "faturamento": ["faturamento", "faturam", "receita anual", "renda"],
         "campaign": ["campanha", "utm_campaign", "campaign"],
         "adset": ["conjunto", "adset", "ad set"], "ad": ["anuncio", "utm_content", "ad name"]},
        {"created": None, "phone": None, "name": None, "faturamento": None,
         "campaign": None, "adset": None, "ad": None},
    )
    print(f"  [leads] colunas: {describe_idx(cheader, cidx)}", file=sys.stderr)
    for k in ("created", "faturamento"):
        if cidx[k] is None:
            raise SystemExit(f"ERRO: aba de Leads sem coluna '{k}' reconhecivel (cabecalho: {cheader}). "
                             "Ajuste os aliases em build.py::process().")

    leads = []
    # atribuicao do ANUNCIO/campanha de uma venda por telefone: a 1a conversa
    # daquele telefone (a mais antiga de fato) e' quem levou aquele contato a
    # comprar, entao e' ela que define camp/adset/ad da venda — evita atribuir
    # a mesma compra a mais de uma conversa quando o numero aparece varias vezes.
    # A DATA da venda, porem, e' a data real da compra (aba Compradores), nunca
    # a data da conversa — datas diferentes nao devem ser somadas no mesmo dia.
    rows_sorted = sorted(
        [r for r in conversas_rows[1:] if any((c or "").strip() for c in r)],
        key=lambda r: parse_date(cell(r, cidx["created"])) or "",
    )
    attributed_phones: set[str] = set()
    phone_attrib: dict[str, dict] = {}
    for row in rows_sorted:
        if is_test_lead(" ".join(str(c) for c in row)):
            continue
        campaign_raw = cell(row, cidx["campaign"])
        campaign_valid = valid_utm(campaign_raw)
        src = "meta" if campaign_valid else "org"
        phone = canon_phone(cell(row, cidx["phone"]))
        camp = campaign_raw if campaign_valid else "(sem campanha)"
        adset = cell(row, cidx["adset"]) if campaign_valid else "(sem conjunto)"
        ad = cell(row, cidx["ad"]) if campaign_valid else "(sem anúncio)"
        conversa_date = parse_date(cell(row, cidx["created"]))
        if phone and phone not in attributed_phones:
            attributed_phones.add(phone)
            phone_attrib[phone] = {"src": src, "camp": camp, "adset": adset, "ad": ad, "d": conversa_date}
        specialty = pretty_faixa(cell(row, cidx["faturamento"]))
        leads.append({
            "d": parse_date(cell(row, cidx["created"])),
            "src": src,
            "plat": "ig" if src == "meta" else "—",
            "camp": camp,
            "adset": adset,
            "ad": ad,
            "prof": specialty,
            "bucket": specialty,
            "q": 1 if is_mql_faturamento(cell(row, cidx["faturamento"])) else 0,
            "utm": 1 if campaign_valid else 0,
            "nm": first_last_initial(cell(row, cidx["name"])),
            "em": "—",
            "ph": mask_phone(cell(row, cidx["phone"])),
        })

    # Check-ins: um registro POR CHECK-IN, na data do check-in. Entram TODOS os
    # check-ins; camp/adset/ad vem do 1o lead daquele telefone (phone_attrib, chave
    # canonica canon_phone). Sem lead correspondente, o check-in ainda conta, como
    # "(sem campanha)" / src="org". Carregados em sales[] com vendas=1 (o front
    # exibe "Checkins"); fat/receita = 0 (nao ha faturamento nesta etapa).
    sales = []
    NO_ATTRIB = {"src": "org", "camp": "(sem campanha)", "adset": "(sem conjunto)",
                 "ad": "(sem anúncio)", "d": None}
    for phone, items in sales_index.items():
        attrib = phone_attrib.get(canon_phone(phone)) or NO_ATTRIB
        for p in items:
            sales.append({
                "d": p["d"] or attrib["d"],
                "src": attrib["src"],
                "camp": attrib["camp"],
                "adset": attrib["adset"],
                "ad": attrib["ad"],
                "vendas": 1,
                "fat": 0,
                "receita": 0,
            })

    log_unmatched_checkins(sales_index, phone_attrib)

    mheader = meta_rows[0] if meta_rows else []
    midx = header_index(
        mheader,
        {"day": ["day", "data"], "campaign": ["campaign name", "campaign"], "adset": ["ad set name", "adset"],
         "ad": ["ad name"], "spent": ["amount spent", "valor gasto", "gasto"], "impr": ["impressions", "impress"],
         "clicks": ["link clicks", "clicks", "cliques"], "leads": ["leads"],
         "pv": ["landing page views", "page views", "pageviews"],
         # Cliente não tem evento "Initiate Checkout" configurado no pixel — usa
         # "Adds to Cart" como proxy de Checkout (decisão do cliente).
         "chk": ["adds to cart", "add to cart", "initiate checkout", "checkouts iniciados", "checkouts"],
         # Link do criativo (ex. Instagram) — coluna opcional adicionada pelo cliente
         # na aba de mídia. Usada na aba Relatório (Top/Piores anúncios) para linkar
         # o anúncio. Aliases cobrem variações do cabeçalho.
         "link": ["creative instagram permalink", "instagram permalink", "permalink",
                  "creative link", "link do anuncio", "link do criativo"]},
        {"day": 0, "campaign": 2, "adset": 3, "ad": 4, "spent": 5, "impr": 6, "clicks": 7, "leads": None, "pv": 8},
    )

    meta = []
    # Anúncio (nome) -> 1 permalink do criativo. "Qualquer um correlato" ao
    # anúncio serve (o mesmo criativo pode rodar em vários dias/conjuntos);
    # guardamos o primeiro link não-vazio encontrado para cada anúncio.
    ad_links = {}
    for row in meta_rows[1:]:
        if not any((c or "").strip() for c in row):
            continue
        ad = cell(row, midx["ad"]) or "(sem anúncio)"
        link = cell(row, midx["link"])
        if link and ad not in ad_links:
            ad_links[ad] = link
        meta.append({
            "d": parse_date(cell(row, midx["day"])),
            "camp": cell(row, midx["campaign"]) or "(sem campanha)",
            "adset": cell(row, midx["adset"]) or "(sem conjunto)",
            "ad": ad,
            "sp": round(to_float(cell(row, midx["spent"])), 4),
            "im": to_float(cell(row, midx["impr"])),
            "cl": to_float(cell(row, midx["clicks"])),
            "pv": to_float(cell(row, midx["pv"])),
            "ck": to_float(cell(row, midx["chk"])),
            "ml": to_float(cell(row, midx["leads"])),
        })

    dates = sorted({d for d in (
        [l["d"] for l in leads if l["d"]] + [m["d"] for m in meta if m["d"]] + [s["d"] for s in sales if s["d"]]
    )})
    now_brt = datetime.now(BRT)
    return {
        "build": {
            "generated_at_brt": now_brt.strftime("%d/%m/%Y %H:%M"),
            "build_id": datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S"),
            "today": now_brt.strftime("%Y-%m-%d"),
            "date_min": dates[0] if dates else None,
            "date_max": dates[-1] if dates else None,
            "tax_factor": TAX_FACTOR,
            # config da aba Relatório (lida pelo front)
            "sample_min_spend": SAMPLE_MIN_SPEND,
            "sample_min_mqls": SAMPLE_MIN_MQLS,
            "top_ads_n": TOP_ADS_N,
            # metas & parâmetros (defaults do painel editável; None = não definida)
            "meta_cpmql": META_CPMQL,
            "meta_cac": META_CAC,
            "volume_min_amostral": VOLUME_MIN_AMOSTRAL,
            "n_dias_corte": N_DIAS_CORTE,
        },
        "leads": leads,
        "meta": meta,
        "sales": sales,
        # Anúncio -> permalink do criativo (aba Relatório).
        "ad_links": ad_links,
        # Insights de Tráfego (texto pré-escrito, lido de relatorios.json). Preenchido
        # em main() via load_briefings(); fica {} se relatorios.json não existir.
        "briefings": {},
    }


# --------------------------------------------------------------------------- #
# Insights de Tráfego (aba Relatório)
# --------------------------------------------------------------------------- #
def load_briefings(path: str) -> dict:
    """Lê build/relatorios.json. Estrutura:
        {"generated_at": "...", "periodos": {"<preset>": {"html": "..."}, ...}}
    Retorna o dict inteiro (ou {} se o arquivo não existir/for inválido).
    A geração NÃO acontece aqui — este build só lê o texto já pronto, sem
    chamar nenhuma API (custo zero no build/no navegador)."""
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            obj = json.load(f)
        return obj if isinstance(obj, dict) else {}
    except (ValueError, OSError):
        return {}


# --------------------------------------------------------------------------- #
# Render
# --------------------------------------------------------------------------- #
def render(data, template_path):
    # A dashboard e montada a partir de arquivos separados (visual x logica):
    #   template.html          -> esqueleto HTML (placeholders __STYLES__/__APP_JS__)
    #   identidade-visual.css  -> TODAS as cores (edite aqui p/ mexer so em cor)
    #   estilos.css            -> layout/componentes
    #   app.js                 -> logica + renderizacao
    # Esta funcao so COSTURA os arquivos e injeta os dados; nao altera nada deles.
    base = os.path.dirname(os.path.abspath(template_path))

    def readf(name):
        with open(os.path.join(base, name), "r", encoding="utf-8") as f:
            return f.read()

    with open(template_path, "r", encoding="utf-8") as f:
        tpl = f.read()
    styles = readf("identidade-visual.css") + "\n" + readf("estilos.css")
    tpl = tpl.replace("__STYLES__", styles)
    tpl = tpl.replace("__APP_JS__", readf("app.js"))
    tpl = tpl.replace("__DATA_JSON__", json.dumps(data, ensure_ascii=False))
    tpl = tpl.replace("__BUILD_ID__", data["build"]["build_id"])
    tpl = tpl.replace("__GENERATED_BRT__", data["build"]["generated_at_brt"])
    return tpl


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--leads-file", help="CSV local da aba de Leads")
    ap.add_argument("--meta-file")
    ap.add_argument("--checkins-file", help="CSV local da aba Check-In Realizado")
    ap.add_argument("--template", default="build/template.html")
    ap.add_argument("--out", default="dist/index.html")
    args = ap.parse_args()

    conversas_rows = load_rows(EXPORT_URL.format(sid=SPREADSHEET_ID, gid=GID_CONVERSAS), args.leads_file)
    meta_rows = load_rows(EXPORT_URL.format(sid=SPREADSHEET_ID, gid=GID_META), args.meta_file)
    sales_rows = load_rows(EXPORT_URL.format(sid=SPREADSHEET_ID, gid=GID_SALES), args.checkins_file)

    data = process(conversas_rows, meta_rows, sales_rows)

    # Insights de Tráfego (texto pré-escrito) — lidos do arquivo versionado ao
    # lado do template. Sem chamada de API no build.
    briefings_path = os.path.join(os.path.dirname(os.path.abspath(args.template)), "relatorios.json")
    data["briefings"] = load_briefings(briefings_path)

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(render(data, args.template))

    b = data["build"]
    q = sum(l["q"] for l in data["leads"])
    vd = sum(s["vendas"] for s in data["sales"])
    print("== build ok ==", file=sys.stderr)
    print(f"  periodo   : {b['date_min']} -> {b['date_max']}", file=sys.stderr)
    print(f"  leads     : {len(data['leads'])}  MQLs (>= R$ 2 mi/ano): {q}", file=sys.stderr)
    print(f"  checkins  : {vd}", file=sys.stderr)
    print(f"  meta      : {len(data['meta'])} linhas", file=sys.stderr)
    print(f"  out       : {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
