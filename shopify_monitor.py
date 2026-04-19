## “””
Shopify Stock Monitor - Discord Webhook Notifier

Monitors the ENTIRE Very Cosmetics store for:

- New products (new product ID appears in store)
- Variants going in / out of stock
- Quantity increases on products with a stored qty

How it works:

1. Fetches ALL products from the store with pagination
1. Compares against snapshot.json from the previous run
1. Fires Discord embeds for any changes found
1. Saves a fresh snapshot so the next run can compare

Run:   py shopify_monitor.py
Deps:  pip install requests
“””

import json
import os
import re
import time
import requests
from datetime import datetime, timezone

# ─── CONFIG ────────────────────────────────────────────────────────────────────

BASE_URL        = “https://www.verycosmetics.co.uk”
DISCORD_WEBHOOK = os.getenv(
“DISCORD_WEBHOOK”,
“https://discord.com/api/webhooks/929897869450809345/”
“HTxoTfFFJCVX82M0hdq-2uUXwYXiuRajhFjPQRJYjI2h2j4wn6RZZEnXiu1sR5XsUKEv”
)
CHECK_INTERVAL  = int(os.getenv(“CHECK_INTERVAL”, “300”))   # seconds (5 min)
RUN_ONCE        = os.getenv(“RUN_ONCE”, “false”).lower() == “true”  # set by GitHub Actions
SNAPSHOT_FILE   = “snapshot.json”
PAGE_SIZE       = 250     # max Shopify allows per page
REQUEST_DELAY   = 0.5     # polite delay between paginated fetches (seconds)

HEADERS = {“User-Agent”: “Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36”}

# Discord embed colours

COLOUR_NEW       = 0x57F287   # green  — new product
COLOUR_IN_STOCK  = 0x3498DB   # blue   — back in stock
COLOUR_OUT_STOCK = 0xE74C3C   # red    — out of stock
COLOUR_QTY_UP    = 0xF1C40F   # yellow — qty increased

# ─── UTILITIES ─────────────────────────────────────────────────────────────────

def ts():
return datetime.now(timezone.utc).strftime(”%H:%M:%S UTC”)

def vat_price(price_str):
try:
return f”{float(price_str) * 1.2:.2f}”
except (ValueError, TypeError):
return str(price_str)

def barcode_field(code):
return {“name”: “Barcode”, “value”: f”`{code}`” if code else “—”, “inline”: True}

def find_pid(state, handle):
for pid, p in state.items():
if p.get(“handle”) == handle:
return pid
return None

# ─── FETCHING ──────────────────────────────────────────────────────────────────

def fetch_all_products():
“”“Paginate through /products.json and return every product in the store.”””
all_products = []
page = 1
while True:
url = f”{BASE_URL}/products.json?limit={PAGE_SIZE}&page={page}”
try:
r = requests.get(url, headers=HEADERS, timeout=15)
r.raise_for_status()
batch = r.json().get(“products”, [])
except Exception as exc:
print(f”[{ts()}] Fetch error (page {page}): {exc}”)
break

```
    if not batch:
        break

    all_products.extend(batch)
    print(f"[{ts()}] Page {page} — {len(all_products)} products fetched so far")

    if len(batch) < PAGE_SIZE:
        break

    page += 1
    time.sleep(REQUEST_DELAY)

return all_products
```

def fetch_product_page_data(handle):
“””
Returns {“barcodes”: {variant_id: str}, “qty”: int|None}
Barcodes from product JSON, qty from ‘var QTY = N;’ in page HTML.
“””
result = {“barcodes”: {}, “qty”: None}
try:
r = requests.get(f”{BASE_URL}/products/{handle}.json”, headers=HEADERS, timeout=15)
r.raise_for_status()
variants = r.json().get(“product”, {}).get(“variants”, [])
result[“barcodes”] = {str(v[“id”]): v.get(“barcode”, “”) or “” for v in variants}
except Exception as exc:
print(f”[{ts()}] Barcode error ({handle}): {exc}”)

```
try:
    r = requests.get(f"{BASE_URL}/products/{handle}", headers=HEADERS, timeout=15)
    r.raise_for_status()
    m = re.search(r'var\s+QTY\s*=\s*(\d+)', r.text)
    if m:
        result["qty"] = int(m.group(1))
except Exception as exc:
    print(f"[{ts()}] QTY error ({handle}): {exc}")

return result
```

def fetch_qty(handle):
“”“Quick qty-only fetch for the periodic qty-increase check.”””
try:
r = requests.get(f”{BASE_URL}/products/{handle}”, headers=HEADERS, timeout=15)
r.raise_for_status()
m = re.search(r’var\s+QTY\s*=\s*(\d+)’, r.text)
return int(m.group(1)) if m else None
except Exception:
return None

# ─── STATE ─────────────────────────────────────────────────────────────────────

