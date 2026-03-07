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

## 出力

```text
docs/
  index.html
  styles.css
  app.js
  data/
    manifest.json
    manifest.js
    images/
      ecmwf/
      gfs/
      icon/
```

`manifest.json` と `manifest.js` は、同じ予報時刻ごとの 3 モデル画像を指します。

画像内の地名ラベルは入れていません。代わりに、平滑化した気圧場から局所極大・極小を検出して、主要な中心に `高` / `低` を重ねています。

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
