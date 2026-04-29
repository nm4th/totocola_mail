#!/usr/bin/env python3
"""
Shopify Messaging に下書きキャンペーンを自動作成する。

output/YYYY-MM-DD/manifest.json を読み、各HTMLを Shopify Messaging の
「キャンペーンを作成する → 独自コード」フローで下書きとして登録する。
件名・プレビュー・宛先（すべての購読者）まで入力する。配信は手動。

事前準備:
    pip install -r requirements.txt
    playwright install chromium
    # ローカルで scripts/init_session.py を実行してセッション保存
    # GitHub Actions では SHOPIFY_STORAGE_STATE Secret から復元

環境変数:
    SHOPIFY_STORE          : ストア識別子（例: totonoido）
    SHOPIFY_STORAGE_STATE  : storage_state.json の中身（base64エンコード）
                              ※ ローカル実行時は scripts/storage_state.json を直接使う
    NEWSLETTER_DATE        : output/{date}/ の日付（例: 2026-04-27）
    HEADLESS               : "false" にすると有頭モード（デバッグ用）

使い方:
    # ローカル
    SHOPIFY_STORE=totonoido NEWSLETTER_DATE=2026-04-27 \\
        python3 scripts/upload_to_shopify.py

    # GitHub Actions では workflow がこれらの env を渡す
"""

import asyncio
import base64
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
)

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
OUTPUT_BASE = REPO_ROOT / "output"
LOCAL_STORAGE_STATE = SCRIPT_DIR / "storage_state.json"
SCREENSHOT_DIR = SCRIPT_DIR / "screenshots"

# Playwright 各操作のデフォルトタイムアウト（ms）
DEFAULT_TIMEOUT = 30_000

# Shopify Messaging アプリのURL（ストア識別子で置換）
MESSAGING_URL_TEMPLATE = "https://admin.shopify.com/store/{store}/apps/shopify-messaging/landing"


# ── ユーティリティ ────────────────────────────────


def load_storage_state() -> dict | None:
    """storage_state を環境変数（base64 or 生JSON）またはローカルファイルから読み込む。"""
    raw = os.environ.get("SHOPIFY_STORAGE_STATE", "")
    if raw:
        # 全種類の空白文字（半角・全角スペース、改行、タブなど）を除去
        import re
        cleaned = re.sub(r"\s+", "", raw, flags=re.UNICODE)

        # 1. 生JSON として直接パースを試みる（Secretに `{...}` を貼るパターン）
        if cleaned.startswith("{"):
            try:
                return json.loads(cleaned)
            except json.JSONDecodeError as e:
                print(f"❌ SHOPIFY_STORAGE_STATE をJSONとしてパースできません: {e}", file=sys.stderr)
                sys.exit(1)

        # 2. base64 として復号
        # 非ASCII文字（混入したスマートクオート等）は除外
        ascii_bytes = cleaned.encode("ascii", errors="ignore")
        try:
            decoded = base64.b64decode(ascii_bytes, validate=False).decode("utf-8")
            return json.loads(decoded)
        except Exception as e:
            print(
                f"❌ SHOPIFY_STORAGE_STATE のデコードに失敗: {e}\n"
                f"   入力長(strip後): {len(cleaned)} chars\n"
                f"   ASCII変換後長: {len(ascii_bytes)} bytes\n"
                f"   先頭40文字: {cleaned[:40]!r}",
                file=sys.stderr,
            )
            sys.exit(1)

    if LOCAL_STORAGE_STATE.exists():
        with open(LOCAL_STORAGE_STATE, "r", encoding="utf-8") as f:
            return json.load(f)

    print(
        "❌ Shopify セッションが見つかりません。\n"
        "   ローカル: scripts/init_session.py を先に実行してください\n"
        "   CI:      Secret SHOPIFY_STORAGE_STATE を設定してください",
        file=sys.stderr,
    )
    sys.exit(1)


