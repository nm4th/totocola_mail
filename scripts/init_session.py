#!/usr/bin/env python3
"""
Shopify Admin セッション保存スクリプト（ローカルで1回だけ実行）

このスクリプトを実行すると Chromium が立ち上がる。
普段通り Shopify Admin にログインしてください。
ログイン完了後、Enterキーを押すとセッションが scripts/storage_state.json に保存される。

そのファイルを base64 エンコードして GitHub Secrets `SHOPIFY_STORAGE_STATE` に登録すれば、
GitHub Actions 上で同じセッションを使ってログイン済み状態でブラウザを起動できる。

事前準備:
    pip install -r requirements.txt
    playwright install chromium

使い方:
    python3 scripts/init_session.py
    # → ブラウザでログイン → ターミナルで Enter
    # → scripts/storage_state.json が生成される

GitHub Secrets への登録:
    base64 -i scripts/storage_state.json | pbcopy   # macOS
    # → GitHub の Settings → Secrets → Actions → New repository secret
    #    Name: SHOPIFY_STORAGE_STATE
    #    Value: ペースト
"""

import asyncio
from pathlib import Path

from playwright.async_api import async_playwright

SCRIPT_DIR = Path(__file__).resolve().parent
STORAGE_PATH = SCRIPT_DIR / "storage_state.json"
SHOPIFY_LOGIN_URL = "https://accounts.shopify.com/store-login"


async def main() -> None:
    print("=" * 60)
    print("Shopify Admin セッション保存ツール")
    print("=" * 60)
    print()
    print("Chromium を起動します。")
    print("普段通り Shopify Admin にログインしてください。")
    print("ログイン完了後、このターミナルで Enter キーを押してください。")
    print()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(locale="ja-JP")
        page = await context.new_page()
        await page.goto(SHOPIFY_LOGIN_URL)

        # ユーザーがログイン完了するまで待つ
        await asyncio.get_event_loop().run_in_executor(
            None, input, "ログインが完了したら Enter を押してください... "
        )

        await context.storage_state(path=str(STORAGE_PATH))
        await browser.close()

    print()
    print(f"✅ セッションを保存しました: {STORAGE_PATH}")
    print()
    print("次のステップ:")
    print("1. base64 エンコード:")
    print(f"     base64 -i {STORAGE_PATH} | pbcopy   # macOS")
    print(f"     base64 -w 0 {STORAGE_PATH} | xclip  # Linux")
    print("2. GitHub Secrets に登録:")
    print("     Settings → Secrets and variables → Actions → New repository secret")
    print("     Name : SHOPIFY_STORAGE_STATE")
    print("     Value: ペースト")
    print()
    print("⚠️  storage_state.json には認証情報が含まれます。")
    print("    .gitignore で除外されているのでコミットしないでください。")


if __name__ == "__main__":
    asyncio.run(main())
