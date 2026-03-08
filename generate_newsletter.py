#!/usr/bin/env python3
"""
ととコーラ メルマガ自動生成スクリプト

毎週日曜日〜月曜日に実行し、1週間分（7本）のメルマガHTMLファイルを
~/Desktop/ととコーラメルマガ/YYYY-MM-DD/ に出力する。

Claude APIで毎週新しいコンテンツを生成し、HTMLテンプレートに流し込む。

使い方:
    python3 generate_newsletter.py              # 次の月曜日を起点に生成
    python3 generate_newsletter.py 2026-03-10   # 指定日を起点に生成

環境変数:
    ANTHROPIC_API_KEY  : Claude APIキー（必須）
"""

import json
import os
import re
import sys
import time
from datetime import date, datetime, timedelta
from html import escape as html_escape
from pathlib import Path

import requests

# ── 定数 ──────────────────────────────────────────

SCRIPT_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = SCRIPT_DIR / "templates"
CONTENT_REQUIREMENTS = SCRIPT_DIR / "content_requirements.txt"
STATE_FILE = Path.home() / ".totocola_state.json"
OUTPUT_BASE = Path.home() / "Desktop" / "ととコーラメルマガ"

CLAUDE_API_URL = "https://api.anthropic.com/v1/messages"
CLAUDE_MODEL = "claude-sonnet-4-6"
CLAUDE_MAX_TOKENS = 2000

# 7本分のタイプ配分: 世界観系5本 + 商品体験系1本 + 購買背中押し系1本
WEEKLY_TYPE_SCHEDULE = [
    "world_view",   # 月
    "product",      # 火
    "world_view",   # 水
    "world_view",   # 木
    "purchase",     # 金
    "world_view",   # 土
    "world_view",   # 日
]

TYPE_LABELS = {
    "world_view": "世界観系",
    "product": "商品体験系",
    "purchase": "購買背中押し系",
}

# 既存テンプレートファイル（トーン・構造のサンプルとして使用）
SAMPLE_TEMPLATES = ["vol1", "vol2", "vol3", "volA", "volB", "volC"]

# 初期使用済みテーマ（既存6本のテーマ）
INITIAL_USED_THEMES = [
    "SNS比較・他者との比較疲れ",
    "決断疲れ・選択のエネルギー枯渇",
    "ひとり時間の価値・孤独との違い",
    "立ち止まるための飲み物",
    "4通りの割り方・飲み方",
    "夜の引き算・スマホと脳の疲れ",
    "タイパ・コスパ思考への疑問",
    "飲む vs 味わうの違い",
    "時間が早く過ぎる感覚",
    "機嫌が悪い日のリセット",
    "成長には止まる時間が必要",
    "何もしない時間の価値",
    "記憶に残らない日々",
    "頑張れるのに続かない理由",
]

# リンク
PRODUCT_URL = "https://totocola.com/products/%E3%81%A8%E3%81%A8%E3%82%B3%E3%83%BC%E3%83%A9200ml"
PRODUCT_IMAGE_URL = "https://totocola.com/cdn/shop/files/S__164634650.jpg?v=1768718814&width=1946"
LINE_URL = "https://lin.ee/aDWPWkhv"

# ファイル名に使えない文字
INVALID_FILENAME_CHARS = re.compile(r'[/\\:*?"<>|]')


# ── ステート管理 ──────────────────────────────────

def load_state() -> dict:
    """ステートファイルを読み込む。"""
    if STATE_FILE.exists():
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "used_themes": list(INITIAL_USED_THEMES),
        "last_run": None,
    }


def save_state(state: dict) -> None:
    """ステートファイルに保存する。"""
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


# ── テキスト抽出 ──────────────────────────────────

def extract_text_from_html(html: str) -> str:
    """HTMLからプレーンテキストを抽出（サンプル用）。"""
    # タグを除去
    text = re.sub(r'<[^>]+>', '', html)
    # 連続空白・改行を整理
    text = re.sub(r'\n\s*\n', '\n\n', text)
    text = re.sub(r'[ \t]+', ' ', text)
    # ヘッダー・フッター部分を除去（ととコーラの後のCTA以降）
    if "そろそろなくなりそうな方へ" in text:
        text = text[:text.index("そろそろなくなりそうな方へ")]
    return text.strip()


