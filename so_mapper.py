#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os, sys, json, math, re, argparse, ssl, smtplib, time
from pathlib import Path
from datetime import datetime, timedelta, timezone
import requests
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

# --- .env local opcional ---
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).with_name(".env"))
except Exception:
    pass

# --- Config ---
API_KEY     = os.getenv("HOLDED_API_KEY")
USE_BEARER  = os.getenv("HOLDED_USE_BEARER", "false").lower() in ("1","true","yes")

MAIL_FROM   = os.getenv("MAIL_FROM")
MAIL_TO     = os.getenv("MAIL_TO")      # varios separados por coma
MAIL_CANCEL_TO = os.getenv("MAIL_CANCEL_TO")  # solo para emails de CANCELADO
SMTP_HOST   = os.getenv("SMTP_HOST")
SMTP_PORT   = int(os.getenv("SMTP_PORT", "587"))  # 587 STARTTLS | 465 SSL
SMTP_USER   = os.getenv("SMTP_USER")
SMTP_PASS   = os.getenv("SMTP_PASS")

BASE_DOCS   = "https://api.holded.com/api/invoicing/v1/documents"
BASE_PROD   = "https://api.holded.com/api/invoicing/v1/products"
PAGE_LIMIT  = 200

# Zona horaria para impresión / cómputo de días
try:
    from zoneinfo import ZoneInfo
    TZ_MADRID = ZoneInfo("Europe/Madrid")
except Exception:
    TZ_MADRID = timezone.utc  # fallback

# Preferencias de pack
POSSIBLE_PACK_SIZES = [36, 37, 35, 33, 31, 30]
PACK_RULES = [
    (r"AIKO.*MAH72M", 36),
    (r"AIKO.*\b605\b", 36),
]

# --- Estados Holded (salesorder) con convención interna ---
# Convención interna: 0=Pendiente, 1=Aceptado, -1=Cancelado
STATUS_LABELS = {0: "Pendiente", 1: "Aceptado", -1: "Cancelado"}
CANCELLED = -1

def status_label(n):
    try:
        return STATUS_LABELS.get(int(n), f"Desconocido({n})")
    except Exception:
        return f"Desconocido({n})"

def normalize_status(val):
    """
    Mapea el estado crudo (API/JSON) a la convención interna:
      0 -> 0 (Pendiente)
      1 -> 1 (Aceptado)
      2 -> -1 (Cancelado API -> Cancelado interno)
     -1 -> -1 (Cancelado interno)
    Cualquier otro -> None (desconocido/no usable para transiciones)
    """
    try:
        n = int(val)
    except Exception:
        return None
    if n == 2:
        return -1
    if n in (0, 1, -1):
        return n
    return None

# ----------------------------- Helpers HTTP / tiempo -----------------------------
def H():
    if not API_KEY:
        raise SystemExit("ERROR: falta HOLDED_API_KEY en variables de entorno.")
    h = {"Accept": "application/json"}
    if USE_BEARER:
        h["Authorization"] = f"Bearer {API_KEY}"
    else:
        h["key"] = API_KEY
    return h

def to_madrid_str_from_epoch(s):
    try:
        ts = int(s)
    except Exception:
        return str(s)
    return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(TZ_MADRID).strftime("%Y-%m-%d %H:%M:%S")

def utc_bounds_last_minutes(minutes=10):
    now_tz = datetime.now(TZ_MADRID)
    start = now_tz - timedelta(minutes=minutes)
    return int(start.astimezone(timezone.utc).timestamp()), int(now_tz.astimezone(timezone.utc).timestamp())

def madrid_day_bounds_epoch_seconds(day_offset=0):
    """
    (start_utc, end_utc) para el día 'hoy + day_offset' en Europe/Madrid.
    day_offset=0 -> hoy; -1 -> ayer; +1 -> mañana.
    """
    now_mad = datetime.now(TZ_MADRID)
    target = (now_mad + timedelta(days=day_offset)).date()
    start_mad = datetime(target.year, target.month, target.day, 0, 0, 0, tzinfo=TZ_MADRID)
    end_mad   = datetime(target.year, target.month, target.day, 23, 59, 59, tzinfo=TZ_MADRID)
    return int(start_mad.astimezone(timezone.utc).timestamp()), int(end_mad.astimezone(timezone.utc).timestamp())

