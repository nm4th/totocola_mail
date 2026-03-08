#!/usr/bin/env python3
"""
ととコーラ メルマガ自動生成スクリプト

毎週日曜日〜月曜日に実行し、1週間分（7本）のメルマガHTMLファイルを
~/Desktop/ととコーラメルマガ/YYYY-MM-DD/ に出力する。

使い方:
    python3 generate_newsletter.py              # 次の月曜日を起点に生成
    python3 generate_newsletter.py 2026-03-10   # 指定日を起点に生成
"""

import json
import os
import re
import shutil
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

# ── 定数 ──────────────────────────────────────────

SCRIPT_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = SCRIPT_DIR / "templates"
STATE_FILE = Path.home() / ".totocola_state.json"
OUTPUT_BASE = Path.home() / "Desktop" / "ととコーラメルマガ"

# ローテーション順（仕様書の通り、6本で循環）
ROTATION = [
    "vol1",   # 1: 世界観系
    "vol2",   # 2: 商品体験系
    "vol3",   # 3: 世界観系
    "volA",   # 4: 世界観系
    "volB",   # 5: 世界観系
    "volC",   # 6: 世界観系
]

# 各ボリュームのメタデータ
VOLUME_META = {
    "vol1": {
        "subject": "あなたが選んだのは、コーラじゃなかったかもしれない。",
        "preview": "手に取ったその理由、少し考えてみると面白いかもしれません。",
    },
    "vol2": {
        "subject": "ととコーラ、実は4通りの飲み物です。",
        "preview": "炭酸だけじゃない。あなたに合う飲み方、見つかるかも。",
    },
    "vol3": {
        "subject": "夜に何もしないことが、明日の自分を助ける理由。",
        "preview": "休んでいるつもりなのに、なぜか疲れが取れない。その答えがここにあるかもしれません。",
    },
    "volA": {
        "subject": "他の人と比べてしまう夜に、読んでほしい話。",
        "preview": "比べること自体は、悪いことじゃないかもしれません。",
    },
    "volB": {
        "subject": "小さなことが決められない日は、疲れているサインです。",
        "preview": "優柔不断なのではなく、決断体力が切れているだけかもしれません。",
    },
    "volC": {
        "subject": "ひとりの夜は、寂しいんじゃなくて、必要な夜かもしれない。",
        "preview": "誰かと過ごす時間と同じくらい、ひとりの時間にも意味があります。",
    },
}

# ファイル名に使えない文字の正規表現
INVALID_FILENAME_CHARS = re.compile(r'[/\\:*?"<>|]')


# ── ステート管理 ──────────────────────────────────

def load_state() -> dict:
    """ステートファイルを読み込む。なければ初期値を返す。"""
    if STATE_FILE.exists():
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"last_index": -1}


def save_state(state: dict) -> None:
    """ステートファイルに保存する。"""
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


# ── ユーティリティ ────────────────────────────────

def sanitize_filename(text: str) -> str:
    """ファイル名に使えない文字を除去する。"""
    return INVALID_FILENAME_CHARS.sub("", text)


def get_next_monday(from_date: date) -> date:
    """指定日以降の最初の月曜日を返す。from_dateが月曜日ならそのまま。"""
    days_ahead = (7 - from_date.weekday()) % 7  # 0=月曜
    if days_ahead == 0 and from_date.weekday() == 0:
        return from_date
    # 日曜(6)の場合は翌日=月曜
    if from_date.weekday() == 6:
        return from_date + timedelta(days=1)
    return from_date + timedelta(days=days_ahead)


def truncate_for_filename(text: str, max_len: int = 15) -> str:
    """ファイル名用にテキストを短縮する。"""
    if len(text) <= max_len:
        return text
    return text[:max_len] + "〜"


# ── メイン生成処理 ────────────────────────────────

def generate_week(start_date: date) -> None:
    """1週間分（7本）のメルマガHTMLを生成する。"""
    state = load_state()
    current_index = state["last_index"]

    # 出力フォルダを作成
    output_dir = OUTPUT_BASE / start_date.strftime("%Y-%m-%d")
    output_dir.mkdir(parents=True, exist_ok=True)

    subject_list_lines = []

    for day_offset in range(7):
        delivery_date = start_date + timedelta(days=day_offset)
        date_str = delivery_date.strftime("%Y-%m-%d")

        # ローテーション: 次のインデックスへ
        current_index = (current_index + 1) % len(ROTATION)
        vol_key = ROTATION[current_index]
        meta = VOLUME_META[vol_key]

        subject = meta["subject"]
        preview = meta["preview"]

        # テンプレートHTMLを読み込み
        template_path = TEMPLATES_DIR / f"mailmag_{vol_key}.html"
        if not template_path.exists():
            print(f"[警告] テンプレートが見つかりません: {template_path}")
            continue

        with open(template_path, "r", encoding="utf-8") as f:
            html_content = f.read()

        # ファイル名を生成
        subject_short = truncate_for_filename(sanitize_filename(subject))
        preview_short = truncate_for_filename(sanitize_filename(preview))
        filename = f"{date_str}_{subject_short}_{preview_short}.html"

        # ファイルを出力
        output_path = output_dir / filename
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(html_content)

        print(f"  生成: {filename}")

        # 件名リスト用データを蓄積
        subject_list_lines.append(date_str)
        subject_list_lines.append(f"件名：{subject}")
        subject_list_lines.append(f"プレビューテキスト：{preview}")
        subject_list_lines.append("")

    # 件名リスト.txt を出力
    subject_list_path = output_dir / "件名リスト.txt"
    with open(subject_list_path, "w", encoding="utf-8") as f:
        f.write("\n".join(subject_list_lines).rstrip() + "\n")

    print(f"  生成: 件名リスト.txt")

    # ステートを更新
    state["last_index"] = current_index
    state["last_run"] = datetime.now().isoformat()
    state["last_start_date"] = start_date.strftime("%Y-%m-%d")
    save_state(state)

    print(f"\n完了！ 出力先: {output_dir}")
    print(f"ステート保存先: {STATE_FILE}")


# ── エントリーポイント ────────────────────────────

def main():
    print("=" * 60)
    print("ととコーラ メルマガ自動生成")
    print("=" * 60)

    if len(sys.argv) > 1:
        try:
            start_date = datetime.strptime(sys.argv[1], "%Y-%m-%d").date()
        except ValueError:
            print(f"エラー: 日付の形式が不正です: {sys.argv[1]}")
            print("使い方: python3 generate_newsletter.py [YYYY-MM-DD]")
            sys.exit(1)
    else:
        # 次の月曜日を自動計算
        today = date.today()
        start_date = get_next_monday(today)

    print(f"\n配信開始日: {start_date}")
    print(f"テンプレート: {TEMPLATES_DIR}")
    print(f"ローテーション: {len(ROTATION)}本")
    print()

    generate_week(start_date)


if __name__ == "__main__":
    main()
