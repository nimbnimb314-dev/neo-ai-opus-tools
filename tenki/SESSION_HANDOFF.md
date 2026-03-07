# SESSION HANDOFF

## 現状

- 予想天気図の生成は `Open-Meteo` 多点サンプリング方式ではなく、`gridded data` ベースへ移行済み。
- 生成入口は `generate.ps1`、本体は `tools/gridded_generator.py`、表示は `docs/index.html` / `docs/app.js` / `docs/styles.css`。
- データ元は `ECMWF Open Data` / `NOAA NOMADS GFS` / `DWD ICON Open Data`。
- 海岸線は `Natural Earth` 由来の `tools/ForecastMapGenerator/map-data/japan-region-land.geojson` を使用。

## 直近の反映内容

- 地図表示を単純な経緯度平面から、Lambert Conformal 系の投影風表示に変更。
  - 関連: `tools/gridded_generator.py`
  - `project_coords()` と `draw_projected_grid()` を追加
- `高 / 低` の判定は persistence ベースの極値検出へ変更済み。
  - 関連: `detect_pressure_centers()` / `find_persistent_centers()` / `analyze_extremum()` / `should_keep_candidate()`
- `高 / 低` の下に中心気圧値を表示するように変更済み。
- 地名ラベル (`Japan`, `Sea of Japan` など) は削除済み。
- 時刻 UI は一覧クリック式ではなくスライダー式に変更済み。
- 画像 URL に `manifest.generatedAt` を付けてブラウザキャッシュの取り違えを防止済み。
- 先読みは「全件」ではなく「前後数コマ」のみに変更済み。

## 現在の表示スケジュール

- 72時間先までは 3時間ごと。
- 72時間超は `09:00 JST` と `21:00 JST` のみ。
- 表示期間は 10日先まで。
- そのため `build_slots()` は 240時間先まで作る。

## モデルごとの扱い

- GFS:
  - 最新だが step が短すぎる run は避ける。
  - `PREFERRED_COMMON_STEP = 144` を満たす run を優先。
- ICON:
  - 同様に `144h` 以上ある run を優先。
  - 補間済み `.npz` は地域キー付きでキャッシュ。
- ECMWF:
  - `00/12 UTC` の main run を優先して使う。
  - `06/18 UTC` は extended range に向かないので、10日表示では前の `00/12 UTC` に戻す実装。
  - `00/12 UTC` は `240h` まで扱う。
  - `0-144h` は 3時間刻み、`150-240h` は 6時間刻み。

## スロットとモデル欠損の扱い

- 以前は「全モデル共通で出せるスロット」のみ生成していた。
- 今は「その時刻に出せるモデルだけ入れる」方式。
- そのため後半では `ICON` が先に落ち、さらに最後は `GFS` だけのスロットがある。
- `docs/app.js` は `slot.models` をそのまま描くので、モデル数がスロットごとに変わっても表示できる。

## 現在の生成結果

- 最新 manifest:
  - `docs/data/manifest.json`
  - `generatedAt: 2026-03-07T19:11:00.697447+09:00`
- 現在のスロット数:
  - `39`
- 先頭:
  - `20260307T2100`
- 末尾:
  - `20260317T2100`
- 末尾スロットのモデル:
  - `gfs` のみ

## キャッシュ

- `cache/gridded/latest-runs.json`
  - 最新採用 run と `maxStep` を保存
- `cache/gridded/ecmwf/...`
  - ECMWF GRIB
- `cache/gridded/gfs/...`
  - GFS GRIB
- `cache/gridded/icon/...`
  - ICON GRIB
  - 補間済み `.npz`

同じ run / step は再取得しない。

## テスト

- `tests/test_pressure_centers.py`
  - 代表的な高低圧の残す/消す回帰テスト
- `tests/test_slot_schedule.py`
  - 72時間以降の `09/21 JST` 化
  - ECMWF extended step の扱い
  - `06 UTC` なら前の `00 UTC` main run を選ぶ挙動

実行コマンド:

```powershell
$env:MAMBA_ROOT_PREFIX = (Join-Path $PWD 'tools/mamba-root')
& (Join-Path $PWD 'tools/micromamba/Library/bin/micromamba.exe') run -p (Join-Path $PWD 'tools/grib-env') python -m unittest tests.test_pressure_centers tests.test_slot_schedule
```

## 生成コマンド

```powershell
./generate.ps1
```

1スロットだけ確認する時:

```powershell
./generate.ps1 --limit-slots 1
```

## 次セッションでやること

目的は「この天気図生成を自動で動かす」こと。

優先順位:

1. 自動実行方式を決める
   - 第一候補は Windows タスクスケジューラ
   - `generate.ps1` を定期実行
2. 実行タイミングを決める
   - モデル公開遅延を考えると、毎時間または 3時間ごと実行が無難
   - ECMWF は遅いので、run 時刻ぴったり実行より「定期ポーリング型」が合う
3. ログ出力を追加する
   - 成功 / 失敗 / 採用 run / 生成 slot 数
   - ログファイルを `logs/` などに残す
4. 異常時の扱いを決める
   - あるモデルだけ取れなくても全体を止めるか
   - 直前成功分を残すか
5. 必要なら公開先反映も自動化する
   - まだ未着手

## 注意点

- `generate.ps1` は `tools/grib-env` の Python を使う前提。
- proxy 環境変数は `generate.ps1` 側で消している。
- `docs/data-next` で staging してから `docs/data` に差し替えるので、生成途中の中途半端な出力は見えない。
- `README.md` は一部文字化けしている。次に触るなら UTF-8 で直したほうがよい。
- 旧 C# 生成器 `tools/ForecastMapGenerator/Program.cs` は残っているが、現行では未使用。