def load_manifest(date_str: str) -> dict:
    """output/{date}/manifest.json を読み込む。"""
    manifest_path = OUTPUT_BASE / date_str / "manifest.json"
    if not manifest_path.exists():
        print(f"❌ manifest.json が見つかりません: {manifest_path}", file=sys.stderr)
        sys.exit(1)
    with open(manifest_path, "r", encoding="utf-8") as f:
        return json.load(f)


async def find_in_any_frame(page: Page, role: str, name: str, timeout: int = 30_000):
    """Locate an element by role/name in main frame OR any iframe（Shopify Admin はアプリをiframe埋込）。

    見つかったら Locator を返す。タイムアウトしたら例外を投げる。
    """
    import time as _time

    deadline = _time.time() + timeout / 1000
    last_error: Exception | None = None
    while _time.time() < deadline:
        # メインフレーム
        try:
            loc = page.get_by_role(role, name=name).first
            await loc.wait_for(state="visible", timeout=2_000)
            return loc
        except Exception as e:
            last_error = e

        # iframes
        for frame in page.frames:
            if frame == page.main_frame:
                continue
            try:
                loc = frame.get_by_role(role, name=name).first
                await loc.wait_for(state="visible", timeout=2_000)
                return loc
            except Exception as e:
                last_error = e

        await page.wait_for_timeout(500)

    raise PlaywrightTimeoutError(
        f"Element role={role} name={name!r} がどのフレームでも見つかりません: {last_error}"
    )


async def screenshot_on_failure(page: Page, label: str) -> None:
    """失敗時にスクリーンショットを保存（CIのartifact用）。"""
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = SCREENSHOT_DIR / f"{ts}_{label}.png"
    try:
        await page.screenshot(path=str(path), full_page=True)
        print(f"  📸 スクリーンショット: {path}")
    except Exception:
        pass


# ── 1本の下書きを作成する ────────────────────────


async def create_draft(
    context: BrowserContext, store: str, entry: dict, html: str
) -> bool:
    """1本のメルマガをShopify Messaging に下書きとして登録する。

    成功なら True, 失敗なら False を返す（個別失敗で全体は止めない）。
    """
    label = f"{entry['date']}_{entry['weekday']}"
    print(f"\n──── {label} {entry['subject']} ────")

    page = await context.new_page()
    page.set_default_timeout(DEFAULT_TIMEOUT)

    try:
        # 1. Messaging アプリを開く（domcontentloaded で十分。
        #    networkidle は Shopify の analytics で永遠に来ないので使わない）
        await page.goto(
            MESSAGING_URL_TEMPLATE.format(store=store),
            wait_until="domcontentloaded",
        )
        # ページ読み込み直後の状態をデバッグ用に1枚撮る
        await page.wait_for_timeout(3_000)
        SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        await page.screenshot(
            path=str(SCREENSHOT_DIR / f"01_landing_{label}.png"),
            full_page=False,
        )

        # 2. 「キャンペーンを作成する」を待機してクリック
        # Shopify Admin はアプリを iframe で埋め込むので、両方探す
        create_button = await find_in_any_frame(
            page, "button", "キャンペーンを作成する", timeout=DEFAULT_TIMEOUT
        )
        await create_button.click()

        # 3. テンプレ選択画面で「独自コード」を選ぶ
        await page.get_by_text("独自コード", exact=True).first.click()

        # 4. エディタが表示されるまで待つ（コードエリア + 件名フィールド）
        # Shopify Messaging は Monaco エディタを使っている可能性が高い
        await page.wait_for_selector(
            ".monaco-editor, [data-testid='code-editor']", timeout=DEFAULT_TIMEOUT
        )

        # 5. HTMLをエディタに流し込む
        # Monaco エディタの textarea にフォーカス → 全選択 → insert_text
        editor = page.locator(".monaco-editor").first
        await editor.click()
        await page.keyboard.press("Control+a" if sys.platform != "darwin" else "Meta+a")
        await page.keyboard.press("Delete")
        await page.keyboard.insert_text(html)

        # 少し待つ（エディタの反映時間）
        await page.wait_for_timeout(500)

        # 6. 件名を入力
        subject_field = page.get_by_label("件名").first
        await subject_field.click()
        await subject_field.fill(entry["subject"])

        # 7. プレビューテキストを入力
        preview_field = page.get_by_label("プレビューテキスト").first
        await preview_field.click()
        await preview_field.fill(entry["preview"])

        # 8. 宛先「すべての購読者」を選択
        # 「セグメントを選択」ボタン → ドロップダウン → 「すべての購読者」
        try:
            await page.get_by_text("セグメントを選択").first.click()
            await page.get_by_text("すべての購読者", exact=False).first.click()
        except PlaywrightTimeoutError:
            print("  ⚠️  宛先の自動選択に失敗（手動で設定が必要）")

        # 9. 自動保存を待つ
        await page.wait_for_timeout(2_000)

        # 10. ページを閉じる（下書きは自動保存されている）
        await page.close()

        print(f"  ✅ 下書き作成: {entry['subject']}")
        return True

    except Exception as e:
        print(f"  ❌ 失敗: {e}")
        await screenshot_on_failure(page, f"failed_{label}")
        try:
            await page.close()
        except Exception:
            pass
        return False


