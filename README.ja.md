<p align="center">
  <img src="./assets/icon.svg" alt="Image2Slides icon" width="128" height="128">
</p>

# Image2Slides

**Languages:** [English](./README.md) | [中文](./README.zh-CN.md) | 日本語

Image2Slides は、GPT-image slide composition を editable PowerPoint deck に変換する Codex plugin です。基本不変条件は単純です。GPT-image-2 が visual target と matching text-free background を作り、最終 PowerPoint は text を editable のまま保持します。

Plugin entrypoint: `/image2slides`
Workflow contract: [skills/image2slides/SKILL.md](./skills/image2slides/SKILL.md)
Helper CLI: [skills/image2slides/scripts/image2slides.py](./skills/image2slides/scripts/image2slides.py)

## この Prompt をコピー

次の prompt をローカル Codex または coding agent に渡してください。依存関係、plugin import、conditional hook の設定は agent に任せます。

```text
https://github.com/Starry-49/image2slides から Image2Slides をローカルにインストールし、Codex App で /image2slides plugin として使える状態にしてください。

端から端まで実行してください:
1. repository をローカル workspace に clone または update する。
2. 変更前に README.md、.codex-plugin/plugin.json、skills/image2slides/SKILL.md、package.json、pyproject.toml、tests を確認する。
3. pyproject.toml から helper workflow を editable Python project としてインストールし、doctor/tests の前に numpy と Pillow が使える状態にする。
4. repository root から Codex App plugin を import または refresh する。manifest は .codex-plugin/plugin.json を使う。Codex に skills/ subdirectory を指定しない。
5. hooks/image2slides-native-hook.mjs を conditional Codex hook として UserPromptSubmit、Bash PreToolUse、Stop に登録する。unrelated work では何もせず、/image2slides prompt または image2slides CLI command の後だけ有効にする。
6. /image2slides が index されていることを確認し、doctor/tests を実行し、小さな deck workspace で wiki、prompts、completed、background、analysis、pptx、reports を初期化できることを確認する。
7. GPT-image-2 はデフォルトで Codex native image_gen を使い、completed/background registration は $CODEX_HOME/generated_images/.../ig_*.png からコピーした native receipt manifest 付きの場合だけ許可する。私が SDK/API fallback を明示しない限り、OPENAI_API_KEY を求めない。
8. plugin path、hook path、runtime choices、verification evidence、必要な Codex App refresh 手順を報告する。

生成した deck と private knowledge-base material はすべて local に保持してください。私が明示的に依頼しない限り、example artifacts を公開しないでください。
```

## Core Rules

- Codex Desktop usage は silent execution ではなく、visible guide から始めます。workflow、inputs、outputs を説明する必要がある場合は `image2slides guide` を実行します。
- Required inputs: style/tone、aspect ratio、page count、purpose、scene、user knowledge materials。
- Required inputs は control metadata であり、visible slide text に出してはいけません。
- Required input が欠けている場合、agent は file 作成前に不足項目だけを質問します。`image2slides intake` はこの checklist を出力します。
- Facts、citations、current data、source-locked results は `wiki/grep/` に置き、generated narrative と visual ideas は `wiki/generate/` に置きます。
- `completed/` は GPT-image-2 full-slide reference with visible text でなければなりません。PPTX/PDF render、screenshot、local template、deterministic drawing で埋めてはいけません。
- `background/` は matching completed の GPT-image-2 edit で、text だけを除去し geometry を維持します。
- Native registration には、各 PNG が Codex native `image_gen` output `$CODEX_HOME/generated_images/.../ig_*.png` からコピーされたことを示す receipt manifest が必要です。
- Final PPTX の text は editable PowerPoint text として matching background の上に重ねます。

## Conditional Hook

[hooks/image2slides-native-hook.mjs](./hooks/image2slides-native-hook.mjs) は workflow guard であり、global PPT rule ではありません。

- `UserPromptSubmit` は `/image2slides` または明示的な Image2Slides request のときだけ two-pass GPT-image-2 contract を注入します。
- Bash `PreToolUse` は `image2slides ...` CLI command を検査し、Image2Slides project が active の間は PPTX/python-pptx bypass を block します。
- `Stop` は active project に native completed/background provenance がない場合、早すぎる終了を block します。
- Native `register-completed` と `register-background` は `--native-manifest` がない場合 block されます。
- `compose-source-locked`、`analyze`、`build-pptx`、`qa`、`audit-layout`、`audit-boundaries` は completed/background provenance がない場合 block されます。
- background generation と registration には completed provenance が先に必要です。

## Workflow

1. Deck project を初期化します。

   ```bash
   image2slides guide
   image2slides intake
   image2slides init --project decks/my-deck --spec examples/spec.example.json
   ```

2. Wiki boundary を埋めます。

   `wiki/02_content_boundary.md`、`wiki/03_source_registry.yml`、`wiki/04_slide_plan.json` を更新します。

3. Prompts を作り、source panels を正規化します。

   ```bash
   image2slides queue --project decks/my-deck
   image2slides normalize-source-panels --project decks/my-deck --check --strict
   ```

   `queue` は `reports/native_imagegen_run.md` と native receipt manifest templates も書き出します。

4. Two GPT-image-2 image batches を生成します。

   Codex native `image_gen` で completed slides を作り、その completed slides を text-free backgrounds に edit します。

   ```bash
   image2slides register-completed --project decks/my-deck --native-manifest decks/my-deck/reports/native_imagegen_completed_manifest.json
   image2slides register-background --project decks/my-deck --native-manifest decks/my-deck/reports/native_imagegen_background_manifest.json
   ```

5. Source-locked figures を patch し、PPTX を build します。

   ```bash
   image2slides compose-source-locked --project decks/my-deck
   image2slides analyze --project decks/my-deck
   image2slides build-pptx --project decks/my-deck
   ```

6. Verify してから短い human detail check をします。

   ```bash
   image2slides qa --project decks/my-deck --strict
   ```

   QA は local tools が利用可能な場合 PPTX を render し、`completed/` と比較し、source-panel layout、background uniqueness、boundary overlays を確認します。最後の human pass では panel padding、line breaks、text scale、overflow、page consistency、source data/results が改変されていないことだけを確認します。

## Output Map

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

## Example

Repository には full workflow の minimal mental model として [howitworks/](./howitworks/) が含まれています。

![Howitworks human-reviewed PDF preview](./howitworks/image2slides_run/pptx/image2slides_preview.png)

Primary editable output: [howitworks/image2slides_run/pptx/image2slides.pptx](./howitworks/image2slides_run/pptx/image2slides.pptx)
Reviewed visual snapshot: [howitworks/image2slides_run/pptx/image2slides.pdf](./howitworks/image2slides_run/pptx/image2slides.pdf)

Lightweight plugin bundle には、この大きな example artifact set は含まれません。example を確認または再実行する場合は GitHub repo を clone してください。
