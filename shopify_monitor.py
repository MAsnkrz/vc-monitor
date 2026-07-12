import json
import os
import re
import time
import requests
from datetime import datetime, timezone

# --- CONFIG -------------------------------------------------------------------

BASE_URL        = "https://www.verycosmetics.co.uk"
DISCORD_WEBHOOK = os.getenv(
    "DISCORD_WEBHOOK",
    "https://discord.com/api/webhooks/929897869450809345/"
    "HTxoTfFFJCVX82M0hdq-2uUXwYXiuRajhFjPQRJYjI2h2j4wn6RZZEnXiu1sR5XsUKEv"
)
CHECK_INTERVAL  = int(os.getenv("CHECK_INTERVAL", "300"))
RUN_ONCE        = os.getenv("RUN_ONCE", "false").lower() == "true"
SNAPSHOT_FILE   = "snapshot.json"
PAGE_SIZE       = 250
REQUEST_DELAY   = 0.5

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

COLOUR_NEW       = 0x57F287
COLOUR_IN_STOCK  = 0x3498DB
COLOUR_OUT_STOCK = 0xE74C3C
COLOUR_QTY_UP    = 0xF1C40F

POUND = "\u00a3"

# --- UTILITIES ----------------------------------------------------------------

def ts():
    return datetime.now(timezone.utc).strftime("%H:%M:%S UTC")

def vat_price(price_str):
    try:
        return "{:.2f}".format(float(price_str) * 1.2)
    except (ValueError, TypeError):
        return str(price_str)

def barcode_field(code):
    value = "`{}`".format(code) if code else "-"
    return {"name": "Barcode", "value": value, "inline": True}

def selleramp_field(barcode, price_str):
    if not barcode:
        return {"name": "SellerAmp", "value": "-", "inline": False}
    price = vat_price(price_str)
    url   = "https://sas.selleramp.com/sas/lookup/?search_term={}&sas_cost_price={}".format(barcode, price)
    return {"name": "SellerAmp", "value": "[Open in SellerAmp]({})".format(url), "inline": False}

def find_pid(state, handle):
    for pid, p in state.items():
        if p.get("handle") == handle:
            return pid
    return None

# --- FETCHING -----------------------------------------------------------------

def fetch_all_products():
    all_products = []
    page = 1
    while True:
        url = "{}/products.json?limit={}&page={}".format(BASE_URL, PAGE_SIZE, page)
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
            r.raise_for_status()
            batch = r.json().get("products", [])
        except Exception as exc:
            print("[{}] Fetch error (page {}): {}".format(ts(), page, exc))
            break
        if not batch:
            break
        all_products.extend(batch)
        print("[{}] Page {} - {} products fetched so far".format(ts(), page, len(all_products)))
        if len(batch) < PAGE_SIZE:
            break
        page += 1
        time.sleep(REQUEST_DELAY)
    return all_products


def fetch_product_page_data(handle):
    result = {"barcodes": {}, "qty": None}
    try:
        r = requests.get("{}/products/{}.json".format(BASE_URL, handle), headers=HEADERS, timeout=15)
        r.raise_for_status()
        variants = r.json().get("product", {}).get("variants", [])
        result["barcodes"] = {str(v["id"]): v.get("barcode", "") or "" for v in variants}
    except Exception as exc:
        print("[{}] Barcode error ({}): {}".format(ts(), handle, exc))
    try:
        r = requests.get("{}/products/{}".format(BASE_URL, handle), headers=HEADERS, timeout=15)
        r.raise_for_status()
        m = re.search(r"var\s+QTY\s*=\s*(\d+)", r.text)
        if m:
            result["qty"] = int(m.group(1))
    except Exception as exc:
        print("[{}] QTY error ({}): {}".format(ts(), handle, exc))
    return result


def fetch_qty(handle):
    try:
        r = requests.get("{}/products/{}".format(BASE_URL, handle), headers=HEADERS, timeout=15)
        r.raise_for_status()
        m = re.search(r"var\s+QTY\s*=\s*(\d+)", r.text)
        return int(m.group(1)) if m else None
    except Exception:
        return None

# --- STATE --------------------------------------------------------------------

def flatten_variants(product):
    pack_size_pos = None
    for i, opt in enumerate(product.get("options", []), start=1):
        if "pack" in opt.get("name", "").lower():
            pack_size_pos = i
            break
    result = {}
    for v in product.get("variants", []):
        result[str(v["id"])] = {
            "title":     v.get("title", ""),
            "available": v.get("available", False),
            "price":     v.get("price", ""),
            "sku":       v.get("sku", ""),
            "pack_size": v.get("option{}".format(pack_size_pos), "") or "" if pack_size_pos else "",
        }
    return result