def load_sample_texts() -> str:
    """既存テンプレートからサンプルテキストを抽出して結合。"""
    samples = []
    for vol in SAMPLE_TEMPLATES:
        path = TEMPLATES_DIR / f"mailmag_{vol}.html"
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                text = extract_text_from_html(f.read())
            samples.append(f"--- {vol} ---\n{text}")
    return "\n\n".join(samples)


# ── Claude API ────────────────────────────────────

SYSTEM_PROMPT = """あなたはととコーラというブランドのメルマガライターです。
以下の要件定義に従って、メルマガ本文をJSON形式で生成してください。

【ブランド情報】
- 商品: ととコーラ（ノンカフェイン・低カロリー・無添加のスパイスシロップ）
- コンセプト: 「前に進む前に、立ち止まる」
- ポジション: 「立ち止まるための飲み物」
- ターゲット: 購入経験のある読者（夜に自分の時間を大切にしたい30代前後）

【文体の原則】
- 一人称で語りかける（断定せず「〜かもしれません」「〜と思います」）
- 説教しない。共感から始めて、そっと視点を渡す
- 夜の静けさに合うトーン。押しつけがましくない
- 短い文を積み重ねるリズム感
- 難しい言葉を使わない

【構造】
- 冒頭ブロック: 読者が「自分のことだ」と感じる情景描写 + 問いかけ（100〜150文字）
- セクション3〜4つ: 見出し + 共感 + 視点転換 + ととコーラへの接続
- クロージング: 小さな行動提案 + 🌙 ととコーラ でサインオフ

【見出しルール】
- 10文字前後、体言止めか短い文
- 読者の内側の感覚を言語化する表現
- 「〜のおすすめポイント」「方法5選」のような見出しは禁止

【ととコーラへの接続ルール】
- 本文中にととコーラの名前は1〜2回まで
- 「ととコーラを飲んでください」とは書かない
- 飲む行為を通じて「夜の過ごし方」を変えることを示唆する

【禁止事項】
- 「！」の多用（1通に1回まで）
- 「〜しましょう」
- 商品スペックの列挙
- 「今だけ」などのセール的表現（購買背中押し系以外）
- 読者を「ユーザー」「お客様」と呼ぶ
- 長い段落（1段落は3〜4文まで）

【文字数の目安】
- 冒頭ブロック: 100〜150文字
- 各セクション: 150〜250文字
- クロージング: 50〜80文字
- 全体: 600〜900文字（本文のみ）

必ずJSONのみ返してください。```や説明文は不要です。"""


def build_user_prompt(newsletter_type: str, used_themes: list, sample_text: str) -> str:
    """Claude APIへのユーザープロンプトを構築する。"""
    themes_str = "\n".join(f"- {t}" for t in used_themes)

    return f"""以下の条件でメルマガを1本生成してください。

タイプ: {TYPE_LABELS[newsletter_type]}
（world_view=世界観系 / product=商品体験系 / purchase=購買背中押し系）

使用済みテーマ（重複禁止）:
{themes_str}

参考にすべきトーン・構造（既存メルマガの抜粋）:
{sample_text}

以下のJSON形式で返してください:
{{
  "subject": "件名（25〜35文字）",
  "preview": "プレビューテキスト（25〜40文字）",
  "subtitle": "サブタイトル（ヘッダー下に表示・10文字前後）",
  "theme": "今回使用したテーマ（used_themesに追記するため）",
  "opening": {{
    "paragraphs": ["冒頭の文章1", "冒頭の文章2", "冒頭の文章3（問いかけ）"]
  }},
  "sections": [
    {{
      "heading": "見出し",
      "paragraphs": ["段落1", "段落2"],
      "bullets": ["箇条書き1", "箇条書き2"],
      "emphasis": "この段落内で強調する一文（なければ空文字）"
    }}
  ],
  "closing": {{
    "paragraphs": ["締めの文章1", "締めの文章2"]
  }}
}}

bulletsは羅列が3つ以上ある場合のみ使用。不要な場合は空配列[]。
emphasisは各セクションで最も伝えたい一文を指定。"""


