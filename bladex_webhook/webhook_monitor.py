import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from html import unescape
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit, urlunsplit


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_FILE = os.path.join(BASE_DIR, ".env")
CONFIG_FILE = os.path.join(BASE_DIR, "products.json")
STATE_FILE = os.path.join(BASE_DIR, "state.json")
BASE_URL = "https://www.oneone.com.tw"


def load_env(path):
    if not os.path.exists(path):
        return

    with open(path, "r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue

            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


def load_json(path, default):
    if not os.path.exists(path):
        return default

    with open(path, "r", encoding="utf-8") as file:
        return json.load(file)


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)


def send_discord_message(webhook_url, content):
    payload = json.dumps({"content": content}, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        webhook_url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "oneone-monitor/1.0",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            if response.status >= 300:
                raise RuntimeError(f"Discord webhook failed with HTTP {response.status}")
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Discord webhook failed with HTTP {error.code}: {body}") from error


def fetch_html(url):
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0 Safari/537.36"
            )
        },
    )

    with urllib.request.urlopen(request, timeout=20) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


def clean_text(value):
    value = re.sub(r"<[^>]+>", " ", value)
    value = unescape(value)
    return re.sub(r"\s+", " ", value).strip()


def page_url(search_url, page):
    parts = urlsplit(search_url)
    query = parse_qs(parts.query, keep_blank_values=True)
    query["page"] = [str(page)]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query, doseq=True), parts.fragment))


def parse_max_page(html):
    pages = [int(match) for match in re.findall(r"[?&](?:amp;)?page=(\d+)", html)]
    return max(pages) if pages else 1


def parse_listing_products(html):
    products = []
    seen = set()
    product_ids = re.findall(r'href="/product/(\d+)"', html)

    for product_id in product_ids:
        if product_id in seen:
            continue
        seen.add(product_id)

        start = html.find(f'href="/product/{product_id}"')
        end = html.find('<div class="col-lg-3 col-md-6 col-6 mb-4 resetmr">', start + 1)
        block = html[start:end if end != -1 else len(html)]

        title_match = re.search(r'<img[^>]+(?:alt|title)="([^"]+)"', block)
        remain_match = re.search(rf"data-product-remain-{product_id}[^>]*>\s*(\d+)\s*</span>\s*/\s*(\d+)", block)
        price_match = re.search(r"(?:售價|特價)\s*<span[^>]*>\s*(\d+)", block)

        if not title_match or not remain_match:
            continue

        products.append(
            {
                "id": product_id,
                "name": clean_text(title_match.group(1)) if title_match else f"product {product_id}",
                "url": urljoin(BASE_URL, f"/product/{product_id}"),
                "price": int(price_match.group(1)) if price_match else None,
                "remain": int(remain_match.group(1)) if remain_match else None,
                "total": int(remain_match.group(2)) if remain_match else None,
            }
        )

    return products


def parse_product_price(html, product_id):
    data_layer_match = re.search(
        rf"'product_id':\s*'{re.escape(product_id)}'.*?'price':\s*(\d+)",
        html,
        flags=re.S,
    )
    if data_layer_match:
        return int(data_layer_match.group(1))

    price_match = re.search(
        r"(?:售價|特價)\s*</span>\s*<span[^>]*>\s*(\d+)",
        html,
    )
    if price_match:
        return int(price_match.group(1))

    return None


def parse_detail(product):
    html = fetch_html(product["url"])

    price = parse_product_price(html, product["id"])
    if price is not None:
        product["price"] = price

    table_match = re.search(r'<tbody class="pd-table">(.*?)</tbody>', html, flags=re.S)
    prizes = []

    if table_match:
        rows = re.findall(r"<tr[^>]*>(.*?)</tr>", table_match.group(1), flags=re.S)
        for row in rows:
            cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, flags=re.S)
            if len(cells) < 2:
                continue

            name = clean_text(cells[0])
            count_text = clean_text(cells[1])
            count_match = re.search(r"(\d+)\s*/\s*(\d+)", count_text)

            if name == "獎項" or not count_match:
                continue

            prizes.append(
                {
                    "name": name,
                    "remaining": int(count_match.group(1)),
                    "total": int(count_match.group(2)),
                }
            )

    product["prizes"] = prizes
    return product


def is_big_prize(prize, product_total, max_ratio):
    if not product_total:
        return False

    return (prize["total"] / product_total) < max_ratio


def snapshot_product(product):
    return {
        "price": product.get("price"),
        "remain": product.get("remain"),
        "total": product.get("total"),
        "prizes": product.get("prizes", []),
    }