def build_state(products):
    state = {}
    for p in products:
        pid   = str(p["id"])
        image = p["images"][0].get("src", "") if p.get("images") else ""
        state[pid] = {
            "title":    p.get("title", "Unknown"),
            "handle":   p.get("handle", ""),
            "image":    image,
            "qty":      None,
            "variants": flatten_variants(p),
        }
    return state


def load_snapshot():
    if os.path.exists(SNAPSHOT_FILE):
        try:
            with open(SNAPSHOT_FILE, "r") as fh:
                return json.load(fh)
        except json.JSONDecodeError as exc:
            backup = "{}.corrupted.{}".format(SNAPSHOT_FILE, int(time.time()))
            print("[{}] Snapshot corrupted ({}), backing up to {} and starting fresh".format(
                ts(), exc, backup))
            try:
                os.rename(SNAPSHOT_FILE, backup)
            except OSError:
                pass
            return {}
    return {}


def save_snapshot(state):
    tmp = SNAPSHOT_FILE + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(state, fh)
    os.replace(tmp, SNAPSHOT_FILE)

# --- DISCORD ------------------------------------------------------------------

def send_embed(title, description, colour, url="", image="", fields=None):
    embed = {
        "title":       title[:256],
        "description": description[:2048],
        "color":       colour,
        "timestamp":   datetime.now(timezone.utc).isoformat(),
        "footer":      {"text": "Very Cosmetics Monitor"},
    }
    if url:    embed["url"]       = url
    if image:  embed["thumbnail"] = {"url": image}
    if fields: embed["fields"]    = fields[:25]
    try:
        r = requests.post(DISCORD_WEBHOOK, json={"embeds": [embed]}, timeout=10)
        r.raise_for_status()
    except Exception as exc:
        print("[{}] Discord error: {}".format(ts(), exc))


def variant_fields(v, barcode, qty, old_qty=None):
    qty_value = "-"
    if qty is not None:
        if old_qty is not None and old_qty != qty:
            qty_value = "{} -> **{}**".format(old_qty, qty)
        else:
            qty_value = str(qty)
    fields = [
        {"name": "Price (inc. VAT)", "value": "{}{}".format(POUND, vat_price(v["price"])), "inline": True},
        {"name": "SKU",              "value": v["sku"] or "-",                              "inline": True},
        barcode_field(barcode),
        {"name": "Qty Available",    "value": qty_value,                                    "inline": True},
    ]
    if v.get("pack_size"):
        fields.append({"name": "Pack Size", "value": v["pack_size"], "inline": True})
    fields.append(selleramp_field(barcode, v["price"]))
    return fields

# --- NOTIFICATIONS ------------------------------------------------------------

def notify_new_product(product, new_state):
    variants  = product["variants"]
    available = sum(1 for v in variants.values() if v["available"])
    page_data = fetch_product_page_data(product["handle"])
    barcodes  = page_data["barcodes"]
    qty       = page_data["qty"]
    pid = find_pid(new_state, product["handle"])
    if pid:
        new_state[pid]["qty"] = qty
    fields = []
    for vid, v in list(variants.items())[:4]:
        status  = "In stock" if v["available"] else "Out of stock"
        barcode = barcodes.get(vid, "")
        fields.append({
            "name":   v["title"] if v["title"] != "Default Title" else "Default",
            "value":  "{}\n{}{} inc. VAT".format(status, POUND, vat_price(v["price"])),
            "inline": True,
        })
        fields.append(barcode_field(barcode))
        fields.append(selleramp_field(barcode, v["price"]))
    if qty is not None:
        fields.append({"name": "Qty Available", "value": str(qty), "inline": True})
    send_embed(
        title       = "New arrival: {}".format(product["title"]),
        description = "{}/{} variants in stock".format(available, len(variants)),
        colour      = COLOUR_NEW,
        url         = "{}/products/{}".format(BASE_URL, product["handle"]),
        image       = product["image"],
        fields      = fields,
    )