def call_claude_api(api_key: str, newsletter_type: str, used_themes: list, sample_text: str) -> dict:
    """Claude APIを呼び出してメルマガコンテンツを生成する。"""
    user_prompt = build_user_prompt(newsletter_type, used_themes, sample_text)

    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }

    payload = {
        "model": CLAUDE_MODEL,
        "max_tokens": CLAUDE_MAX_TOKENS,
        "system": SYSTEM_PROMPT,
        "messages": [
            {"role": "user", "content": user_prompt}
        ],
    }

    response = requests.post(CLAUDE_API_URL, headers=headers, json=payload, timeout=120)
    response.raise_for_status()

    result = response.json()
    content_text = result["content"][0]["text"]

    # JSONパース（```で囲まれている場合も対応）
    clean = content_text.strip()
    if clean.startswith("```"):
        clean = re.sub(r'^```(?:json)?\s*', '', clean)
        clean = re.sub(r'\s*```$', '', clean)

    return json.loads(clean)


# ── HTML生成 ──────────────────────────────────────

def render_emphasis(text: str, emphasis: str) -> str:
    """テキスト内のemphasis文字列をbold italic whiteのspanで囲む。"""
    if not emphasis or emphasis not in text:
        return html_escape(text).replace("\n", "<br>")
    before, _, after = text.partition(emphasis)
    return (
        html_escape(before).replace("\n", "<br>")
        + '<span style="font-weight:700;font-style:italic;color:#ffffff;">'
        + html_escape(emphasis).replace("\n", "<br>")
        + '</span>'
        + html_escape(after).replace("\n", "<br>")
    )


def build_html(content: dict) -> str:
    """JSONコンテンツからメルマガHTMLを生成する。"""

    # 冒頭ブロックの段落
    opening_html = "<br><br>\n".join(
        html_escape(p).replace("\n", "<br>") for p in content["opening"]["paragraphs"]
    )

    # セクション
    sections_html = ""
    for section in content["sections"]:
        heading = html_escape(section["heading"])
        emphasis = section.get("emphasis", "")

        # 段落
        paragraphs_parts = []
        for i, para in enumerate(section["paragraphs"]):
            rendered = render_emphasis(para, emphasis)
            # 箇条書きの直前の段落はmargin-bottom: 4px
            if section.get("bullets") and i == len(section["paragraphs"]) - 1:
                margin = "margin:0 0 4px 0"
            else:
                margin = "margin:0 0 16px 0"
            paragraphs_parts.append(
                f'<p style="{margin};font-size:18px;line-height:1.6;color:#c8cdd8;">\n{rendered}\n</p>'
            )

        paragraphs_html = "\n".join(paragraphs_parts)

        # 箇条書き
        bullets_html = ""
        if section.get("bullets"):
            bullet_items = "\n".join(
                f'<p style="margin:0;font-size:18px;line-height:1.6;color:#c8cdd8;padding:1px 0;">✦ {html_escape(b)}</p>'
                for b in section["bullets"]
            )
            bullets_html = f"\n{bullet_items}"

        sections_html += f"""
<!-- セクション -->
<tr><td style="padding:0 24px;">
<hr style="border:none;border-top:1px solid #2e3650;margin:0 0 24px 0;">
<p style="margin:0 0 16px 0;font-size:17px;color:#c8cdd8;"><span style="border-left:3px solid #c9a84c;padding-left:10px;">{heading}</span></p>
{paragraphs_html}{bullets_html}
</td></tr>
"""

    # クロージング段落
    closing_paragraphs = "\n".join(
        f'<p style="margin:0 0 8px 0;font-size:18px;line-height:1.6;color:#c8cdd8;text-align:center;">\n{html_escape(p).replace(chr(10), "<br>")}\n</p>'
        for p in content["closing"]["paragraphs"]
    )

    subject = html_escape(content["subject"])
    subtitle = html_escape(content["subtitle"])

    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{subject}</title>