def madrid_year_to_date_bounds_epoch_seconds():
    """
    Devuelve (start_utc, end_utc) desde el 1 de enero del año actual
    hasta el final del día actual (23:59:59).
    """
    now_mad = datetime.now(TZ_MADRID)
    start_mad = datetime(now_mad.year, 1, 1, 0, 0, 0, tzinfo=TZ_MADRID)
    end_mad   = datetime(now_mad.year, now_mad.month, now_mad.day, 23, 59, 59, tzinfo=TZ_MADRID)
    return int(start_mad.astimezone(timezone.utc).timestamp()), int(end_mad.astimezone(timezone.utc).timestamp())

def fmt_eur(n, decimals=4):
    try:
        v = float(n or 0)
    except Exception:
        return str(n)
    s = f"{v:,.{decimals}f} €"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")

# ----------------------------- API calls -----------------------------
def get_salesorder(doc_id):
    url = f"{BASE_DOCS}/salesorder/{doc_id}"
    r = requests.get(url, headers=H(), timeout=60)
    if r.status_code == 404:
        url = f"{BASE_DOCS}/{doc_id}"
        r = requests.get(url, headers=H(), timeout=60)
    r.raise_for_status()
    return r.json()

def list_salesorders_between(start_epoch_utc, end_epoch_utc, page_limit=PAGE_LIMIT, verbose=False):
    url = f"{BASE_DOCS}/salesorder"
    page = 1
    out = []
    while True:
        params = {"page": page, "limit": page_limit,
                  "starttmp": str(start_epoch_utc), "endtmp": str(end_epoch_utc)}
        r = requests.get(url, headers=H(), params=params, timeout=60)
        if r.status_code == 401:
            raise SystemExit(f"401 Unauthorized: {r.text}")
        r.raise_for_status()
        batch = r.json()
        if not batch:
            if verbose:
                print(f"[fetch] page {page}: 0 docs")
            break
        out.extend(batch)
        if verbose:
            print(f"[fetch] page {page}: +{len(batch)} (total {len(out)})")
        if len(batch) < page_limit:
            break
        page += 1
    return out

_prod_cache = {}
def get_product(product_id):
    if not product_id:
        return {}
    if product_id in _prod_cache:
        return _prod_cache[product_id]
    url = f"{BASE_PROD}/{product_id}"
    r = requests.get(url, headers=H(), timeout=60)
    r.raise_for_status()
    data = r.json()
    _prod_cache[product_id] = data
    return data

# ----------------------------- Helpers de estado -----------------------------
def load_status_map(path):
    try:
        p = Path(path)
        if not p.exists():
            return {}
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}

def save_status_map(path, m):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")

# ----------------------------- Detectar Transporte por NOMBRE -----------------------------
TRANSPORT_NAME_PATTERNS = [
    r"^\s*transporte\s*$",
    r"^\s*shipping\s*costs?\s*$",
    r"^\s*shipping\s*$",
    r"^\s*shipment\s*$",
    r"^\s*transport\s*$",
    r"^\s*flete\s*$",
    r"^\s*portes?\s*$",
    r"^\s*env[ií]o\s*$",
]
def is_transport_name(name: str) -> bool:
    n = (name or "").strip().lower()
    for pat in TRANSPORT_NAME_PATTERNS:
        if re.match(pat, n):
            return True
    return False