def flatten_variants(product):
pack_size_pos = None
for i, opt in enumerate(product.get(“options”, []), start=1):
if “pack” in opt.get(“name”, “”).lower():
pack_size_pos = i
break

```
result = {}
for v in product.get("variants", []):
    result[str(v["id"])] = {
        "title":     v.get("title", ""),
        "available": v.get("available", False),
        "price":     v.get("price", ""),
        "sku":       v.get("sku", ""),
        "pack_size": v.get(f"option{pack_size_pos}", "") or "" if pack_size_pos else "",
    }
return result
```

def build_state(products):
“””
{product_id: {title, handle, image, qty, variants}}
qty is None until populated after a page fetch.
“””
state = {}
for p in products:
pid   = str(p[“id”])
image = p[“images”][0].get(“src”, “”) if p.get(“images”) else “”
state[pid] = {
“title”:    p.get(“title”, “Unknown”),
“handle”:   p.get(“handle”, “”),
“image”:    image,
“qty”:      None,
“variants”: flatten_variants(p),
}
return state

def load_snapshot():
if os.path.exists(SNAPSHOT_FILE):
with open(SNAPSHOT_FILE, “r”) as fh:
return json.load(fh)
return {}

def save_snapshot(state):
with open(SNAPSHOT_FILE, “w”) as fh:
json.dump(state, fh)

# ─── DISCORD ───────────────────────────────────────────────────────────────────

def send_embed(title, description, colour, url=””, image=””, fields=None):
embed = {
“title”:       title[:256],
“description”: description[:2048],
“color”:       colour,
“timestamp”:   datetime.now(timezone.utc).isoformat(),
“footer”:      {“text”: “Very Cosmetics Monitor”},
}
if url:    embed[“url”]       = url
if image:  embed[“thumbnail”] = {“url”: image}
if fields: embed[“fields”]    = fields[:25]
try:
r = requests.post(DISCORD_WEBHOOK, json={“embeds”: [embed]}, timeout=10)
r.raise_for_status()
except Exception as exc:
print(f”[{ts()}] Discord error: {exc}”)

def selleramp_field(barcode, price_str):
“”“SellerAmp SAS lookup link using barcode and VAT price.”””
if not barcode:
return {“name”: “SellerAmp”, “value”: “—”, “inline”: False}
price = vat_price(price_str)
url   = f”https://sas.selleramp.com/sas/lookup/?search_term={barcode}&sas_cost_price={price}”
return {“name”: “SellerAmp”, “value”: f”[🔍 Open in SellerAmp]({url})”, “inline”: False}

def variant_fields(v, barcode, qty, old_qty=None):
“”“Standard fields for a variant — price, SKU, barcode, qty, pack size, SellerAmp.”””
qty_value = “—”
if qty is not None:
qty_value = f”{old_qty} → **{qty}**” if (old_qty is not None and old_qty != qty) else str(qty)

```
fields = [
    {"name": "Price (inc. VAT)", "value": f"£{vat_price(v['price'])}", "inline": True},
    {"name": "SKU",              "value": v["sku"] or "—",             "inline": True},
    barcode_field(barcode),
    {"name": "Qty Available",    "value": qty_value,                   "inline": True},
]
if v.get("pack_size"):
    fields.append({"name": "Pack Size", "value": v["pack_size"], "inline": True})
fields.append(selleramp_field(barcode, v["price"]))
return fields
```

# ─── NOTIFICATIONS ─────────────────────────────────────────────────────────────

def notify_new_product(product, new_state):
variants  = product[“variants”]
available = sum(1 for v in variants.values() if v[“available”])

```
page_data = fetch_product_page_data(product["handle"])
barcodes  = page_data["barcodes"]
qty       = page_data["qty"]

pid = find_pid(new_state, product["handle"])
if pid:
    new_state[pid]["qty"] = qty   # persist for next run's qty comparison

fields = []
for vid, v in list(variants.items())[:4]:
    status  = "✅ In stock" if v["available"] else "❌ Out of stock"
    barcode = barcodes.get(vid, "")
    fields.append({
        "name":   v["title"] if v["title"] != "Default Title" else "Default",
        "value":  f"{status}\n£{vat_price(v['price'])} inc. VAT",
        "inline": True,
    })
    fields.append(barcode_field(barcode))
    fields.append(selleramp_field(barcode, v["price"]))

if qty is not None:
    fields.append({"name": "Qty Available", "value": str(qty), "inline": True})

send_embed(
    title       = f"🆕 New arrival: {product['title']}",
    description = f"{available}/{len(variants)} variants in stock",
    colour      = COLOUR_NEW,
    url         = f"{BASE_URL}/products/{product['handle']}",
    image       = product["image"],
    fields      = fields,
)
```

def notify_stock_change(product, variant_id, variant_title, old_v, new_v, new_state):
went_in  = not old_v[“available”] and new_v[“available”]
went_out = old_v[“available”]     and not new_v[“available”]
if not went_in and not went_out:
return

