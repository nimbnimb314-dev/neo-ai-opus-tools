# SESSION HANDOFF

## 現状

- 対象は `tenki/` 配下の気圧予想図ジェネレータ。
- 実行入口は `generate.ps1`、本体は `tools/gridded_generator.py`。
- 出力は `docs/data/manifest.json` / `docs/data/manifest.js` / `docs/data/run-summary.json` / `docs/data/images/*`。
- 表示側は `docs/index.html` と `docs/app.js`。

## 最新生成

- 最新成功: `2026-03-08 09:07 JST`
- `run-summary.json`
  - `generatedAt: 2026-03-08T09:07:51.874277+09:00`
  - `requestedSlotCount: 38`
  - `firstGeneratedSlotId: 20260308T1200`
  - `lastGeneratedSlotId: 20260318T0900`
- 採用 run
  - ECMWF: `2026-03-07 12:00 UTC`
  - GFS: `2026-03-07 18:00 UTC`
  - ICON: `2026-03-07 12:00 UTC`
- 生成枚数
  - ECMWF: `37`
  - GFS: `38`
  - ICON: `32`
- 最新ログ: `logs/generate-20260308-090606.log`

## 今回までの修正

### 1. 画像端で等圧線が切れる問題

- 原因は「表示矩形の角」が、等圧線計算用データ範囲の外に出ていたこと。
- 対応として、等圧線用のデータ取得範囲を固定で広げた。
  - 表示範囲: `114E-158E / 19N-52N`
  - データ取得範囲: `94E-178E / 12N-60N`
- 地図の見た目は従来の矩形フレームのまま。
- 台形クリップ案は破棄済み。
- 関連箇所:
  - `tools/gridded_generator.py`
  - `tests/test_pressure_centers.py::test_data_region_covers_visible_plot_corners`

### 2. ブラウザが古い画像を掴み続ける問題

- `manifest.js` だけでなく `app.js` もキャッシュで残っていた。
- `docs/index.html` で `manifest.js` / `app.js` を `?v=Date.now()` 付きで読むように変更。
- `docs/app.js` 側もその前提で動的ロードに変更。

### 3. 高 / 低 の中心検出

- 現在は `detect_pressure_centers()` で以下を実施:
  - 平滑化場から局所極値候補を抽出
  - 閉じた領域の persistence を見て候補化
  - 表示範囲外の候補は採用前に除外
  - 近接する同種候補を整理
  - 異種候補の競合を整理
- 直近の調整:
  - `高` は絶対気圧が高いほど出しやすくした
  - `低` には同じボーナスを入れない
  - 高圧場の中にある浅い `低` は出にくくした
  - 表示外候補が `per_kind=4` の上限を食わないようにした
- まだ「低をさらに減らす」余地はある。今は少し保守的に戻した状態。

## テスト

- 最新通過コマンド:

```powershell
$env:MAMBA_ROOT_PREFIX = (Join-Path $PWD 'tools/mamba-root')
& (Join-Path $PWD 'tools/micromamba/Library/bin/micromamba.exe') run -p (Join-Path $PWD 'tools/grib-env') python -m unittest tests.test_pressure_centers tests.test_slot_schedule tests.test_run_resolution
```

- 現在 `16 tests` 通過。

## 実行コマンド

```powershell
./generate.ps1
```

厳密失敗モード:

```powershell
./generate.ps1 --strict-models
```

## 主に見るファイル

- `tools/gridded_generator.py`
- `tests/test_pressure_centers.py`
- `tests/test_run_resolution.py`
- `docs/index.html`
- `docs/app.js`
- `docs/data/run-summary.json`

## 注意

- Git のトップレベルは `C:\Users\n_m_n\webapp`。`tenki` はそのサブディレクトリ。
- リポジトリ直下には `tenki` 以外の未追跡ファイルや別作業ディレクトリもある。コミットや push は `tenki` 配下だけを対象にするのが安全。
