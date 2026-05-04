<p align="center">
  <img src="./assets/icon.svg" alt="Image2Slides アイコン" width="128" height="128">
</p>

# Image2Slides

**Languages:** [English](./README.md) | [中文](./README.zh-CN.md) | 日本語

Image2Slides は、GPT-image で生成したスライド参照画像を編集可能な PowerPoint に変換する Codex plugin と CLI workflow です。完成版スライド画像を視覚ターゲットとして作り、そこから文字なし背景を生成し、その背景の上に編集可能な PowerPoint テキストを配置し、最後にレンダリング結果を参照画像と比較して検証します。

Plugin の入口は `/image2slides` です。手順は [skills/image2slides/SKILL.md](./skills/image2slides/SKILL.md)、決定的な処理を行う CLI は [skills/image2slides/scripts/image2slides.py](./skills/image2slides/scripts/image2slides.py) にあります。

## インストール

npm で GitHub から直接インストールできます。

```bash
npm install -g git+https://github.com/Starry-49/image2slides.git
image2slides doctor
```

ローカル開発:

```bash
git clone https://github.com/Starry-49/image2slides.git
cd image2slides
PYTHONPATH=skills/image2slides/scripts python3 tests/test_image2slides.py
python3 skills/image2slides/scripts/image2slides.py doctor
```

実際に GPT-image-2 を呼び出すには、system Image Gen skill CLI、現在の Python 環境の OpenAI SDK、そして `OPENAI_API_KEY` が必要です。dry-run、プロジェクト初期化、画像解析、PPTX 生成、ローカル QA は API key なしで実行できます。

## 必須入力

`/image2slides` は、生成前に次の入力を必須とします。

- スライドのベーススタイルと色調
- スライドのアスペクト比
- スライド枚数
- 用途: speech または showcase
- 利用シーン: academic、enterprise、classroom、life、または明示された別シーン
- ナレッジベース: ユーザー提供のテキスト、画像、参照資料、資料パス

[examples/spec.example.json](./examples/spec.example.json) を初期形として使えます。

## Workflow から Results まで

1. プロジェクト wiki と出力構造を作成します。

   ```bash
   image2slides init --project decks/my-deck --spec examples/spec.example.json
   ```

   `project.json`、`wiki/00_project_brief.md`、`wiki/01_wiki_map.md`、`wiki/02_content_boundary.md`、`wiki/03_source_registry.yml`、`wiki/04_slide_plan.json` が作成されます。

2. コンテンツ境界を記入します。

   - `wiki/grep/` には、資料・引用・web/search に基づく必要がある事実を書きます。
   - `wiki/generate/` には、生成してよい物語構成、比喩、例、表現案を書きます。
   - `wiki/02_content_boundary.md` を更新し、各スライドの grep_required と generation_allowed を明確にします。

3. prompt queue を作成します。

   ```bash
   image2slides queue --project decks/my-deck
   ```

   出力:
   - `prompts/completed_prompts.jsonl`
   - `prompts/background_edit_prompts.jsonl`

4. GPT-image-2 で completed slide 参照画像を生成します。

   ```bash
   image2slides imagegen --project decks/my-deck --phase completed --dry-run
   image2slides imagegen --project decks/my-deck --phase completed --execute
   ```

   出力先は `completed/slide_XX_completed.png` です。

5. completed 画像を編集して文字なし background を生成します。

   ```bash
   image2slides imagegen --project decks/my-deck --phase background --dry-run
   image2slides imagegen --project decks/my-deck --phase background --execute
   ```

   出力先は `background/slide_XX_background.png` です。background prompt は、文字だけを除去し、レイアウト、図形、色、ジオメトリを保持することを要求します。

6. テキスト領域と blank 領域を解析します。

   ```bash
   image2slides analyze --project decks/my-deck
   ```

   出力:
   - `analysis/slide_XX.json`
   - `analysis/manifest.json`

   解析器は `completed/` と `background/` を比較し、ピクセル差分をテキスト mask として扱い、主背景色、テキスト領域、低変化の blank 領域を推定します。

7. 編集可能な PPTX を作成します。

   ```bash
   image2slides build-pptx --project decks/my-deck
   ```

   出力:
   - `pptx/image2slides.pptx`

   各スライドは対応する background 画像をベースとして使い、`wiki/04_slide_plan.json` の文字を編集可能な PowerPoint テキストボックスとして重ねます。

8. レンダリングして検証します。

   ```bash
   image2slides qa --project decks/my-deck
   ```

   出力:
   - `reports/rendered/`
   - `reports/qa_similarity.json`
   - `reports/qa_report.md`

   LibreOffice と `pdftoppm` が使える場合、QA は PPTX をローカルでレンダリングし、`completed/` と pixel / patch similarity を比較します。

## 出力ディレクトリ

```text
decks/my-deck/
├── project.json
├── wiki/
│   ├── grep/
│   ├── generate/
│   ├── 02_content_boundary.md
│   └── 04_slide_plan.json
├── prompts/
├── completed/
├── background/
├── analysis/
├── pptx/
└── reports/
```

## 設計上の境界

Image2Slides は最終 deck を画像だけにはしません。完成版スライド画像は視覚参照と QA ターゲットです。最終成果物では重要なテキストを PowerPoint 上で編集可能に保ち、文字なし background を安定したベースレイヤーとして使います。