def notify_stock_change(product, variant_id, variant_title, old_v, new_v, new_state):
    went_in  = not old_v["available"] and new_v["available"]
    went_out = old_v["available"]     and not new_v["available"]
    if not went_in and not went_out:
        return
    page_data = fetch_product_page_data(product["handle"])
    barcode   = page_data["barcodes"].get(variant_id, "")
    qty       = page_data["qty"]
    pid = find_pid(new_state, product["handle"])
    if pid:
        new_state[pid]["qty"] = qty
    if went_in:
        title  = "Back in stock: {}".format(product["title"])
        desc   = "**{}** is now available".format(variant_title)
        colour = COLOUR_IN_STOCK
    else:
        title  = "Out of stock: {}".format(product["title"])
        desc   = "**{}** just sold out".format(variant_title)
        colour = COLOUR_OUT_STOCK
    send_embed(
        title       = title,
        description = desc,
        colour      = colour,
        url         = "{}/products/{}".format(BASE_URL, product["handle"]),
        image       = product["image"],
        fields      = variant_fields(new_v, barcode, qty),
    )


def notify_qty_increase(product, old_qty, new_qty, new_state):
    pid = find_pid(new_state, product["handle"])
    if pid:
        new_state[pid]["qty"] = new_qty
    variants  = product["variants"]
    first_v   = next((v for v in variants.values() if v["available"]),
                     next(iter(variants.values()), {}))
    first_vid = next((vid for vid, v in variants.items() if v["available"]), "")
    page_data = fetch_product_page_data(product["handle"])
    barcode   = page_data["barcodes"].get(first_vid, "")
    send_embed(
        title       = "Restocked: {}".format(product["title"]),
        description = "Quantity increased from **{}** to **{}**".format(old_qty, new_qty),
        colour      = COLOUR_QTY_UP,
        url         = "{}/products/{}".format(BASE_URL, product["handle"]),
        image       = product["image"],
        fields      = variant_fields(first_v, barcode, new_qty, old_qty),
    )

# --- DIFF ---------------------------------------------------------------------

def diff_and_notify(old_state, new_state):
    changes = 0
    for pid, new_p in new_state.items():
        if pid not in old_state:
            has_stock = any(v["available"] for v in new_p["variants"].values())
            if not has_stock:
                print("[{}] NEW PRODUCT (skipped - no stock): {}".format(ts(), new_p["title"]))
                continue
            print("[{}] NEW PRODUCT: {}".format(ts(), new_p["title"]))
            notify_new_product(new_p, new_state)
            changes += 1
            continue
        old_p        = old_state[pid]
        old_variants = old_p["variants"]
        new_variants = new_p["variants"]
        if new_p["qty"] is None and old_p.get("qty") is not None:
            new_state[pid]["qty"] = old_p["qty"]
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
                print("[{}] STOCK CHANGE: {} - {}".format(ts(), new_p["title"], vtitle))
                notify_stock_change(new_p, vid, vtitle, old_v, new_v, new_state)
                changes += 1
        old_qty      = old_p.get("qty")
        is_available = any(v["available"] for v in new_variants.values())
        if old_qty is not None and is_available:
            new_qty = fetch_qty(new_p["handle"])
            time.sleep(0.2)   # only delay when we actually fetched a page
            if new_qty is not None:
                new_state[pid]["qty"] = new_qty
                if new_qty > old_qty:
                    print("[{}] QTY INCREASE: {} {} -> {}".format(ts(), new_p["title"], old_qty, new_qty))
                    notify_qty_increase(new_p, old_qty, new_qty, new_state)
                    changes += 1
    if changes == 0:
        print("[{}] No changes detected.".format(ts()))
    else:
        print("[{}] {} change(s) notified.".format(ts(), changes))

# --- MAIN ---------------------------------------------------------------------

def main():
    mode = " (single run)" if RUN_ONCE else " - checking every {}s".format(CHECK_INTERVAL)
    print("Very Cosmetics monitor starting{}".format(mode))
    print("  Store    : {}".format(BASE_URL))
    print("  Snapshot : {}".format(SNAPSHOT_FILE))
    while True:
        products = fetch_all_products()
        if not products:
            print("[{}] No products returned - retrying after interval.".format(ts()))
            if RUN_ONCE:
                return
            time.sleep(CHECK_INTERVAL)
            continue
        new_state = build_state(products)
        old_state = load_snapshot()
        if old_state:
            diff_and_notify(old_state, new_state)
        else:
            print("[{}] First run - snapshotted {} products. Monitoring from next check.".format(
                ts(), len(new_state)))
        save_snapshot(new_state)
        if RUN_ONCE:
            print("[{}] Single run complete.".format(ts()))
            return
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()