# --- Comercial por tags ---
SALESPERSON_TAGS = {"tomi":"Tomás","canet":"Jorge","supa":"Susana","juanv":"Juan"}
DEFAULT_SALESPERSON = "Juan"
def infer_salesperson(line_tags, doc_tags):
    def norm_tags(t):
        if isinstance(t, list): return [str(x).strip().lower() for x in t]
        if isinstance(t, str):  return [t.strip().lower()]
        return []
    line_t = norm_tags(line_tags); doc_t = norm_tags(doc_tags)
    for t in line_t:
        if t in SALESPERSON_TAGS: return SALESPERSON_TAGS[t]
    for t in doc_t:
        if t in SALESPERSON_TAGS: return SALESPERSON_TAGS[t]
    return DEFAULT_SALESPERSON

# ----------------------------- Extractores robustos -----------------------------
def try_fields(container, candidates, default=None):
    if not isinstance(container, dict):
        return default
    for key in candidates:
        if key in container:
            val = container[key]
            if val not in (None, "", []):
                return val
        attrs = container.get("attributes") or {}
        if key in attrs:
            val = attrs.get(key)
            if val not in (None, "", []):
                return val
        cfs = container.get("customFields")
        if isinstance(cfs, dict) and key in cfs:
            val = cfs.get(key)
            if val not in (None, "", []):
                return val
        if isinstance(cfs, list):
            for entry in cfs:
                if isinstance(entry, dict) and entry.get("field") == key:
                    val = entry.get("value")
                    if val not in (None, "", []):
                        return val
    return default

def extract_power_w(product, *, item_name="", item_sku=""):
    val = try_fields(product, ["power_w", "Potencia", "potencia_w", "power", "watt", "W"])
    if val not in (None, "", []):
        try:
            return float(val)
        except Exception:
            pass
    texts = [item_name or "", item_sku or "",
             str(try_fields(product, ["name"]) or ""),
             str(try_fields(product, ["sku"]) or "")]
    for txt in texts:
        m = re.findall(r"(?<!\d)(\d{3,4})\s*[Ww]\s*(?:[Pp])?", txt)
        cands = [int(x) for x in m if 300 <= int(x) <= 1000]
        if cands:
            return float(max(cands))
    generic = []
    for txt in texts:
        for x in re.findall(r"(?<!\d)(\d{3,4})(?!\d)", txt):
            n = int(x)
            if 300 <= n <= 1000:
                generic.append(n)
    return float(max(generic)) if generic else 0.0

def extract_units_per_pallet(product):
    val = try_fields(product, [
        "units_per_pallet", "unitsPerPallet", "pallet_units",
        "ud_pallet", "uds_pallet", "unitsPallet"
    ])
    try:
        return float(val)
    except Exception:
        return 0.0

def compute_price_per_w(line_amount, qty, power_w):
    if qty and power_w:
        return float(line_amount) / (float(qty) * float(power_w))
    return 0.0

def extract_transport_amount_from_doc(doc):
    total = 0.0; found = False
    for p in (doc.get("products") or []):
        if is_transport_name(p.get("name") or ""):
            price = float(p.get("price") or 0); units = float(p.get("units") or 0)
            total += price * units; found = True
    return total if found else "-"

def has_transport_line(doc):
    for p in (doc.get("products") or []):
        if is_transport_name(p.get("name") or ""): return True
    return False

def to_date_label(doc):
    v = doc.get("date") or doc.get("createdAt") or doc.get("issuedOn") or doc.get("updatedAt")
    if v is None: return "-"
    return to_madrid_str_from_epoch(v) if str(v).isdigit() else str(v)

# ----------------------------- Normalización de líneas -----------------------------
def iter_document_lines(doc):
    for it in (doc.get("products") or []):
        name = (it.get("name") or "").strip()
        yield {
            "name": name,
            "desc": it.get("desc"),
            "qty": float(it.get("units") or 0),
            "unit_price": float(it.get("price") or 0),
            "amount": float(it.get("price") or 0) * float(it.get("units") or 0),
            "productId": it.get("productId"),
            "sku": (str(it.get("sku")) if it.get("sku") is not None else ""),
            "is_transport": is_transport_name(name),
            "tags": it.get("tags") or [],
        }