def state_snapshot(entry):
    if isinstance(entry, dict) and "snapshot" in entry:
        return entry["snapshot"]

    return entry


def state_last_notified_at(entry):
    if isinstance(entry, dict):
        return float(entry.get("last_notified_at", 0) or 0)

    return 0.0


def product_changed(previous, current):
    if not previous:
        return True

    return state_snapshot(previous) != snapshot_product(current)


def format_product_message(product, max_ratio):
    product_total = product.get("total")
    big_prizes = [
        prize
        for prize in product.get("prizes", [])
        if is_big_prize(prize, product_total, max_ratio)
    ]
    probability = big_prize_probability(product, max_ratio)
    prize_lines = [
        f"- {prize['name']}: {prize['remaining']} / {prize['total']}"
        for prize in big_prizes
    ]

    if not prize_lines:
        percent = int(max_ratio * 100)
        prize_lines.append(f"- 未找到總數低於全部品項 {percent}% 的獎項")

    return "\n".join(
        [
            f"oneone 監測更新：{product['name']}",
            f"價格：{product.get('price')}",
            f"總剩餘：{product.get('remain')} / {product.get('total')}",
            f"大獎中獎率：約 {probability * 100:.2f}%",
            "大獎：",
            *prize_lines,
            product["url"],
        ]
    )


def big_prize_probability(product, max_ratio):
    product_remain = product.get("remain")
    product_total = product.get("total")

    if not product_remain:
        return 0.0

    big_prize_remaining = sum(
        prize["remaining"]
        for prize in product.get("prizes", [])
        if is_big_prize(prize, product_total, max_ratio)
    )

    return big_prize_remaining / product_remain


def scan_once(config):
    search_url = config["search_url"]
    first_html = fetch_html(page_url(search_url, 1))
    max_page = parse_max_page(first_html)
    products = []

    for page in range(1, max_page + 1):
        html = first_html if page == 1 else fetch_html(page_url(search_url, page))
        page_products = parse_listing_products(html)
        print(f"[page {page}/{max_page}] {len(page_products)} products")
        products.extend(page_products)
        time.sleep(0.3)

    detailed_products = []
    for index, product in enumerate(products, start=1):
        print(f"[detail {index}/{len(products)}] {product['name']}")
        detailed_products.append(parse_detail(product))
        time.sleep(0.3)

    return detailed_products


def monitor_once(config, state, webhook_url):
    big_prize_max_ratio = float(config.get("big_prize_max_ratio", 0.1))
    notify_min_probability = float(config.get("notify_min_big_prize_probability", 0.1))
    cooldown_seconds = int(config.get("notification_cooldown_seconds", 300))
    notify_on_first_run = bool(config.get("notify_on_first_run", True))
    notify_only_changes = bool(config.get("notify_only_changes", True))
    products = scan_once(config)
    sent = 0

    for product in products:
        product_id = product["id"]
        previous = state.get(product_id)
        last_notified_at = state_last_notified_at(previous)
        is_first_seen = previous is None
        changed = product_changed(previous, product)
        probability = big_prize_probability(product, big_prize_max_ratio)
        should_report_probability = probability > notify_min_probability
        cooldown_expired = (time.time() - last_notified_at) >= cooldown_seconds

        if should_report_probability and cooldown_expired and (
            (changed and (notify_on_first_run or not is_first_seen)) or not notify_only_changes
        ):
            send_discord_message(webhook_url, format_product_message(product, big_prize_max_ratio))
            sent += 1
            last_notified_at = time.time()
            time.sleep(0.8)

        state[product_id] = {
            "snapshot": snapshot_product(product),
            "last_notified_at": last_notified_at,
        }

    save_json(STATE_FILE, state)
    return len(products), sent


def main():
    load_env(ENV_FILE)

    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if not webhook_url:
        raise SystemExit("Missing DISCORD_WEBHOOK_URL in .env")

    if "--test" in sys.argv:
        send_discord_message(webhook_url, "發送監測機器人測試")
        print("Webhook test message sent.")
        return

    config = load_json(CONFIG_FILE, {})
    if not config.get("search_url"):
        raise SystemExit("Missing search_url in products.json")

    interval = int(os.environ.get("CHECK_INTERVAL_SECONDS", "300"))
    state = load_json(STATE_FILE, {})

    try:
        while True:
            total, sent = monitor_once(config, state, webhook_url)
            print(f"[done] scanned {total} products, sent {sent} messages")

            if "--once" in sys.argv:
                break

            time.sleep(interval)

    except (urllib.error.URLError, TimeoutError, RuntimeError) as error:
        print(f"Monitor failed: {error}")


if __name__ == "__main__":
    main()
