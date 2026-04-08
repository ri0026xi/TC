# TwinCAT Conveyor Control System

TwinCAT PLC によるコンベア制御システムと、リアルタイム WebHMI、および自動 TDD パイプラインを統合したプロジェクトです。

## システム構成

```
┌─────────────┐     ADS      ┌──────────────┐   WebSocket   ┌──────────────┐
│  TwinCAT    │◄────────────►│  bridge.py   │◄────────────►│  WebHMI      │
│  PLC (UmRT) │  pyads       │  (aiohttp)   │  JSON         │  (React SPA) │
└─────────────┘              └──────────────┘               └──────────────┘
```

### PLC (`PLC_JobManagementFramework/`)

- **ジョブ管理フレームワーク**: `FB_Executor` / `Future` パターンによる汎用タスク実行基盤
- **コンベア制御**: `PRG_Conveyor` — モーター制御、センサー監視、アラーム管理
- **TcUnit テスト**: `FB_TaskRunner_Test`, `FB_ConveyorControl_Test`
- **Variant 分離**: `{IF NOT defined (Release)}` プラグマでテストコードを Release ビルドから除外

### WebHMI (`web_hmi/`)

- **bridge.py**: pyads で PLC の ADS 変数を読み書きし、WebSocket でブラウザにブロードキャスト
- **index.html**: React SPA — リアルタイムダッシュボード（速度/負荷ゲージ、トレンドグラフ、コンベア図、アラーム表示）
- デモモード (`--demo`) で PLC なしの開発が可能

### TDD パイプライン (`scripts/`)

- **twincat_tdd.py**: UmRT 起動 → Variant 切替 → TcUnit-Runner → xUnit XML パース → JSON レポート
- **twincat_variant.py**: DTE COM 経由で TwinCAT Variants (Test/Release) を切り替え
- **ads_monitor.py**: ADS 状態監視ユーティリティ

## クイックスタート

### テスト実行

```bash
python scripts/twincat_tdd.py 2>tdd_log.txt
```

stdout に JSON レポートが出力されます（`status: "PASS"` / `"TEST_FAIL"`）。

### WebHMI 起動

```bash
# PLC 接続モード
python web_hmi/bridge.py

# デモモード（PLC 不要）
python web_hmi/bridge.py --demo
```

ブラウザで `http://localhost:8080` にアクセスします。

## 動作環境

- TwinCAT XAE 3.1 Build 4024.15+
- TwinCAT Usermode Runtime (TF1700)
- TcUnit + TcUnit-Runner
- Python 3.12+ (`pyads`, `aiohttp`)

## ライセンス

ジョブ管理フレームワークの詳細は [Beckhoff Japan TwinCAT HowTo](https://beckhoff-jp.github.io/TwinCATHowTo/job_management/index.html) を参照してください。