# ----------------------------- Packs / filas -----------------------------
def hint_units_per_pallet_by_pattern(name="", sku="", product=None):
    text = " ".join([(name or ""), (sku or ""), str((product or {}).get("name") or ""), str((product or {}).get("sku") or "")])
    for pat, val in PACK_RULES:
        if re.search(pat, text, flags=re.IGNORECASE):
            return float(val)
    return 0.0

def infer_units_per_pallet(product, *, name="", sku="", qty=0):
    if (upp := extract_units_per_pallet(product)) > 0:
        leftover = qty % upp if qty and upp else 0
        return upp, "attr", [], int(leftover)
    upp = hint_units_per_pallet_by_pattern(name=name, sku=sku, product=product)
    if upp > 0:
        leftover = qty % upp if qty and upp else 0
        return upp, "pattern", [], int(leftover)
    if qty:
        exact = [p for p in POSSIBLE_PACK_SIZES if qty % p == 0]
        if len(exact) == 1: return float(exact[0]), "divisible", [], 0
        elif len(exact) > 1:
            preferred = 36 if 36 in exact else max(exact)
            others = [p for p in exact if p != preferred]
            return float(preferred), "ambiguous_divisible", others, 0
        best_p = None; best_leftover = None
        for p in POSSIBLE_PACK_SIZES:
            rem = qty % p; score = (rem, -p)
            if best_leftover is None or score < (best_leftover, -best_p):
                best_leftover = rem; best_p = p
        return float(best_p), "closest", [], int(best_leftover or 0)
    return 0.0, "unknown", [], 0

def build_row(doc, line, *, fetch_product=False):
    cliente_name = doc.get("contactName") or "-"
    item_name = line["name"] or "-"
    qty = float(line["qty"] or 0)
    amount = float(line["amount"] or 0)

    product = {}
    if fetch_product and line.get("productId"):
        try:
            product = get_product(line["productId"])
        except Exception:
            product = {}

    power_w = extract_power_w(product, item_name=item_name, item_sku=line.get("sku",""))

    pallets_display = "-"
    pallets_num = 0
    if power_w:
        upp, _, _, leftover = infer_units_per_pallet(product, name=item_name, sku=line.get("sku",""), qty=int(qty))
        pallets = math.ceil(qty / upp) if (qty > 0 and upp > 0) else "-"
        pallets_display = (f"{int(pallets)} (+{leftover})" if (isinstance(pallets, (int,float)) and leftover)
                           else (str(int(pallets)) if pallets != "-" else "-"))
        pallets_num = int(pallets) if isinstance(pallets, (int,float)) else 0

    if power_w:
        precio_valor = compute_price_per_w(amount, qty, power_w)   # €/W
        precio_unidad = "€/W"; decs = 4
    else:
        precio_valor = float(line.get("unit_price") or 0)          # €/ud
        precio_unidad = "€/ud"; decs = 2

    return {
        "Fecha reserva": to_date_label(doc),
        "Material": item_name,
        "Potencia (W)": int(power_w) if power_w else "-",
        "Cantidad uds": int(qty),
        "Nº Pallets": pallets_display,
        "PalletsNum": pallets_num,
        "Cliente": (cliente_name or "-"),
        "PrecioValor": precio_valor,
        "PrecioUnidad": precio_unidad,
        "PrecioDecs": decs,
        "Transporte": "-",
        "Comercial": infer_salesperson(line.get("tags"), doc.get("tags")),
    }

def _display_rows_for_console(rows):
    disp = []
    for r in rows:
        precio_txt = fmt_eur(r["PrecioValor"], r["PrecioDecs"]).replace(" €", f" {r['PrecioUnidad']}")
        transp_txt = fmt_eur(r["Transporte"], 2) if isinstance(r["Transporte"], (int, float)) else str(r["Transporte"])
        disp.append({
            "Fecha reserva": str(r["Fecha reserva"]),
            "Material": str(r["Material"]),
            "Potencia (W)": str(r["Potencia (W)"]),
            "Cantidad uds": str(r["Cantidad uds"]),
            "Nº Pallets": str(r["Nº Pallets"]),
            "Cliente": str(r["Cliente"]),
            "Precio": precio_txt,
            "Transporte": transp_txt,
            "Comercial": str(r["Comercial"]),
        })
    return disp