# ── メイン ────────────────────────────────────────


async def main() -> int:
    store = os.environ.get("SHOPIFY_STORE", "").strip()
    if not store:
        print("❌ SHOPIFY_STORE 環境変数が未設定です", file=sys.stderr)
        return 1

    date_str = os.environ.get("NEWSLETTER_DATE", "").strip()
    if not date_str:
        print("❌ NEWSLETTER_DATE 環境変数が未設定です", file=sys.stderr)
        return 1

    headless = os.environ.get("HEADLESS", "true").lower() != "false"

    storage_state = load_storage_state()
    manifest = load_manifest(date_str)
    output_dir = OUTPUT_BASE / date_str

    print("=" * 60)
    print("Shopify Messaging 下書き自動作成")
    print("=" * 60)
    print(f"ストア      : {store}")
    print(f"配信開始日  : {date_str}")
    print(f"対象本数    : {len(manifest['newsletters'])}")
    print(f"headless    : {headless}")
    print()

    succeeded: list[str] = []
    failed: list[str] = []

    async with async_playwright() as p:
        # Bot検知を回避するため、実際のChrome（channel='chrome'）+ ステルスフラグで起動
        # self-hosted runnerが Mac の場合、ユーザーの Chrome がそのまま使われる
        browser: Browser = await p.chromium.launch(
            channel="chrome",
            headless=headless,
            args=["--disable-blink-features=AutomationControlled"],
            ignore_default_args=["--enable-automation"],
        )
        context: BrowserContext = await browser.new_context(
            storage_state=storage_state,
            locale="ja-JP",
            viewport={"width": 1440, "height": 900},
        )
        # webdriver flag を JS で隠す
        await context.add_init_script(
            'Object.defineProperty(navigator, "webdriver", {get: () => undefined})'
        )

        for entry in manifest["newsletters"]:
            html_path = output_dir / entry["filename"]
            if not html_path.exists():
                print(f"⚠️  HTMLファイルが見つかりません: {html_path}")
                failed.append(entry["filename"])
                continue

            with open(html_path, "r", encoding="utf-8") as f:
                html = f.read()

            ok = await create_draft(context, store, entry, html)
            if ok:
                succeeded.append(entry["filename"])
            else:
                failed.append(entry["filename"])

            # 連続操作にならないよう少し間隔を空ける
            await asyncio.sleep(2)

        await browser.close()

    print()
    print("=" * 60)
    print(f"完了: 成功 {len(succeeded)} / 失敗 {len(failed)}")
    if failed:
        print("失敗ファイル:")
        for f in failed:
            print(f"  - {f}")
        return 2  # 部分失敗
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