<link href="https://fonts.googleapis.com/css2?family=Zen+Old+Mincho:wght@700&display=swap" rel="stylesheet">
</head>
<body style="margin:0;padding:0;background-color:#1a1f2e;font-family:'Helvetica Neue',Arial,sans-serif;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:#1a1f2e;">
<tr><td align="center" style="padding:20px 0;">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" style="background-color:#1e2436;max-width:600px;width:100%;">

<!-- ヘッダー -->
<tr><td style="padding:32px 24px 0 24px;text-align:center;">
<p style="margin:0;font-family:'Zen Old Mincho',serif;font-weight:700;font-size:22px;color:#c9a84c;letter-spacing:2px;">ととコーラ</p>
<hr style="border:none;border-top:1px solid #c9a84c;width:80%;margin:16px auto;">
<p style="margin:0;font-size:13px;color:#8b92a8;">{subtitle}</p>
</td></tr>

<!-- 冒頭ブロック -->
<tr><td style="padding:24px;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:#252b40;border-radius:4px;">
<tr><td style="padding:24px;font-size:18px;line-height:1.6;color:#c8cdd8;">
{opening_html}
</td></tr>
</table>
</td></tr>
{sections_html}
<!-- クロージング -->
<tr><td style="padding:0 24px 24px 24px;">
<hr style="border:none;border-top:1px solid #2e3650;margin:0 0 24px 0;">
{closing_paragraphs}
<table role="presentation" width="100%" cellpadding="0" cellspacing="0">
<tr><td align="center">
<hr style="border:none;border-top:1px solid #c9a84c;width:60%;margin:16px auto 12px auto;">
<p style="margin:0;font-family:'Zen Old Mincho',serif;font-weight:700;font-size:16px;color:#c9a84c;">🌙 ととコーラ</p>
<hr style="border:none;border-top:1px solid #c9a84c;width:60%;margin:12px auto 0 auto;">
</td></tr>
</table>
</td></tr>

<!-- CTA -->
<tr><td style="padding:0 24px 24px 24px;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:#252b40;border-radius:4px;">
<tr><td style="padding:24px;text-align:center;">
<p style="margin:0 0 8px 0;font-size:12px;color:#8b92a8;">そろそろなくなりそうな方へ</p>
<p style="margin:0 0 16px 0;font-size:16px;color:#c9a84c;font-weight:700;">ととコーラシロップ（送料無料）</p>
<a href="{PRODUCT_URL}" target="_blank" style="text-decoration:none;">
<img src="{PRODUCT_IMAGE_URL}" alt="ととコーラシロップ" width="280" style="max-width:100%;height:auto;border-radius:4px;display:block;margin:0 auto 16px auto;">
</a>
<p style="margin:0 0 16px 0;font-size:18px;color:#c8cdd8;">¥2,490〜</p>
<a href="{PRODUCT_URL}" target="_blank" style="display:inline-block;background-color:#c9a84c;color:#1a1f2e;font-size:16px;font-weight:700;text-decoration:none;padding:14px 40px;border-radius:4px;margin-bottom:12px;">商品ページを見る</a>
<br>
<a href="{LINE_URL}" target="_blank" style="display:inline-block;border:2px solid #06C755;color:#06C755;font-size:14px;font-weight:700;text-decoration:none;padding:12px 32px;border-radius:4px;background:transparent;">LINEで友だち追加</a>
</td></tr>
</table>
</td></tr>

<!-- フッター -->
<tr><td style="padding:24px;text-align:center;">
<p style="margin:0 0 8px 0;font-size:12px;color:#8b92a8;">ととコーラ · 日本 〒107-0061, 東京都 港区, 北青山1-3-3, 三橋ビル 3階</p>
<p style="margin:0 0 8px 0;font-size:12px;color:#8b92a8;">© 2026 ととコーラ</p>
<p style="margin:0;font-size:12px;color:#8b92a8;">{{{{ unsubscribe_link }}}}</p>
</td></tr>