def print_table(rows):
    if not rows:
        print("No hay líneas que mostrar."); return
    headers = ["Fecha reserva","Material","Potencia (W)","Cantidad uds","Nº Pallets","Cliente","Precio","Transporte","Comercial"]
    disp = _display_rows_for_console(rows)
    widths = {h: max(len(h), max(len(d[h]) for d in disp)) for h in headers}
    sep = " | "; line = "-+-".join("-"*widths[h] for h in headers)
    print(sep.join(h.ljust(widths[h]) for h in headers)); print(line)
    for d in disp:
        print(sep.join(d[h].ljust(widths[h]) for h in headers))

def dump_json(obj, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[dump] JSON guardado en: {path}")

# ----------------------------- Email -----------------------------
def build_email_subject(doc, rows):
    cliente = doc.get("contactName") or "-"
    if rows:
        materials = [r.get("Material") or "-" for r in rows]
        distinct = []
        for m in materials:
            if m not in distinct: distinct.append(m)
        material_label = distinct[0] if len(distinct) <= 1 else f"{distinct[0]} (+{len(distinct)-1} más)"
    else:
        material_label = "Transporte" if has_transport_line(doc) else "Sin líneas"
    pallets_total = sum(int(r.get("PalletsNum") or 0) for r in rows) if rows else 0
    if pallets_total > 0:
        qty = pallets_total; unit_word = "pallets" if qty != 1 else "pallet"
    else:
        units_total = sum(int(r.get("Cantidad uds") or 0) for r in rows) if rows else 0
        qty = units_total; unit_word = "uds" if qty != 1 else "ud"
    return f"VENDIDO {qty} {unit_word} {material_label} a {cliente}"

def build_html_table(doc, rows):
    number = doc.get("number") or doc.get("code") or doc.get("docNumber") or (doc.get("_id") or doc.get("id") or "-")
    cliente = doc.get("contactName") or "-"
    fecha = to_date_label(doc)
    transporte_amount = extract_transport_amount_from_doc(doc)
    head = (
        f"<h3 style='margin:0 0 8px'>Reserva de material — Pedido {number}</h3>"
        f"<p style='margin:0 0 10px'>Cliente: <b>{cliente}</b> &nbsp;|&nbsp; Fecha: <b>{fecha}</b>"
        f" &nbsp;|&nbsp; Transporte: <b>{(fmt_eur(transporte_amount,2) if isinstance(transporte_amount,(int,float)) else transporte_amount)}</b></p>"
    )
    headers = ["Fecha reserva","Material","Potencia (W)","Cantidad uds","Nº Pallets","Cliente","Precio","Transporte","Comercial"]
    tr = []
    for r in rows:
        precio_html = fmt_eur(r["PrecioValor"], r["PrecioDecs"]).replace(" €", f" {r['PrecioUnidad']}")
        transp_html = fmt_eur(r["Transporte"], 2) if isinstance(r["Transporte"], (int, float)) else r["Transporte"]
        tr.append(
            "<tr>"
            f"<td>{r['Fecha reserva']}</td>"
            f"<td>{r['Material']}</td>"
            f"<td style='text-align:right'>{r['Potencia (W)']}</td>"
            f"<td style='text-align:right'>{r['Cantidad uds']}</td>"
            f"<td style='text-align:right'>{r['Nº Pallets']}</td>"
            f"<td>{r['Cliente']}</td>"
            f"<td style='text-align:right'>{precio_html}</td>"
            f"<td style='text-align:right'>{transp_html}</td>"
            f"<td>{r['Comercial']}</td>"
            "</tr>"
        )
    body = (
        "<table border='1' cellspacing='0' cellpadding='6' style='border-collapse:collapse'>"
        "<thead><tr>" + "".join(f"<th>{h}</th>" for h in headers) + "</tr></thead>"
        f"<tbody>{''.join(tr) if tr else '<tr><td colspan=9>Sin líneas</td></tr>'}</tbody>"
        "</table>"
    )
    return "<div style='font-family:system-ui,Segoe UI,Roboto,Arial,sans-serif'>" + head + body + "</div>"

def send_email(subject, html, *, to_recipients=None):
    missing = [k for k,v in {
        "MAIL_FROM":MAIL_FROM, "MAIL_TO":MAIL_TO, "SMTP_HOST":SMTP_HOST,
        "SMTP_PORT":SMTP_PORT, "SMTP_USER":SMTP_USER, "SMTP_PASS":SMTP_PASS
    }.items() if not v]
    if missing:
        raise SystemExit(f"Faltan variables SMTP en entorno: {', '.join(missing)}")

    # Permite sobrescribir destinatarios para casos especiales (p.ej., CANCELADO)
    if to_recipients is None:
        recipients = [e.strip() for e in (MAIL_TO or "").split(",") if e.strip()]
        to_header = MAIL_TO
    else:
        recipients = [e.strip() for e in to_recipients if e.strip()]
        to_header = ", ".join(recipients)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = MAIL_FROM
    msg["To"] = to_header
    msg.attach(MIMEText(html, "html"))

    try:
        if SMTP_PORT == 465:
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=context, timeout=60) as server:
                server.login(SMTP_USER, SMTP_PASS)
                server.sendmail(MAIL_FROM, recipients, msg.as_string())
        else:
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=60) as server:
                server.ehlo()
                server.starttls()
                server.ehlo()
                server.login(SMTP_USER, SMTP_PASS)
                server.sendmail(MAIL_FROM, recipients, msg.as_string())
    except smtplib.SMTPAuthenticationError as e:
        raise SystemExit("Autenticación SMTP fallida (535). En Gmail usa contraseña de aplicación y MAIL_FROM=SMTP_USER.") from e

