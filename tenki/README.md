# 日本周辺 予想天気図ビューア

日本周辺の海面更正気圧を、`ECMWF / GFS / ICON` の 3 モデルで比較表示するローカル実行版です。  
各モデルの `gridded data` から等圧線画像を生成し、`docs/index.html` で切り替えて見ます。

## 現在の構成

- 画像生成: Python
- データ取得:
  - ECMWF Open Data
  - NOAA NOMADS GFS
  - DWD ICON Open Data
- 地図境界: Natural Earth
- 生成画像: `1280x960` JPEG
- 表示: 静的 HTML / CSS / JavaScript
- 描画: 等圧線を主表示にし、主要な高気圧/低気圧中心へ `高` / `低` を自動配置

## キャッシュ

取得したデータは `cache/gridded/` に保存されます。

- `cache/gridded/latest-runs.json`
  - 各モデルの最新 run 情報と最大ステップ
- `cache/gridded/ecmwf/...`
  - ECMWF の GRIB
- `cache/gridded/gfs/...`
  - GFS の GRIB
- `cache/gridded/icon/...`
  - ICON の GRIB、座標、再格子化済み `.npz`

同じ run / step がすでにキャッシュにあれば、再取得せず再利用します。

## 実行前提

- Windows
- `tools/micromamba/`
- `tools/grib-env/`
- インターネット接続

このリポジトリでは `generate.ps1` が `tools/grib-env` の Python を使って生成します。

## 実行方法

```powershell
./generate.ps1
```

生成後にこれを開きます。

```text
docs/index.html
```

短い確認だけしたい場合:

```powershell
./generate.ps1 --limit-slots 1
```

モデル欠損も含めて厳密に失敗扱いしたい場合:

```powershell
./generate.ps1 --strict-models
```

## 出力

```text
docs/
  index.html
  styles.css
  app.js
  data/
    manifest.json
    manifest.js
    run-summary.json
    images/
      ecmwf/
      gfs/
      icon/
logs/
  generate-YYYYMMDD-HHMMSS.log
```

`manifest.json` と `manifest.js` は、同じ予報時刻ごとのモデル画像を指します。`run-summary.json` には採用 run、各モデルの出力スロット数、warning 一覧を保存します。`logs/` には `generate.ps1` の実行ログを残します。

画像内の地名ラベルは入れていません。代わりに、平滑化した気圧場から局所極大・極小を検出して、主要な中心に `高` / `低` を重ねています。

## 異常時の挙動

- run の取得に失敗しても、`cache/gridded/latest-runs.json` に直前の採用 run があればそれを再利用します。
- あるモデルの特定スロット生成に失敗しても、既定ではそのモデルだけを飛ばして他モデルの生成を続けます。
- 1 枚も生成できなかった場合だけ全体を失敗にします。
- 生成は `docs/data-next/` で staging してから `docs/data/` に差し替えるので、途中失敗しても公開中の `docs/data/` は壊しません。
- 厳密運用が必要なら `--strict-models` を付けると、モデル単位の失敗でも停止します。

## 定期実行へ移行する場合

推奨は Windows タスクスケジューラです。`generate.ps1` を 1 時間ごと、または 3 時間ごとに実行する構成が扱いやすいです。ECMWF の公開遅延を考えると、run 時刻ぴったりではなく定期ポーリング型にする方が安定します。

実行コマンド例:

```text
powershell.exe -ExecutionPolicy Bypass -File C:\Users\n_m_n\webapp\tenki\generate.ps1
```

設定時のポイント:

- 開始フォルダはリポジトリルート `C:\Users\n_m_n\webapp\tenki`
- 成功/失敗確認は `logs/` と `docs/data/run-summary.json`
- 失敗時も直前成功分の `docs/data/` は残る
- 将来 GitHub Actions に移す場合も、起点は `generate.ps1` のままでよい

## データ出典

- Forecast data: ECMWF Open Data / NOAA NOMADS GFS / DWD ICON Open Data
- Map boundary data: Natural Earth

Natural Earth の地図境界データはパブリックドメインです。

## 主な実装ファイル

- `generate.ps1`
- `tools/gridded_generator.py`
- `docs/index.html`
- `docs/app.js`
- `docs/styles.css`