</table>
</td></tr>
</table>
{{{{ open_tracking_block }}}}
</body>
</html>"""


# ── ユーティリティ ────────────────────────────────

def sanitize_filename(text: str) -> str:
    """ファイル名に使えない文字を除去する。"""
    return INVALID_FILENAME_CHARS.sub("", text)


def truncate_for_filename(text: str, max_len: int = 15) -> str:
    """ファイル名用にテキストを短縮する。"""
    if len(text) <= max_len:
        return text
    return text[:max_len] + "〜"


def get_next_monday(from_date: date) -> date:
    """指定日以降の最初の月曜日を返す。"""
    days_ahead = (7 - from_date.weekday()) % 7
    if from_date.weekday() == 0:
        return from_date
    if from_date.weekday() == 6:
        return from_date + timedelta(days=1)
    return from_date + timedelta(days=days_ahead)


# ── メイン生成処理 ────────────────────────────────

def generate_week(start_date: date) -> None:
    """1週間分（7本）のメルマガHTMLを生成する。"""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("エラー: ANTHROPIC_API_KEY 環境変数を設定してください。")
        sys.exit(1)

    state = load_state()
    used_themes = state.get("used_themes", list(INITIAL_USED_THEMES))

    # サンプルテキストを読み込み
    print("サンプルテキストを読み込み中...")
    sample_text = load_sample_texts()

    # 出力フォルダを作成
    output_dir = OUTPUT_BASE / start_date.strftime("%Y-%m-%d")
    output_dir.mkdir(parents=True, exist_ok=True)

    subject_list_lines = []
    new_themes = []

    for day_offset in range(7):
        delivery_date = start_date + timedelta(days=day_offset)
        date_str = delivery_date.strftime("%Y-%m-%d")
        newsletter_type = WEEKLY_TYPE_SCHEDULE[day_offset]

        print(f"\n[{day_offset + 1}/7] {date_str} ({TYPE_LABELS[newsletter_type]})")
        print("  Claude API呼び出し中...")

        # Claude APIでコンテンツ生成
        content = call_claude_api(
            api_key=api_key,
            newsletter_type=newsletter_type,
            used_themes=used_themes + new_themes,
            sample_text=sample_text,
        )

        subject = content["subject"]
        preview = content["preview"]
        theme = content["theme"]
        new_themes.append(theme)

        print(f"  テーマ: {theme}")
        print(f"  件名: {subject}")

        # HTMLを生成
        html_content = build_html(content)

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

        # API レート制限対策（1本生成ごとに少し待つ）
        if day_offset < 6:
            time.sleep(2)

    # 件名リスト.txt を出力
    subject_list_path = output_dir / "件名リスト.txt"
    with open(subject_list_path, "w", encoding="utf-8") as f:
        f.write("\n".join(subject_list_lines).rstrip() + "\n")

    print(f"\n  生成: 件名リスト.txt")

    # ステートを更新
    state["used_themes"] = used_themes + new_themes
    state["last_run"] = datetime.now().isoformat()
    save_state(state)

    print(f"\n{'=' * 60}")
    print(f"完了！ 出力先: {output_dir}")
    print(f"ステート保存先: {STATE_FILE}")
    print(f"新規テーマ {len(new_themes)}件を追加（合計{len(state['used_themes'])}件）")


# ── エントリーポイント ────────────────────────────

def main():
    print("=" * 60)
    print("ととコーラ メルマガ自動生成（Claude API連携）")
    print("=" * 60)

    if len(sys.argv) > 1:
        try:
            start_date = datetime.strptime(sys.argv[1], "%Y-%m-%d").date()
        except ValueError:
            print(f"エラー: 日付の形式が不正です: {sys.argv[1]}")
            print("使い方: python3 generate_newsletter.py [YYYY-MM-DD]")
            sys.exit(1)
    else:
        today = date.today()
        start_date = get_next_monday(today)

    print(f"\n配信開始日: {start_date}")
    print(f"テンプレート参照: {TEMPLATES_DIR}")
    print(f"タイプ配分: 世界観系5本 + 商品体験系1本 + 購買背中押し系1本")
    print()

    generate_week(start_date)


if __name__ == "__main__":
    main()