```
page_data = fetch_product_page_data(product["handle"])
barcode   = page_data["barcodes"].get(variant_id, "")
qty       = page_data["qty"]

pid = find_pid(new_state, product["handle"])
if pid:
    new_state[pid]["qty"] = qty   # persist for next run

title  = (f"✅ Back in stock: {product['title']}" if went_in
          else f"❌ Out of stock: {product['title']}")
desc   = (f"**{variant_title}** is now available" if went_in
          else f"**{variant_title}** just sold out")
colour = COLOUR_IN_STOCK if went_in else COLOUR_OUT_STOCK

send_embed(
    title       = title,
    description = desc,
    colour      = colour,
    url         = f"{BASE_URL}/products/{product['handle']}",
    image       = product["image"],
    fields      = variant_fields(new_v, barcode, qty),
)
```

def notify_qty_increase(product, old_qty, new_qty, new_state):
pid = find_pid(new_state, product[“handle”])
if pid:
new_state[pid][“qty”] = new_qty

```
variants = product["variants"]
first_v  = next((v for v in variants.values() if v["available"]),
                next(iter(variants.values()), {}))
first_vid = next((vid for vid, v in variants.items() if v["available"]), "")

page_data = fetch_product_page_data(product["handle"])
barcode   = page_data["barcodes"].get(first_vid, "")

send_embed(
    title       = f"📦 Restocked: {product['title']}",
    description = f"Quantity increased from **{old_qty}** → **{new_qty}**",
    colour      = COLOUR_QTY_UP,
    url         = f"{BASE_URL}/products/{product['handle']}",
    image       = product["image"],
    fields      = variant_fields(first_v, barcode, new_qty, old_qty),
)
```

# ─── DIFF ──────────────────────────────────────────────────────────────────────

def diff_and_notify(old_state, new_state):
changes = 0

```
for pid, new_p in new_state.items():

    # ── New product ──────────────────────────────────────────────────────
    if pid not in old_state:
        print(f"[{ts()}] NEW PRODUCT: {new_p['title']}")
        notify_new_product(new_p, new_state)
        changes += 1
        continue

    old_p        = old_state[pid]
    old_variants = old_p["variants"]
    new_variants = new_p["variants"]

    # Carry forward stored qty if we haven't refreshed it yet this cycle
    if new_p["qty"] is None and old_p.get("qty") is not None:
        new_state[pid]["qty"] = old_p["qty"]

    # ── Variant availability changes ──────────────────────────────────────
    for vid, new_v in new_variants.items():
        if vid not in old_variants:
            if new_v["available"]:
                notify_stock_change(new_p, vid, new_v["title"],
                                    {"available": False}, new_v, new_state)
                changes += 1
            continue

        old_v  = old_variants[vid]
        vtitle = new_v["title"] if new_v["title"] != "Default Title" else new_p["title"]

        if old_v["available"] != new_v["available"]:
            print(f"[{ts()}] STOCK CHANGE: {new_p['title']} — {vtitle}")
            notify_stock_change(new_p, vid, vtitle, old_v, new_v, new_state)
            changes += 1

    # ── Qty increase check (only for products with a stored previous qty) ─
    old_qty = old_p.get("qty")
    is_available = any(v["available"] for v in new_variants.values())

    if old_qty is not None and is_available:
        new_qty = fetch_qty(new_p["handle"])
        if new_qty is not None:
            new_state[pid]["qty"] = new_qty
            if new_qty > old_qty:
                print(f"[{ts()}] QTY INCREASE: {new_p['title']} {old_qty} → {new_qty}")
                notify_qty_increase(new_p, old_qty, new_qty, new_state)
                changes += 1
        time.sleep(0.3)   # avoid rate-limiting across large catalogues

if changes == 0:
    print(f"[{ts()}] No changes detected.")
else:
    print(f"[{ts()}] {changes} change(s) notified.")
```

# ─── MAIN ──────────────────────────────────────────────────────────────────────

def main():
print(f”Very Cosmetics monitor starting”
f”{’ (single run)’ if RUN_ONCE else f’ — checking every {CHECK_INTERVAL}s’}”)
print(f”  Store    : {BASE_URL}”)
print(f”  Snapshot : {SNAPSHOT_FILE}\n”)

```
while True:
    products = fetch_all_products()
    if not products:
        print(f"[{ts()}] No products returned — retrying after interval.")
        if RUN_ONCE:
            return
        time.sleep(CHECK_INTERVAL)
        continue

    new_state = build_state(products)
    old_state = load_snapshot()

    if old_state:
        diff_and_notify(old_state, new_state)
    else:
        print(f"[{ts()}] First run — snapshotted {len(new_state)} products. "
              "Monitoring from next check.")

    save_snapshot(new_state)

    if RUN_ONCE:
        print(f"[{ts()}] Single run complete.")
        return

    time.sleep(CHECK_INTERVAL)
```

if **name** == “**main**”:
main()
