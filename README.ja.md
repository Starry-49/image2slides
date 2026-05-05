<p align="center">
  <img src="./assets/icon.svg" alt="Image2Slides アイコン" width="128" height="128">
</p>

# Image2Slides

**Languages:** [English](./README.md) | [中文](./README.zh-CN.md) | 日本語

Image2Slides は、GPT-image のスライド視覚結果を編集可能な PowerPoint に変換する Codex plugin と CLI workflow です。デフォルトでは Codex native `image_gen` で GPT-image-2 の視覚ベースを生成し、source-locked のデータ図を正確に保持し、対応する background の上に編集可能な PowerPoint テキストを配置して、最後にレンダリング結果を参照画像と比較します。

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

デフォルトの GPT-image-2 実行は Codex native `image_gen` を使うため、`OPENAI_API_KEY` は不要です。OpenAI SDK/API-key CLI は、API/SDK 実行を明示的に使う場合だけの fallback です。

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

4. Codex native `image_gen` で GPT-image-2 の completed slide reference を生成します。

   デフォルトの plugin workflow は Codex native image generation を使うため、`OPENAI_API_KEY` は不要です。各 `completed/slide_XX_completed.png` は、GPT-image-2 が生成した、表示テキストを含む full-slide reference でなければなりません。PPTX/PDF render、local screenshot、deterministic drawing output から `completed/` を埋めてはいけません。native image_gen 出力を `completed/` にコピーしたら登録します。

   ```bash
   image2slides register-completed --project decks/my-deck
   ```

5. completed 画像を GPT-image-2 edit して、文字なし background を生成します。

   background pass は文字だけを除去し、`completed/` と揃ったレイアウト、図形、色、ジオメトリを保持します。native image_gen edit の出力を `background/` にコピーした後は登録が必須です。

   ```bash
   image2slides imagegen --project decks/my-deck --phase background --dry-run
   image2slides imagegen --project decks/my-deck --phase background --execute
   image2slides register-background --project decks/my-deck
   ```

   変更してはいけないデータや結果は元の図を `wiki/sources/` に置き、trim 済み source 図を既存の image_gen completed/background pair に正確に patch します。

   ```bash
   image2slides compose-source-locked --project decks/my-deck
   image2slides audit-layout --project decks/my-deck --strict
   ```

   出力先は `background/slide_XX_background.png` と `background/.image2slides_background_provenance.json` です。background prompt は、文字だけを除去し、レイアウト、図形、色、ジオメトリを保持することを要求します。`analyze`、`build-pptx`、`compose-source-locked`、`qa` は、未登録の local template、screenshot、deterministic drawing、古い background、現在の completed provenance に紐づかない background batch を拒否します。
   layout audit は、source 図が検出された native panel 内に収まること、panel inset ratio が trim 後の source 図の比率に合うこと、source panel/image 同士や編集可能テキストと重ならないこと、figure-first slide では図がテキストより視覚的に主役であること、native imagegen panel の上に重複した角丸フレームを追加しないことを固定チェックします。
   source fitting は blank pixel を先に裁ち、GPT-image-2 prompt では source 画像の比率に合う panel を予約します。compose 時には生成された下地または background の実際の panel エッジを検出し、`fit_margin_px` で内側に縮め、4 辺の平行エッジ制約の下で最大スケール化して slack を最小化し、画像中心を inset panel の中心に合わせます。

   Figure-first standard:
   - 値、軸、統計関係を正確に保つ必要がある result/data figure だけを exact source panel として残す
   - 装飾的な diagram、icon、内部テキストを抽出できる text-heavy screenshot には大きな panel を予約しない
   - テキストは少なくし、図の読み方を支える役割に限定する
   - 複数図で混み合う場合は、全図を縮小するのではなくスライドを分ける

   completed generation の API/SDK fallback は、明示的に必要な場合だけ使います。

   ```bash
   image2slides imagegen --project decks/my-deck --phase completed --dry-run
   image2slides imagegen --project decks/my-deck --phase completed --execute
   ```

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
   image2slides qa --project decks/my-deck --strict
   ```

   出力:
   - `reports/rendered/`
   - `reports/qa_similarity.json`
   - `reports/qa_report.md`
   - `reports/source_layer_audit.md`
   - `reports/background_audit.md`

   LibreOffice と `pdftoppm` が使える場合、QA は PPTX をローカルでレンダリングし、`completed/` と pixel / patch similarity を比較し、固定の source-layer layout audit も再実行します。strict mode では byte-identical または視覚的に近すぎる background page も拒否します。default strict similarity preset は overall pixel similarity >= 0.90 を要求し、GPT-image-2 reference text と editable PowerPoint text の通常の font rasterization 差分だけを許容します。

どこかの段階がこの順序から外れた場合は、最初に失効した段階から下流 artifact を削除し、最後の信頼できる checkpoint から再開します。例えば `completed/` が無効なら completed、background、analysis、PPTX、QA をやり直し、`background/` が無効なら background、analysis、PPTX、QA をやり直します。

9. 短い human detail check を行います。

   `pptx/image2slides.pptx` を開き、QA が過剰適合すべきでない細部だけを確認します。source 図と panel の見た目の余白、改行、フォントサイズ、明らかなテキストはみ出し、ページ間の一貫性、source data/results が変更されていないことを見ます。この手順は strict QA 後の軽い最終確認であり、QA の代替ではありません。

## Howitworks Example

このリポジトリには、workflow 全体の最小メンタルモデルとして [howitworks/](./howitworks/) を含めています。ナレッジベース文書、抽出テキストと図、構造化 wiki、GPT-image-2 native base、source-locked の completed/background 画像、最終 PPTX、レンダリング結果、QA report、短い example README が入っています。

![Howitworks human-reviewed PDF preview](./howitworks/image2slides_run/pptx/image2slides_preview.png)

主な編集可能成果物は [howitworks/image2slides_run/pptx/image2slides.pptx](./howitworks/image2slides_run/pptx/image2slides.pptx) です。human-reviewed の視覚 export は [howitworks/image2slides_run/pptx/image2slides.pdf](./howitworks/image2slides_run/pptx/image2slides.pdf) です。最終的な見た目確認には、この PDF を優先してください。レビュー済み PowerPoint から直接書き出されたもので、後続変換によるずれを避けられます。npm package は軽量に保つため、この大きな example artifact set は含めません。example を確認または再実行する場合は GitHub repo を clone してください。

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