# ----------------------------- Main -----------------------------
def main():
    ap = argparse.ArgumentParser(description="SO monitor (Holded) — Rango + emails por transiciones")
    g = ap.add_mutually_exclusive_group(required=False)
    g.add_argument("--doc-id", help="ID de documento (salesorder) a descargar")
    g.add_argument("--minutes", type=int, help="Buscar pedidos creados en los últimos X minutos")
    g.add_argument("--days", type=int, help="Buscar pedidos de los últimos N días incluyendo hoy (1 = hoy+yesterday)")
    ap.add_argument("--ytd", action="store_true", help="Todo el año en curso hasta ahora (si no pasas --days/--minutes/--doc-id)")
    ap.add_argument("--limit", type=int, default=100000, help="Máximo de documentos a procesar")
    ap.add_argument("--dump-json", help="Ruta base para volcar el JSON crudo de cada documento (añade sufijo con el id)")
    ap.add_argument("--send-email", dest="send_email", action="store_true",
                    help="Enviar email cuando haya transición o cuando el pedido se vea por primera vez")
    # Flags opcionales de compatibilidad (ya no necesarias para el primer envío):
    ap.add_argument("--email-new-accepted", dest="email_new_accepted", action="store_true",
                    help="(Opcional) Forzar VENDIDO solo si el primer estado es Pendiente/Aceptado")
    ap.add_argument("--email-new-any", dest="email_new_any", action="store_true",
                    help="(Opcional) Forzar VENDIDO si es la primera vez (cualquier estado)")
    ap.add_argument("--status-file", default="state/so_status.json",
                    help="Mapa {doc_id: status} para detectar transiciones (se actualiza al final)")
    ap.add_argument("--quiet", action="store_true", help="Logs mínimos (ideal CI)")
    ap.add_argument("--verbose", action="store_true", help="Logs de progreso de fetch/paginación")
    ap.add_argument("--fetch-product", action="store_true", help="Activar llamadas a ficha de producto (más lento)")
    args = ap.parse_args()

    t0 = time.perf_counter()

    # Obtener docs según el modo (por defecto YTD + HOY)
    if args.doc_id:
        docs = [get_salesorder(args.doc_id)]
    elif args.minutes:
        start, end = utc_bounds_last_minutes(args.minutes)
        docs = list_salesorders_between(start, end, verbose=args.verbose)
    elif args.days is not None:
        days = max(0, int(args.days or 0))
        start_utc, _ = madrid_day_bounds_epoch_seconds(day_offset=-days)
        _, end_utc   = madrid_day_bounds_epoch_seconds(day_offset=0)
        docs = list_salesorders_between(start_utc, end_utc, verbose=args.verbose)
    else:
        start_utc, end_utc = madrid_year_to_date_bounds_epoch_seconds()
        docs = list_salesorders_between(start_utc, end_utc, verbose=args.verbose)
        # Unión defensiva con HOY completo
        s_today, e_today = madrid_day_bounds_epoch_seconds(day_offset=0)
        if args.verbose:
            print("[union] añadiendo ventana HOY (00:00–23:59) a resultados YTD")
        docs_today = list_salesorders_between(s_today, e_today, verbose=args.verbose)
        docs.extend(docs_today)

    # Deduplicar por ID y ordenar por fecha
    def _doc_id(d):
        return d.get("_id") or d.get("id") or d.get("docNumber") or d.get("number") or ""
    uniq = {}
    for d in docs:
        did = _doc_id(d)
        if did and did not in uniq:
            uniq[did] = d
    docs = list(uniq.values())
    try:
        docs.sort(key=lambda d: int(d.get("date") or 0), reverse=True)
    except Exception:
        pass
    docs = docs[:args.limit]

    if not docs:
        print("No se han encontrado documentos en la ventana solicitada.")
        return

    status_map = load_status_map(args.status_file)

    sent_vendidos = 0
    sent_cancelados = 0

    for idx, doc in enumerate(docs, 1):
        doc_id = _doc_id(doc)
        number = doc.get("number") or doc.get("code") or doc.get("docNumber") or doc_id
        cliente = doc.get("contactName") or "-"

        # Normalización de estados y detección de "primer vistazo"
        cur_status  = normalize_status(doc.get("status"))
        prev_status = normalize_status(status_map.get(doc_id, None))
        first_seen  = (doc_id not in status_map)  # <--- CLAVE: nuevo pedido detectado

        # Dump JSON (si se pide)
        if args.dump_json:
            out_path = f"{args.dump_json.rstrip('.json')}_{doc_id}.json"
            dump_json(doc, out_path)

        # Filas (sin ficha producto por defecto → rápido)
        lines = list(iter_document_lines(doc))
        material_lines = [ln for ln in lines if not ln.get("is_transport")]
        rows = [build_row(doc, ln, fetch_product=args.fetch_product) for ln in material_lines]
        transp_amount = extract_transport_amount_from_doc(doc)
        for i, r in enumerate(rows):
            r["Transporte"] = transp_amount if i == 0 else "-"

        if not args.quiet:
            print(f"\n[{idx}/{len(docs)}] === Sales Order: {number} (id: {doc_id}) ===")
            if first_seen:
                print("Estado actual: (nuevo documento) " + (status_label(cur_status) if cur_status is not None else "Sin estado reconocible"))
            else:
                if prev_status is not None:
                    print(f"Estado actual: {status_label(cur_status)} (antes: {status_label(prev_status)})")
                else:
                    print(f"Estado actual: {status_label(cur_status)}")
            print_table(rows)

        # --- Decidir envíos ---
        send_reason = None

        if first_seen:
            # En cuanto nace un pedido, enviamos (comportamiento del script 1)
            send_reason = "NEW_ANY"
        else:
            # Transiciones clásicas
            if (prev_status is not None) and (cur_status is not None):
                if prev_status == CANCELLED and cur_status in (0, 1):
                    send_reason = "REOPENED_TO_SALE"
                elif prev_status in (0, 1) and cur_status == CANCELLED:
                    send_reason = "CANCELLED"
            # Flags opcionales, por si quieres afinar (no necesarias ya)
            if send_reason is None and prev_status is None and cur_status is not None:
                if args.email_new_accepted and cur_status in (0, 1):
                    send_reason = "NEW_ACCEPTED"
                elif args.email_new_any:
                    send_reason = "NEW_ANY"

        if args.send_email and send_reason:
            if send_reason in ("REOPENED_TO_SALE", "NEW_ACCEPTED", "NEW_ANY"):
                subject = build_email_subject(doc, rows)
                html = build_html_table(doc, rows)
                send_email(subject, html)
                sent_vendidos += 1
                if not args.quiet:
                    print(f"Email enviado (VENDIDO) — motivo: {send_reason}.")
            elif send_reason == "CANCELLED":
                mat_lines = [ln for ln in iter_document_lines(doc) if not ln.get("is_transport")]
                html_lines = ""
                for ln in mat_lines:
                    nombre = ln.get("name") or "-"
                    cantidad = int(ln.get("qty") or 0)
                    html_lines += f"<li>{nombre} — <b>{cantidad}</b> uds</li>"
                if not html_lines:
                    html_lines = "<li>Sin líneas de material</li>"

                html_cancel = f"""
                <div style='font-family:system-ui,Segoe UI,Roboto,Arial,sans-serif'>
                    <h3 style='margin:0 0 8px;color:#b30000'>❌ Pedido CANCELADO — {number}</h3>
                    <p style='margin:0 0 8px'>
                        Cliente: <b>{cliente}</b><br>
                        Fecha: <b>{to_date_label(doc)}</b><br>
                        Estado: <b>{status_label(cur_status)}</b>
                    </p>
                    <p style='margin:10px 0 4px;font-weight:bold'>Material cancelado:</p>
                    <ul style='margin:0 0 10px 20px;padding:0'>
                        {html_lines}
                    </ul>
                </div>
                """

                # Destinatarios normales + específicos de cancelación (sin duplicados)
                base = [e.strip() for e in (MAIL_TO or "").split(",") if e.strip()]
                extra = [e.strip() for e in (MAIL_CANCEL_TO or "").split(",") if e.strip()]
                merged = []
                for e in base + extra:
                    if e and e not in merged:
                        merged.append(e)

                send_email(
                    f"CANCELADO pedido {number} — {cliente}",
                    html_cancel,
                    to_recipients=merged
                )
                sent_cancelados += 1
                if not args.quiet:
                    print("Email enviado (CANCELADO).")

        # Actualizar estado conocido (id -> status) ya normalizado (0/1/-1)
        if cur_status is not None:
            status_map[doc_id] = cur_status
        else:
            # Si no pudimos normalizar estado pero es la primera vez que lo vemos,
            # persistimos un marcador neutro para que no vuelva a disparar NEW_ANY.
            if first_seen:
                status_map[doc_id] = "seen"

    # Guardado final de mapa de estados
    save_status_map(args.status_file, status_map)

    t1 = time.perf_counter()
    if args.quiet:
        print(f"[ok] docs={len(docs)} vendidos={sent_vendidos} cancelados={sent_cancelados} t={t1 - t0:.2f}s")
    else:
        print(f"\n[resumen] Documentos: {len(docs)} | Emails VENDIDO: {sent_vendidos} | Emails CANCELADO: {sent_cancelados} | Tiempo: {t1 - t0:.2f}s")

if __name__ == "__main__":
    try:
        main()
    except requests.HTTPError as e:
        print(f"[HTTPError] {e.response.status_code}: {e.response.text}")
        sys.exit(1)
    except Exception as e:
        print(f"[ERROR] {e}")
        sys.exit(1)
