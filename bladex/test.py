import json
import time
from playwright.sync_api import sync_playwright

CONFIG_FILE = "test_products.json"
ACCOUNT_FILE = "account.json"
PROFILE_DIR = "sen_sen_profile"

PAGE_TIMEOUT = 3000
BUTTON_TIMEOUT = 1500
LOOP_SLEEP = 0.4
MAX_ROUNDS = 0  # 0 = 無限輪巡


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def fast_login(page):
    print("[登入] 開始自動登入...")

    account = load_json(ACCOUNT_FILE)
    username = account.get("phone", "")
    password = account.get("password", "")

    try:
        page.goto(
            "https://www.sen-sen.com.tw/account/login",
            wait_until="domcontentloaded",
            timeout=PAGE_TIMEOUT,
        )

        page.fill("input#login", username, timeout=1000)
        page.fill("input#password", password, timeout=1000)
        page.click("button.btn.submit[type='submit']", timeout=1000)

        try:
            page.wait_for_url(lambda url: "/account/login" not in url, timeout=5000)
        except Exception:
            pass

        print("[登入] 完成")
        return True

    except Exception as e:
        print("[登入失敗]", e)
        page.pause()
        return False


def open_product(page, collection_url, product):
    name = product["name"]
    url = product.get("url", "").strip()

    try:
        if url:
            print(f"[商品] 直進商品頁：{name}")
            page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            return True

        print(f"[商品] 無網址，從列表尋找：{name}")
        page.goto(collection_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.get_by_text(name, exact=False).first.click(timeout=BUTTON_TIMEOUT)
        return True

    except Exception:
        print(f"[跳過] 商品頁開啟失敗：{name}")
        return False


def set_quantity(page, quantity):
    if quantity <= 1:
        return

    for selector in ["input[name='quantity']", "input[type='number']"]:
        try:
            page.fill(selector, str(quantity), timeout=700)
            print(f"[數量] 已設定 {quantity}")
            return
        except Exception:
            pass


def verify_cart(page, product_name):
    print("[驗證] 前往購物車確認商品是否存在")

    try:
        page.goto(
            "https://www.sen-sen.com.tw/cart",
            wait_until="domcontentloaded",
            timeout=5000
        )

        page.wait_for_timeout(1000)
        body = page.locator("body").inner_text(timeout=3000)

        keyword = product_name[:10]

        if keyword in body:
            print("[成功] 購物車內確認有商品")
            return True

        print("[失敗] 購物車內沒有找到商品")
        return False

    except Exception as e:
        print("[失敗] 無法進入購物車驗證")
        print(e)
        return False


def try_add_to_cart(page, quantity, product_name):
    page.wait_for_timeout(200)
    set_quantity(page, quantity)

    try:
        btn = page.locator("button.addToCart:visible").first
        btn.wait_for(state="visible", timeout=3000)
        btn.scroll_into_view_if_needed()
        btn.click(timeout=3000)

        print("[點擊] 已點擊加入購物車")
        page.wait_for_timeout(1200)

        return verify_cart(page, product_name)

    except Exception as e:
        print("[無貨/失敗] 找不到可點擊的加入購物車按鈕")
        print(e)
        return False


def rush_loop(page, config):
    collection_url = config.get("collection_url", "")
    quantity = int(config.get("quantity", 1))
    products = config["products"]
    dry_run = bool(config.get("dry_run", True))

    round_count = 0
    start_time = time.time()

    while True:
        round_count += 1
        print(f"\n========== 第 {round_count} 輪巡 ==========")

        for product in products:
            name = product["name"]

            if not open_product(page, collection_url, product):
                continue

            if try_add_to_cart(page, quantity, name):
                print(f"[命中] 成功加入：{name}")

                if dry_run:
                    print("[測試模式] 已停止，不會送出訂單")
                    page.pause()
                else:
                    print("[正式模式] 已進入購物車")
                    page.pause()

                return True

        if MAX_ROUNDS > 0 and round_count >= MAX_ROUNDS:
            print("[結束] 已達最大輪巡次數")
            return False

        elapsed = int(time.time() - start_time)
        print(f"[等待] 本輪無貨，{LOOP_SLEEP} 秒後重試，已執行 {elapsed} 秒")
        page.wait_for_timeout(int(LOOP_SLEEP * 1000))


def main():
    config = load_json(CONFIG_FILE)

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            PROFILE_DIR,
            headless=False,
            viewport={"width": 1400, "height": 900}
        )

        page = context.pages[0] if context.pages else context.new_page()
        page.set_default_timeout(PAGE_TIMEOUT)

        print("========== 森森測試版（購物車驗證） ==========")
        print(f"CONFIG_FILE = {CONFIG_FILE}")
        print("===========================================")

        if not fast_login(page):
            context.close()
            return

        rush_loop(page, config)

        context.close()


if __name__ == "__main__":
    main()